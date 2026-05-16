"""HITL (human-in-the-loop) resume helpers — Kanban #986 (#950 umbrella).

This module contains the engine-side glue between LangGraph's `interrupt()`
primitive and the Kanban task model's `interaction_kind` + `question_payload`
columns. Three responsibilities, kept thin on purpose so the worker can call
them without owning graph internals:

  1. **`request_user_input(payload)`** — canonical wrapper around
     `langgraph.types.interrupt`. A specialist node calls it to pause execution
     and surface a structured prompt to the user. On resume LangGraph hands
     back the resume value (the user's answer string), which `request_user_input`
     returns verbatim. The single-call indirection lets us evolve the payload
     shape in one place if Kanban's question_payload schema changes.

  2. **`validate_answer(question_payload, answer)`** — Q3=A strict validation
     per design doc §5.1. Decision tasks (payload.options non-empty) require
     the answer to match one of the option strings exactly. Question tasks
     (payload.options None or empty) accept any non-empty string. Raises
     `InvalidAnswerError` subclasses on each failure mode so the worker can
     PATCH a structured halt_reason.

  3. **`resume_graph(graph, task_id, answer, checkpoint_required=True)`** — the
     single async entrypoint the worker uses to resume a paused thread. It
     wraps:
       - `Command(resume=answer)` construction (no string concat — Q1=A locked).
       - Checkpoint-presence check via `graph.aget_state(config)` — when the
         caller asks for `checkpoint_required=True` (the production path) and
         the thread has no prior state, raise `CheckpointMissingError` so the
         worker PATCHes `halt_reason='checkpoint_missing'` instead of silently
         starting a fresh run.
       - `graph.ainvoke(Command(resume=...), config={"configurable":
         {"thread_id": f"task-{task_id}"}})`. Any exception raised by the graph
         (engine crash mid-resume) bubbles to the worker as `EngineCrashError`.

Idempotency note: LangGraph's checkpoint is durable. Re-issuing `Command(resume=X)`
against a thread that has already advanced past the interrupt is a no-op —
the second invoke returns the same final state without re-executing the node
body (verified 2026-05-16 against LangGraph 1.2.0; see test_hitl.py
`test_resume_idempotent_no_double_execution`). The worker still PATCHes per
resume call, but the audit trail captures the duplicate without DB-side
side-effects because the graph state didn't change.

Thread-id convention mirrors `worker.py::_poll_once`: `f"task-{task_id}"`.
We don't import it from worker to avoid a circular dep; the constant is
load-bearing wire contract pinned by `test_resume_thread_id_matches_worker`.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command, interrupt

logger = logging.getLogger("langgraph.hitl")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HITLError(Exception):
    """Base for all HITL-specific failures the worker turns into halt_reason."""

    halt_code: str = "hitl_error"

    def as_halt_reason(self) -> str:
        """Format this exception as the string the worker writes to
        `tasks.halt_reason`. Pinned shape: `<halt_code>:<short message>`.
        Stable wire contract — UI may grep on the prefix to colour-code halt
        causes."""
        return f"{self.halt_code}:{self}"


class InvalidAnswerError(HITLError):
    """Top-level for answer-validation failures. Specific subclasses carry the
    actual halt_code so the UI can distinguish "empty answer" from "not in
    options" without parsing the message."""

    halt_code = "invalid_answer"


class MissingQuestionPayloadError(InvalidAnswerError):
    halt_code = "invalid_answer_missing_payload"


class EmptyAnswerError(InvalidAnswerError):
    halt_code = "invalid_answer_empty"


class AnswerNotInOptionsError(InvalidAnswerError):
    halt_code = "invalid_answer_not_in_options"


class CheckpointMissingError(HITLError):
    """The thread has no checkpoint to resume from. Either the task was never
    paused via `interrupt()` (caller bug), or the checkpoint was wiped (admin
    cleanup). The worker PATCHes halt_reason and waits for human review."""

    halt_code = "checkpoint_missing"


class EngineCrashError(HITLError):
    """LangGraph raised during resume — the underlying cause is wrapped via
    `from exc` so `__cause__` carries the original traceback. The halt_reason
    string includes the original exception class + truncated message so the
    operator can grep without digging through logs."""

    halt_code = "engine_crash"

    def __init__(self, cause: BaseException) -> None:
        # Cap the inlined message so a pathological traceback doesn't bloat
        # the halt_reason column (DB column is plain TEXT but the worker
        # truncates at _HALT_REASON_MAX=500; we cap earlier to leave room
        # for the prefix).
        cause_msg = str(cause)[:300]
        super().__init__(f"{type(cause).__name__}: {cause_msg}")


# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------


def thread_id_for_task(task_id: int) -> str:
    """Canonical thread-id string used by both invoke + resume code paths.

    Mirrors `worker.py::_poll_once` (which builds the same string inline).
    Centralising here keeps the convention testable; drift would surface in
    `test_resume_thread_id_matches_worker`."""
    return f"task-{task_id}"


def resume_config(task_id: int) -> dict[str, Any]:
    """The `config` dict passed to `graph.ainvoke` on resume.

    Returns `{"configurable": {"thread_id": "task-<id>"}}` — same shape the
    worker uses for fresh invokes. The checkpointer keys all state by thread
    so this is the only handle needed to load the paused state."""
    return {"configurable": {"thread_id": thread_id_for_task(task_id)}}


# ---------------------------------------------------------------------------
# Interrupt emission (specialist-node side)
# ---------------------------------------------------------------------------


def request_user_input(payload: dict[str, Any]) -> str:
    """Pause graph execution and surface `payload` to the worker for HITL.

    `payload` SHOULD mirror Kanban's question_payload shape so the worker can
    forward it to the DB without re-shaping:

        {"question": "Deploy to staging or production?",
         "options": ["staging", "prod"]}    # decision (options is non-empty)

        {"question": "Describe the bug"}    # question (options absent/empty)

    On the FIRST call inside a node this raises `GraphInterrupt`, halts the
    graph, and the worker PATCHes the task. On SUBSEQUENT calls within the
    same task (after resume) the same `interrupt()` returns the user's answer
    string, which this function returns to the specialist.

    The returned answer is a plain string (Q1=A locked: no JSON, no concat;
    the worker pre-validates and passes the raw string through Command.resume).
    """
    return interrupt(payload)


# ---------------------------------------------------------------------------
# Answer validation (worker-side, pre-resume)
# ---------------------------------------------------------------------------


def validate_answer(
    question_payload: dict[str, Any] | None, answer: Any
) -> str:
    """Validate `answer` against the task's question_payload (strict).

    Per design doc §5.1 Q3=A:
      - missing question_payload → MissingQuestionPayloadError
      - empty answer (None / "" / whitespace-only) → EmptyAnswerError
      - decision (options is a non-empty list) → answer MUST match an option
        verbatim; mismatch → AnswerNotInOptionsError
      - question (options is None or empty) → any non-empty string accepted

    Returns the normalised answer string (stripped of leading/trailing
    whitespace) — the value to pass into `Command(resume=...)`.

    Options contract:
        `options` MUST be `list[str]`. Non-string options will never match
        because validate_answer coerces user-submitted answers to `str(answer)`
        before comparison. Enforced by typing convention; not runtime-guarded.
    """
    if question_payload is None:
        raise MissingQuestionPayloadError(
            "task has no question_payload; cannot validate answer"
        )

    if answer is None:
        raise EmptyAnswerError("answer is None")
    if not isinstance(answer, str):
        # Coerce numeric / bool answers to str; the wire contract is string.
        # An empty list / dict still fails the non-empty check below.
        answer = str(answer)
    normalised = answer.strip()
    if not normalised:
        raise EmptyAnswerError("answer is empty or whitespace-only")

    options = question_payload.get("options") or []
    if options:
        # Decision task — answer must exactly match one of the options.
        if normalised not in options:
            raise AnswerNotInOptionsError(
                f"answer {normalised!r} not in options {list(options)!r}"
            )
    # question task — any non-empty string is acceptable; the specialist
    # node decides if the content is usable.
    return normalised


# ---------------------------------------------------------------------------
# Resume entrypoint (worker-side)
# ---------------------------------------------------------------------------


async def has_checkpoint(graph: Any, task_id: int) -> bool:
    """True iff the thread for `task_id` has a prior checkpoint.

    Tests the `state.created_at` field returned by `graph.aget_state(config)`.
    On a thread that was never invoked, LangGraph returns a state with
    `created_at=None`; on a thread with at least one prior invoke, it's a
    real ISO-8601 timestamp."""
    state = await graph.aget_state(resume_config(task_id))
    return getattr(state, "created_at", None) is not None


async def resume_graph(
    graph: Any,
    task_id: int,
    answer: str,
    *,
    checkpoint_required: bool = True,
) -> dict[str, Any]:
    """Resume a paused graph thread with `answer` as the interrupt value.

    Returns the final state dict (whatever the graph emits — usually a dict
    with `messages` + optionally `final_result` / `halt_reason`).

    `checkpoint_required=True` (production default): if the thread has no
    prior checkpoint, raise `CheckpointMissingError`. This is the safety net
    against the worker silently starting a fresh run on a corrupted task
    (e.g., admin deleted the checkpoint rows but left the task BLOCKED).

    Engine crashes (anything raised by `graph.ainvoke`) are wrapped in
    `EngineCrashError` so the worker can PATCH a structured halt_reason.
    """
    if checkpoint_required:
        if not await has_checkpoint(graph, task_id):
            raise CheckpointMissingError(
                f"no prior checkpoint for task-{task_id}"
            )

    config = resume_config(task_id)
    try:
        return await graph.ainvoke(Command(resume=answer), config=config)
    # DELIBERATE: `except Exception` (not BaseException) lets asyncio.CancelledError
    # pass through unchanged — required for graceful task cancellation in py3.11+.
    except Exception as exc:
        raise EngineCrashError(exc) from exc
