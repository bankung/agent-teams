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

Issue 2 of the 2026-05-09 raw-SQL-DML incident response: this conftest now
isolates pytest from the live `agent_teams` DB by pointing every test run at a
freshly-built `agent_teams_test` DB. The override happens at module-import
time (top of file) BEFORE any `from src import ...` statement so `src.db`
binds its module-level engine to the test DB. The session-scoped fixture
below drops + creates the DB, runs alembic upgrade, runs seed, then drops the
DB on teardown. See context/projects/agent-teams/shared/decisions.md for the
locked design rationale.
"""

from __future__ import annotations

# ---- Test-DB isolation env override (must run BEFORE any src.* import) -----
# Build the test DSN by swapping the trailing dbname on whatever DATABASE_URL
# the harness is providing (or the docker-compose default `db:5432/agent_teams`).
# This must execute before `from src import ...` because src.db builds a
# module-level engine at import time from get_settings().database_url.
import os as _os

_DEFAULT_DEV_URL = "postgresql+asyncpg://postgres:postgres@db:5432/agent_teams"
_TEST_URL = (
    _os.environ.get("DATABASE_URL", _DEFAULT_DEV_URL).rsplit("/", 1)[0]
    + "/agent_teams_test"
)
_os.environ["DATABASE_URL"] = _TEST_URL
# Kanban #707 (T2): disable the apscheduler tick during pytest. The lifespan
# context still enters/exits cleanly (smoke-tested explicitly), but no
# background job fires. Avoids flakiness from time-sensitive ticks racing
# fixtures and per-test test-DB resets.
_os.environ.setdefault("APP_SCHEDULER_DISABLE", "true")
# ----------------------------------------------------------------------------

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# Live-DB DSN derived from the SAME pattern as the test-DB rewrite at the top
# of this module. We re-derive (rather than capture pre-rewrite) so the fixture
# is robust to the rewrite running before this code path. The dbname is pinned
# to the canonical live name `agent_teams` so the guard catches any drift
# regardless of what `DATABASE_URL` currently points at.
_LIVE_DB_URL = (
    _os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/agent_teams"
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _live_db_row_count_invariant():
    """Session-scope guard: live `agent_teams` DB row totals MUST be unchanged
    by the pytest run.

    This is the broader catch-all for the test-DB isolation contract — not
    just "the engine URL is correct at import time" (covered by
    `tests/test_db_isolation.py`) but "no actual writes leak to the live DB
    through ANY code path during the session".

    Mechanism: open a separate async engine pointed at `agent_teams` (the
    LIVE DB, NOT the test DB), count `projects` + `tasks` total rows
    (including soft-deleted — we want raw row totals) before yielding,
    re-count on teardown, assert equality. On mismatch raise with the deltas
    + a hint pointing the operator at the most likely culprit.

    Ordering: defined ABOVE `_setup_test_database` so pytest-asyncio's
    definition-order session-fixture setup runs this first (baseline captured
    before the test DB build).

    See conftest header + context/projects/agent-teams/shared/decisions.md
    (2026-05-09 entry) for the locked isolation design this guard pins.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    live_engine = create_async_engine(_LIVE_DB_URL, isolation_level="AUTOCOMMIT")

    async def _counts() -> tuple[int, int]:
        async with live_engine.connect() as conn:
            proj = (await conn.execute(text("SELECT count(*) FROM projects"))).scalar_one()
            tasks = (await conn.execute(text("SELECT count(*) FROM tasks"))).scalar_one()
        return int(proj), int(tasks)

    try:
        pre_proj, pre_tasks = await _counts()
    except Exception:
        # If the live DB isn't reachable (CI without it, dev workstation
        # offline), skip the invariant rather than mis-fire. The two import-
        # time checks in `tests/test_db_isolation.py` remain as the load-
        # bearing canary.
        await live_engine.dispose()
        yield
        return

    yield

    try:
        post_proj, post_tasks = await _counts()
    finally:
        await live_engine.dispose()

    if (post_proj, post_tasks) != (pre_proj, pre_tasks):
        raise AssertionError(
            "LIVE DB ROW COUNT DRIFT — pytest wrote to `agent_teams` (the "
            "production DB) during this session. The test-DB isolation in "
            "`tests/conftest.py` (DATABASE_URL rewrite at lines 32-39) did "
            "NOT contain this run.\n"
            f"  projects: {pre_proj} -> {post_proj} (delta {post_proj - pre_proj:+d})\n"
            f"  tasks:    {pre_tasks} -> {post_tasks} (delta {post_tasks - pre_tasks:+d})\n"
            "Hint: check the most recent test additions for fixtures that "
            "open their own engine / SessionLocal without going through "
            "`from src.db import ...` (which IS bound to the test DB after "
            "the conftest rewrite). Also check any subprocess that re-reads "
            "DATABASE_URL outside this process — alembic upgrade is the "
            "known-safe path because we explicitly pass test_url in env."
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_test_database():
    """Drop + create `agent_teams_test`, run alembic upgrade head, run seed.

    Runs once per pytest invocation. Teardown drops the test DB so the next
    invocation starts from a clean slate. The DATABASE_URL env override at
    the top of this module guarantees alembic's env.py and src.db both bind
    to the test DB (alembic via its env reading get_settings(); src.db
    similarly).

    Defensive `pg_terminate_backend` before DROP DATABASE so a leftover
    connection from a prior crash doesn't block the drop.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    test_url = os.environ["DATABASE_URL"]
    # Connect to the maintenance `postgres` database to issue CREATE/DROP.
    admin_url = test_url.rsplit("/", 1)[0] + "/postgres"

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = 'agent_teams_test' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text("DROP DATABASE IF EXISTS agent_teams_test"))
            await conn.execute(text("CREATE DATABASE agent_teams_test"))
    finally:
        await admin_engine.dispose()

    # Run alembic upgrade head against the test DB. Subprocess keeps alembic's
    # sync internals out of our async event loop, and the env var (set at the
    # top of this module) flows into the child process so env.py picks it up.
    alembic_run = subprocess.run(
        ["alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        cwd="/repo/api",
        env={**os.environ, "DATABASE_URL": test_url},
    )
    if alembic_run.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed for test DB.\n"
            f"stdout:\n{alembic_run.stdout}\n"
            f"stderr:\n{alembic_run.stderr}"
        )

    # Run the seed against the test DB. `_seed` is the async coroutine inside
    # scripts/seed.py — it opens its own session via SessionLocal which is now
    # bound to agent_teams_test (since src.db built its engine after the env
    # override).
    from scripts.seed import _seed

    await _seed()

    # Dispose the engine after seed so the connection used during seed (which
    # bound to the seed-time event loop) is released; the per-test
    # `_reset_engine_pool_per_test` fixture takes over from here.
    from src import db as _db

    await _db.engine.dispose()

    yield

    # Teardown — dispose any connections then drop the test DB.
    try:
        await _db.engine.dispose()
    except Exception:
        pass

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = 'agent_teams_test' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text("DROP DATABASE IF EXISTS agent_teams_test"))
    finally:
        await admin_engine.dispose()


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
