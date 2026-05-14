"""Background worker — polls Kanban /api/tasks/next-autorun and feeds the
compiled LangGraph graph (Kanban #852 — Phase 4 step 4 of 4).

Started as an asyncio task from `graph.py`'s lifespan AFTER graph compilation +
LLM probe succeed. On shutdown the lifespan cancels the task and the worker
exits cleanly within ~5 seconds.

Lifecycle for one polled task:

  1. GET /api/tasks/next-autorun  (X-Project-Id header)
     -> NextAutorunResponse {next_task, resume_tasks, pending_questions}
  2. If next_task is null -> sleep + continue.
  3. PATCH /api/tasks/{id} {process_status: 2, started_at: now}     -> IN_PROGRESS
  4. compiled_graph.ainvoke(initial_state, config={"configurable": {"thread_id": f"task-{id}"}})
  5. On success + halt_reason is None:
       PATCH {process_status: 5, completed_at: now,
              status_change_reason: final_result[:400]}              -> DONE
     On success + halt_reason is not None (question / decision / error from a node):
       PATCH {process_status: 4, halt_reason, is_pending: true,
              status_change_reason: ...}                              -> BLOCKED
     On exception inside ainvoke:
       PATCH {process_status: 4, halt_reason: "langgraph error: ..."} -> BLOCKED

Out of scope for #852 (deferred to #852b):
  - Consuming `resume_tasks` from the next-autorun payload.  This worker
    ignores that list and logs a single line per poll when it's non-empty.
  - Driving the question/decision interactive UX.

Error isolation invariant: one bad task MUST NOT crash the loop.  Every
iteration body is wrapped in try/except inside `run_worker_loop` — only
`asyncio.CancelledError` propagates (so graceful shutdown works).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from types import ModuleType
from typing import Any

import httpx

from llm import resolve_model, resolve_provider

logger = logging.getLogger("langgraph.worker")

# Defaults — overridable via env-vars resolved at startup by WorkerConfig.
DEFAULT_POLL_INTERVAL_SEC = 30
DEFAULT_API_BASE = "http://api:8456"  # compose-internal hostname; host-dev overrides via env

# Kanban process_status codes (mirror api/src/constants.py::TaskStatus).
# We intentionally re-declare instead of importing to keep the langgraph
# container decoupled from the api package (no shared source tree at runtime).
STATUS_IN_PROGRESS = 2
STATUS_BLOCKED = 4
STATUS_DONE = 5

# PATCH bodies use status_change_reason / halt_reason; cap the inlined text so
# we don't push pathologically large final_result strings into the DB. 400 is
# the same cap the Kanban UI's status drawer renders before truncation.
_REASON_MAX = 400
_HALT_REASON_MAX = 500


class WorkerConfig:
    """Resolved at lifespan startup.  Raises RuntimeError on any missing /
    malformed required env-var so the container fails fast instead of starting
    a worker that immediately crashes on the first poll."""

    def __init__(self) -> None:
        proj = os.getenv("LANGGRAPH_PROJECT_ID", "").strip()
        if not proj or not proj.isdigit() or int(proj) < 1:
            raise RuntimeError(
                "LANGGRAPH_PROJECT_ID env-var is required (positive integer). "
                "Set LANGGRAPH_PROJECT_ID=<id> in .env — use the project the "
                "Kanban session is bound to (dogfood default: 1). "
                "Without it the worker doesn't know which project's task board to poll."
            )
        self.project_id: int = int(proj)

        self.api_base: str = (
            os.getenv("LANGGRAPH_KANBAN_API_BASE", DEFAULT_API_BASE).strip().rstrip("/")
        )
        if not self.api_base:
            raise RuntimeError(
                "LANGGRAPH_KANBAN_API_BASE resolved to empty string; "
                f"unset to use the default {DEFAULT_API_BASE!r}."
            )

        interval = os.getenv(
            "LANGGRAPH_POLL_INTERVAL_SEC", str(DEFAULT_POLL_INTERVAL_SEC)
        ).strip()
        if not interval.isdigit() or int(interval) < 1:
            raise RuntimeError(
                "LANGGRAPH_POLL_INTERVAL_SEC must be a positive integer (seconds); "
                f"got {interval!r}. Default is {DEFAULT_POLL_INTERVAL_SEC}."
            )
        self.poll_interval_sec: int = int(interval)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run_worker_loop(graph_module: ModuleType) -> None:
    """Background poll loop.  Runs until cancelled by the lifespan shutdown.

    `graph_module` is the imported `graph` module passed in by the lifespan
    so the worker reads `graph_module.graph` (the compiled StateGraph) on
    each iteration.  This avoids a circular import (worker imports graph
    statically -> graph imports worker statically) and lets a future hot
    reload swap the compiled graph in-place.
    """
    cfg = WorkerConfig()
    logger.info(
        "worker starting: project_id=%d api_base=%s poll_interval=%ds provider=%s model=%s",
        cfg.project_id,
        cfg.api_base,
        cfg.poll_interval_sec,
        resolve_provider(),
        resolve_model(),
    )
    headers = {
        "X-Project-Id": str(cfg.project_id),
        "Content-Type": "application/json",
    }
    # Single AsyncClient owns the connection pool for the worker's lifetime.
    # Closing it on shutdown happens via the `async with` exit (also reached
    # when CancelledError unwinds the frame).
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                await _poll_once(client, graph_module, cfg, headers)
            except asyncio.CancelledError:
                logger.info("worker shutdown requested — exiting loop")
                raise
            except Exception:
                # Defensive: never let a bad iteration kill the worker.  The
                # specific exception is logged at exc level so ops can pull a
                # full traceback; the next iteration retries from a clean state
                # after the idle sleep below.
                logger.exception("worker iteration crashed; sleeping and continuing")

            try:
                await asyncio.sleep(cfg.poll_interval_sec)
            except asyncio.CancelledError:
                logger.info("worker shutdown requested during sleep — exiting loop")
                raise


# ---------------------------------------------------------------------------
# One poll tick
# ---------------------------------------------------------------------------


async def _poll_once(
    client: httpx.AsyncClient,
    graph_module: ModuleType,
    cfg: WorkerConfig,
    headers: dict[str, str],
) -> None:
    """One polling tick.  GET next-autorun, optionally pick + invoke + PATCH."""
    # 1) Poll the Kanban for the next eligible task.
    resp = await client.get(f"{cfg.api_base}/api/tasks/next-autorun", headers=headers)
    if resp.status_code != 200:
        logger.warning(
            "next-autorun returned %d: %s", resp.status_code, resp.text[:200]
        )
        return
    payload = resp.json()

    # HITL resume is deferred (#852b). Log once per poll when there's pending
    # work the worker isn't yet equipped to handle, so operators see it in logs.
    resume_tasks = payload.get("resume_tasks") or []
    if resume_tasks:
        logger.info(
            "next-autorun returned %d resume_tasks — HITL resume not yet implemented (see #852b)",
            len(resume_tasks),
        )

    task = payload.get("next_task")
    if task is None:
        logger.debug("no task to run; sleeping")
        return

    task_id = task["id"]
    logger.info("picked task %d: %r", task_id, task.get("title"))

    # 2) Flip to IN_PROGRESS.
    started_at = _now_iso()
    patch_in_progress = await _patch_task(
        client,
        cfg,
        headers,
        task_id,
        {"process_status": STATUS_IN_PROGRESS, "started_at": started_at},
    )
    if patch_in_progress is None:
        # _patch_task already logged the failure; drop the task on the floor
        # for this iteration — next-autorun will re-surface it once a human
        # un-jams the state.
        return

    # 3) Invoke the compiled graph.
    compiled = getattr(graph_module, "graph", None)
    if compiled is None:
        # Lifespan-ordering bug: worker should never start before the graph
        # is compiled.  PATCH the task back to BLOCKED so the operator sees
        # the failure on the board.
        logger.error(
            "graph_module.graph is None — lifespan ordering bug; PATCHing task %d to BLOCKED",
            task_id,
        )
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "langgraph error: compiled_graph not initialized (lifespan ordering bug)",
            },
        )
        return

    initial_state: dict[str, Any] = {
        "task_id": task_id,
        "brief": (task.get("description") or task.get("title") or ""),
        "assigned_role": task.get("assigned_role"),
        "messages": [],
        "intermediate_results": {},
    }
    config = {"configurable": {"thread_id": f"task-{task_id}"}}

    try:
        final_state = await compiled.ainvoke(initial_state, config=config)
    except asyncio.CancelledError:
        # Shutdown mid-invoke. The task stays in IN_PROGRESS; the operator can
        # restart the worker and `next-autorun`'s queue logic / resume_tasks
        # path (deferred #852b) will recover it.
        logger.info(
            "task %d interrupted by worker shutdown; leaving in IN_PROGRESS", task_id
        )
        raise
    except Exception as exc:
        logger.exception("graph crashed on task %d", task_id)
        # Truncate but include type + message so the audit trail is useful.
        halt_msg = f"langgraph error: {type(exc).__name__}: {str(exc)[:_HALT_REASON_MAX]}"
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            {
                "process_status": STATUS_BLOCKED,
                "halt_reason": halt_msg,
            },
        )
        return

    # 4) Finalize.
    completed_at = _now_iso()
    halt = final_state.get("halt_reason")
    final_result = (final_state.get("final_result") or "").strip()

    if halt is None:
        body: dict[str, Any] = {
            "process_status": STATUS_DONE,
            "completed_at": completed_at,
            "status_change_reason": (final_result or "(no final_result emitted)")[
                :_REASON_MAX
            ],
        }
    else:
        # Halt reasons in AgentState are constrained Literals ("question",
        # "decision", "error") — but we coerce to str defensively so a future
        # node returning a free-form string still gets PATCHed cleanly.
        body = {
            "process_status": STATUS_BLOCKED,
            "halt_reason": str(halt)[:_HALT_REASON_MAX],
            "is_pending": True,
            "status_change_reason": (final_result or f"halted: {halt}")[:_REASON_MAX],
        }

    if await _patch_task(client, cfg, headers, task_id, body) is None:
        return
    logger.info("task %d completed: halt=%s", task_id, halt)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _patch_task(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    task_id: int,
    body: dict[str, Any],
) -> httpx.Response | None:
    """PATCH /api/tasks/{task_id}; log + return None on non-200.

    Returns the Response on 200 so callers can chain if needed.  Non-200 is
    logged with status + truncated body; the caller decides whether to abort
    the iteration (it always does in #852).
    """
    resp = await client.request(
        "PATCH",
        f"{cfg.api_base}/api/tasks/{task_id}",
        headers=headers,
        json=body,
    )
    if resp.status_code != 200:
        logger.error(
            "PATCH /api/tasks/%d failed: %d %s body=%r",
            task_id,
            resp.status_code,
            resp.text[:200],
            body,
        )
        return None
    return resp


def _now_iso() -> str:
    """UTC ISO-8601 timestamp the API accepts on PATCH (started_at, completed_at)."""
    return datetime.now(timezone.utc).isoformat()
