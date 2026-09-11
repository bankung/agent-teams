"""Row-changed broker — one asyncpg LISTEN connection per worker, fans out
payloads to per-client asyncio queues (Kanban #782).

Wiring:
    main.lifespan → start_listener(app) on enter, stop_listener(app) on exit.
    main.lifespan → schedule_row_changed_healthcheck_job(scheduler) registers
        the reconnect healthcheck into the SAME AsyncIOScheduler used by
        hitl_nudge / audit_archive (Kanban #2834) — no parallel scheduler.
    routers/events.py → broker.add_listener(project_id) / remove_listener.
    services/agents_watcher.py → broker.broadcast({"table": "agents", ...})
        (Kanban #1019) — a non-DB, platform-level signal fanned to EVERY
        listener (no project filter), reusing this same broker/SSE stream
        rather than a second one.

Cross-project leak guard lives in `_dispatch`:
- A queue with `project_id=None` receives EVERY event (wildcard, used by
  the dashboard).
- A queue with `project_id=N` receives:
    - tasks events whose payload.project_id == N, AND
    - projects events (the `projects` table has no project_id column;
      project-level changes are always relevant to project-bound listeners).

The connection is normally held for the lifetime of the worker process and
uses `add_listener(channel, callback)` (asyncpg's LISTEN API). One connection
per worker — sufficient for V1 single-uvicorn-worker deploy; multi-worker
scales via the DB being the broker. A periodic healthcheck (`ensure_connected`,
Kanban #2834) detects a silently-dropped connection (container blip, idle
reaper, a DNS flap) and re-arms it — `start()`'s idempotency guard only ever
checked `_conn is not None`, never liveness, so a dead connection used to
wedge the broker (no more row_changed events, no error) until the worker
process restarted.

Skip startup when `APP_SSE_DISABLE=true` (pytest default — fixtures that need
the broker flip this back to false explicitly). The healthcheck job respects
the same flag so it doesn't fight an intentionally-disabled broker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Optional

import asyncpg

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


# NOTIFY channel name — must match the literal in
# api/alembic/versions/2026_05_12_1500_row_changed_triggers.py.
CHANNEL = "row_changed"

# Queue cap per listener — guards against a slow client backing the broker
# up unboundedly. 1000 events is generous; SSE clients should drain fast.
_QUEUE_MAXSIZE = 1000


class RowChangedBroker:
    """In-process fan-out from one asyncpg LISTEN connection to N SSE clients.

    Thread-safe is NOT required — the broker lives inside the FastAPI asyncio
    event loop. The asyncpg LISTEN callback runs on the same loop, and SSE
    handlers `await queue.get()` on the same loop.
    """

    def __init__(self) -> None:
        # Each listener: (queue, project_id_filter_or_None).
        self._listeners: set[tuple[asyncio.Queue[dict], Optional[int]]] = set()
        self._conn: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()

    # ---------------- lifecycle -------------------------------------------

    async def start(self) -> None:
        """Open the asyncpg LISTEN connection and register the dispatch
        callback. Idempotent — calling twice while already started is a no-op.
        """
        async with self._lock:
            if self._conn is not None:
                return
            dsn = _coerce_asyncpg_dsn(_database_url())
            self._conn = await asyncpg.connect(dsn=dsn)
            await self._conn.add_listener(CHANNEL, self._dispatch)
            logger.info(
                "row_changed broker connected — channel=%s listeners=%d",
                CHANNEL,
                len(self._listeners),
            )

    async def stop(self) -> None:
        """Close the asyncpg connection (best-effort) and clear listeners."""
        async with self._lock:
            conn = self._conn
            self._conn = None
            if conn is not None:
                try:
                    await conn.remove_listener(CHANNEL, self._dispatch)
                except Exception:
                    logger.exception("row_changed broker: remove_listener failed")
                try:
                    await conn.close()
                except Exception:
                    logger.exception("row_changed broker: close failed")
            self._listeners.clear()
            logger.info("row_changed broker stopped")

    async def ensure_connected(self) -> None:
        """Health-check the LISTEN connection and re-arm it if dead (#2834).

        `start()`'s idempotency guard only checks `self._conn is not None` —
        it never verifies the connection is still alive. When the socket
        drops silently (container blip, idle reaper, a DNS flap — the same
        class of failure already fixed for the telegram poller in #2698)
        `_conn` stays a non-None-but-dead object forever because nothing
        ever reconnects: the broker quietly stops delivering `row_changed`
        events with no visible error until the process restarts.

        Meant to be polled on an interval by the scheduled healthcheck job
        below. A live connection is a cheap no-op. A dead one (`_conn is
        None` or `_conn.is_closed()`) is re-armed by resetting `_conn` to
        `None` FIRST — so `start()`'s guard actually reconnects instead of
        no-op'ing — then calling `start()`, which opens a fresh connection
        and re-registers the NOTIFY callback.

        The lock is held only for the read-and-decide step, not across the
        `start()` call — `asyncio.Lock` is not reentrant and `start()`
        acquires the same lock itself.

        Resilient by construction: a failed reconnect attempt is logged and
        swallowed (not raised) so a still-down DB just means "retry next
        tick" instead of killing the scheduled job.
        """
        async with self._lock:
            conn = self._conn
            if conn is not None and not conn.is_closed():
                return  # healthy — no-op, cheap to poll
            # Dead or never connected: clear the ref under the lock so the
            # start() call below (outside the lock) actually reconnects
            # instead of no-op'ing on its idempotency guard.
            self._conn = None

        # shortcut: the lock is released here, so a concurrent stop() (e.g.
        # lifespan shutdown) could interleave with the start() call below and
        # open a fresh connection right after shutdown meant to close
        # everything. Narrow window (a few event-loop ticks); pre-existing
        # property of start()/stop() not being atomic against each other
        # (this method doesn't widen it) and out of scope per #2834 ("don't
        # change start()/stop() beyond what the re-arm needs"). Upgrade path
        # if it ever bites: a shutdown flag checked after start() returns, or
        # a second lock covering the full decide+start span.
        logger.warning(
            "row_changed broker: connection unhealthy (channel=%s) — reconnecting",
            CHANNEL,
        )
        try:
            await self.start()
        except Exception:
            logger.exception(
                "row_changed broker: reconnect attempt failed — will retry "
                "on next healthcheck"
            )
            return
        logger.info("row_changed broker: reconnected (channel=%s)", CHANNEL)

    # ---------------- public API ------------------------------------------

    def add_listener(self, project_id: int | None) -> asyncio.Queue[dict]:
        """Register a new SSE client. Returns the queue the SSE handler
        should `await get()` on. The (queue, project_id) tuple is the
        cross-project leak boundary.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._listeners.add((queue, project_id))
        return queue

    def remove_listener(self, queue: asyncio.Queue[dict]) -> None:
        """Detach an SSE client. Idempotent — silently ignores unknown queues.
        Called from the SSE handler's `finally` block on disconnect.
        """
        # set comprehension is the safe path — modifying during iteration is
        # not allowed, but rebinding the set is.
        self._listeners = {
            (q, pid) for (q, pid) in self._listeners if q is not queue
        }

    def broadcast(self, payload: dict) -> None:
        """Fan `payload` out to EVERY current listener queue, no filter.

        Unlike `_dispatch` (which applies the tasks/projects per-project
        filter), this delivers to wildcard AND project-filtered listeners
        alike — for platform-level events with no project scope of their own
        (e.g. `{"table": "agents", ...}` from the agents-dir watcher, Kanban
        #1019). `payload` is already a dict (no NOTIFY JSON to parse).
        """
        for queue, _listener_filter in list(self._listeners):
            self._put_or_drop(queue, payload)

    # ---------------- internal -------------------------------------------

    def _put_or_drop(self, queue: asyncio.Queue[dict], payload: dict) -> None:
        """Shared fan-out primitive: non-blocking put, drop-with-warning on full."""
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Slow client — drop the event rather than block the broker.
            logger.warning(
                "row_changed: queue full for a listener; dropping event (table=%s)",
                payload.get("table"),
            )

    def _dispatch(
        self,
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload_str: str,
    ) -> None:
        """asyncpg LISTEN callback — fan out to matching listener queues.

        Synchronous (asyncpg requires sync callbacks for add_listener); each
        per-queue put is non-blocking via `put_nowait`.

        Filter rule: `None` filter = wildcard (every event). Integer filter:
        - tasks events: match payload.project_id == filter
        - projects events: always match (project-level changes reach all
          project-bound listeners — the table has no project_id column)
        """
        try:
            payload = json.loads(payload_str)
        except Exception:
            logger.exception("row_changed: invalid JSON payload from PG")
            return

        table = payload.get("table")
        evt_project_id = payload.get("project_id")

        # Snapshot the set so a concurrent add/remove during iteration is OK.
        for queue, listener_filter in list(self._listeners):
            if listener_filter is not None:
                # Filtered listener.
                if table == "projects":
                    pass  # always reach project-bound listeners
                elif table == "tasks":
                    if evt_project_id != listener_filter:
                        continue
                else:
                    continue
            # Wildcard listener (filter=None) falls through — always delivers.
            self._put_or_drop(queue, payload)


# Module-level singleton — created lazily by start_listener(); imported by
# the events router. Tests that want isolation should call .stop() on
# teardown.
broker: RowChangedBroker = RowChangedBroker()


# ---------------- lifespan glue --------------------------------------------


def is_disabled() -> bool:
    """Return True iff env says "skip SSE listener". Default false."""
    return os.environ.get("APP_SSE_DISABLE", "false").lower() == "true"


async def start_listener() -> None:
    """Called from FastAPI lifespan on enter. No-op when disabled."""
    if is_disabled():
        logger.info("row_changed broker disabled via APP_SSE_DISABLE")
        return
    await broker.start()


async def stop_listener() -> None:
    """Called from FastAPI lifespan on exit. Always safe."""
    await broker.stop()


# ---------------- reconnect healthcheck (Kanban #2834) ---------------------


async def _healthcheck_tick() -> None:
    """APScheduler job target — mirrors hitl_nudge._nudge_tick /
    audit_archive._audit_archive_tick: a catch-all exception guard so
    APScheduler never silently drops the job on an unhandled error.
    `ensure_connected()` already swallows its own reconnect failures; this
    is defense-in-depth for anything unexpected (e.g. the lock itself).

    Respects APP_SSE_DISABLE — the healthcheck must not fight a broker that
    was intentionally left unstarted (pytest default; an operator could also
    disable SSE while leaving the rest of the scheduler running).
    """
    if is_disabled():
        return
    try:
        await broker.ensure_connected()
    except Exception:
        logger.exception("row_changed: _healthcheck_tick unhandled error")


def schedule_row_changed_healthcheck_job(scheduler: "AsyncIOScheduler") -> None:
    """Register the LISTEN-connection reconnect healthcheck (Kanban #2834).

    Called from main.py lifespan startup AFTER the scheduler is created but
    BEFORE scheduler.start() — mirrors hitl_nudge.schedule_nudge_job /
    audit_archive.schedule_audit_archive_job. Registered into the SAME
    AsyncIOScheduler instance; no parallel scheduler, no always-on
    task/thread. Must tick after start_listener()'s initial connect — that
    call already happens earlier in lifespan, well before this job's first
    IntervalTrigger fire (which is one interval AFTER scheduler.start()).

    Interval: ROW_CHANGED_HEALTHCHECK_INTERVAL_SECONDS env, default 30s —
    frequent enough that a dropped connection is caught well inside typical
    SSE-client patience, without hammering the DB with liveness checks every
    tick. Clamped to >= 5s so a bad env value can't create a tight loop.
    """
    from apscheduler.triggers.interval import IntervalTrigger

    interval_seconds = int(
        os.environ.get("ROW_CHANGED_HEALTHCHECK_INTERVAL_SECONDS", "30")
    )
    interval_seconds = max(5, interval_seconds)

    scheduler.add_job(
        _healthcheck_tick,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id="row_changed_healthcheck_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "row_changed: healthcheck job registered — every %ds "
        "(job_id=row_changed_healthcheck_tick)",
        interval_seconds,
    )


# ---------------- DSN helper -----------------------------------------------


def _database_url() -> str:
    """Read DATABASE_URL via settings to honor the conftest override."""
    from src.settings import get_settings

    return get_settings().database_url


def _coerce_asyncpg_dsn(url: str) -> str:
    """asyncpg.connect rejects the `postgresql+asyncpg://` SQLAlchemy form;
    strip the +asyncpg dialect suffix.
    """
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url
