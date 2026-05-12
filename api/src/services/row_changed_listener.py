"""Row-changed broker — one asyncpg LISTEN connection per worker, fans out
payloads to per-client asyncio queues (Kanban #782).

Wiring:
    main.lifespan → start_listener(app) on enter, stop_listener(app) on exit.
    routers/events.py → broker.add_listener(project_id) / remove_listener.

Cross-project leak guard lives in `_dispatch`:
- A queue with `project_id=None` receives EVERY event (wildcard, used by
  the dashboard).
- A queue with `project_id=N` receives:
    - tasks events whose payload.project_id == N, AND
    - projects events (the `projects` table has no project_id column;
      project-level changes are always relevant to project-bound listeners).

The connection is held for the lifetime of the worker process and uses
`add_listener(channel, callback)` (asyncpg's LISTEN API). One connection per
worker — sufficient for V1 single-uvicorn-worker deploy; multi-worker scales
via the DB being the broker.

Skip startup when `APP_SSE_DISABLE=true` (pytest default — fixtures that need
the broker flip this back to false explicitly).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

import asyncpg

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

    # ---------------- internal -------------------------------------------

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
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow client — drop the event rather than block the broker.
                logger.warning(
                    "row_changed: queue full for listener (filter=%s); dropping event",
                    listener_filter,
                )


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
