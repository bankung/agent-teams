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

HITL resume (Kanban #986): after the normal next-autorun handling, the worker
also walks `pending_questions` and resumes any task that:
  - is BLOCKED with halt_reason in {'question', 'decision'}, AND
  - has at least one valid answer in question_payload.answer_history newer
    than the last cursor stored in resume_context.last_consumed_answered_at.
For each such task it validates the answer, calls
`hitl.resume_graph(...)` with `Command(resume=<answer>)`, and PATCHes the
result back. Validation failures + checkpoint-missing + engine-crash all map
to structured halt_reason strings (see hitl.HITLError subclasses); the
worker NEVER raw-concatenates the answer into the prompt (design doc §5.3).

Error isolation invariant: one bad task MUST NOT crash the loop.  Every
iteration body is wrapped in try/except inside `run_worker_loop` — only
`asyncio.CancelledError` propagates (so graceful shutdown works).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import re
import shutil
import time
from types import ModuleType
from typing import Any

import httpx

from agent_context_sanitizer import sanitize_for_agent_context
from approval_evaluator import evaluate_policy
import config as _config
from config import resolve_session_id
from content_safety import sanitize_agent_action, scan_task_content
import multiboard as _multiboard
from hitl import (
    CheckpointMissingError,
    EngineCrashError,
    HITLError,
    InvalidAnswerError,
    resume_graph,
    validate_answer,
)
from llm import resolve_model, resolve_provider

logger = logging.getLogger("langgraph.worker")

# Defaults — overridable via env-vars resolved at startup by WorkerConfig.
DEFAULT_POLL_INTERVAL_SEC = 30
# Re-export so tests that import DEFAULT_API_BASE from worker continue to work.
DEFAULT_API_BASE: str = _config.DEFAULT_API_BASE

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

# Kanban #2136 — transient-retry config (resolved at call time from env, not
# at startup, so tests can monkeypatch without a WorkerConfig roundtrip).
_DEFAULT_TRANSIENT_RETRIES = 2
_DEFAULT_RETRY_BACKOFF_SEC = 5.0


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Map an exception to (kind, short_class) for structured halt taxonomy.

    Kanban #2136.  Classification priority (first match wins):
      (a) httpx transport errors → transient
      (b) duck-typed status_code attribute: 429 / 5xx → transient;
          4xx (400 → bad_request, 401/403 → auth) → permanent;
          also walks exc.__cause__ / exc.__context__ (depth ≤ 5) when the top
          exception carries no usable status — langchain wrappers raise from
          an inner google/httpx exception that DOES carry the code (Kanban #2274)
      (c) class-name heuristics for common SDK rate-limit wrappers;
          then conservative message heuristic: RESOURCE_EXHAUSTED (exact) or
          (regex \b429\b AND a quota/rate context word) → transient:rate_limit
      (d) default → ('permanent', 'unknown') — fail-safe halts, never retry-loops

    asyncio.CancelledError is NOT passed here (it's re-raised before this point).
    """
    # (a) httpx transport exceptions — network-level, always transient
    if isinstance(exc, httpx.TimeoutException):
        return ("transient", "timeout")
    if isinstance(exc, (httpx.ConnectError, httpx.TransportError)):
        return ("transient", "connection")

    # (b) duck-type status_code — provider SDKs expose it under different attrs.
    # First try the exception itself; if no usable status found, walk the cause
    # chain (depth ≤ 5) for langchain/google wrappers that raise from an inner
    # exception that carries the real HTTP code (Kanban #2274).
    def _extract_status(e: BaseException) -> int | None:
        for attr in ("status_code", "code"):
            val = getattr(e, attr, None)
            if isinstance(val, int):
                return val
        resp = getattr(e, "response", None)
        if resp is not None:
            val = getattr(resp, "status_code", None)
            if isinstance(val, int):
                return val
        return None

    status: int | None = _extract_status(exc)
    if status is None:
        # Walk cause chain (bounded, cycle-safe via seen set).
        seen: set[int] = {id(exc)}
        cursor: BaseException | None = exc.__cause__ or exc.__context__
        depth = 0
        while cursor is not None and depth < 5:
            if id(cursor) in seen:
                break
            seen.add(id(cursor))
            status = _extract_status(cursor)
            if status is not None:
                break
            cursor = cursor.__cause__ or cursor.__context__
            depth += 1

    if status is not None:
        if status == 429:
            return ("transient", "rate_limit")
        if 500 <= status <= 599:
            return ("transient", "server_error")
        if status in (401, 403):
            return ("permanent", "auth")
        if 400 <= status <= 499:
            return ("permanent", "bad_request")

    # (c) class-name heuristics for common SDK wrappers that don't expose
    #     status_code reliably (e.g. anthropic.RateLimitError before httpx wraps)
    type_name = type(exc).__name__.lower()
    if "ratelimit" in type_name or "rate_limit" in type_name:
        return ("transient", "rate_limit")
    if "timeout" in type_name:
        return ("transient", "timeout")
    if "connection" in type_name or "transport" in type_name:
        return ("transient", "connection")

    # Conservative message heuristic (Kanban #2274): catches Google
    # RESOURCE_EXHAUSTED / 429 signals from SDK wrappers (e.g.
    # ChatGoogleGenerativeAIError) that expose neither status_code nor a
    # recognisable class name.  Two independent signals accepted:
    #   1. "RESOURCE_EXHAUSTED" present (exact, case-sensitive) — strong Google signal.
    #   2. bare \b429\b AND a quota/rate context word (required pairing so a
    #      message like "error at line 429" never misclassifies).
    exc_msg = str(exc)
    if "RESOURCE_EXHAUSTED" in exc_msg:
        return ("transient", "rate_limit")
    if re.search(r"\b429\b", exc_msg) and re.search(
        r"quota|rate limit|rate-limit|Too Many Requests", exc_msg, re.IGNORECASE
    ):
        return ("transient", "rate_limit")

    # (d) default — fail-safe permanent halt; never retry-loops on unknowns
    return ("permanent", "unknown")


class WorkerConfig:
    """Resolved at lifespan startup.  Raises RuntimeError on any missing /
    malformed required env-var so the container fails fast instead of starting
    a worker that immediately crashes on the first poll.

    Kanban #2184 — multi-board mode:
      LANGGRAPH_PROJECT_ID SET   -> single-board mode (byte-identical to pre-#2184).
      LANGGRAPH_PROJECT_ID UNSET -> multi-board mode: discover eligible projects
                                    via GET /api/projects on each refresh cycle.
      In multi-board mode project_id is None and multi_board=True.
    """

    def __init__(self) -> None:
        proj = os.getenv("LANGGRAPH_PROJECT_ID", "").strip()
        if proj:
            # Single-board mode: env set — validate same rules as before.
            if not proj.isdigit() or int(proj) < 1:
                raise RuntimeError(
                    "LANGGRAPH_PROJECT_ID env-var must be a positive integer. "
                    "Set LANGGRAPH_PROJECT_ID=<id> in .env — use the project the "
                    "Kanban session is bound to (dogfood default: 1). "
                    "Without it the worker doesn't know which project's task board to poll."
                )
            self.project_id: int | None = int(proj)
            self.multi_board: bool = False
        else:
            # Multi-board mode: no project pinned — worker discovers eligible
            # projects at runtime via GET /api/projects. #2184
            self.project_id = None
            self.multi_board = True

        self.api_base: str = _config.resolve_api_base()
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

    Kanban #2184: when LANGGRAPH_PROJECT_ID is unset, delegates to
    `_run_multi_board_loop` instead of the single-board path below.
    """
    cfg = WorkerConfig()
    if cfg.multi_board:
        # Warn if LANGGRAPH_SESSION_ID is set — it's single-board-only. #2184
        if os.getenv("LANGGRAPH_SESSION_ID", "").strip():
            logger.warning(
                "LANGGRAPH_SESSION_ID is set but multi-board mode is active "
                "(LANGGRAPH_PROJECT_ID unset) — session env override ignored; "
                "per-project sessions are managed in-process. #2184"
            )
        await _run_multi_board_loop(cfg, graph_module)
        return

    # Single-board mode — byte-identical to pre-#2184.
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
# Multi-board loop (Kanban #2184)
# ---------------------------------------------------------------------------


async def _fetch_all_projects(
    client: httpx.AsyncClient,
    api_base: str,
) -> list[dict] | None:
    """GET /api/projects and return the list.  Returns None on any failure.

    No X-Project-Id header needed — list endpoint is not project-scoped.
    """
    try:
        resp = await client.get(f"{api_base}/api/projects")
    except httpx.HTTPError as exc:
        logger.warning("multi-board: GET /api/projects HTTP error: %r", exc)
        return None
    if resp.status_code != 200:
        logger.warning(
            "multi-board: GET /api/projects returned %d: %s",
            resp.status_code,
            resp.text[:200],
        )
        return None
    try:
        return resp.json()
    except Exception:
        logger.warning("multi-board: GET /api/projects returned non-JSON body")
        return None


async def _ensure_project_session(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    project_id: int,
    project_name: str | None,
) -> int | None:
    """Return (or create) a session_id for `project_id` in the process cache.

    Creates via POST /api/sessions with process_label 'harness-worker (<name>)'.
    On creation failure: logs warning + returns None (usage reporting skipped,
    worker does NOT crash). #2184
    """
    cached = _multiboard.get_cached_session(project_id)
    if cached is not None:
        return cached

    label = f"harness-worker ({project_name or project_id})"
    url = f"{cfg.api_base}/api/sessions"
    try:
        resp = await client.post(
            url,
            json={"project_id": project_id, "process_label": label},
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "multi-board: session create HTTP error for project %d: %r",
            project_id,
            exc,
        )
        return None
    if resp.status_code != 201:
        logger.warning(
            "multi-board: session create returned %d for project %d: %s",
            resp.status_code,
            project_id,
            resp.text[:200],
        )
        return None
    try:
        session_id = resp.json().get("id")
    except Exception:
        logger.warning(
            "multi-board: session create non-JSON for project %d", project_id
        )
        return None
    if not isinstance(session_id, int):
        logger.warning(
            "multi-board: session create missing int id for project %d", project_id
        )
        return None
    _multiboard.put_cached_session(project_id, session_id)
    logger.info(
        "multi-board: session created: project_id=%d session_id=%d label=%r",
        project_id,
        session_id,
        label,
    )
    return session_id


async def _multiboard_has_work(
    client: httpx.AsyncClient,
    api_base: str,
    headers: dict[str, str],
) -> bool:
    """Peek at next-autorun for one project; return True if ACTIONABLE work exists.

    Actionable work = next_task is not None OR at least one pending_questions
    entry passes _needs_resume (i.e. it has an unconsumed operator answer).

    Critically, a pending question that is still AWAITING input (no answer yet,
    or cursor already advanced past the latest answer) is NOT actionable — the
    worker cannot do anything until the operator answers. Counting unanswered
    questions as work caused board-starvation: a permanently-parked question on
    board A (e.g. ps=4 debris, task 2283 on board 661) made has_work(A) = True
    every tick, preventing board B from ever being polled. Kanban #2298.

    The consumable-question predicate is _needs_resume — the SAME function that
    _poll_once step (a) uses when it decides whether to resume a HITL task. Reuse
    ensures the two callsites stay in sync with zero drift risk. #2298
    """
    try:
        resp = await client.get(f"{api_base}/api/tasks/next-autorun", headers=headers)
    except httpx.HTTPError:
        return False
    if resp.status_code != 200:
        return False
    try:
        payload = resp.json()
    except Exception:
        return False
    pending_questions = payload.get("pending_questions") or []
    return bool(
        payload.get("next_task") is not None
        or any(_needs_resume(q)[0] for q in pending_questions)
    )


async def _run_multi_board_loop(
    cfg: WorkerConfig,
    graph_module: ModuleType,
) -> None:
    """Multi-board poll loop — LANGGRAPH_PROJECT_ID unset path. Kanban #2184.

    Per-tick semantics (serial):
      1. Every `multiboard_refresh_ticks()` ticks refresh the eligible project
         list from GET /api/projects. Log the set whenever it changes.
      2. Iterate eligible projects in id-ascending order; peek next-autorun per
         project via _multiboard_has_work. "Has work" means ACTIONABLE work:
         next_task non-null OR at least one pending_questions entry has an
         unconsumed operator answer (_needs_resume). An unanswered parked question
         is NOT actionable and must not starve later boards. Kanban #2298.
      3. At the FIRST project with actionable work, call _poll_once and end the
         tick (serial — no other project processes that tick).
      4. If no project has work, the tick ends idle.
    """
    logger.info(
        "worker starting (multi-board): api_base=%s poll_interval=%ds provider=%s model=%s",
        cfg.api_base,
        cfg.poll_interval_sec,
        resolve_provider(),
        resolve_model(),
    )
    eligible: list[dict] = []
    last_eligible_ids: set[int] = set()
    tick = 0
    refresh_ticks = _multiboard.multiboard_refresh_ticks()

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            # Refresh eligible list every N ticks. #2184
            if tick % refresh_ticks == 0:
                raw = await _fetch_all_projects(client, cfg.api_base)
                if raw is not None:
                    eligible = _multiboard.filter_eligible(raw)
                    new_ids = {p["id"] for p in eligible}
                    if new_ids != last_eligible_ids:
                        logger.info(
                            "multi-board eligible projects: %s",
                            [(p["id"], p.get("name")) for p in eligible],
                        )
                        last_eligible_ids = new_ids
                if not eligible:
                    logger.info(
                        "multi-board: no eligible projects; sleeping (tick=%d)", tick
                    )

            tick += 1

            # Find first project with work; process it; end tick. #2184
            for project in eligible:
                project_id: int = project["id"]
                project_name: str | None = project.get("name")
                headers = {
                    "X-Project-Id": str(project_id),
                    "Content-Type": "application/json",
                }
                has_work = await _multiboard_has_work(client, cfg.api_base, headers)
                if not has_work:
                    continue

                # Ensure per-project session for usage metering. #2184
                session_id = await _ensure_project_session(
                    client, cfg, project_id, project_name
                )
                # Inject session_id into env so resolve_session_id() inside
                # _poll_once picks it up — restored immediately after. Safe
                # because this coroutine is serial (no concurrent _poll_once).
                _prev_session_env = os.environ.get("LANGGRAPH_SESSION_ID")
                if session_id is not None:
                    os.environ["LANGGRAPH_SESSION_ID"] = str(session_id)
                elif "LANGGRAPH_SESSION_ID" in os.environ:
                    del os.environ["LANGGRAPH_SESSION_ID"]
                try:
                    try:
                        await _poll_once(client, graph_module, cfg, headers)
                    except asyncio.CancelledError:
                        logger.info("multi-board: worker shutdown — exiting loop")
                        raise
                    except Exception:
                        logger.exception(
                            "multi-board: iteration crashed for project %d; continuing",
                            project_id,
                        )
                finally:
                    # Restore previous session env exactly. #2184
                    if _prev_session_env is not None:
                        os.environ["LANGGRAPH_SESSION_ID"] = _prev_session_env
                    elif "LANGGRAPH_SESSION_ID" in os.environ:
                        del os.environ["LANGGRAPH_SESSION_ID"]
                break  # first actionable project processed; end tick. #2184

            try:
                await asyncio.sleep(cfg.poll_interval_sec)
            except asyncio.CancelledError:
                logger.info("multi-board: worker shutdown during sleep — exiting loop")
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
    """One polling tick.  GET next-autorun, optionally pick + invoke + PATCH.

    Order of work per tick (each step is best-effort and isolated):
      a. Process HITL resumes for pending_questions whose answer_history has
         advanced since the last resume cursor — done BEFORE picking a new
         next_task so resumed work doesn't starve under a steady inflow.
      b. (Legacy #852b note) `resume_tasks` (BLOCKED tasks whose dependency
         blocker is now DONE) — the api returns these but the worker does NOT
         drive them through the graph (no checkpoint to resume from; their
         halt_reason was set by Lead, not by the engine). Logged at INFO only.
      c. Pick `next_task` and run it through the normal IN_PROGRESS → DONE /
         BLOCKED path.
    """
    # Resolve effective project_id from headers (supports multi-board where
    # cfg.project_id may be None — the per-project headers carry the right id).
    # #2184
    _effective_project_id: int = cfg.project_id or int(headers.get("X-Project-Id", 0))

    # 1) Poll the Kanban for the next eligible task.
    resp = await client.get(f"{cfg.api_base}/api/tasks/next-autorun", headers=headers)
    if resp.status_code != 200:
        logger.warning(
            "next-autorun returned %d: %s", resp.status_code, resp.text[:200]
        )
        return
    payload = resp.json()

    # 1a) HITL resume — walk pending_questions and resume any task whose
    # answer_history has advanced since the last consumed cursor. Errors are
    # caught + logged per-task; one bad resume MUST NOT block the rest of
    # the tick (parity with run_worker_loop's loop-isolation contract).
    pending_questions = payload.get("pending_questions") or []
    for q_task in pending_questions:
        try:
            await _maybe_resume_hitl_task(client, graph_module, cfg, q_task, headers)
        except Exception:
            logger.exception(
                "hitl resume crashed for task %s; continuing tick",
                q_task.get("id"),
            )

    # 1b) The api's `resume_tasks` field is for BLOCKED-by-dependency tasks
    # (blocker now DONE) — those have NO engine checkpoint (their halt was
    # set by Lead via halt_reason text), so the worker can't ainvoke(Command)
    # them. Log once per poll so the gap remains visible.
    resume_tasks = payload.get("resume_tasks") or []
    if resume_tasks:
        logger.info(
            "next-autorun returned %d resume_tasks (dependency-resumable) — "
            "not consumed by the engine (no checkpoint state); HITL resume "
            "consumes pending_questions instead",
            len(resume_tasks),
        )

    task = payload.get("next_task")
    if task is None:
        logger.debug("no task to run; sleeping")
        return

    task_id = task["id"]
    logger.info("picked task %d: %r", task_id, task.get("title"))

    # 1c) L17 content-safety gate (Kanban #1114). Static regex scan of task
    # title + description + AC text BEFORE any LLM invocation. If matched,
    # PATCH BLOCKED with halt_reason='destructive_intent_detected' and SKIP
    # the IN_PROGRESS flip + the LLM call entirely (zero token spend). This is
    # the last automated layer before prompt-layer discipline takes over; a
    # red-team task that slipped past L14 creation-tag + L15 template-confirm
    # gets halted here. See content_safety.py + 2026-05-17-dev-db-wipe.md.
    matched = scan_task_content(
        task.get("title"),
        task.get("description"),
        task.get("acceptance_criteria"),
    )
    if matched:
        logger.warning(
            "L17: REFUSING to invoke agent on task %d — content matched destructive patterns: %s",
            task_id,
            matched,
        )
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "destructive_intent_detected",
                "status_change_reason": (
                    f"L17 worker gate: task content matched destructive patterns "
                    f"({matched}). Human review required before auto-run."
                )[:_REASON_MAX],
            },
        )
        return

    # 1d) Mode-B Phase-1 host-prereq gate (Kanban #1800 / #1652). Adjacent to
    # the L17 gate, BEFORE the IN_PROGRESS flip + the LLM call (zero token
    # spend). The bound project may declare `required_binaries` — host
    # executables its Mode-B tools shell out to (e.g. ffmpeg, yt-dlp). A binary
    # dep can ONLY be satisfied by editing langgraph/Dockerfile + rebuilding the
    # shared image (a CORE edit — the anti-pattern), so until #1652 Phase 2
    # builds per-project images, a binary-dep project is Mode-A-only. Rather
    # than let the tool blow up mid-run with an opaque FileNotFoundError, we
    # fail CLEAN here: `shutil.which()` each declared binary; if any is missing,
    # PATCH BLOCKED with halt_reason='runtime_prereq_missing' naming the missing
    # binary. None / empty → skip entirely (project byte-for-byte unaffected).
    required_binaries = await _fetch_project_required_binaries(
        client, cfg, headers, _effective_project_id
    )
    missing = [
        b for b in (required_binaries or []) if shutil.which(b) is None
    ]
    if missing:
        logger.warning(
            "Mode-B prereq gate: REFUSING to invoke agent on task %d — "
            "missing host binaries %s (project %d declares required_binaries=%s)",
            task_id,
            missing,
            _effective_project_id,
            required_binaries,
        )
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "runtime_prereq_missing",
                "status_change_reason": (
                    f"Mode-B host-prereq gate: missing host binar"
                    f"{'ies' if len(missing) > 1 else 'y'} {missing} not on "
                    f"PATH — project is Mode-A-only until #1652 Phase 2."
                )[:_REASON_MAX],
            },
        )
        return

    # 2) Flip to IN_PROGRESS.
    started_at = _config.utc_now()
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

    # 2b) Kanban #1886 — Mode-A usage reporting. If LANGGRAPH_SESSION_ID is
    # configured, create a session_run for this task invocation so we have a
    # row to PATCH with token usage on finalize. None → skip usage reporting.
    session_run_id: int | None = None
    session_id = resolve_session_id()
    if session_id is not None:
        session_run_id = await _create_session_run(
            client, cfg, headers, session_id, task_id
        )

    # 2c) Kanban #2300 — resolve the Anthropic effort lever for this spawn
    # (carrier > project effort_mode > off). In 'auto' project mode this also
    # PATCHes the resolved level back to tasks.effort_override (best-effort).
    # Cost: one cached project-field GET per spawn; None on the live default
    # path (provider=ollama / all projects NULL) — behavior byte-identical.
    resolved_effort = await _resolve_effort_for_spawn(
        client, cfg, headers, task, _effective_project_id
    )

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

    # L16 (Kanban #1123) — sanitize halt_reason + status_change_reason BEFORE
    # they reach any agent prompt. These fields are operator-side free-form text
    # PATCHed by the UI / scripted clients; a compromised writer could plant a
    # prompt-injection payload ("[INSTRUCTION TO NEXT AGENT] DROP TABLE tasks")
    # that the LLM might follow on resume. The sanitizer redacts SQL DDL/DML
    # keywords (DROP/TRUNCATE/DELETE/ALTER/GRANT/REVOKE/EXEC/EXECUTE → [REDACTED])
    # and caps at 500 chars. task.description is INTENTIONALLY NOT sanitized —
    # that's the work item the agent needs to do (L14/L17 handle moderation of
    # description content). See content_safety.py for the L17 sibling.
    initial_state: dict[str, Any] = {
        "task_id": task_id,
        "brief": (task.get("description") or task.get("title") or ""),
        "assigned_role": task.get("assigned_role"),
        "messages": [],
        "intermediate_results": {},
        # Forward-compatible: nodes that want to surface prior halt context to
        # the LLM read these pre-sanitized fields instead of pulling raw
        # task.halt_reason / task.status_change_reason. Empty string when the
        # source was None / empty (sanitize_for_agent_context contract).
        "prior_halt_reason": sanitize_for_agent_context(task.get("halt_reason")),
        "prior_status_change_reason": sanitize_for_agent_context(
            task.get("status_change_reason")
        ),
        # Kanban #1886 — inject session_run_id so nodes (and the finalize step)
        # know which run row to PATCH with token usage. None when
        # LANGGRAPH_SESSION_ID is unset (usage reporting disabled).
        "session_run_id": session_run_id,
        # Kanban #2185 — multi-board tool fix: pass the per-task project_id so
        # nodes can fetch tools_config for the correct project even when
        # LANGGRAPH_PROJECT_ID env is unset (multi-board mode). In single-board
        # mode _effective_project_id == cfg.project_id, so behavior is identical.
        "project_id": _effective_project_id,
        # Kanban #2300 — resolved Anthropic effort lever for this run. None = off
        # (no thinking; the specialist node's default cache entry = today's
        # behavior). Anthropic-only; other providers ignore it in make_chat_model.
        "effort": resolved_effort,
    }
    config = {"configurable": {"thread_id": f"task-{task_id}"}}

    # Kanban #2136 — structured halt taxonomy + bounded transient retry.
    # LangGraph checkpoints make re-invocation safe (idempotent per thread_id).
    # Retry count / backoff come from env-vars resolved here so tests can
    # monkeypatch without a WorkerConfig roundtrip.
    _retries_raw = os.getenv("LANGGRAPH_TRANSIENT_RETRIES", str(_DEFAULT_TRANSIENT_RETRIES))
    try:
        _max_retries = max(0, int(_retries_raw))
    except ValueError:
        raise RuntimeError(
            f"LANGGRAPH_TRANSIENT_RETRIES must be a non-negative integer; "
            f"got {_retries_raw!r}. Unset to use the default ({_DEFAULT_TRANSIENT_RETRIES})."
        )
    _backoff_raw = os.getenv("LANGGRAPH_RETRY_BACKOFF_SEC", str(_DEFAULT_RETRY_BACKOFF_SEC))
    try:
        _backoff_base = max(0.0, float(_backoff_raw))
    except ValueError:
        raise RuntimeError(
            f"LANGGRAPH_RETRY_BACKOFF_SEC must be a non-negative number (seconds); "
            f"got {_backoff_raw!r}. Unset to use the default ({_DEFAULT_RETRY_BACKOFF_SEC})."
        )
    final_state: dict[str, Any] | None = None
    _last_exc: BaseException | None = None
    _attempts_made = 0
    for _attempt in range(_max_retries + 1):
        _attempts_made = _attempt + 1
        try:
            final_state = await compiled.ainvoke(initial_state, config=config)
            _last_exc = None
            break  # success — exit retry loop
        except asyncio.CancelledError:
            # Shutdown mid-invoke. The task stays in IN_PROGRESS; the operator
            # can restart the worker and `next-autorun`'s queue logic /
            # resume_tasks path (deferred #852b) will recover it.
            logger.info(
                "task %d interrupted by worker shutdown; leaving in IN_PROGRESS",
                task_id,
            )
            raise
        except Exception as exc:
            _last_exc = exc
            kind, short_class = classify_exception(exc)
            if kind == "transient" and _attempt < _max_retries:
                # Exponential backoff + small jitter so concurrent workers
                # don't pile up on the same provider endpoint.
                delay = _backoff_base * math.pow(2, _attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "task %d graph transient error (class=%s attempt=%d/%d); "
                    "retrying in %.1fs: %s: %s",
                    task_id,
                    short_class,
                    _attempt + 1,
                    _max_retries + 1,
                    delay,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    logger.info(
                        "task %d interrupted during retry sleep; leaving in IN_PROGRESS",
                        task_id,
                    )
                    raise
            else:
                # Permanent error or transient retries exhausted.
                logger.exception("graph crashed on task %d (class=%s)", task_id, short_class)
                break

    if _last_exc is not None:
        kind, short_class = classify_exception(_last_exc)
        retries_done = _attempts_made - 1
        # Security MED-2: strip non-printable chars from the exc message before
        # it lands in halt_reason (client-supplied provider/model can embed
        # control chars). Keep printable ASCII + Thai (L23 parity deferred to
        # #2155; this filter covers the accepted fix).
        _raw_exc_msg = re.sub(r"[^\x20-\x7E฀-๿]", "?", str(_last_exc))
        # Compute prefix + suffix first; cap detail to remaining budget so the
        # retry suffix is never silently eaten by a second truncation.
        _class_name = type(_last_exc).__name__
        if retries_done > 0:
            _suffix = f" (after {retries_done} retries)"
        else:
            _suffix = ""
        _prefix = f"{kind}:{short_class}: {_class_name}: "
        _detail_budget = _HALT_REASON_MAX - len(_prefix) - len(_suffix)
        _detail = _raw_exc_msg[:max(0, _detail_budget)]
        halt_msg = f"{_prefix}{_detail}{_suffix}"
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

    # 4) Finalize.  `final_state` is guaranteed non-None here: the error path
    # above returns early, so reaching this line means the invoke loop succeeded.
    assert final_state is not None
    body = _build_finalize_body(final_state, completed_at=_config.utc_now())

    # Kanban #957 Phase 1 — approval-policy hook. Only fires on HITL pause
    # bodies (halt_reason in {question, decision}). Pre-empts the BLOCKED
    # PATCH with either a synthetic resume (auto_approve) or a recoloured
    # halt (auto_deny). Non-HITL halts + DONE bodies skip the hook entirely,
    # so this code path adds zero overhead for normal task lifecycle.
    if body.get("halt_reason") in ("question", "decision") and body.get(
        "question_payload"
    ):
        policies = await _fetch_project_policies(
            client, cfg, headers, _effective_project_id
        )
        action, default_answer, rule_name = evaluate_policy(
            body["question_payload"], policies
        )
        if action == "auto_approve":
            logger.info(
                "task %d auto-approved by policy %r; resuming with %r",
                task_id,
                rule_name,
                default_answer,
            )
            # Synthesise the minimum task dict shape `_resume_hitl_task`
            # expects (id + question_payload). The worker just built the
            # payload above; pass it back in. No answer_history present —
            # validate_answer only checks the answer against the payload,
            # not against history.
            # Kanban #1695: carry interaction_kind ('decision' when the pause
            # had options) so the finalize PATCH sets chosen_id on an
            # auto-approved DECISION — otherwise the #1007 done-flip validator
            # 422s and the give-up path (Fix B) fires on every poll.
            synthetic_task = {
                "id": task_id,
                "question_payload": body["question_payload"],
                "interaction_kind": body.get("interaction_kind"),
                "resume_context": None,
            }
            await _resume_hitl_task(
                client,
                graph_module,
                cfg,
                synthetic_task,
                default_answer,
                headers,
                policy_rule_name=rule_name,
            )
            return
        if action == "auto_deny":
            logger.info(
                "task %d auto-denied by policy %r", task_id, rule_name
            )
            policy_label = f"policy {rule_name!r}" if rule_name else "policy"
            body = {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "operator_rejected",
                "status_change_reason": (
                    f"auto-denied by {policy_label}"
                )[:_REASON_MAX],
            }

    if await _patch_task(client, cfg, headers, task_id, body) is None:
        return
    logger.info(
        "task %d finalized: halt=%s ps=%s",
        task_id,
        final_state.get("halt_reason"),
        body.get("process_status"),
    )

    # Kanban #1886 — Mode-A usage reporting. PATCH the session_run with
    # accumulated token usage if a run was registered for this invocation.
    # Best-effort: runs AFTER the task finalize PATCH so a usage-PATCH failure
    # never blocks or reverts the task's process_status.
    run_id_from_state = (
        final_state.get("session_run_id")
        if isinstance(final_state, dict)
        else None
    )
    effective_run_id = run_id_from_state or session_run_id
    if effective_run_id is not None:
        await _patch_session_run_usage(
            client, cfg, headers, effective_run_id, final_state
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_finalize_body(
    final_state: dict[str, Any], *, completed_at: str
) -> dict[str, Any]:
    """Build the PATCH body for finalizing a graph invocation.

    Three categories driven by `final_state.get("__interrupt__")` and
    `final_state.get("halt_reason")`:

      - **HITL pause** (`__interrupt__` set) → BLOCKED + `halt_reason` in
        {question, decision} + `question_payload` populated. NO `is_pending`
        key (API rule: `is_pending=True` requires `process_status=2`).
      - **DONE** (halt_reason is None, no interrupt) → DONE + `completed_at`.
      - **Non-HITL halt** (any other halt_reason — transient_error,
        auditor_giveup, ambiguous, operator_rejected, error, …) → BLOCKED
        + free-form halt_reason. NO `is_pending` key — the API validator
        (services/is_pending.py) rejects `is_pending=True` paired with any
        process_status other than IN_PROGRESS (2).

    Audit fields (`audit_report`, `audit_retry_count`) are appended on any
    branch when present in state — the worker is the sole writer of these
    columns and they survive across DONE / halt categories alike.

    Pure helper: no I/O, no client. Trivially unit-testable.
    """
    interrupts = final_state.get("__interrupt__")
    if interrupts:
        # HITL pause path — LangGraph 1.2.0 `ainvoke` does NOT raise
        # GraphInterrupt when a node calls `interrupt()`; it returns
        # final_state with a `"__interrupt__"` key holding a list of
        # `langgraph.types.Interrupt` objects. Take the first (only one
        # supported per pause point).
        pause = interrupts[0]
        raw_payload = getattr(pause, "value", None) or {}
        if not isinstance(raw_payload, dict):
            raw_payload = {"question": str(raw_payload)}
        # Normalise to the API's QuestionPayload contract:
        #   required: `question` (str, min_length=1)
        #   optional: `options` (list[str] | None)
        #   optional: `answer_history` (list[AnswerHistoryEntry])
        # Engine-side helpers historically used `text` + `answers`; translate
        # both keys so a specialist that emits either shape lands cleanly.
        question = raw_payload.get("question") or raw_payload.get("text") or ""
        payload: dict[str, Any] = {"question": str(question)}
        if raw_payload.get("options"):
            payload["options"] = list(raw_payload["options"])
        # WARN-1 (security review 2026-05-17, Kanban #1106) — intentionally DO
        # NOT forward `answer_history` / `answers` from Interrupt.value into the
        # PATCH body. Those fields are server-side-only: the API's append-answer
        # service (POST /api/tasks/{id}/answer) is the SOLE writer of the audit
        # trail. Accepting them from the worker — whose Interrupt.value can be
        # influenced by an LLM-controlled tool or compromised specialist —
        # would let an attacker pre-seed phantom audit entries indistinguishable
        # from real operator answers in tasks_history (CWE-345). Strip silently;
        # any legitimate history will be re-fetched from the DB by the next
        # next-autorun poll.
        kind = "decision" if payload.get("options") else "question"
        # L23 (Kanban #1126) — `payload["question"]` is LLM-authored free text.
        # If the agent echoed destructive SQL inside an interrupt prompt
        # (Phase 9B Ollama pattern), the sanitizer returns None; demote the
        # prompt to a safe placeholder and stamp a CRITICAL halt instead so
        # the operator sees the trigger before re-running.
        raw_prompt = payload["question"][:200]
        prompt_text = sanitize_agent_action(raw_prompt)
        if prompt_text is None:
            logger.critical(
                "L23: interrupt prompt echoed destructive SQL; demoting "
                "to sanitized placeholder. raw_prompt_prefix=%r",
                raw_prompt[:80],
            )
            prompt_text = (
                "[sanitized: agent echoed destructive SQL — review needed]"
            )
        body: dict[str, Any] = {
            "process_status": STATUS_BLOCKED,
            "halt_reason": kind,
            "interaction_kind": kind,
            "question_payload": payload,
            "status_change_reason": f"awaiting user input ({kind}): {prompt_text}"[
                :_REASON_MAX
            ],
        }
    else:
        halt = final_state.get("halt_reason")
        final_result = (final_state.get("final_result") or "").strip()
        # L23 (Kanban #1126) — `final_result` is LLM free-form output extracted
        # into the operator-trusted status_change_reason field. Phase 9B showed
        # LLMs can REFUSE a destructive task but echo the SQL string verbatim
        # in their refusal; if a downstream UI / cron / recommender extracts
        # "next action" from this field, the SQL leaks back into an executable
        # context. Sanitize before the field crosses the LLM→operator
        # trust boundary.
        safe_final_result = sanitize_agent_action(final_result)
        sanitized_fired = (
            safe_final_result is None and final_result != ""
        )
        if sanitized_fired:
            logger.critical(
                "L23: final_result echoed destructive SQL; demoting task to "
                "BLOCKED with sanitized placeholder. raw_prefix=%r",
                final_result[:80],
            )
        if halt is None and not sanitized_fired:
            body = {
                "process_status": STATUS_DONE,
                "completed_at": completed_at,
                "status_change_reason": (
                    safe_final_result or "(no final_result emitted)"
                )[:_REASON_MAX],
            }
        elif halt is None and sanitized_fired:
            # L23 override: would have been DONE, but the sanitizer fired —
            # halt for human review instead of forwarding the echoed SQL.
            body = {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "agent_output_sanitized",
                "status_change_reason": (
                    "[sanitized: agent echoed destructive SQL — review needed]"
                )[:_REASON_MAX],
            }
        else:
            # Non-HITL halts (auditor_giveup, operator_rejected, transient_error,
            # ambiguous, error, etc.) land the task BLOCKED awaiting human
            # attention. `is_pending` is omitted (defaults False) — the API
            # validator (services/is_pending.py) rejects `is_pending=True`
            # paired with any process_status other than IN_PROGRESS (2).
            reason_body = (
                safe_final_result
                if (safe_final_result and not sanitized_fired)
                else (
                    "[sanitized: agent echoed destructive SQL — review needed]"
                    if sanitized_fired
                    else f"halted: {halt}"
                )
            )
            body = {
                "process_status": STATUS_BLOCKED,
                "halt_reason": str(halt)[:_HALT_REASON_MAX],
                "status_change_reason": reason_body[:_REASON_MAX],
            }

    # Kanban #952 — auditor outputs. Surface audit_report / audit_retry_count
    # on the finalize PATCH when present so tasks.audit_report carries the
    # latest classification and tasks.audit_retry_count reflects the current
    # loop count. Absent keys = the graph didn't reach the auditor (e.g., a
    # specialist halted earlier); leave the DB column untouched.
    audit_report = final_state.get("audit_report")
    if audit_report is not None:
        body["audit_report"] = audit_report
    audit_retry_count = final_state.get("audit_retry_count")
    if audit_retry_count is not None:
        body["audit_retry_count"] = int(audit_retry_count)
    return body


async def _create_session_run(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    session_id: int,
    task_id: int,
) -> int | None:
    """POST /api/sessions/{session_id}/runs to register a run; return its id.

    Kanban #1886 — Mode-A usage reporting. Called before the graph is invoked
    so the run row exists to receive the token PATCH on finalize. Returns None
    on any failure (HTTP error, non-201, JSON parse) — a missing session_run_id
    means the usage PATCH is skipped, never crashes the task lifecycle.

    Note: this endpoint does NOT need X-Project-Id (session endpoints are
    by-id, project comes from the session row). The existing `headers` dict
    carries X-Project-Id harmlessly; no auth issue.
    """
    url = f"{cfg.api_base}/api/sessions/{session_id}/runs"
    try:
        resp = await client.post(url, headers=headers, json={"task_id": task_id})
    except httpx.HTTPError as exc:
        logger.warning(
            "session_run create: HTTP error for task=%d session=%d: %r",
            task_id,
            session_id,
            exc,
        )
        return None
    if resp.status_code != 201:
        logger.warning(
            "session_run create: POST /sessions/%d/runs returned %d (task=%d): %s",
            session_id,
            resp.status_code,
            task_id,
            resp.text[:200],
        )
        return None
    try:
        run_id = resp.json().get("id")
    except Exception:
        logger.warning(
            "session_run create: non-JSON response for task=%d session=%d",
            task_id,
            session_id,
        )
        return None
    if not isinstance(run_id, int):
        logger.warning(
            "session_run create: response missing int id for task=%d session=%d",
            task_id,
            session_id,
        )
        return None
    logger.info(
        "session_run created: run_id=%d task=%d session=%d",
        run_id,
        task_id,
        session_id,
    )
    return run_id


async def _patch_session_run_usage(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    session_run_id: int,
    final_state: dict[str, Any],
) -> None:
    """PATCH /api/session_runs/{id} with accumulated token usage from the graph.

    Kanban #1886 — Mode-A usage reporting. Reads the four usage fields from
    final_state (populated by nodes.py from AIMessage.usage_metadata). Defaults
    to 0 when absent so partial or no-usage providers don't fail the PATCH.
    Also marks the run status='done' and forwards provider/model from env-vars
    so the API can compute total_cost_usd server-side.

    Best-effort: logs warnings on failure, never raises — the task lifecycle
    (DONE / BLOCKED flip) must already have completed before this is called.
    """
    url = f"{cfg.api_base}/api/session_runs/{session_run_id}"
    body: dict[str, Any] = {
        "status": "done",
        "total_input_tokens": int(final_state.get("usage_input_tokens") or 0),
        "total_output_tokens": int(final_state.get("usage_output_tokens") or 0),
        "cache_read_input_tokens": int(final_state.get("usage_cache_read_tokens") or 0),
        "cache_creation_input_tokens": int(final_state.get("usage_cache_creation_tokens") or 0),
    }
    # Kanban #2300 — persist the resolved effort for this run so per-effort spend
    # is comparable in usage reporting. Omit when off/None (endpoint leaves the
    # column unchanged → NULL) so legacy / no-thinking runs stay clean.
    _effort = final_state.get("effort")
    if _effort and _effort != "off":
        body["effort"] = _effort
    # Forward provider + model so the API can compute total_cost_usd.
    from llm import resolve_model, resolve_provider  # local import avoids circular at module level
    try:
        body["provider"] = resolve_provider()
        body["model"] = resolve_model()
    except Exception as exc:
        # Cost computation is best-effort — skip if provider/model resolution
        # fails (e.g. LANGGRAPH_MODEL_ID unset in tests / minimal deployments).
        logger.debug("session_run usage PATCH: resolver skipped (%r)", exc)

    try:
        resp = await client.request("PATCH", url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        logger.warning(
            "session_run usage PATCH: HTTP error for run_id=%d: %r",
            session_run_id,
            exc,
        )
        return
    if resp.status_code != 200:
        logger.warning(
            "session_run usage PATCH: returned %d for run_id=%d: %s",
            resp.status_code,
            session_run_id,
            resp.text[:200],
        )
        return
    logger.info(
        "session_run usage PATCH ok: run_id=%d inp=%d out=%d cr=%d cc=%d",
        session_run_id,
        body["total_input_tokens"],
        body["total_output_tokens"],
        body["cache_read_input_tokens"],
        body["cache_creation_input_tokens"],
    )


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




# ---------------------------------------------------------------------------
# Project-field fetch helper (Kanban #957 Phase 1 / #1800 / #1652)
# ---------------------------------------------------------------------------

# Shared TTL for both per-field caches. Saves GET /api/projects/{id} on every
# HITL pause / poll tick while still picking up operator-side edits within ~10s.
# Each cache is keyed by project_id and process-local; restart clears.
_POLICY_CACHE_TTL_SEC = 10.0

# Per-field caches — kept as module-level names so tests can inspect / clear
# them by name (test_worker_policy_hook.py uses `worker._policy_cache` directly;
# test_worker_prereq_gate.py imports `_required_binaries_cache_clear` by name).
_policy_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}
_required_binaries_cache: dict[int, tuple[float, list[str] | None]] = {}
# Kanban #2300 — per-project effort_mode cache (own dict, same TTL as above).
_effort_mode_cache: dict[int, tuple[float, str | None]] = {}


def _policy_cache_clear() -> None:
    """Test hook — clear the in-process policy cache."""
    _policy_cache.clear()


def _required_binaries_cache_clear() -> None:
    """Test hook — clear the in-process required_binaries cache."""
    _required_binaries_cache.clear()


def _effort_mode_cache_clear() -> None:
    """Test hook — clear the in-process effort_mode cache."""
    _effort_mode_cache.clear()


async def _fetch_project_field(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    project_id: int,
    *,
    field: str,
    log_prefix: str,
    on_error_suffix: str,
    cache: dict[int, tuple[float, Any]],
) -> Any:
    """GET /api/projects/{project_id} and return one named field.

    Returns None on any non-200 response, missing/null field, or JSON failure.
    Does NOT cache failures — the next call retries. Results cached ~10s per
    project_id (TTL shared with approval-policy and required-binaries fetchers).
    """
    now = time.monotonic()
    cached = cache.get(project_id)
    if cached is not None and (now - cached[0]) < _POLICY_CACHE_TTL_SEC:
        return cached[1]

    try:
        resp = await client.get(
            f"{cfg.api_base}/api/projects/{project_id}", headers=headers
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "%s fetch: project %d HTTP error %s; %s",
            log_prefix,
            project_id,
            exc,
            on_error_suffix,
        )
        # Do NOT cache the failure — the next call should retry.
        return None
    if resp.status_code != 200:
        logger.warning(
            "%s fetch: project %d returned %d; %s",
            log_prefix,
            project_id,
            resp.status_code,
            on_error_suffix,
        )
        return None
    try:
        body = resp.json()
    except ValueError:
        logger.warning(
            "%s fetch: project %d returned non-JSON body",
            log_prefix,
            project_id,
        )
        return None
    value = body.get(field) if isinstance(body, dict) else None
    cache[project_id] = (now, value)
    return value


async def _fetch_project_policies(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    project_id: int,
) -> dict[str, Any] | None:
    """Return `approval_policies` from GET /api/projects/{project_id}.

    Cached ~10s per project_id (Kanban #957 Phase 1). Returns None on any
    read failure; caller falls back to REQUIRE_ATTENTION.

    Note: GET /api/projects/{id} does NOT consult the X-Project-Id header
    (project endpoints are by-id), but passing the existing headers is
    harmless and keeps the call signature uniform with _patch_task.
    """
    return await _fetch_project_field(
        client, cfg, headers, project_id,
        field="approval_policies",
        log_prefix="approval_policies",
        on_error_suffix="falling back to REQUIRE_ATTENTION",
        cache=_policy_cache,
    )


async def _fetch_project_required_binaries(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    project_id: int,
) -> list[str] | None:
    """Return `required_binaries` from GET /api/projects/{project_id}.

    Returns None (= "no host-binary requirements"; gate skips) on:
      - any non-200 response
      - missing / null `required_binaries` field in the body
      - JSON parse failure
      - a non-list value (defensive — a hand-edited row should not crash the
        gate; treat malformed shapes as "no requirements" and proceed).

    FAIL-OPEN rationale: a transient API hiccup must NOT block every task on the
    board. The gate's job is to catch the *declared-and-missing* case crisply;
    when we cannot read the declaration, we proceed (the legacy opaque
    FileNotFoundError remains the backstop). Cached ~10s per project_id
    (Kanban #1800 / #1652).
    """
    value = await _fetch_project_field(
        client, cfg, headers, project_id,
        field="required_binaries",
        log_prefix="required_binaries",
        on_error_suffix="proceeding (gate fails open on read failure)",
        cache=_required_binaries_cache,
    )
    # Value-tolerant: only a list of names is meaningful. A hand-edited scalar /
    # dict is treated as "no requirements" so the gate never crashes on a
    # malformed row (parity with the API's value-tolerant ProjectRead).
    if value is not None and not isinstance(value, list):
        logger.warning(
            "required_binaries fetch: project %d has non-list value %r; "
            "treating as no requirements",
            project_id,
            value,
        )
        value = None
    return value


# ---------------------------------------------------------------------------
# Effort lever resolution (Kanban #2300)
# ---------------------------------------------------------------------------

# Ladder order for the auto-path server-side clamp. 'off' is the floor; 'extra'
# is the hard cap auto may reach (design lock D4 — 'max' is manual-only via the
# per-task carrier and is NEVER selected by auto). Index = rank.
_EFFORT_LADDER: tuple[str, ...] = ("off", "low", "medium", "high", "extra")
# Values legal as a per-task CARRIER (tasks.effort_override). Superset of the
# ladder: 'max' is reachable here (manual-only). A carrier outside this set is
# treated as absent (fall through to project mode).
_EFFORT_CARRIER_VALUES: frozenset[str] = frozenset(
    {"off", "low", "medium", "high", "extra", "max"}
)
# Values legal as a project MODE (projects.effort_mode). Presets + 'auto'.
_EFFORT_PROJECT_PRESETS: frozenset[str] = frozenset(
    {"off", "low", "medium", "high", "extra"}
)


async def _fetch_project_effort_mode(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    project_id: int,
) -> str | None:
    """Return `effort_mode` from GET /api/projects/{project_id} (Kanban #2300).

    Cached ~10s per project_id (same TTL as policies / required_binaries).
    Returns None (= global default off) on any read failure or missing/null
    field — fail-CLOSED to off so a transient API hiccup never silently turns
    thinking ON for a project that didn't ask for it.
    """
    value = await _fetch_project_field(
        client, cfg, headers, project_id,
        field="effort_mode",
        log_prefix="effort_mode",
        on_error_suffix="defaulting to off (no thinking)",
        cache=_effort_mode_cache,
    )
    if value is not None and not isinstance(value, str):
        # Value-tolerant: a hand-edited non-string row is treated as "off".
        logger.warning(
            "effort_mode fetch: project %d has non-str value %r; treating as off",
            project_id,
            value,
        )
        return None
    return value


def _clamp_effort(value: str | None) -> str:
    """Clamp an effort value to the auto-path ceiling 'extra' (design lock D4).

    UNCONDITIONAL server-side cap (AC7): 'max' and any unknown/out-of-ladder
    value collapse to 'extra'; a legal ladder value passes through. None → 'off'.
    This is the only thing standing between a hacked heuristic output and an
    unbounded spend, so it never trusts its input.
    """
    if value is None:
        return "off"
    if value in _EFFORT_LADDER:
        return value
    # 'max' or anything unrecognized — cap at the top of the auto ladder.
    return "extra"


def _resolve_auto_effort(task: dict[str, Any]) -> str:
    """Heuristic effort level for project mode 'auto' (Kanban #2300).

    Rules (in order; first match wins for the low tier, then the high tier):
      - default                                  → 'medium'
      - task_type in {docs, chore}               → 'low'
      - model_override == 'opus'                 → 'high'
      - len(description) > 4000                  → 'high'
      - assigned_role (string) contains 'sr-'    → 'high'   (see NOTE)

    NOTE on the 'sr-' clause: `tasks.assigned_role` is an INTEGER role code
    (1..5) at every layer — the senior/`sr-` tier is a spawn-time subagent
    choice the Lead makes, never recorded on the task row. The check is kept
    string-tolerant so it fires IF a future payload ever carries a role SLUG,
    but in practice the load-bearing high signals are opus + large-spec.

    The result is ALWAYS passed through `_clamp_effort` by the caller (the cap
    is unconditional on the auto path) — this function never returns 'max', but
    even if a future edit did, the clamp would catch it.
    """
    task_type = (task.get("task_type") or "").strip().lower()
    if task_type in ("docs", "chore"):
        return "low"

    if (task.get("model_override") or "").strip().lower() == "opus":
        return "high"

    description = task.get("description") or ""
    if len(description) > 4000:
        return "high"

    # String-tolerant 'sr-' check (defensive — see docstring NOTE).
    role = task.get("assigned_role")
    if isinstance(role, str) and "sr-" in role.lower():
        return "high"

    return "medium"


async def _resolve_effort_for_spawn(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    task: dict[str, Any],
    project_id: int,
) -> str | None:
    """Resolve the effort level for a task spawn (Kanban #2300).

    Precedence (design lock D3): task carrier > project effort_mode > off.
      1. A valid `tasks.effort_override` carrier wins outright (incl. manual 'max').
      2. Else fetch the project's `effort_mode`:
         - a preset (off/low/medium/high/extra) → use it directly;
         - 'auto' → `_resolve_auto_effort(task)`, then UNCONDITIONALLY clamped
           through `_clamp_effort` (server-side cap at 'extra', AC7) AND written
           back to the carrier (best-effort PATCH) so the resolution is visible.
         - NULL / absent / invalid → None (= off).

    Returns the resolved effort string, or None for off (None and 'off' are
    equivalent downstream — make_chat_model treats both as the no-thinking path).
    """
    carrier = task.get("effort_override")
    if isinstance(carrier, str) and carrier in _EFFORT_CARRIER_VALUES:
        return carrier

    mode = await _fetch_project_effort_mode(client, cfg, headers, project_id)
    if mode in _EFFORT_PROJECT_PRESETS:
        return mode
    if mode != "auto":
        # NULL / absent / unknown project mode → off.
        return None

    # 'auto' — heuristic + UNCONDITIONAL clamp (the cap never trusts the
    # heuristic; a hacked 'max' output collapses to 'extra').
    resolved = _clamp_effort(_resolve_auto_effort(task))
    # Best-effort: write the resolved level to the carrier so it's visible on the
    # task. Never block the run on a PATCH failure (log-warn only).
    task_id = task.get("id")
    if task_id is not None:
        try:
            await _patch_task(
                client, cfg, headers, task_id, {"effort_override": resolved}
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "effort auto-resolution: carrier PATCH failed for task %s: %r",
                task_id,
                exc,
            )
    return resolved


# ---------------------------------------------------------------------------
# HITL resume (Kanban #986)
# ---------------------------------------------------------------------------


def _last_valid_answer(question_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the last entry in answer_history with is_valid=True, or None.

    Walks backwards (newest first) so a long history is cheap. None when the
    payload is missing, answer_history is empty, or every entry is invalidated.
    """
    if not question_payload:
        return None
    history = question_payload.get("answer_history") or []
    for entry in reversed(history):
        if entry.get("is_valid"):
            return entry
    return None


def _needs_resume(task: dict[str, Any]) -> tuple[bool, str | None]:
    """Decide whether `task` is HITL-paused with an unconsumed answer.

    Returns (needs_resume, answer_value). `needs_resume=False` (the common
    case — task is awaiting input, not yet answered) returns (False, None)
    without raising. Idempotency contract: a task already resumed (cursor
    advanced past the newest answer) returns (False, None) so the worker
    skips it.
    """
    halt = task.get("halt_reason")
    # Only paused-for-HITL tasks are candidates. halt_reason='question' or
    # 'decision' is the worker-stamped marker; LLM-stamped halt_reason strings
    # (e.g., 'tool_permission_review: ...') are NOT auto-resumable here.
    if halt not in ("question", "decision"):
        return False, None
    answer = _last_valid_answer(task.get("question_payload"))
    if answer is None:
        return False, None
    answered_at = answer.get("answered_at")
    if not answered_at:
        # Malformed entry (shouldn't happen with append_answer's shape) —
        # treat as not resumable rather than crash the tick.
        return False, None
    # Idempotency cursor: resume_context.last_consumed_answered_at carries the
    # ISO timestamp of the most recent answer the worker has consumed for
    # this task. If the latest valid answer's answered_at is <= that cursor,
    # the worker has already resumed (or attempted to) — skip.
    ctx = task.get("resume_context") or {}
    cursor = ctx.get("last_consumed_answered_at")
    if cursor is not None and answered_at <= cursor:
        return False, None
    return True, answer.get("value")


async def _maybe_resume_hitl_task(
    client: httpx.AsyncClient,
    graph_module: ModuleType,
    cfg: WorkerConfig,
    task: dict[str, Any],
    headers: dict[str, str],
) -> None:
    """Inspect one pending_questions task; resume it if it has an unconsumed answer.

    No-op on tasks that aren't HITL-paused (halt_reason mismatch), have no
    valid answer, or whose latest answer was already consumed. Otherwise
    delegates to `_resume_hitl_task` which does the actual graph invoke +
    PATCH.
    """
    needs, raw_answer = _needs_resume(task)
    if not needs:
        return
    await _resume_hitl_task(client, graph_module, cfg, task, raw_answer, headers)


async def _resume_hitl_task(
    client: httpx.AsyncClient,
    graph_module: ModuleType,
    cfg: WorkerConfig,
    task: dict[str, Any],
    raw_answer: Any,
    headers: dict[str, str],
    *,
    policy_rule_name: str | None = None,
) -> None:
    """Resume a single HITL-paused task with `raw_answer` from answer_history.

    Sequence:
      1. Validate the answer against question_payload (strict — Q3=A).
      2. Resolve compiled graph from graph_module; if missing, PATCH BLOCKED.
      3. Call `hitl.resume_graph(...)` — wraps `graph.ainvoke(Command(resume=...))`.
      4. Map the final state to a PATCH body:
           - halt_reason absent → DONE (process_status=5, completed_at, etc.)
           - halt_reason present → BLOCKED (process_status=4, halt_reason carried)
           - HITLError raised → BLOCKED with halt_reason = error's halt_code
      5. Stamp resume_context.last_consumed_answered_at on the PATCH so a
         duplicate poll doesn't re-resume.

    `policy_rule_name` (Kanban #957): when the resume was triggered by an
    auto-approve policy hit, this is the matched rule's name — surfaced into
    `status_change_reason` so `tasks_history` carries the audit trail
    (per-policy audit log deferred to a later slice). None on operator-driven
    resumes (the original #986 flow).
    """
    task_id = task["id"]
    question_payload = task.get("question_payload")
    # interaction_kind discriminates decision tasks (which require chosen_id on
    # the DONE flip — Kanban #1007 / #1695) from plain question tasks. TaskRead
    # always carries this field; default to 'question' if a synthetic caller
    # (auto-approve policy path) omitted it — only 'decision' triggers the
    # chosen_id merge, so the default is the safe / no-op branch.
    interaction_kind = task.get("interaction_kind") or "question"
    # Capture the answered_at NOW so we can stamp the cursor on the PATCH.
    # _last_valid_answer was just called inside _needs_resume; re-derive here
    # so this helper stays callable independently for testing.
    last_answer = _last_valid_answer(question_payload)
    answered_at = (last_answer or {}).get("answered_at")

    # 1) Validate.
    try:
        validated = validate_answer(question_payload, raw_answer)
    except InvalidAnswerError as exc:
        logger.warning(
            "hitl resume: task %d invalid answer (%s): %s",
            task_id,
            exc.halt_code,
            exc,
        )
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            _build_resume_halt_body(exc, answered_at, task.get("resume_context")),
        )
        return

    # 2) Resolve graph.
    compiled = getattr(graph_module, "graph", None)
    if compiled is None:
        logger.error(
            "hitl resume: graph_module.graph is None — PATCHing task %d BLOCKED",
            task_id,
        )
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "langgraph error: compiled_graph not initialized",
            },
        )
        return

    # 3) Invoke resume.
    try:
        final_state = await resume_graph(compiled, task_id, validated)
    except CheckpointMissingError as exc:
        logger.warning("hitl resume: task %d checkpoint missing", task_id)
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            _build_resume_halt_body(exc, answered_at, task.get("resume_context")),
        )
        return
    except EngineCrashError as exc:
        logger.exception("hitl resume: task %d engine crash", task_id)
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            _build_resume_halt_body(exc, answered_at, task.get("resume_context")),
        )
        return
    except asyncio.CancelledError:
        logger.info("hitl resume: task %d interrupted by shutdown", task_id)
        raise

    # 4) Map final state to PATCH body.
    halt = final_state.get("halt_reason") if isinstance(final_state, dict) else None
    final_result = ""
    if isinstance(final_state, dict):
        final_result = (final_state.get("final_result") or "").strip()
    # L23 (Kanban #1126) — same defense as `_build_finalize_body`. The HITL
    # resume path lands `final_result` in `status_change_reason` (operator-
    # trusted), so an LLM that echoes destructive SQL in its post-resume
    # output must be caught here too.
    safe_final_result = sanitize_agent_action(final_result)
    sanitized_fired = safe_final_result is None and final_result != ""
    if sanitized_fired:
        logger.critical(
            "L23 (resume path): final_result echoed destructive SQL; "
            "demoting to BLOCKED + sanitized placeholder. raw_prefix=%r",
            final_result[:80],
        )
        final_result = (
            "[sanitized: agent echoed destructive SQL — review needed]"
        )
    else:
        final_result = safe_final_result or ""
    # Also check for a fresh __interrupt__ — the graph paused again (multi-step
    # HITL). Treat as BLOCKED with halt_reason='question' (default; the node's
    # own emission semantics would have set halt_reason if it wanted a
    # different value).
    fresh_interrupt = (
        isinstance(final_state, dict) and final_state.get("__interrupt__")
    )

    # Kanban #957 — when the resume was triggered by an auto-approve policy,
    # prefix the status_change_reason so tasks_history captures which rule
    # fired. Per-policy audit log column deferred (Phase 1 minimal).
    policy_prefix = (
        f"auto-approved by policy {policy_rule_name!r}: "
        if policy_rule_name
        else ""
    )

    if halt is None and not fresh_interrupt and not sanitized_fired:
        reason_body = final_result or "(resumed; no final_result)"
        body: dict[str, Any] = {
            "process_status": STATUS_DONE,
            "completed_at": _config.utc_now(),
            "status_change_reason": f"{policy_prefix}{reason_body}"[:_REASON_MAX],
            # Clear halt_reason now that the engine finished — leaving it set
            # would keep the FE banner up.
            "halt_reason": None,
            "is_pending": False,
            "resume_context": _stamped_resume_context(
                task.get("resume_context"), answered_at
            ),
        }
        # Kanban #1695 (Fix A) — a DECISION task can only flip to DONE with
        # chosen_id set: the api's #1007 done-flip validator (tasks.py ~1822,
        # services/task_interaction.py::validate_decision_payload) rejects the
        # PATCH 422 otherwise, and the bundled cursor never persists →
        # _needs_resume re-resumes forever (#1081, #1094 stuck since ~2026-05-16).
        # `validated` is the chosen option string (validate_answer matched it
        # against question_payload.options, so it IS a valid option id —
        # mirrors the /decide #1007 contract: chosen_id lives in
        # question_payload, chosen_at is UTC Z-suffix). PRESERVE the existing
        # payload (question / options / answer_history); only question tasks
        # (interaction_kind != 'decision') skip the merge — they have no
        # chosen_id requirement.
        if interaction_kind == "decision":
            body["question_payload"] = {
                **(question_payload or {}),
                "chosen_id": validated,
                "chosen_at": _config.utc_now(),
            }
    elif halt is None and not fresh_interrupt and sanitized_fired:
        # L23 override: would have been DONE, but sanitizer fired — halt for
        # human review instead of forwarding the echoed SQL.
        body = {
            "process_status": STATUS_BLOCKED,
            "halt_reason": "agent_output_sanitized",
            "status_change_reason": f"{policy_prefix}{final_result}"[:_REASON_MAX],
            "resume_context": _stamped_resume_context(
                task.get("resume_context"), answered_at
            ),
        }
    else:
        # Either an explicit halt_reason from a node or a fresh interrupt.
        if fresh_interrupt and halt is None:
            halt_value = "question"
        else:
            halt_value = str(halt) if halt is not None else "question"
        reason_body = final_result or f"halted: {halt_value}"
        body = {
            "process_status": STATUS_BLOCKED,
            "halt_reason": halt_value[:_HALT_REASON_MAX],
            "status_change_reason": f"{policy_prefix}{reason_body}"[:_REASON_MAX],
            "resume_context": _stamped_resume_context(
                task.get("resume_context"), answered_at
            ),
        }

    resp = await _patch_task(client, cfg, headers, task_id, body)
    if resp is None:
        # Kanban #1695 (Fix B) — the finalize PATCH was rejected (e.g. 422 from
        # the #1007 decision-done validator, or any other api-side rejection).
        # The cursor stamped in `body.resume_context` rode along on that SAME
        # rejected PATCH, so it never persisted → _needs_resume would re-resume
        # this task every poll (10s) FOREVER (the #1081 / #1094 loop).
        #
        # Defense-in-depth: issue ONE structured give-up PATCH that BOTH
        #   (a) advances resume_context.last_consumed_answered_at (decoupled
        #       from DONE success) so _needs_resume returns False next poll, AND
        #   (b) sets halt_reason='resume_finalize_failed' (not in
        #       {question, decision}) so the failure is VISIBLE on the board as
        #       BLOCKED rather than silently looping.
        # _patch_task already logged the rejection (status + body); add a
        # task-scoped error line so the loop cause is greppable.
        logger.error(
            "hitl resume: task %d finalize PATCH rejected; issuing give-up "
            "PATCH (halt_reason='resume_finalize_failed', cursor advanced) to "
            "break the re-resume loop. rejected_body=%r",
            task_id,
            body,
        )
        await _patch_task(
            client,
            cfg,
            headers,
            task_id,
            {
                "process_status": STATUS_BLOCKED,
                "halt_reason": "resume_finalize_failed",
                "status_change_reason": (
                    "resume finalize PATCH was rejected by the api; halted for "
                    "human review (see worker logs for the rejected body)."
                )[:_REASON_MAX],
                "resume_context": _stamped_resume_context(
                    task.get("resume_context"), answered_at
                ),
            },
        )
        return
    logger.info(
        "hitl resume: task %d resumed; halt=%s",
        task_id,
        body.get("halt_reason"),
    )


def _build_resume_halt_body(
    exc: HITLError,
    answered_at: str | None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PATCH body for a HITL failure (invalid answer / missing checkpoint / crash).

    BLOCKED + halt_reason from the exception's halt_code. `is_pending` is
    omitted (defaults False) — the API validator rejects `is_pending=True`
    paired with any process_status other than IN_PROGRESS (2). The cursor is
    stamped so a duplicate poll doesn't retry the same broken answer
    endlessly. `existing` is the prior resume_context dict — callers should
    pass `task.get("resume_context")` so free-form keys stashed by upstream
    survive the failure PATCH.
    """
    return {
        "process_status": STATUS_BLOCKED,
        "halt_reason": exc.as_halt_reason()[:_HALT_REASON_MAX],
        "status_change_reason": str(exc)[:_REASON_MAX],
        "resume_context": _stamped_resume_context(existing, answered_at),
    }


def _stamped_resume_context(
    existing: dict[str, Any] | None, answered_at: str | None
) -> dict[str, Any]:
    """Return a resume_context dict with `last_consumed_answered_at` set.

    Preserves any other keys the caller had stashed (free-form per the schema).
    """
    base = dict(existing or {})
    if answered_at:
        base["last_consumed_answered_at"] = answered_at
    return base
