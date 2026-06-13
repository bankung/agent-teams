"""Rate-limit smoke tests for POST /api/usage/events (Kanban #2355).

Coverage:
  - under limit → 201 (happy path unaffected);
  - over limit → 429 with a detail string (not 500);
  - per-project isolation: project A trips the limit, project B still gets 201;
  - window reset: after reset(), previously-blocked project_id allows new hits.

The service-level sliding window is testable without HTTP — checked separately
to verify the deque eviction logic.  The HTTP tests prove the integration (router
wires check_and_consume correctly and re-raises as 429 not 500).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str = "rl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "rate-limit test fixture",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _project_id(client) -> int:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _minimal_payload() -> dict:
    return {"model": "claude-opus-4-8", "input_tokens": 1, "output_tokens": 1}


# ---------------------------------------------------------------------------
# Service-layer unit test (no HTTP) — verifies deque eviction logic
# ---------------------------------------------------------------------------


def test_service_window_resets_after_eviction() -> None:
    """Hits within the window block; after the window elapses they evict and
    new hits succeed.  Tested via injected clock (no real sleep).
    """
    from src.services.usage_events_rate_limit import (
        RateLimitError,
        check_and_consume,
        reset,
    )

    reset()
    project_id = 99999  # won't collide with live data

    t0 = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    limit = 3

    # Fill the window.
    for i in range(limit):
        check_and_consume(project_id, now=t0 + timedelta(seconds=i * 0.1), limit=limit)

    # One more hit within the 10-second window → blocked.
    with pytest.raises(RateLimitError):
        check_and_consume(
            project_id,
            now=t0 + timedelta(seconds=5),
            limit=limit,
        )

    # After 10 s the old entries are outside the window and are evicted.
    # A hit 10.1s after t0 should succeed.
    check_and_consume(
        project_id,
        now=t0 + timedelta(seconds=10.1),
        limit=limit,
    )


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_under_limit_returns_201(client) -> None:
    """N requests under the limit all succeed with 201 (or 200 on dedup hit)."""
    import os

    from src.services.usage_events_rate_limit import reset

    reset()
    project_id = await _project_id(client)

    # Set a very low limit via env so we don't need 61 round-trips.
    original = os.environ.get("USAGE_EVENTS_RATE_LIMIT_PER_10S")
    os.environ["USAGE_EVENTS_RATE_LIMIT_PER_10S"] = "5"
    try:
        reset()
        for _ in range(5):
            resp = await client.post(
                "/api/usage/events",
                json=_minimal_payload(),
                headers={"X-Project-Id": str(project_id)},
            )
            # Each insert gets 201; idempotent dedup would be 200 but we send
            # no dedup_key so each is a fresh insert.
            assert resp.status_code == 201, resp.text
    finally:
        if original is None:
            os.environ.pop("USAGE_EVENTS_RATE_LIMIT_PER_10S", None)
        else:
            os.environ["USAGE_EVENTS_RATE_LIMIT_PER_10S"] = original


@pytest.mark.asyncio
async def test_over_limit_returns_429_not_500(client) -> None:
    """One request over the per-project limit → 429 with detail, not 500."""
    import os

    from src.services.usage_events_rate_limit import reset

    reset()
    project_id = await _project_id(client)

    os.environ["USAGE_EVENTS_RATE_LIMIT_PER_10S"] = "3"
    try:
        reset()
        for _ in range(3):
            r = await client.post(
                "/api/usage/events",
                json=_minimal_payload(),
                headers={"X-Project-Id": str(project_id)},
            )
            assert r.status_code == 201, r.text

        over = await client.post(
            "/api/usage/events",
            json=_minimal_payload(),
            headers={"X-Project-Id": str(project_id)},
        )
        # NEGATIVE: must be 429, not 500 and not 201.
        assert over.status_code == 429, over.text
        # Detail must mention rate limit — confirms it's our handler, not a generic 5xx.
        assert "rate limit" in over.json()["detail"].lower()
    finally:
        os.environ.pop("USAGE_EVENTS_RATE_LIMIT_PER_10S", None)


@pytest.mark.asyncio
async def test_per_project_isolation(client, scaffold_cleanup) -> None:
    """Project A trips the limit; project B still gets 201 (buckets are isolated)."""
    import os

    from src.services.usage_events_rate_limit import reset

    reset()
    project_a = await _project_id(client)

    name_b = scaffold_cleanup(_unique_name("rl-b"))
    resp_create = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    assert resp_create.status_code == 201, resp_create.text
    project_b = resp_create.json()["id"]

    os.environ["USAGE_EVENTS_RATE_LIMIT_PER_10S"] = "2"
    try:
        reset()

        # Exhaust project A's bucket.
        for _ in range(2):
            r = await client.post(
                "/api/usage/events",
                json=_minimal_payload(),
                headers={"X-Project-Id": str(project_a)},
            )
            assert r.status_code == 201, r.text

        # Project A is now rate-limited.
        blocked = await client.post(
            "/api/usage/events",
            json=_minimal_payload(),
            headers={"X-Project-Id": str(project_a)},
        )
        assert blocked.status_code == 429, blocked.text

        # POSITIVE: project B's bucket is untouched — still succeeds.
        ok_b = await client.post(
            "/api/usage/events",
            json=_minimal_payload(),
            headers={"X-Project-Id": str(project_b)},
        )
        assert ok_b.status_code == 201, ok_b.text
    finally:
        os.environ.pop("USAGE_EVENTS_RATE_LIMIT_PER_10S", None)
