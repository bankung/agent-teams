"""FastAPI app entrypoint.

Phase 2b.2: foundation + projects/tasks routers mounted under /api.
Kanban #707 (T2): apscheduler boots inside the FastAPI lifespan and ticks
the recurrence subsystem (templates spawn + one-shots transition).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from src.routers import projects as projects_router
from src.routers import sessions as sessions_router
from src.routers import tasks as tasks_router
from src.settings import get_settings

logger = logging.getLogger(__name__)

# Module-level scheduler holder so tests can introspect / shut down.
_scheduler: AsyncIOScheduler | None = None


async def _recurrence_tick() -> None:
    """Wrapper called by APScheduler — opens its own session via SessionLocal.

    Imported lazily so tests that disable the scheduler don't pay the import
    cost on the hot path.
    """
    from src.db import SessionLocal
    from src.services.recurrence import tick_once

    try:
        await tick_once(SessionLocal)
    except Exception:
        # Never let an exception escape the scheduler thread — APScheduler
        # would silently drop the job otherwise.
        logger.exception("recurrence_tick: unhandled error")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot apscheduler on enter; shutdown gracefully on close.

    Tick interval is `APP_SCHEDULER_TICK_SECONDS` (default 60). Set
    `APP_SCHEDULER_DISABLE=true` to skip starting the scheduler entirely
    (used by tests + ad-hoc runs that don't want background ticks).
    """
    global _scheduler

    disabled = os.environ.get("APP_SCHEDULER_DISABLE", "false").lower() == "true"
    if disabled:
        logger.info("recurrence scheduler disabled via APP_SCHEDULER_DISABLE")
        _scheduler = None
        yield
        return

    tick_seconds = int(os.environ.get("APP_SCHEDULER_TICK_SECONDS", "60"))
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _recurrence_tick,
        trigger=IntervalTrigger(seconds=tick_seconds),
        id="recurrence_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "recurrence scheduler started — tick every %ds (job_id=recurrence_tick)",
        tick_seconds,
    )
    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
            logger.info("recurrence scheduler stopped")
        except Exception:
            logger.exception("recurrence scheduler shutdown failed")
        _scheduler = None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="agent-teams API",
        version="0.1.0",
        description="Self-hosted Kanban backend (projects + tasks, audit trail via PG trigger).",
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — does NOT touch the DB."""
        return {"status": "ok", "env": settings.app_env}

    app.include_router(projects_router.router, prefix="/api")
    app.include_router(tasks_router.router, prefix="/api")
    app.include_router(sessions_router.router, prefix="/api")
    app.include_router(sessions_router.runs_router, prefix="/api")

    return app


app = create_app()
