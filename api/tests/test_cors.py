"""CORS middleware contract tests — Kanban #805.

FE `web/lib/api.ts` `jsonFetch` calls localhost:8456 from a browser origin
(localhost:5431). Without `CORSMiddleware` FastAPI returns 405 on every
preflight OPTIONS, and the browser surfaces `TypeError: Failed to fetch`.

These tests pin:
- Preflight OPTIONS from an allowed origin → 200 + Access-Control-Allow-Origin
  echoes the request Origin + Allow-Methods header present.
- GET from an allowed origin → 200 + Access-Control-Allow-Origin set.
- Preflight OPTIONS from a disallowed origin → no
  Access-Control-Allow-Origin header (Starlette default behavior — the
  browser will then block the request client-side).
"""

from __future__ import annotations

import pytest


ALLOWED_ORIGIN = "http://localhost:5431"
DISALLOWED_ORIGIN = "http://evil.example.com"


@pytest.mark.asyncio
async def test_preflight_allowed_origin_returns_cors_headers(client) -> None:
    """OPTIONS /api/projects from localhost:5431 must succeed with CORS headers."""
    resp = await client.options(
        "/api/projects",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200, (
        f"expected 200 on preflight from allowed origin, got {resp.status_code}"
    )
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert "access-control-allow-methods" in {k.lower() for k in resp.headers.keys()}


@pytest.mark.asyncio
async def test_get_allowed_origin_includes_allow_origin_header(client) -> None:
    """GET /api/projects?status=1 with Origin: localhost:5431 must echo origin."""
    resp = await client.get(
        "/api/projects?status=1",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


@pytest.mark.asyncio
async def test_preflight_disallowed_origin_omits_allow_origin(client) -> None:
    """OPTIONS from a non-allowlisted origin must NOT carry
    Access-Control-Allow-Origin. Starlette's CORSMiddleware still responds
    (often with 400) but omits the allow-origin header so the browser
    enforces the block client-side.
    """
    resp = await client.options(
        "/api/tasks",
        headers={
            "Origin": DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    # Either the middleware returns 400 (disallowed-origin preflight) or 200
    # without the allow-origin header; either way the critical contract is
    # that no allow-origin header echoes the disallowed origin.
    assert resp.headers.get("access-control-allow-origin") != DISALLOWED_ORIGIN
    # And the header MUST NOT be a wildcard either — that would defeat the
    # allow-list. Absent or empty is the only acceptable outcome.
    allow_origin = resp.headers.get("access-control-allow-origin")
    assert allow_origin in (None, ""), (
        f"disallowed origin should not receive allow-origin header, got {allow_origin!r}"
    )
