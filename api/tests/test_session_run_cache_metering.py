"""G2 (#1689) — Mode-A cache-metering smoke tests.

Covers the NON-GATED slice: PATCH /api/session_runs/{id} now accepts
`cache_read_input_tokens` and `cache_creation_input_tokens` and forwards them
to `compute_cost` so cached-input cost uses the correct multipliers (0.10x
read, 1.25x write) rather than the full input rate.

Three tests:

1. Round-trip — POST run + PATCH with explicit cache tokens for a known model
   → assert total_cost_usd equals the exact value compute_cost would return
   (concrete numeric example), and that the run appears in
   GET /api/projects/stats cost_usage SUM for the project.

2. Idempotency-via-overwrite — PATCH the SAME run_id a second time with
   identical tokens → GET /api/projects/stats SUM is unchanged (PATCH
   overwrites, does not increment).

3. Schema smoke — cache fields omitted entirely → backward-compatible (cost
   computed without cache, existing callers unaffected).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"G2 cache metering fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, slug: str = "g2") -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _cost_usage(client, project_id: int) -> dict:
    resp = await client.get(f"/api/projects/stats?project_id={project_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1, f"expected 1 entry for project {project_id}: {body}"
    return body[0]["cost_usage"]


async def _make_session(client, project_id: int) -> int:
    resp = await client.post("/api/sessions", json={"project_id": project_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _session_cleanup(session_id: int) -> None:
    import shutil
    from pathlib import Path

    from src.settings import get_settings

    target = Path(get_settings().repo_root) / "_sessions" / str(session_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


# ===========================================================================
# Test 1 — round-trip: cache tokens produce the correct cost + appear in SUM
# ===========================================================================
#
# Concrete numeric example using claude-sonnet-4-6 ($3/M input, $15/M output):
#
#   input_tokens (non-cached)   = 100_000  → cost = 100k/1M * 3.00  = 0.30
#   output_tokens               = 50_000   → cost = 50k/1M  * 15.00 = 0.75
#   cache_read_input_tokens     = 200_000  → cost = 200k/1M * 3.00 * 0.10 = 0.06
#   cache_creation_input_tokens = 80_000   → cost = 80k/1M  * 3.00 * 1.25 = 0.30
#
#   total = 0.30 + 0.75 + 0.06 + 0.30 = 1.41 USD → quantized to 1.4100
#
# Before the G2 fix, cache tokens were silently dropped.  The handler called
# compute_cost(provider, model, 100_000, 50_000) → 0.30 + 0.75 = 1.05 USD.
# The test below would therefore have FAILED on the old code (1.05 != 1.41).


@pytest.mark.asyncio
async def test_cache_tokens_produce_correct_cost_and_appear_in_stats(
    client, scaffold_cleanup
) -> None:
    """PATCH with cache token fields → total_cost_usd uses 0.10x/1.25x
    multipliers; the run is included in the project's stats cost_usage SUM.
    """
    from src.services.cost_tracker import compute_cost

    project = await _make_project(client, scaffold_cleanup, slug="g2-round")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        # Create a run.
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id = create_resp.json()["id"]

        # PATCH with cache tokens.
        patch_resp = await client.patch(
            f"/api/session_runs/{run_id}",
            json={
                "status": "done",
                "total_input_tokens": 100_000,
                "total_output_tokens": 50_000,
                "cache_read_input_tokens": 200_000,
                "cache_creation_input_tokens": 80_000,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert patch_resp.status_code == 200, patch_resp.text
        body = patch_resp.json()

        # Server-computed cost must include cache multipliers.
        expected = compute_cost(
            "anthropic",
            "claude-sonnet-4-6",
            100_000,
            50_000,
            cache_read_input_tokens=200_000,
            cache_creation_input_tokens=80_000,
        )
        # expected = Decimal("1.4100")
        assert Decimal(str(body["total_cost_usd"])) == expected, (
            f"cost mismatch: got {body['total_cost_usd']!r}, expected {expected}"
        )

        # Run must appear in the stats cost_usage SUM.
        cu = await _cost_usage(client, project_id)
        assert cu["session_run_count"] == 1, cu
        assert cu["total_input_tokens"] == 100_000, cu
        assert cu["total_output_tokens"] == 50_000, cu
        # The SUM picks up total_cost_usd from the persisted column.
        assert Decimal(cu["total_cost_usd"]) == expected, (
            f"stats cost_usage SUM mismatch: got {cu['total_cost_usd']!r}, "
            f"expected {expected}"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ===========================================================================
# Test 2 — idempotency-via-overwrite
# ===========================================================================
#
# PATCHing the same run_id a second time with identical tokens MUST leave
# the stats SUM unchanged — PATCH overwrites, does not increment.


@pytest.mark.asyncio
async def test_patch_same_run_twice_leaves_stats_sum_unchanged(
    client, scaffold_cleanup
) -> None:
    """Idempotency: re-PATCHing the same run_id with identical token counts
    must NOT double-count cost in GET /api/projects/stats cost_usage.
    """
    from src.services.cost_tracker import compute_cost

    project = await _make_project(client, scaffold_cleanup, slug="g2-idem")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id = create_resp.json()["id"]

        patch_body = {
            "status": "done",
            "total_input_tokens": 60_000,
            "total_output_tokens": 20_000,
            "cache_read_input_tokens": 100_000,
            "cache_creation_input_tokens": 40_000,
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        }

        # First PATCH.
        r1 = await client.patch(f"/api/session_runs/{run_id}", json=patch_body)
        assert r1.status_code == 200, r1.text

        cu_after_first = await _cost_usage(client, project_id)
        assert cu_after_first["session_run_count"] == 1, cu_after_first

        expected = compute_cost(
            "anthropic",
            "claude-sonnet-4-6",
            60_000,
            20_000,
            cache_read_input_tokens=100_000,
            cache_creation_input_tokens=40_000,
        )
        assert Decimal(cu_after_first["total_cost_usd"]) == expected, cu_after_first

        # Second PATCH — identical tokens on the SAME run_id.
        r2 = await client.patch(f"/api/session_runs/{run_id}", json=patch_body)
        assert r2.status_code == 200, r2.text

        cu_after_second = await _cost_usage(client, project_id)
        # Still exactly 1 run and the SAME cost (overwrite, not increment).
        assert cu_after_second["session_run_count"] == 1, (
            f"expected 1 run but got {cu_after_second['session_run_count']}: {cu_after_second}"
        )
        assert Decimal(cu_after_second["total_cost_usd"]) == expected, (
            f"double-count detected: second PATCH incremented stats SUM "
            f"from {expected} to {cu_after_second['total_cost_usd']}"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ===========================================================================
# Test 3 — backward-compat: omitting cache fields still works
# ===========================================================================
#
# Existing callers that send only input/output tokens must continue to work
# with no breakage (cache_read/creation default to 0).


@pytest.mark.asyncio
async def test_cache_fields_omitted_backward_compatible(
    client, scaffold_cleanup
) -> None:
    """Callers that omit cache token fields must get the same behavior as
    before G2 — cost computed with cache=0 (pre-G2 behavior preserved).
    """
    from src.services.cost_tracker import compute_cost

    project = await _make_project(client, scaffold_cleanup, slug="g2-compat")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id = create_resp.json()["id"]

        # No cache fields in the body.
        r = await client.patch(
            f"/api/session_runs/{run_id}",
            json={
                "status": "done",
                "total_input_tokens": 500_000,
                "total_output_tokens": 100_000,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()

        # Cost must equal compute_cost with zero cache.
        expected_no_cache = compute_cost(
            "anthropic", "claude-sonnet-4-6", 500_000, 100_000
        )
        assert Decimal(str(body["total_cost_usd"])) == expected_no_cache, (
            f"backward-compat broken: got {body['total_cost_usd']!r}, "
            f"expected {expected_no_cache}"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")
