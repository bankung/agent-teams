"""FastAPI app entrypoint.

Phase 2b.2: foundation + projects/tasks routers mounted under /api.
Kanban #707 (T2): apscheduler boots inside the FastAPI lifespan and ticks
the recurrence subsystem (templates spawn + one-shots transition).
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.routers import events as events_router
from src.routers import projects as projects_router
from src.routers import scaffold as scaffold_router
from src.routers import sessions as sessions_router
from src.routers import tasks as tasks_router
from src.services.row_changed_listener import start_listener, stop_listener
from src.settings import get_settings

# Project-scoped logging — uvicorn does NOT propagate non-uvicorn loggers
# to stdout by default. Attach a StreamHandler directly to the `src` umbrella
# logger (pointed at sys.stdout). Kanban #739 v2 — `basicConfig` attaches to
# stderr, which uvicorn `--reload` workers do not forward to docker the same
# way as stdout; surgical handler attachment bypasses that gap entirely.
# Idempotent under uvicorn `--reload` re-import. Propagation is left enabled
# so pytest's caplog (which installs a handler at root) can still capture
# WARNING records from `src.routers.*` in existing test suites — and since
# production root never gains a handler (no `basicConfig` call), there is no
# duplicate-emit risk in live runs.
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_src_logger = logging.getLogger("src")
_src_logger.setLevel(logging.INFO)
if not _src_logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter(_LOG_FORMAT))
    _src_logger.addHandler(_h)

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

    # Kanban #782 — boot the row_changed SSE broker before the scheduler so
    # tests / smoke ordering is deterministic. stop_listener runs in the
    # cleanup branch regardless of how the lifespan exits.
    await start_listener()

    disabled = os.environ.get("APP_SCHEDULER_DISABLE", "false").lower() == "true"
    if disabled:
        logger.info("recurrence scheduler disabled via APP_SCHEDULER_DISABLE")
        _scheduler = None
        try:
            yield
        finally:
            await stop_listener()
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
        # Kanban #782 — release the SSE broker connection on shutdown.
        await stop_listener()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="agent-teams API",
        version="0.1.0",
        description="Self-hosted Kanban backend (projects + tasks, audit trail via PG trigger).",
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    # CORS — Kanban #805. Browser preflight (OPTIONS) on /api/* must succeed
    # or FE jsonFetch surfaces TypeError "Failed to fetch". `allow_credentials`
    # is False because the FE doesn't use cookies (`X-Project-Id` is a plain
    # custom header, not a credential). Wildcard methods/headers are fine for
    # this single-tenant local-dev app. Origins come from settings — defaults
    # to localhost:3000, overridable via CORS_ALLOW_ORIGINS env var.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — does NOT touch the DB."""
        return {"status": "ok", "env": settings.app_env}

    app.include_router(projects_router.router, prefix="/api")
    app.include_router(tasks_router.router, prefix="/api")
    app.include_router(sessions_router.router, prefix="/api")
    app.include_router(sessions_router.runs_router, prefix="/api")
    app.include_router(scaffold_router.router, prefix="/api")
    app.include_router(events_router.router, prefix="/api")  # Kanban #782 SSE

    return app


app = create_app()
