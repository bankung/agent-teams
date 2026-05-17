"""Kanban #1124 (2026-05-17, L19 prevention) — per-IP rate limit on
endpoints that allocate disk-side resources (currently POST /api/projects).

Hammer-test FINDING #11 (T-DOS-4): 20 POST /api/projects in <5s succeeded —
20 folders created on disk via REPO_ROOT mount. A network-reachable attacker
with valid request shape could flood `context/projects/` with thousands of
folders, and soft-delete cleans the DB row but NOT the disk (companion fix in
projects.py.delete_project archives the folder to `.deleted/` rather than
hard-removing).

Library: slowapi. Storage is in-memory (the default); fine for the single-
instance docker-compose dev deployment. A production-scale multi-replica
deployment would point slowapi at a shared Redis backend.

The limit is INTENTIONALLY low (5/minute/IP). Legitimate project creation is
human-paced; sustained high-rate POSTs are a defensive trigger by definition.
Override via env `RATE_LIMIT_PROJECTS_POST` (e.g. `"30/minute"`) for the rare
ops scenario that needs to bulk-import projects — tests use this hook to
verify both the live limit and a custom override.

Test hook: tests that want to verify the limit fires can read
`RATE_LIMIT_PROJECTS_POST` at decoration time via `_projects_post_limit()`
so monkey-patching env vars works. The decorator on the route is applied
once at import time; tests must use `app.state.limiter.reset()` to clear
the in-memory counter between tests (handled by the `_reset_rate_limiter`
fixture in conftest.py).
"""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# `key_func=get_remote_address` keys the bucket on the client IP. Behind a
# proxy / load balancer the request.client.host is the proxy IP, so a future
# production deployment needs to either set up `X-Forwarded-For` parsing
# (slowapi has a helper) or run slowapi behind a layer that propagates the
# real client identity. For docker-compose dev there is no proxy chain.
#
# `default_limits=[]` means the limiter does NOT apply a global limit; only
# the explicit `@limiter.limit(...)` decorator on the route fires. Other
# endpoints are unaffected.
# `headers_enabled=False`: slowapi's header injection requires the route to
# return a `starlette.Response` (it inspects the response to set X-RateLimit-*).
# Our POST /api/projects returns the Pydantic model directly, so slowapi raises
# `parameter response must be an instance of starlette.responses.Response` on
# every successful call. Disabling headers keeps the 429 path intact (the
# exception handler in main.py still fires) without forcing every route on the
# limiter to materialise a Response.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    headers_enabled=False,
)


def _projects_post_limit() -> str:
    """Resolve the POST /api/projects per-IP limit at CALL time.

    Read env on each call so tests can monkeypatch RATE_LIMIT_PROJECTS_POST
    without re-import. Default `5/minute` matches the L19 spec.
    """
    return os.environ.get("RATE_LIMIT_PROJECTS_POST", "5/minute")
