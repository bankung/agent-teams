"""Pytest fixtures shared across the api/tests/ tree.

Phase 2b.2 shipped import-level smoke tests; QA phase added DB-backed contract
tests in `tests/test_routes_smoke.py`.

Why we dispose the engine before each async test: `src.db` builds a
module-level async engine on import. asyncpg connections bind to the running
event loop the first time the pool dispenses them. With pytest-asyncio's
default function-scoped loop, each test gets a fresh loop — but the engine's
pool keeps the connection bound to the *first* test's (now-closed) loop,
surfacing as "got Future ... attached to a different loop" RuntimeErrors. The
autouse fixture below disposes the pool before each test so the next call
opens a fresh asyncpg connection on the current loop.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
async def _reset_engine_pool_per_test():
    """Drop the async engine's connection pool before each test so connections
    re-bind to whatever loop the current test is running on. Without this,
    tests that hit the DB after the first one fail with
    "Future attached to a different loop".

    Synchronous tests still benefit (engine.dispose() is cheap on an idle pool).
    """
    from src import db

    await db.engine.dispose()
    yield
    # Best-effort dispose on teardown so the leaked-connection warning at exit
    # doesn't fire when the loop closes.
    try:
        await db.engine.dispose()
    except Exception:
        pass


@pytest.fixture
async def client():
    """AsyncClient bound to the FastAPI ASGI app — no real network."""
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
