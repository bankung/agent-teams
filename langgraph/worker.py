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
import os
import time
from datetime import datetime, timezone
from types import ModuleType
from typing import Any

import httpx

from approval_evaluator import evaluate_policy
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
    body = _build_finalize_body(final_state, completed_at=_now_iso())

    # Kanban #957 Phase 1 — approval-policy hook. Only fires on HITL pause
    # bodies (halt_reason in {question, decision}). Pre-empts the BLOCKED
    # PATCH with either a synthetic resume (auto_approve) or a recoloured
    # halt (auto_deny). Non-HITL halts + DONE bodies skip the hook entirely,
    # so this code path adds zero overhead for normal task lifecycle.
    if body.get("halt_reason") in ("question", "decision") and body.get(
        "question_payload"
    ):
        policies = await _fetch_project_policies(
            client, cfg, headers, cfg.project_id
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
            synthetic_task = {
                "id": task_id,
                "question_payload": body["question_payload"],
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
        history = raw_payload.get("answer_history") or raw_payload.get("answers")
        if history:
            payload["answer_history"] = list(history)
        kind = "decision" if payload.get("options") else "question"
        prompt_text = payload["question"][:200]
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
        if halt is None:
            body = {
                "process_status": STATUS_DONE,
                "completed_at": completed_at,
                "status_change_reason": (
                    final_result or "(no final_result emitted)"
                )[:_REASON_MAX],
            }
        else:
            # Non-HITL halts (auditor_giveup, operator_rejected, transient_error,
            # ambiguous, error, etc.) land the task BLOCKED awaiting human
            # attention. `is_pending` is omitted (defaults False) — the API
            # validator (services/is_pending.py) rejects `is_pending=True`
            # paired with any process_status other than IN_PROGRESS (2).
            body = {
                "process_status": STATUS_BLOCKED,
                "halt_reason": str(halt)[:_HALT_REASON_MAX],
                "status_change_reason": (final_result or f"halted: {halt}")[
                    :_REASON_MAX
                ],
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


# ---------------------------------------------------------------------------
# Approval-policy fetch (Kanban #957 Phase 1)
# ---------------------------------------------------------------------------

# Tiny in-process TTL cache for approval_policies. Saves a GET /api/projects/{id}
# on every HITL pause while still picking up operator-side edits within ~10s.
# The cache is keyed by project_id and intentionally bounded (one entry per
# project the worker has seen this process — typically 1 since the worker
# is single-project per env). Process-local; restart clears.
_POLICY_CACHE_TTL_SEC = 10.0
_policy_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}


def _policy_cache_clear() -> None:
    """Test hook — clear the in-process policy cache."""
    _policy_cache.clear()


async def _fetch_project_policies(
    client: httpx.AsyncClient,
    cfg: WorkerConfig,
    headers: dict[str, str],
    project_id: int,
) -> dict[str, Any] | None:
    """GET /api/projects/{project_id} and return its `approval_policies` field.

    Returns None on:
      - any non-200 response (the worker logs + falls back to REQUIRE_ATTENTION)
      - missing / null `approval_policies` field in the body
      - JSON parse failure

    Results are cached for ~10 seconds per project_id to avoid hammering the
    API on every HITL pause. Operator edits propagate within the TTL window;
    immediate uptake requires a worker restart (acceptable for Phase 1 —
    policy edits are a low-frequency operation).

    Note: GET /api/projects/{id} does NOT consult the X-Project-Id header
    (project endpoints are by-id), but passing the existing headers is
    harmless and keeps the call signature uniform with _patch_task.
    """
    now = time.monotonic()
    cached = _policy_cache.get(project_id)
    if cached is not None and (now - cached[0]) < _POLICY_CACHE_TTL_SEC:
        return cached[1]

    try:
        resp = await client.get(
            f"{cfg.api_base}/api/projects/{project_id}", headers=headers
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "approval_policies fetch: project %d HTTP error %s; falling back to REQUIRE_ATTENTION",
            project_id,
            exc,
        )
        # Do NOT cache the failure — the next pause should retry.
        return None
    if resp.status_code != 200:
        logger.warning(
            "approval_policies fetch: project %d returned %d; falling back to REQUIRE_ATTENTION",
            project_id,
            resp.status_code,
        )
        return None
    try:
        body = resp.json()
    except ValueError:
        logger.warning(
            "approval_policies fetch: project %d returned non-JSON body",
            project_id,
        )
        return None
    policies = body.get("approval_policies") if isinstance(body, dict) else None
    _policy_cache[project_id] = (now, policies)
    return policies


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

    if halt is None and not fresh_interrupt:
        reason_body = final_result or "(resumed; no final_result)"
        body: dict[str, Any] = {
            "process_status": STATUS_DONE,
            "completed_at": _now_iso(),
            "status_change_reason": f"{policy_prefix}{reason_body}"[:_REASON_MAX],
            # Clear halt_reason now that the engine finished — leaving it set
            # would keep the FE banner up.
            "halt_reason": None,
            "is_pending": False,
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

    await _patch_task(client, cfg, headers, task_id, body)
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
