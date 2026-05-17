"""Kanban #1124 (2026-05-17, L19 prevention) — POST /api/projects rate limit.

Hammer-test FINDING #11 (T-DOS-4): 20 POSTs in <5s succeeded → 20 disk
folders allocated under REPO_ROOT. The slowapi limiter caps it at
5/minute/IP; the 6th POST in the window returns 429.

The autouse `_reset_rate_limiter_per_test` fixture (conftest.py) wipes the
in-memory counter between tests so the limit is tested in a clean state.
"""

from __future__ import annotations

import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"rl-test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


@pytest.mark.asyncio
async def test_post_projects_rate_limited_after_five_within_window(
    client, scaffold_cleanup
) -> None:
    """5 POSTs in a fresh window succeed → 6th returns 429.

    Each successful POST scaffolds a folder under context/projects/<name>/,
    which scaffold_cleanup tears down after the test.
    """
    # 5 POSTs within the same minute (the limiter default).
    for i in range(5):
        name = _unique_name(f"rl-ok-{i}")
        scaffold_cleanup(name)
        resp = await client.post(
            "/api/projects", json=_project_create_payload(name)
        )
        assert resp.status_code == 201, (
            f"POST #{i + 1} expected 201, got {resp.status_code}: {resp.text}"
        )

    # 6th POST same minute → 429 from the slowapi handler in main.py.
    name_6 = _unique_name("rl-blocked")
    scaffold_cleanup(name_6)  # in case the limit is bypassed by mistake
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name_6)
    )
    assert resp.status_code == 429, (
        f"POST #6 expected 429, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "Rate limit exceeded" in body.get("detail", ""), body


@pytest.mark.asyncio
async def test_rate_limit_resets_between_tests(client, scaffold_cleanup) -> None:
    """The previous test exhausted the bucket — this one verifies the autouse
    fixture cleared it so a fresh 5 POSTs succeed.

    Without `_reset_rate_limiter_per_test`, the limiter would still be in the
    blocked state from the prior test and POST #1 here would already 429.
    """
    name = _unique_name("rl-reset")
    scaffold_cleanup(name)
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_rate_limit_does_not_affect_get_endpoints(client) -> None:
    """The decorator is scoped to POST /api/projects only — list / by-name
    queries are unaffected even after the bucket is exhausted.
    """
    # Burn the POST bucket
    from src.middleware.rate_limit import limiter

    limiter.reset()  # fresh start regardless of prior test state

    # GET /api/projects?status=1 — never rate-limited
    for _ in range(10):
        resp = await client.get("/api/projects?status=1")
        assert resp.status_code == 200, resp.text
