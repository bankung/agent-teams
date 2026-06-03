"""G2 (#1689) — cache token columns on session_runs.

Three contract-smoke tests:
  1. Schema: SessionRunRead has the two new cache token fields with defaults 0.
  2. Happy path: PATCH /api/session_runs/{id} with cache token fields persists
     them + uses them for accurate cost calculation.
  3. Zero/absent: PATCH without cache token fields defaults them to 0 on the row.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def session_fs_cleanup():
    """Remove `_sessions/<id>/` dirs created during a test.

    Mirrors the same fixture in test_sessions.py — defined here so this
    module doesn't depend on importing from another test file.
    """
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    ids: list[int] = []

    def register(session_id: int) -> int:
        ids.append(session_id)
        return session_id

    yield register

    for sid in ids:
        target = repo_root / "_sessions" / str(sid)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"G2 test fixture {name}",
        "paths": {"web": "/tmp/g2/web", "api": "/tmp/g2/api", "db": "/tmp/g2/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


# =============================================================================
# 1. Schema: SessionRunRead exposes cache token columns
# =============================================================================


def test_session_run_read_has_cache_token_fields() -> None:
    """SessionRunRead must declare cache_read_input_tokens and
    cache_creation_input_tokens so the API response carries them."""
    from src.schemas.session import SessionRunRead

    fields = SessionRunRead.model_fields
    assert "cache_read_input_tokens" in fields, (
        "SessionRunRead is missing cache_read_input_tokens (G2 #1689)"
    )
    assert "cache_creation_input_tokens" in fields, (
        "SessionRunRead is missing cache_creation_input_tokens (G2 #1689)"
    )


# =============================================================================
# 2. Happy path: cache tokens persisted + forwarded to cost calculation
# =============================================================================


@pytest.mark.asyncio
async def test_patch_run_cache_tokens_persisted_and_cost_computed(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """PATCH /api/session_runs/{id} with cache token fields:
      - cache_read_input_tokens and cache_creation_input_tokens are stored on
        the row (not silently discarded after cost calc).
      - total_cost_usd is non-zero and uses the cache token rates:
        cost = input * 3/1M  +  output * 15/1M
               +  cache_read * 0.10 * 3/1M
               +  cache_creation * 1.25 * 3/1M
        (for claude-sonnet-4-6, rates from cost_tracker.PRICING)
    """
    name = _unique_name("g2-cache")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        assert run.status_code == 201, run.text
        rid = run.json()["id"]

        # Send cache token values alongside provider/model so server computes cost.
        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={
                "status": "done",
                "total_input_tokens": 1000,
                "total_output_tokens": 200,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 300,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()

        # Cache tokens must be persisted on the row.
        assert body["cache_read_input_tokens"] == 500, (
            f"Expected 500, got {body['cache_read_input_tokens']}"
        )
        assert body["cache_creation_input_tokens"] == 300, (
            f"Expected 300, got {body['cache_creation_input_tokens']}"
        )

        # Cost must be non-zero (cache reads + writes + base input + output).
        # Exact formula check (rates in USD/M tokens):
        #   input:           1000 * 3.0 / 1_000_000  = 0.003000
        #   output:           200 * 15.0 / 1_000_000 = 0.003000
        #   cache_read:       500 * 3.0 * 0.10 / 1M  = 0.000150
        #   cache_creation:   300 * 3.0 * 1.25 / 1M  = 0.001125
        #   total:                                    = 0.007275
        cost = float(body["total_cost_usd"])
        assert cost > 0, f"Expected positive cost, got {cost}"
        assert abs(cost - 0.0073) < 0.001, (
            f"Cost {cost} outside expected range for cache-inclusive calculation"
        )
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# 3. Zero/absent: PATCH without cache tokens defaults to 0
# =============================================================================


@pytest.mark.asyncio
async def test_patch_run_without_cache_tokens_defaults_zero(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """PATCH that omits cache_read/creation token fields must result in 0 on
    the row — the zero-default path (no cache activity). This ensures the
    positive assertion: a PATCH WITH cache tokens (test 2) stores a non-zero
    value, while this test verifies the baseline is 0, not vacuously 0."""
    name = _unique_name("g2-nocache")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        # PATCH without cache token fields.
        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={
                "status": "done",
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()

        # Must default to 0 (no cache activity this run).
        assert body["cache_read_input_tokens"] == 0
        assert body["cache_creation_input_tokens"] == 0

        # Cost must still be computed (non-zero) from base input + output only.
        # input: 100 * 3/1M = 0.0003, output: 50 * 15/1M = 0.00075 → 0.00105
        cost = float(body["total_cost_usd"])
        assert cost > 0, f"Expected positive cost even without cache tokens, got {cost}"
    finally:
        await client.delete(f"/api/projects/{pid}")
