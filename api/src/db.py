"""Async SQLAlchemy engine + session factory.

Use `get_session` as a FastAPI dependency in Task 2 endpoints.
Use `get_or_404` from router handlers to collapse the
select+execute+scalar_one_or_none+404 pattern into one line.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy import select
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
    return create_async_engine(
        settings.database_url,
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
