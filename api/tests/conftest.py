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

import shutil
from pathlib import Path

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


@pytest.fixture
def scaffold_cleanup():
    """Cleanup helper for tests that POST /api/projects with non-`agent-teams` names.

    Why: POST /api/projects scaffolds `context/projects/<name>/` on disk. The DB
    row is soft-deleted on test exit, but the filesystem folder is not — without
    this fixture every run leaks dirs into the working tree (M8).

    Usage — register the project name during the test, the fixture removes the
    folder on teardown regardless of test outcome:

        async def test_x(client, scaffold_cleanup):
            name = _unique_name("proj-x")
            scaffold_cleanup(name)
            await client.post("/api/projects", json=_project_create_payload(name))
            ...

    Pulls repo_root from src.settings so tests and the router share the same
    on-disk root. `shutil.rmtree(... ignore_errors=True)` keeps teardown safe
    when the folder doesn't exist (e.g., POST failed before scaffolding).
    """
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    names: list[str] = []

    def register(name: str) -> str:
        names.append(name)
        return name

    yield register

    for name in names:
        target = repo_root / "context" / "projects" / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


@pytest.fixture
async def db_session():
    """Direct AsyncSession for tests that need to read tables without a public
    HTTP endpoint (e.g., `tasks_history` for audit-row counts).

    Use sparingly — prefer HTTP-based testing. Reserved for assertions on
    audit / trigger-only side effects that the public API doesn't expose.
    """
    from src.db import SessionLocal

    async with SessionLocal() as session:
        yield session
