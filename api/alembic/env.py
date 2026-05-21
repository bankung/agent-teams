"""Alembic environment — async engine via SQLAlchemy 2.0.

Reads DATABASE_URL from env (loaded via pydantic-settings in src.settings).
Uses async_engine_from_config + run_sync(do_run_migrations) per SQLAlchemy 2.0 async pattern.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.models.base import Base
# Import all model modules so their tables register on Base.metadata
from src.models import credential as _credential  # noqa: F401
from src.models import project as _project  # noqa: F401
from src.models import session as _session  # noqa: F401
from src.models import task as _task  # noqa: F401
from src.models import tool_call as _tool_call  # noqa: F401
from src.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DATABASE_URL from settings into Alembic config so async_engine_from_config picks it up.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# L10 prevention (Kanban #1117) — refuse to migrate against a non-_test DB
# without explicit MIGRATION_TARGET=live ack. Catches the future failure mode
# where a destructive DDL slips into a migration and a developer points alembic
# at the live DB by accident. Conftest passes `agent_teams_test` which ends
# with `_test` so the gate is transparent for the test suite. Live procedure
# documented under readme_dev.md "Live migration procedure". See
# context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
_db_name = make_url(settings.database_url).database or ""
if (
    not (_db_name.endswith("_test") or "_test_" in _db_name)
    and os.environ.get("MIGRATION_TARGET") != "live"
):
    raise RuntimeError(
        f"alembic: refusing to migrate against {_db_name!r} (non-_test DB). "
        "If this IS intended (live migration), set MIGRATION_TARGET=live env "
        "and re-run. See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md"
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to script without DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
