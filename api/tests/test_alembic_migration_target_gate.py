"""L10 prevention: alembic env.py MIGRATION_TARGET gate (Kanban #1117).

Verifies that `api/alembic/env.py` refuses to apply migrations against a
non-`_test` DB unless `MIGRATION_TARGET=live` is set. Catches the future
failure mode where a destructive DDL slips into a migration and a developer
points alembic at the live DB by accident.

Sibling to L6 (purge fixture), L7 (langgraph DATABASE_URI), L8 (api lifespan).
See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

The subprocess approach mirrors how alembic is invoked in practice:
``docker compose exec api alembic upgrade head``. We invoke `alembic current`
(NOT `upgrade head`) because `current` is read-only — it loads `env.py`
(which fires the gate) but does NOT apply any DDL, so even on a green
gate path nothing changes on the live DB. This keeps the test idempotent
and safe to run repeatedly without MIGRATION_TARGET set.
"""
from __future__ import annotations

import os
import subprocess

import pytest


# Live DB DSN — captured by conftest into _PG_ADMIN_URL at module load. The
# test DB DSN (in os.environ["DATABASE_URL"]) is the pytest_runner-scoped
# `agent_teams_test` and is used implicitly by other tests; here we want the
# LIVE name `agent_teams` to exercise the gate.
def _live_db_url() -> str:
    """Return the live `agent_teams` DSN (re-derived from _PG_ADMIN_URL)."""
    admin = os.environ["_PG_ADMIN_URL"]
    base = admin.rsplit("/", 1)[0]
    return f"{base}/agent_teams"


def _run_alembic(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run `alembic current` in a subprocess with the supplied env overlay.

    `current` is a read-only command — it loads env.py (firing our gate) but
    does not apply any DDL. This makes the test safe to run against the live
    DB without risk of mutation.
    """
    env = {**os.environ, **extra_env}
    return subprocess.run(
        ["alembic", "current"],
        check=False,
        capture_output=True,
        text=True,
        cwd="/repo/api",
        env=env,
    )


def test_alembic_refuses_live_db_without_migration_target() -> None:
    """AC-5: subprocess alembic with DATABASE_URL=agent_teams (no
    MIGRATION_TARGET) raises RuntimeError before any migration runs.
    """
    result = _run_alembic({"DATABASE_URL": _live_db_url()})

    assert result.returncode != 0, (
        f"alembic should have failed.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "RuntimeError" in result.stderr, (
        f"expected RuntimeError trace in stderr.\nstderr:\n{result.stderr}"
    )
    assert "refusing to migrate against 'agent_teams'" in result.stderr, (
        f"expected gate message naming the live DB.\nstderr:\n{result.stderr}"
    )
    assert "MIGRATION_TARGET=live" in result.stderr, (
        f"expected gate to mention the escape hatch env.\nstderr:\n{result.stderr}"
    )


def test_alembic_accepts_live_db_with_migration_target_live() -> None:
    """AC-2: with MIGRATION_TARGET=live the gate passes and alembic proceeds.

    `current` succeeds against the live DB (it's at head already — verified
    pre-test). This proves the gate is openable via the documented escape
    hatch.
    """
    result = _run_alembic(
        {"DATABASE_URL": _live_db_url(), "MIGRATION_TARGET": "live"},
    )

    assert result.returncode == 0, (
        f"alembic current with MIGRATION_TARGET=live should succeed.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "RuntimeError" not in result.stderr, (
        f"gate must not fire when MIGRATION_TARGET=live.\nstderr:\n{result.stderr}"
    )


def test_alembic_accepts_test_db_without_migration_target() -> None:
    """AC-3: test DB DSN (endswith _test) passes the gate without any env ack.

    This is the path the conftest itself uses on every pytest invocation —
    the gate must be transparent for `_test`-suffixed DB names.
    """
    # os.environ["DATABASE_URL"] is the conftest-overridden test DSN
    # (...agent_teams_test). No MIGRATION_TARGET in extra_env.
    test_url = os.environ["DATABASE_URL"]
    assert test_url.rsplit("/", 1)[-1].endswith("_test"), (
        f"sanity: expected conftest DSN to end with _test; got {test_url!r}"
    )
    result = _run_alembic({})

    assert result.returncode == 0, (
        f"alembic current against test DB should succeed without "
        f"MIGRATION_TARGET.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "RuntimeError" not in result.stderr, (
        f"gate must not fire on _test-suffixed DB.\nstderr:\n{result.stderr}"
    )
