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


# ===========================================================================
# Test 4 (negative) — negative cache token value → 422 from Field(ge=0)
# ===========================================================================
#
# SessionRunUpdate declares:
#   cache_read_input_tokens: int | None = Field(default=None, ge=0)
#   cache_creation_input_tokens: int | None = Field(default=None, ge=0)
#
# Pydantic v2 enforces ge=0 at parse time → 422 Unprocessable Entity.
# The 200 path must NOT be reached — this is a schema-layer rejection.
# We verify the exact HTTP code (422 ≠ 400 ≠ 200).


@pytest.mark.asyncio
async def test_negative_cache_token_returns_422(client, scaffold_cleanup) -> None:
    """PATCH with cache_read_input_tokens=-1 must return 422 (ge=0 violated)."""
    project = await _make_project(client, scaffold_cleanup, slug="g2-neg422")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id = create_resp.json()["id"]

        # Negative cache_read_input_tokens — must be rejected by schema validation.
        resp = await client.patch(
            f"/api/session_runs/{run_id}",
            json={
                "status": "running",
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "cache_read_input_tokens": -1,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert resp.status_code == 422, (
            f"expected 422 Unprocessable Entity for negative cache token, "
            f"got {resp.status_code}: {resp.text}"
        )

        # Negative cache_creation_input_tokens must also be rejected.
        resp2 = await client.patch(
            f"/api/session_runs/{run_id}",
            json={
                "status": "running",
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "cache_creation_input_tokens": -1,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert resp2.status_code == 422, (
            f"expected 422 Unprocessable Entity for negative cache_creation token, "
            f"got {resp2.status_code}: {resp2.text}"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ===========================================================================
# Test 5 (unknown model) — unknown provider/model with cache tokens → no crash
# ===========================================================================
#
# The handler catches ValueError from compute_cost, logs a warning, and leaves
# total_cost_usd unchanged (DB default 0.0000 / whatever was persisted before).
# Supplying cache tokens alongside an unknown (provider, model) pair must NOT
# make PATCH crash — the response must still be 200 and cost stays at 0.
#
# Positive-path pairing: we verify on a KNOWN model that cost IS computed
# (non-zero), ensuring the test would catch a regression where cost is always 0.


@pytest.mark.asyncio
async def test_unknown_model_with_cache_tokens_does_not_crash(
    client, scaffold_cleanup
) -> None:
    """PATCH with unknown (provider, model) + cache tokens → 200, cost not
    computed (left at 0); no 500 crash.  Paired with positive-path assertion
    on a known model to guard against vacuous-shape (Kanban #76).
    """
    from decimal import Decimal

    from src.services.cost_tracker import compute_cost

    project = await _make_project(client, scaffold_cleanup, slug="g2-unk")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        # ---- NEGATIVE path: unknown provider / model ----
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id_unknown = create_resp.json()["id"]

        resp_unknown = await client.patch(
            f"/api/session_runs/{run_id_unknown}",
            json={
                "status": "done",
                "total_input_tokens": 10_000,
                "total_output_tokens": 5_000,
                "cache_read_input_tokens": 20_000,
                "cache_creation_input_tokens": 8_000,
                "provider": "unknown-provider",
                "model": "unknown-model-xyz",
            },
        )
        assert resp_unknown.status_code == 200, (
            f"expected 200 for unknown model, got {resp_unknown.status_code}: "
            f"{resp_unknown.text}"
        )
        body_unknown = resp_unknown.json()
        # Cost must NOT have been computed — DB default is 0.
        assert Decimal(str(body_unknown["total_cost_usd"])) == Decimal("0.0000"), (
            f"unknown model: expected cost=0.0000 (not computed), "
            f"got {body_unknown['total_cost_usd']!r}"
        )

        # ---- POSITIVE path: same tokens on a KNOWN model → cost IS computed ----
        # This assertion proves the zero above is because the model is unknown,
        # NOT because cost computation is broken for all paths (Kanban #76 guard).
        create_resp2 = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp2.status_code == 201, create_resp2.text
        run_id_known = create_resp2.json()["id"]

        resp_known = await client.patch(
            f"/api/session_runs/{run_id_known}",
            json={
                "status": "done",
                "total_input_tokens": 10_000,
                "total_output_tokens": 5_000,
                "cache_read_input_tokens": 20_000,
                "cache_creation_input_tokens": 8_000,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert resp_known.status_code == 200, resp_known.text
        body_known = resp_known.json()
        expected_known = compute_cost(
            "anthropic",
            "claude-sonnet-4-6",
            10_000,
            5_000,
            cache_read_input_tokens=20_000,
            cache_creation_input_tokens=8_000,
        )
        # Cost must be non-zero and match exact value (positive-path guard).
        assert Decimal(str(body_known["total_cost_usd"])) == expected_known, (
            f"known model: expected cost={expected_known}, "
            f"got {body_known['total_cost_usd']!r}"
        )
        assert expected_known > Decimal("0"), (
            "sanity: known-model cost must be > 0 to prove positive path fires"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ===========================================================================
# Test 6 (cache-only) — input=0, output=0, cache>0, known model → cost>0
# ===========================================================================
#
# Concrete numeric example (claude-sonnet-4-6, $3/M input, $15/M output):
#
#   total_input_tokens          =       0  → $0.00
#   total_output_tokens         =       0  → $0.00
#   cache_read_input_tokens     = 500_000  → 500k/1M * $3.00 * 0.10 = $0.15
#   cache_creation_input_tokens = 200_000  → 200k/1M * $3.00 * 1.25 = $0.75
#
#   total = $0.15 + $0.75 = $0.9000
#
# The test asserts the concrete value $0.9000 directly (not just > 0) so that
# a multiplier regression (e.g. cache tokens dropped silently) would FAIL here.


@pytest.mark.asyncio
async def test_cache_only_patch_computes_cost_from_cache_tokens_alone(
    client, scaffold_cleanup
) -> None:
    """PATCH with input=0, output=0 but cache tokens > 0 on a known model →
    total_cost_usd is computed solely from cache multipliers (no base
    input/output cost), matching the locked $0.9000 value.
    """
    from decimal import Decimal

    from src.services.cost_tracker import compute_cost

    # Locked expected value — calculated above.  Also verified by compute_cost.
    EXPECTED_CACHE_ONLY_COST = Decimal("0.9000")

    # Sanity-check our constant against the live implementation.
    assert (
        compute_cost(
            "anthropic",
            "claude-sonnet-4-6",
            0,
            0,
            cache_read_input_tokens=500_000,
            cache_creation_input_tokens=200_000,
        )
        == EXPECTED_CACHE_ONLY_COST
    ), "test constant out of sync with compute_cost — update EXPECTED_CACHE_ONLY_COST"

    project = await _make_project(client, scaffold_cleanup, slug="g2-cacheonly")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/session_runs/{run_id}",
            json={
                "status": "done",
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "cache_read_input_tokens": 500_000,
                "cache_creation_input_tokens": 200_000,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Concrete assertion — not just > 0, but the exact expected value.
        assert Decimal(str(body["total_cost_usd"])) == EXPECTED_CACHE_ONLY_COST, (
            f"cache-only cost mismatch: got {body['total_cost_usd']!r}, "
            f"expected {EXPECTED_CACHE_ONLY_COST} "
            f"(read=500k*0.10x=$0.15, write=200k*1.25x=$0.75)"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ===========================================================================
# Test 7 (null provider/model guard) — cache tokens present, no provider/model
# ===========================================================================
#
# The handler guards:  `if provider is not None and model is not None and ...`
# When provider or model is absent (null/omitted), compute_cost is NOT called —
# cost column is not updated.  The PATCH must succeed with 200 and cost must
# stay at 0 (not mis-computed or crash).
#
# Positive-path pairing ensures the guard fires correctly: with provider+model
# present, cost IS computed (same Kanban #76 anti-vacuous pattern).


@pytest.mark.asyncio
async def test_null_provider_or_model_with_cache_tokens_does_not_crash(
    client, scaffold_cleanup
) -> None:
    """PATCH with cache tokens but provider=null / omitted → 200, cost not
    computed (stays 0).  Positive-path assertion proves cost IS computed when
    provider+model are supplied (Kanban #76 guard).
    """
    from decimal import Decimal

    from src.services.cost_tracker import compute_cost

    project = await _make_project(client, scaffold_cleanup, slug="g2-nullprov")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        # ---- provider and model both omitted ----
        create_resp = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id_omitted = create_resp.json()["id"]

        resp_omitted = await client.patch(
            f"/api/session_runs/{run_id_omitted}",
            json={
                "status": "done",
                "total_input_tokens": 10_000,
                "total_output_tokens": 5_000,
                "cache_read_input_tokens": 20_000,
                "cache_creation_input_tokens": 8_000,
                # provider and model intentionally absent
            },
        )
        assert resp_omitted.status_code == 200, (
            f"expected 200 when provider/model omitted, got "
            f"{resp_omitted.status_code}: {resp_omitted.text}"
        )
        body_omitted = resp_omitted.json()
        # Cost must NOT be computed — guard requires both provider AND model.
        assert Decimal(str(body_omitted["total_cost_usd"])) == Decimal("0.0000"), (
            f"expected cost=0 when provider/model absent, "
            f"got {body_omitted['total_cost_usd']!r}"
        )

        # ---- provider supplied but model explicitly null ----
        create_resp2 = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp2.status_code == 201, create_resp2.text
        run_id_null_model = create_resp2.json()["id"]

        resp_null_model = await client.patch(
            f"/api/session_runs/{run_id_null_model}",
            json={
                "status": "done",
                "total_input_tokens": 10_000,
                "total_output_tokens": 5_000,
                "cache_read_input_tokens": 20_000,
                "cache_creation_input_tokens": 8_000,
                "provider": "anthropic",
                "model": None,
            },
        )
        assert resp_null_model.status_code == 200, (
            f"expected 200 when model=null, got "
            f"{resp_null_model.status_code}: {resp_null_model.text}"
        )
        body_null_model = resp_null_model.json()
        assert Decimal(str(body_null_model["total_cost_usd"])) == Decimal("0.0000"), (
            f"expected cost=0 when model=null, "
            f"got {body_null_model['total_cost_usd']!r}"
        )

        # ---- POSITIVE path: provider + model both present → cost IS computed ----
        # Kanban #76 guard: proves the zeros above are from the guard, not
        # from cost computation being universally broken.
        create_resp3 = await client.post(
            f"/api/sessions/{session_id}/runs", json={}
        )
        assert create_resp3.status_code == 201, create_resp3.text
        run_id_full = create_resp3.json()["id"]

        resp_full = await client.patch(
            f"/api/session_runs/{run_id_full}",
            json={
                "status": "done",
                "total_input_tokens": 10_000,
                "total_output_tokens": 5_000,
                "cache_read_input_tokens": 20_000,
                "cache_creation_input_tokens": 8_000,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert resp_full.status_code == 200, resp_full.text
        body_full = resp_full.json()
        expected_full = compute_cost(
            "anthropic",
            "claude-sonnet-4-6",
            10_000,
            5_000,
            cache_read_input_tokens=20_000,
            cache_creation_input_tokens=8_000,
        )
        assert Decimal(str(body_full["total_cost_usd"])) == expected_full, (
            f"positive path: expected {expected_full}, "
            f"got {body_full['total_cost_usd']!r}"
        )
        assert expected_full > Decimal("0"), (
            "sanity: full-path cost must be > 0 to prove the guard fires correctly"
        )
    finally:
        _session_cleanup(session_id)
        await client.delete(f"/api/projects/{project_id}")
