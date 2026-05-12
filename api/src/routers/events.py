"""HTTP route for server-sent events (Kanban #782).

GET /api/events/stream?project_id=<int>

Returns `text/event-stream`. Each row-change in `tasks` / `projects` (via the
PG trigger `notify_row_changed`) is forwarded to subscribed clients as:

    event: row_changed
    data: {"table":"tasks","op":"update","id":712,"project_id":1,"ts":"..."}

Heartbeat `: keepalive` comments fire every ~25s while idle so HTTP-1.1
proxies and load balancers don't reap the connection.

Cross-project leak guard lives in the broker (see
`services/row_changed_listener.py::RowChangedBroker._dispatch`). This router
is a thin pass-through.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from src.services.row_changed_listener import broker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

# Heartbeat interval — long enough to be cheap, short enough to defeat
# typical HTTP idle-proxy timeouts (Cloudflare 100s, nginx 60s default,
# corporate proxies 30s). 25s is a common pragmatic choice (mirrors the
# task brief).
_HEARTBEAT_SECONDS = 25.0


@router.get("/stream")
async def stream(
    request: Request,
    project_id: int | None = Query(default=None, ge=1),
) -> EventSourceResponse:
    """Open an SSE stream filtered by optional `project_id`.

    Filter contract:
    - omit `project_id` → wildcard, receive ALL events (used by dashboard).
    - `project_id=N` → tasks events for project N AND every projects event.

    The handler returns immediately; sse-starlette drives the async generator
    below. We register the listener up front (in the request scope) and
    remove it in the generator's `finally` to guarantee cleanup on client
    disconnect / server shutdown / unexpected error.
    """
    queue = broker.add_listener(project_id)

    async def _gen() -> AsyncIterator[dict]:
        try:
            while True:
                # Short check for client disconnect every heartbeat tick.
                # sse-starlette ALSO has its own ping mechanism — we keep
                # this explicit so the heartbeat comment is the documented
                # contract and so disconnect detection has bounded latency.
                if await request.is_disconnected():
                    return
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # Idle — emit a comment frame. sse-starlette treats a
                    # dict with `comment` as the SSE comment line `: <text>`.
                    yield {"comment": "keepalive"}
                    continue

                yield {
                    "event": "row_changed",
                    "data": json.dumps(payload),
                }
        finally:
            broker.remove_listener(queue)

    # `ping=None` disables sse-starlette's automatic ping; we own the
    # heartbeat cadence above so the wire contract (": keepalive\n\n") is
    # locked. Default sse-starlette headers already include
    # `Cache-Control: no-cache` and `Content-Type: text/event-stream`; we
    # add `X-Accel-Buffering: no` to disable buffering at nginx-style
    # reverse proxies.
    return EventSourceResponse(
        _gen(),
        ping=None,
        headers={"X-Accel-Buffering": "no"},
    )
