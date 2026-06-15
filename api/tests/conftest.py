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

2026-05-17 incident response (L2 prevention): `_live_db_row_count_invariant`
previously silenced ALL pre-snapshot failures via a bare `except Exception:
yield; return`, hiding live-DB drift for the entire session. The guard now
emits a loud `UserWarning` (markers: "DISABLED", "2026-05-17") and retries
once before accepting a genuine offline state.
"""

from __future__ import annotations

# ---- Test-DB isolation env override (must run BEFORE any src.* import) -----
# Build the test DSN by swapping the trailing dbname on whatever DATABASE_URL
# the harness is providing (or the docker-compose default `db:5432/agent_teams`).
# This must execute before `from src import ...` because src.db builds a
# module-level engine at import time from get_settings().database_url.
#
# Kanban #1109 (L4 prevention) — incident 2026-05-17 dev DB wipe:
# the rewritten DATABASE_URL now uses the constrained `pytest_runner` role
# (created by alembic migration 0034_pytest_runner_role) which has only
# SELECT on the live `agent_teams` DB. If every software-layer defense (L1
# hook, L2 invariant, L3 lazy-load) is bypassed, the DB engine itself will
# refuse destructive ops on live data via `permission denied for table ...`.
#
# Admin operations (DROP/CREATE DATABASE, pg_terminate_backend) still need
# postgres superuser — captured separately into env `_PG_ADMIN_URL` for the
# `_setup_test_database` fixture and for tests that create throwaway DBs
# (e.g. test_tool_calls.py).
import os as _os
import warnings as _warnings

_DEFAULT_DEV_URL = "postgresql+asyncpg://postgres:postgres@db:5432/agent_teams"
# Dev fallback — kept in lockstep with the same constant in the migration
# (api/alembic/versions/2026_05_17_1200_pytest_runner_role.py) and the
# docker-compose default. Production deployments MUST set PYTEST_DB_PASSWORD
# in their .env (it is documented in .env.example).
_DEV_DEFAULT_PYTEST_PASSWORD = "pytest_runner_dev_only_NOT_FOR_PROD"
_PYTEST_PASSWORD = _os.environ.get("PYTEST_DB_PASSWORD") or _DEV_DEFAULT_PYTEST_PASSWORD
if _PYTEST_PASSWORD == _DEV_DEFAULT_PYTEST_PASSWORD and not _os.environ.get(
    "PYTEST_DB_PASSWORD"
):
    _warnings.warn(
        "PYTEST_DB_PASSWORD env not set — falling back to the documented dev "
        "default for the `pytest_runner` role. This is the L4 prevention layer "
        "for the 2026-05-17 dev-DB-wipe incident. Set PYTEST_DB_PASSWORD in "
        "your .env to rotate (see .env.example).",
        UserWarning,
        stacklevel=2,
    )

# Original / superuser DSN — kept for admin work that pytest_runner cannot do
# (DROP/CREATE DATABASE on the `postgres` maintenance DB, pg_terminate_backend
# of other-user sessions). Exported via env so test_tool_calls.py and any
# other test that needs admin access can reuse it without re-deriving.
_ORIGINAL_DATABASE_URL = _os.environ.get("DATABASE_URL", _DEFAULT_DEV_URL)
_os.environ["_PG_ADMIN_URL"] = _ORIGINAL_DATABASE_URL

# Build the constrained pytest DSN: swap `postgres:<pw>` -> `pytest_runner:<pw>`.
# Use the standard URL split (user:pw@host:port/db) — the rsplit on "/" gives
# us "<scheme>://<user>:<pw>@<host>:<port>" which we then surgically rewrite
# user+pw on. Regex is intentionally narrow (anchor on `://` + literal
# `postgres:`) so we don't accidentally rewrite a host called `postgres`.
import re as _re

_base = _ORIGINAL_DATABASE_URL.rsplit("/", 1)[0]
_base = _re.sub(
    r"://postgres:[^@]+@",
    f"://pytest_runner:{_PYTEST_PASSWORD}@",
    _base,
    count=1,
)
_TEST_URL = _base + "/agent_teams_test"
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
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import filelock

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

    # Enumerate all user tables in the public schema, then snapshot every one.
    # Using pg_tables + per-table SELECT count(*) (option 2 from #815) gives
    # exact counts rather than the slightly-noisy pg_stat_user_tables estimates.
    #
    # Excluded tables (Kanban #1371):
    #   alembic_version — legitimately changes during dev migrations.
    #   tasks           — high-churn: live API writes tasks during any test run
    #                     (operator Kanban usage). The pytest_runner role is
    #                     SELECT-only on agent_teams (L4 gate), so test code
    #                     cannot write here; false-positive drift from concurrent
    #                     API activity would swamp signal.
    #   tasks_history   — populated by the tasks audit trigger + notification_router
    #                     direct inserts. Every tasks write (live API) generates
    #                     tasks_history rows. Same concurrent-API-activity reason.
    #
    # The remaining tables (projects, sessions, projects_audit, etc.) are
    # low-churn enough that any increase during a ~10-minute test run is a
    # genuine signal worth investigating.
    _INVARIANT_EXCLUDED = frozenset(
        {
            "alembic_version",
            "tasks",        # high-churn: live API writes during any test run
            "tasks_history",  # populated by the tasks audit trigger on every live write
            "tool_calls",   # activity-rail rows written by live API during any session;
                            # pytest_runner is SELECT-only on agent_teams so test code
                            # cannot write here — same concurrent-API-noise rationale as tasks
        }
    )

    async def _counts() -> dict[str, int]:
        async with live_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' "
                        "ORDER BY tablename"
                    )
                )
            ).fetchall()
            result: dict[str, int] = {}
            for (tbl,) in rows:
                if tbl in _INVARIANT_EXCLUDED:
                    continue
                n = (
                    await conn.execute(text(f"SELECT count(*) FROM {tbl}"))  # noqa: S608
                ).scalar_one()
                result[tbl] = int(n)
        return result

    import warnings

    try:
        pre = await _counts()
    except Exception as _first_exc:
        warnings.warn(
            f"_live_db_row_count_invariant: pre-snapshot failed ({_first_exc!r}). "
            "Live-DB guard DISABLED for this session — row-count drift will NOT be "
            "detected. See 2026-05-17 incident postmortem for why this is loud now.",
            UserWarning,
            stacklevel=2,
        )
        # Retry once — absorbs transient connection blips without disabling the guard.
        try:
            pre = await _counts()
        except Exception as _retry_exc:
            warnings.warn(
                f"_live_db_row_count_invariant: retry also failed ({_retry_exc!r}). "
                "Guard staying DISABLED — accepting genuine offline/CI state. "
                "DISABLED marker: 2026-05-17.",
                UserWarning,
                stacklevel=2,
            )
            await live_engine.dispose()
            yield
            return

    yield

    try:
        post = await _counts()
    finally:
        await live_engine.dispose()

    all_tables = sorted(set(pre) | set(post))
    deltas = {t: post.get(t, 0) - pre.get(t, 0) for t in all_tables if post.get(t, 0) != pre.get(t, 0)}
    if deltas:
        delta_lines = "\n".join(
            f"  {t}: {pre.get(t, 0)} -> {post.get(t, 0)} (delta {d:+d})"
            for t, d in sorted(deltas.items())
        )
        raise AssertionError(
            "LIVE DB ROW COUNT DRIFT — pytest wrote to `agent_teams` (the "
            "production DB) during this session. The test-DB isolation in "
            "`tests/conftest.py` (DATABASE_URL rewrite at lines 32-39) did "
            "NOT contain this run.\n"
            f"{delta_lines}\n"
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

    Cross-invocation lock (#1599): `agent_teams_test` is a HARDCODED DB name.
    Two concurrent pytest invocations share it — the second's DROP DATABASE in
    setup kills the first run's connections mid-suite, causing a non-deterministic
    cascade of failures. A FileLock (OS-level, auto-released on process death)
    serializes invocations so the second waits until the first finishes teardown.
    See Kanban #1599 for the root-cause analysis. Long rationale in decisions.md.
    """
    # --- Cross-invocation serialization lock (#1599) ---
    # All pytest processes in this container contend on the same lock file.
    # FileLock (not SoftFileLock) — OS-level flock; auto-released if process dies.
    _lock_path = Path(tempfile.gettempdir()) / "agent_teams_test_db.setup.lock"
    _lock = filelock.FileLock(str(_lock_path))
    try:
        _lock.acquire(timeout=900)
    except filelock.Timeout:
        raise RuntimeError(
            f"Could not acquire test-DB setup lock within 900 seconds "
            f"(lock file: {_lock_path}). Another pytest invocation has held "
            f"the lock too long. The test DB name `agent_teams_test` is "
            f"hardcoded — concurrent runs corrupt each other (#1599 root cause). "
            f"Wait for the other run to finish or kill it, then retry."
        )

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        test_url = os.environ["DATABASE_URL"]
        # Admin operations (DROP/CREATE DATABASE, pg_terminate_backend of other-
        # user sessions) require postgres superuser — pytest_runner cannot do
        # them. Re-derive the admin URL from the captured original DATABASE_URL
        # (stashed by the top-of-file rewrite into _PG_ADMIN_URL). #1109.
        _admin_base = os.environ["_PG_ADMIN_URL"]
        admin_url = _admin_base.rsplit("/", 1)[0] + "/postgres"

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
                # OWNER pytest_runner gives the constrained role full DDL/DML on
                # the new test DB (CREATE/DROP/ALTER on tables, triggers,
                # functions, sequences) WITHOUT promoting it to superuser. The
                # postgres role still owns the agent_teams (live) DB so the
                # REVOKE matrix from migration 0034 stays effective there.
                await conn.execute(
                    text("CREATE DATABASE agent_teams_test OWNER pytest_runner")
                )
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

        # Teardown also uses the postgres-superuser admin URL — pytest_runner
        # cannot pg_terminate_backend other sessions.
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

    finally:
        # Release the cross-invocation lock AFTER teardown completes so the
        # next waiting invocation only proceeds once the test DB is fully torn
        # down (#1599).
        _lock.release()


@pytest.fixture(autouse=True)
def _reset_rate_limiter_per_test():
    """Reset BOTH in-memory rate-limit counters between tests.

    Kanban #1124 (L19 prevention) — POST /api/projects is limited to
    `5/minute` per IP. Under ASGITransport every test "client" appears as
    127.0.0.1, so without this reset the counter accumulates across the
    session and any subsequent test that POSTs more than 5 projects from
    a fresh limiter perspective sees an unexpected 429.

    Kanban #1328 (M4b) — same shape for the per-(project_id, tag) webhook
    ingest counter (``src/services/webhook_rate_limit.py``). The 60/min cap
    is much higher than the projects cap, but tests that exercise the cap
    explicitly (61 rapid POSTs) MUST start each test from a clean slate.

    Reset via slowapi.Limiter.reset() + the dedicated reset() helper on the
    webhook module. No-op when imports fail (defensive guard for partial
    migrations).
    """
    try:
        from src.middleware.rate_limit import limiter

        limiter.reset()
    except Exception:
        pass
    try:
        from src.services.webhook_rate_limit import reset as _reset_webhook_rl

        _reset_webhook_rl()
    except Exception:
        pass
    try:
        from src.services.usage_events_rate_limit import reset as _reset_ue_rl

        _reset_ue_rl()
    except Exception:
        pass
    yield


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

    Notifications/ subdir coverage (Kanban #1850):
        When a test triggers notification delivery (PATCH→done/failed,
        HITL-needed transition, or POST /api/notifications/deliver) and the
        project has `working_path=None`, `_write_local_fallback` writes to
        `repo_root/context/projects/<name>/notifications/`. That subdir is a
        CHILD of the folder this fixture rmtrees, so the fallback leak is
        covered automatically — no separate patching needed.

        Residual edges NOT covered by this fixture:
        (a) Delivery triggered for a project whose name was NOT registered via
            scaffold_cleanup(name) — the rmtree never runs for that name.
        (b) `working_path` is set to a path OUTSIDE `context/projects/<name>/`
            (e.g., a tmp_path or a real project path) — the fallback writes
            there instead; this fixture does not clean that location.

        If you write a test that falls into edge (a) or (b) AND triggers
        delivery, patch `_write_local_fallback` yourself (see
        `_no_scaffold` autouse fixture in test_push_event_hooks_smoke.py for
        the established pattern) OR use a dedicated `no_fallback_write`
        monkeypatch fixture if the test must not assert on fallback behavior.

        NOTE: do NOT monkeypatch `_write_local_fallback` globally inside this
        fixture — two tests in test_notification_router.py
        (`test_fallback_path_anchored_at_repo_root_when_working_path_null` and
        `test_fallback_path_uses_repo_root_for_windows_working_path`) assert on
        the fallback write behavior and would silently pass vacuously if the
        write were suppressed here.
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

        # Kanban #1124 (L19 prevention) — DELETE /api/projects/{id} now MOVES
        # the scaffolded folder to context/projects/.deleted/<name>-<ts>/
        # rather than leaving it in place. Sweep those archive dirs too so a
        # full test run doesn't leak hundreds of `.deleted/` dirs into the
        # working tree.
        deleted_root = repo_root / "context" / "projects" / ".deleted"
        if deleted_root.exists():
            for archived in deleted_root.glob(f"{name}-*"):
                shutil.rmtree(archived, ignore_errors=True)


@pytest.fixture
def smtp_success_mock() -> MagicMock:
    """A MagicMock behaving as a successful smtplib.SMTP context manager.

    Shared across test_notify_email, test_digest_router, test_digest_integration.
    """
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


@pytest.fixture
def smtp_env(monkeypatch) -> None:
    """Set the 4-var SMTP env triplet that enables the digest send gate.

    Shared across test_digest_router, test_digest_router_failures,
    test_digest_integration.
    """
    monkeypatch.setenv("DIGEST_EMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_SMTP_USER", "test@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "app-pw-16-chars-x")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "dest@example.com")


@pytest.fixture
def ntfy_success_mock():
    """MagicMock behaving as a successful httpx.Client for ntfy push sends.

    Shared across test_digest_router for push-channel smoke tests (Kanban #1218).
    The mock client returns a 200 response so send_push() returns ok=True.
    """
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    client = MagicMock()
    client.post = MagicMock(return_value=resp)
    return client


@pytest.fixture
def ntfy_env(monkeypatch) -> None:
    """Set the 3-var ntfy env triplet that enables the push send gate.

    Shared across test_digest_router (Kanban #1218).
    """
    monkeypatch.setenv("PUSH_ENABLED", "true")
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("NTFY_BASE_URL", "https://ntfy.sh")


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


@pytest.fixture(autouse=True)
def _operator_gate_inactive_by_default(monkeypatch):
    """Baseline the operator-proof gate OFF for the suite (Kanban #2349).

    OPERATOR_ACTION_KEY is now set in the api container env (docker-compose
    activation) -> gate ACTIVE -> business-logic tests that don't send
    X-Operator-Token would 403. Delenv here restores the pre-activation
    default (gate inactive). Gate-AWARE tests (test_calendar_tools,
    test_email_tier1_actions, test_email_send_routes) re-activate it with
    their own monkeypatch.setenv, which runs after this autouse setup and
    overrides it -> they are unaffected.
    """
    monkeypatch.delenv("OPERATOR_ACTION_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _redirect_email_actions_audit(monkeypatch, tmp_path):
    """Redirect tools_email._EMAIL_ACTIONS_PATH to tmp_path for every test.

    Prevents any test from writing to the live _runtime/email-actions.jsonl
    audit trail (test-surface pollution — Kanban #1585 follow-up).

    The new file's explicit `_actions_to_tmp` fixture monkeypatches the same
    attribute a second time on the tests that request it; last-write-wins, so
    those tests still observe their own returned tmp path and this fixture does
    not interfere. The two coexist safely — both use monkeypatch, which stacks
    set-attr calls correctly within pytest's fixture teardown order.
    """
    try:
        from src.routers import tools_email

        monkeypatch.setattr(
            tools_email,
            "_EMAIL_ACTIONS_PATH",
            tmp_path / "email-actions.jsonl",
        )
    except ImportError:
        # tools_email not present (e.g., minimal test env without the router).
        # Silently skip so this fixture is never a blocker.
        pass
