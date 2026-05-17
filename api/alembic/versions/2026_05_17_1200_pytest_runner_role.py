"""pytest_runner role + GRANT/REVOKE matrix (Kanban #1109 — L4 prevention)

Revision ID: 0034_pytest_runner_role
Revises: 0033_projects_approval_policies
Create Date: 2026-05-17 12:00 UTC

L4 prevention layer for the 2026-05-17 dev-DB-wipe incident
(context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md):
last-resort PG-engine-layer gate that refuses destructive ops on the live
`agent_teams` DB during pytest, even when every software-layer defense
(L1 hook, L2 conftest invariant, L3 settings) has been bypassed.

Sibling layers (already shipped earlier this session): L1 (PreToolUse hook
block-pytest-on-live-db.ps1), L2 (conftest live-DB row-count invariant +
loud-warn semantics), L3 (settings lazy-load + seed safety gate).

## What this migration does (idempotent, re-runnable)

1. CREATE ROLE pytest_runner LOGIN PASSWORD <env PYTEST_DB_PASSWORD> — only
   if it doesn't already exist (DO $$ block with IF NOT EXISTS check).
   Password is read from the runtime env at migration time. Migration falls
   back to `pytest_runner_dev_only_NOT_FOR_PROD` if env unset (see header
   warning emitted at conftest load — production deployments MUST rotate).

2. Grant pytest_runner CREATEDB so it can own + drop+recreate
   `agent_teams_test` each pytest invocation via the conftest fixture.

3. On `agent_teams_test` (if it exists at migration time — usually does not,
   conftest tears it down): GRANT full DDL/DML so pytest can ALTER schema,
   CREATE tables, INSERT/UPDATE/DELETE/TRUNCATE freely. `ALTER DEFAULT
   PRIVILEGES` ensures every NEW table created post-migration also
   auto-inherits the same grants — schema-sync handled.

4. On `agent_teams` (LIVE DB): REVOKE every destructive privilege; GRANT
   only CONNECT + SELECT on existing tables. `ALTER DEFAULT PRIVILEGES`
   pre-emptively REVOKEs INSERT/UPDATE/DELETE/TRUNCATE on any FUTURE table
   from pytest_runner — so a new table added in a later migration doesn't
   silently re-open the door.

## Why the migration uses postgres superuser (not pytest_runner)

The migration is run via `alembic upgrade head` which reads
`get_settings().database_url` — that DSN uses the postgres superuser
credentials (set in compose env). Verified at module load: pytest_runner
cannot create roles or grant privileges to itself.

## Password handling

Read from env var `PYTEST_DB_PASSWORD` at upgrade time. If unset, falls
back to the documented dev default `pytest_runner_dev_only_NOT_FOR_PROD`
so `docker compose exec api alembic upgrade head` "just works" on first
checkout. Production deployments MUST set a real password before running
this migration (rotate via a follow-up migration or out-of-band `ALTER
ROLE pytest_runner PASSWORD '...'`). The single-quote escape (doubled
single-quote) keeps the DDL safe even if the operator chooses a password
containing `'`.

## Downgrade

Drops the role after revoking all its grants. REASSIGN OWNED is a no-op
under normal use because conftest's `_setup_test_database` makes
pytest_runner the owner of `agent_teams_test` only — that DB is dropped on
pytest teardown so no objects survive that the role still owns at
downgrade time. The `IF EXISTS` guard keeps downgrade safe on a DB where
the role was already manually dropped.

## Cross-ref

- Incident: context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md
- Spec: _scratch/pending-kanban-2026-05-17/08-p1-bug-L4-postgres-pytest-runner-role.md
- Sibling L5 (next): _scratch/pending-kanban-2026-05-17/09-p1-feature-L5-posttooluse-agent-verify-hook.md
"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0034_pytest_runner_role"
down_revision = "0033_projects_approval_policies"
branch_labels = None
depends_on = None


# Dev default — kept in sync with .env.example, docker-compose.yml, and
# api/tests/conftest.py. Production MUST override via env. The string is
# repeated (not imported) so the migration is hermetic and re-runnable
# without dragging in app code.
_DEV_DEFAULT_PASSWORD = "pytest_runner_dev_only_NOT_FOR_PROD"


def _escape_sql_literal(s: str) -> str:
    """Double single-quotes inside the password — PG's only quote-escape rule
    inside a literal. Avoids dollar-quote collision if password contains $.
    """
    return s.replace("'", "''")


def upgrade() -> None:
    # SHORT-CIRCUIT when running as a non-superuser. The migration's job
    # (CREATE ROLE, GRANT/REVOKE on agent_teams) is purely server-level +
    # live-DB; it has no meaning when the chain replays inside the
    # disposable agent_teams_test DB during pytest setup (which runs as
    # pytest_runner — see conftest.py L4 changes for Kanban #1109). Without
    # this short-circuit, pytest's `alembic upgrade head` would re-execute
    # the role management as pytest_runner and fail with
    # `permission denied to alter role`. Detect via current_user / role
    # attribute lookup — pytest_runner is NOT superuser, postgres IS.
    bind = op.get_bind()
    is_superuser = bind.exec_driver_sql(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ).scalar()
    if not is_superuser:
        # No-op for non-superuser runners (pytest_runner inside test DB).
        # The role + grants were already established by the original
        # superuser-run upgrade against the live `agent_teams` DB.
        return

    password = os.environ.get("PYTEST_DB_PASSWORD") or _DEV_DEFAULT_PASSWORD
    pw_lit = _escape_sql_literal(password)

    # ---- 1) CREATE ROLE pytest_runner (idempotent) -----------------------
    # DO $$ block lets us guard CREATE ROLE with IF NOT EXISTS (PG has no
    # CREATE ROLE IF NOT EXISTS shorthand). CREATEDB so pytest_runner can
    # drop+recreate agent_teams_test from conftest's setup fixture (it owns
    # the test DB after CREATE DATABASE ... OWNER pytest_runner).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pytest_runner') THEN
                CREATE ROLE pytest_runner LOGIN PASSWORD '{pw_lit}' CREATEDB;
            ELSE
                -- Role exists — keep password in sync with current env value.
                -- Safe under re-runs; rotation is the intended path.
                ALTER ROLE pytest_runner WITH LOGIN PASSWORD '{pw_lit}' CREATEDB;
            END IF;
        END
        $$;
        """
    )

    # ---- 2) GRANTs on agent_teams_test (if it exists at migration time) --
    # In normal flow agent_teams_test is created+dropped per pytest
    # invocation by conftest, so this branch is usually a no-op. Guarded by
    # pg_database lookup; we use dynamic SQL to skip silently when absent.
    # The CREATE DATABASE in conftest uses `OWNER pytest_runner` so the
    # role owns it directly and these GRANTs aren't strictly necessary —
    # but having them here covers any out-of-band CREATE DATABASE flow.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_database WHERE datname = 'agent_teams_test') THEN
                EXECUTE 'GRANT ALL PRIVILEGES ON DATABASE agent_teams_test TO pytest_runner';
            END IF;
        END
        $$;
        """
    )
    # public schema grants are issued against the CURRENT DB (which is
    # `agent_teams` when this migration runs). They apply to the live DB
    # only and would be the wrong direction — destructive-by-default — so
    # they are intentionally NOT issued here. The conftest's per-invocation
    # CREATE DATABASE OWNER pytest_runner gives the role full power over
    # agent_teams_test by ownership, no further grants needed in that DB.

    # ---- 3) REVOKEs on agent_teams (LIVE — current DB) -------------------
    # First REVOKE ALL on existing tables + sequences (covers any current
    # write privileges inherited from PUBLIC). Then GRANT SELECT on tables
    # so the conftest's _live_db_row_count_invariant fixture can still
    # SELECT count(*) without superuser. Sequence USAGE intentionally NOT
    # granted — the invariant fixture doesn't need it.
    op.execute("REVOKE ALL ON DATABASE agent_teams FROM pytest_runner;")
    op.execute("GRANT CONNECT ON DATABASE agent_teams TO pytest_runner;")
    op.execute("REVOKE ALL ON SCHEMA public FROM pytest_runner;")
    op.execute("GRANT USAGE ON SCHEMA public TO pytest_runner;")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pytest_runner;")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO pytest_runner;")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM pytest_runner;")

    # ALTER DEFAULT PRIVILEGES: lock down FUTURE objects so the gate
    # doesn't quietly degrade as new migrations land. Scoped to the
    # postgres role (objects created by the migration runner). REVOKE
    # destructive ops; GRANT SELECT only on tables. Empty role spec defaults
    # to the role running ALTER DEFAULT PRIVILEGES (postgres in migration
    # context), which covers every table the future migrations create.
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM pytest_runner;
        """
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
        GRANT SELECT ON TABLES TO pytest_runner;
        """
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
        REVOKE ALL ON SEQUENCES FROM pytest_runner;
        """
    )


def downgrade() -> None:
    # Mirror the superuser-only guard from upgrade(): non-superuser runners
    # (pytest_runner inside test DB) can't manage roles, so silently skip.
    bind = op.get_bind()
    is_superuser = bind.exec_driver_sql(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ).scalar()
    if not is_superuser:
        return

    # Revoke grants first so the role has no remaining dependencies, then
    # drop. IF EXISTS keeps downgrade safe on a DB where the role was
    # already manually dropped (e.g., operator cleanup).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pytest_runner') THEN
                -- Reverse ALTER DEFAULT PRIVILEGES so future-table grant
                -- entries in pg_default_acl don't dangle.
                EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE SELECT ON TABLES FROM pytest_runner';
                EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES TO pytest_runner';
                EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO pytest_runner';
                EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pytest_runner';
                EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM pytest_runner';
                EXECUTE 'REVOKE ALL ON SCHEMA public FROM pytest_runner';
                EXECUTE 'REVOKE ALL ON DATABASE agent_teams FROM pytest_runner';
                -- REASSIGN OWNED handles any objects (test DB ownership)
                -- still attributed to the role. DROP OWNED then drops the
                -- ACL entries that REVOKE missed (pg_default_acl, etc).
                EXECUTE 'REASSIGN OWNED BY pytest_runner TO postgres';
                EXECUTE 'DROP OWNED BY pytest_runner';
                EXECUTE 'DROP ROLE pytest_runner';
            END IF;
        END
        $$;
        """
    )
