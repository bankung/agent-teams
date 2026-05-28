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
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.engine.url import make_url
from starlette.middleware.cors import CORSMiddleware

from src.middleware.rate_limit import limiter
from src.middleware.request_size import request_size_middleware
from src.routers import audit as audit_router
from src.routers import credentials as credentials_router
from src.routers import dashboard as dashboard_router
from src.routers import events as events_router
from src.routers import handoff_templates as handoff_templates_router
from src.routers import ingest as ingest_router
from src.routers import digest as digest_router
from src.routers import notifications as notifications_router
from src.routers import decisions as decisions_router
from src.routers import push as push_router
from src.routers import push_ntfy as push_ntfy_router
from src.routers import templates as templates_router
from src.routers import pl as pl_router
from src.routers.pl import pnl_router
from src.routers import projects as projects_router
from src.routers import scaffold as scaffold_router
from src.routers import sessions as sessions_router
from src.routers import tasks as tasks_router
from src.routers import teams as teams_router
from src.routers import tool_calls as tool_calls_router
from src.routers import tools_email as tools_email_router
from src.routers import transactions as transactions_router
from src.routers import user_actions as user_actions_router
from src.routers import webhooks as webhooks_router
from src.services.row_changed_listener import start_listener, stop_listener
from src.settings import get_settings

# #739 — attach StreamHandler to 'src' logger for stdout; propagate=True for pytest caplog
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


# Kanban #1113 (2026-05-17, L8 prevention) — refuse-to-start gate for the api
# container if DATABASE_URL points at a non-allowed DB. Companion to L6 (purge
# fixture gate) and L7 (langgraph). The DOCKER_COMPOSE_YML is the sole source
# of runtime DATABASE_URL — a typo / accidental commit / staging copy-paste in
# that file would silently rebind every container on next `docker compose up`.
# This gate refuses to construct a service that connects to a rogue DB at
# lifespan-enter (BEFORE scheduler / SSE broker / backup runner). See
# context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
_DEFAULT_ALLOWED_DB_NAMES = "agent_teams,agent_teams_test"


def _allowed_db_names(env: dict[str, str] | None = None) -> set[str]:
    """Parse DB_NAME_ALLOWLIST env (csv) at CALL time so tests can monkeypatch.

    Defaults to `{'agent_teams','agent_teams_test'}` (the live + test DBs the
    dev workflow knows about). Spaces are stripped; empty entries dropped.
    """
    e = env if env is not None else os.environ
    raw = e.get("DB_NAME_ALLOWLIST", _DEFAULT_ALLOWED_DB_NAMES)
    return {part for part in raw.replace(" ", "").split(",") if part}


def _validate_db_url(url: str, allowed: set[str] | None = None) -> None:
    """Refuse to start if engine.url.database is not in the allowlist.

    Raises RuntimeError naming the rejected db_name + the allowlist + how to
    extend it (DB_NAME_ALLOWLIST env, csv). Called from lifespan BEFORE any
    service starts, and from BackupRunner.from_env() for defense in depth.

    `allowed` override is for testing — production path reads env via
    `_allowed_db_names()`.
    """
    db_name = make_url(url).database or ""
    allow_set = allowed if allowed is not None else _allowed_db_names()
    if db_name not in allow_set:
        raise RuntimeError(
            f"REFUSE TO START: DATABASE_URL points at {db_name!r} which is NOT "
            f"in the allowlist {sorted(allow_set)}. To add a new allowed DB, "
            "set DB_NAME_ALLOWLIST env (csv, e.g. "
            "'agent_teams,agent_teams_test,staging_db'). See incident "
            "context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md."
        )


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

    # Kanban #1113 (2026-05-17, L8 prevention) — DB allowlist gate. FIRST thing
    # in lifespan, BEFORE SSE broker / scheduler / backup runner. Refuses to
    # construct services that would bind to a rogue DB. See _validate_db_url.
    from src.db import engine
    _validate_db_url(str(engine.url))

    # Kanban #1326 (M3) — refuse to start if the credentials master key is
    # missing or malformed. Loud failure at lifespan-enter beats a deferred
    # crash at the first credential request. The Fernet instance is cached
    # at module level after first construction; subsequent calls (router
    # encrypt/decrypt) reuse the same instance.
    from src.services.credentials_crypto import get_fernet
    get_fernet()

    # #782 — boot SSE broker before scheduler
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

    # Kanban #959 — off-site encrypted backup nightly job. Refuses to schedule
    # when BACKUP_* env vars are unset (default state). Cron in BACKUP_TIMEZONE
    # (defaults to UTC). One job, one run_once() call per fire.
    from src.services.backup import BackupConfig, BackupRunner

    backup_cfg = BackupConfig.from_env()
    if backup_cfg.is_enabled:
        runner = BackupRunner(backup_cfg)
        scheduler.add_job(
            runner.run_once,
            trigger=CronTrigger.from_crontab(
                backup_cfg.cron_rule, timezone=backup_cfg.timezone,
            ),
            id="backup_nightly",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "backup scheduled: cron=%s tz=%s bucket=%s prefix=%s dry_run=%s",
            backup_cfg.cron_rule, backup_cfg.timezone, backup_cfg.s3_bucket,
            backup_cfg.s3_prefix, backup_cfg.dry_run,
        )
    else:
        logger.warning(
            "Backup disabled — set BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY_ID, "
            "BACKUP_S3_SECRET_ACCESS_KEY, BACKUP_AGE_PUBKEY to enable nightly snapshots"
        )

    # Kanban #960 — periodic Health monitor sweep. ENABLED by default; set
    # HEALTH_MONITOR_DISABLED=1 to skip. Uses IntervalTrigger (not Cron) since
    # the sweep is short and idempotent — every N minutes is the natural cadence.
    from src.services.health_monitor import HealthMonitor, HealthMonitorConfig

    hm_cfg = HealthMonitorConfig.from_env()
    if hm_cfg.is_enabled:
        hm = HealthMonitor(hm_cfg)
        scheduler.add_job(
            hm.run_sweep,
            trigger=IntervalTrigger(minutes=hm_cfg.interval_minutes),
            id="health-monitor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "health monitor enabled — sweep every %d min (api_base=%s)",
            hm_cfg.interval_minutes, hm_cfg.api_base,
        )
    else:
        logger.info("health monitor disabled via HEALTH_MONITOR_DISABLED")

    # Kanban #1011 (2026-05-20) — HITL aging nudge job.
    # Registered into the SAME scheduler instance (no parallel scheduler).
    # Interval defaults to 30 min; override via HITL_NUDGE_INTERVAL_MINUTES env.
    from src.services.hitl_nudge import schedule_nudge_job
    schedule_nudge_job(scheduler)

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

    # Kanban #1115 (2026-05-17, L18 prevention) — payload-size cap. Belt-and-
    # braces on top of Pydantic field constraints. Returns 413 when
    # Content-Length exceeds REQUEST_MAX_BYTES (default 2 MB). See
    # src/middleware/request_size.py for hammer-test FINDING #10 context.
    app.middleware("http")(request_size_middleware)

    # Kanban #1124 (2026-05-17, L19 prevention) — per-IP rate limit on routes
    # that allocate disk resources (POST /api/projects scaffolds a folder).
    # Attach the limiter to app.state so the @limiter.limit decorator on the
    # route can find it via the slowapi-injected `request` arg. The middleware
    # propagates rate-limit headers (X-RateLimit-*); the exception handler
    # converts a RateLimitExceeded into a 429 response.
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request, exc: RateLimitExceeded):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"detail": f"Rate limit exceeded: {exc.detail}"},
            status_code=429,
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
    # Kanban #1620 — team registry (GET /api/teams, global, no X-Project-Id).
    app.include_router(teams_router.router, prefix="/api")
    app.include_router(events_router.router, prefix="/api")  # Kanban #782 SSE
    # Kanban #980 — specialist-tool audit timeline (sub-resource of tasks).
    app.include_router(tool_calls_router.router, prefix="/api")
    # Kanban #1082 — auditor cross-project daily-rollup aggregation.
    app.include_router(audit_router.router, prefix="/api")
    # Kanban #953 — per-project financial separation (transactions ledger + P&L).
    app.include_router(transactions_router.router, prefix="/api")
    app.include_router(pl_router.router, prefix="/api")
    # Kanban #1329 — cross-project P&L rollup (operator-level, no X-Project-Id).
    app.include_router(pnl_router, prefix="/api")
    # Kanban #945 — cross-project active-tasks list (operator-level, no X-Project-Id).
    app.include_router(dashboard_router.router, prefix="/api")
    # Kanban #1010 — cross-project next-action recommender (USER-scoped, no
    # X-Project-Id header). Powers digest section 5 / mobile home tile / inbox
    # empty-state hint.
    app.include_router(user_actions_router.router, prefix="/api")
    # Kanban #1224 — DeliveryTarget DSL + priority routing for HITL/digest push.
    app.include_router(notifications_router.router, prefix="/api")
    # Kanban #1007 — retro decisions feed (GET /api/decisions).
    app.include_router(decisions_router.router, prefix="/api")
    # Kanban #1006 — action template library (GET /api/templates/actions).
    app.include_router(templates_router.router, prefix="/api")
    # Kanban #1004 — handoff templates CRUD (auto-handoff on DONE-flip).
    app.include_router(handoff_templates_router.router, prefix="/api")
    # Kanban #955.A — Web Push subscription CRUD (browser PushManager endpoints).
    app.include_router(push_router.router, prefix="/api")
    # Kanban #1192 — ntfy push-notification fire endpoint (POST /api/push/fire).
    app.include_router(push_ntfy_router.router, prefix="/api")
    # Kanban #1326 (M3) — credentials vault (per-project, Fernet-encrypted).
    app.include_router(credentials_router.router, prefix="/api")
    # Kanban #1325 (M2) — external payment-webhook ingest (Stripe + PayPal).
    app.include_router(webhooks_router.router, prefix="/api")
    # Kanban #1327 (M4a) — email-to-task ingest webhook.
    app.include_router(ingest_router.router, prefix="/api")
    # Kanban #1217 — daily-digest fire endpoint (Gmail SMTP).
    app.include_router(digest_router.router, prefix="/api")
    # Kanban #1604 — email tools (Gmail trash + OAuth + 3-layer safety gate).
    # #1608 will append Outlook routes to the same router file.
    app.include_router(tools_email_router.router, prefix="/api")

    return app


app = create_app()
