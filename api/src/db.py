"""Async SQLAlchemy engine + session factory.

Use `get_session` as a FastAPI dependency in Task 2 endpoints.
Use `get_or_404` from router handlers to collapse the
select+execute+scalar_one_or_none+404 pattern into one line.
"""

from __future__ import annotations

import sys
import warnings
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.settings import get_settings

ModelT = TypeVar("ModelT")


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.database_url

    # L11 canary (Kanban #1118) — if pytest is running, the bound DB MUST be a
    # _test database. A non-_test bind during pytest = test-isolation contract
    # violated (the conftest DATABASE_URL rewrite ran AFTER src.db imported, or
    # a plugin/pytest_plugins chain broke the ordering). Warn loudly so the
    # next test session surfaces the binding instead of silently writing to
    # the live DB. See 2026-05-17 dev-DB-wipe incident postmortem.
    if "pytest" in sys.modules:
        db_name = make_url(url).database or ""
        if not db_name.endswith("_test"):
            warnings.warn(
                f"src.db._build_engine: pytest is running but engine is binding "
                f"to {db_name!r} (expected a _test DB). The conftest "
                "DATABASE_URL rewrite ran AFTER src.db import — check pytest "
                "plugin ordering. See 2026-05-17 incident postmortem.",
                UserWarning,
                stacklevel=2,
            )

    return create_async_engine(
        url,
        echo=settings.app_debug and settings.app_env == "development",
        pool_pre_ping=True,
        future=True,
    )


# Module-level singletons — created once on import.
engine: AsyncEngine = _build_engine()
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields an AsyncSession that auto-closes after the request."""
    async with SessionLocal() as session:
        yield session


async def get_or_404(
    session: AsyncSession,
    model: type[ModelT],
    *,
    detail: str,
    **filters: Any,
) -> ModelT:
    """Fetch a single row by equality filters or raise HTTPException(404, detail).

    Used by router handlers to collapse the select+execute+scalar_one_or_none+404 pattern.
    """
    stmt = select(model).where(*[getattr(model, k) == v for k, v in filters.items()])
    result = await session.execute(stmt)
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail=detail)
    return obj
