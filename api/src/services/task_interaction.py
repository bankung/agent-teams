"""Question/decision task interaction helpers — Kanban #832.

These helpers contain the append/invalidate/auto-unblock logic for
interaction_kind IN ('question', 'decision') tasks. Kept separate from
the router for testability; called from routers/tasks.py PATCH handler.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import TaskStatus


def append_answer(
    existing_payload: dict[str, Any] | None,
    value: str,
    answered_by: str,
    *,
    is_valid: bool = True,
    invalidated_reason: str | None = None,
) -> dict[str, Any]:
    """Return a new question_payload dict with the answer appended.

    `existing_payload` is the current task.question_payload (may be None
    for a just-created question task with no payload yet — though Pydantic
    requires it on POST/PATCH for question tasks, so None only happens
    if the row was created before this validation landed).

    Default behaviour appends with is_valid=True (the #832 happy path).
    Kanban #987 added keyword-only `is_valid` + `invalidated_reason` so
    the PATCH validation gate can record FAILED attempts to the audit
    trail (Q6=A) without reaching for a separate service. The caller
    serialises to JSON-safe dict (mode='json') before writing to JSONB.
    """
    payload = existing_payload or {"question": "", "options": None, "answer_history": []}
    history: list[dict[str, Any]] = list(payload.get("answer_history") or [])
    history.append(
        {
            "value": value,
            "answered_by": answered_by,
            "answered_at": datetime.now(timezone.utc).isoformat(),
            "is_valid": is_valid,
            "invalidated_reason": invalidated_reason,
        }
    )
    return {**payload, "answer_history": history}


def _validate_answer(
    interaction_kind: str,
    question_payload: dict[str, Any] | None,
    answer: str,
) -> tuple[bool, str | None]:
    """API-side answer gate (Kanban #987, Q3=A strict).

    Mirrors `langgraph/hitl.py::validate_answer` but returns (is_valid,
    reason) instead of raising — the PATCH handler needs to BOTH record
    the failed attempt to answer_history AND return 422, so a tuple
    keeps the control flow flat.

    Rules:
      - missing question_payload → (False, "task has no question_payload")
      - empty / whitespace-only answer → (False, "answer is empty or
        whitespace-only")
      - interaction_kind='decision' with a non-empty options list →
        answer (stripped) MUST match an option string exactly; mismatch
        → (False, "answer '<x>' not in options: [...]")
      - interaction_kind='question', OR decision task with empty/missing
        options → any non-empty string is acceptable → (True, None)
    """
    if question_payload is None:
        return False, "task has no question_payload"

    if not isinstance(answer, str):
        answer = str(answer) if answer is not None else ""
    normalised = answer.strip()
    if not normalised:
        return False, "answer is empty or whitespace-only"

    if interaction_kind == "decision":
        options = question_payload.get("options") or []
        if options and normalised not in options:
            return False, f"answer '{normalised}' not in options: {list(options)}"

    return True, None


def invalidate_last_answer(
    existing_payload: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    """Return a new question_payload dict with the last valid answer invalidated.

    Raises ValueError if no valid answer exists (caller converts to 422).
    """
    if existing_payload is None:
        raise ValueError("no question_payload on this task — cannot invalidate")
    history: list[dict[str, Any]] = list(existing_payload.get("answer_history") or [])
    # Walk backwards to find the last valid entry.
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("is_valid") is True:
            history[i] = {**history[i], "is_valid": False, "invalidated_reason": reason}
            return {**existing_payload, "answer_history": history}
    raise ValueError("no valid answer to invalidate")


def validate_decision_payload(
    question_payload: dict[str, Any] | None,
) -> None:
    """AC2 (Kanban #1007) — validate the `question_payload` of a decision task
    before allowing it to be flipped to DONE.

    Two invariants enforced:
      1. `chosen_id` MUST be non-null.
      2. `chosen_id` MUST match one of `options[].id` in the payload.

    Raises ValueError on failure (callers convert to HTTPException 422).
    This function is intentionally free of DB I/O so it can be reused by
    both the PATCH-status path and the `/decide` endpoint.
    """
    if question_payload is None:
        raise ValueError("decision task has no question_payload — cannot flip to DONE")

    chosen_id = question_payload.get("chosen_id")
    if not chosen_id:
        raise ValueError(
            "decision task requires chosen_id to be set before flipping to DONE"
        )

    options = question_payload.get("options") or []
    option_ids = [
        opt if isinstance(opt, str)
        else (opt["id"] if isinstance(opt, dict) else getattr(opt, "id", None))
        for opt in options
    ]
    if chosen_id not in option_ids:
        raise ValueError(
            f"chosen_id '{chosen_id}' does not match any option id in this decision task"
        )


async def auto_unblock_dependents(
    session: AsyncSession,
    question_task_id: int,
) -> None:
    """When a question/decision task is marked DONE, clear blocked_by + halt_reason
    on any tasks that are blocked by it and whose halt_reason starts with 'Question:'.

    This makes the parent task auto-resumable by the next auto-run loop tick
    (GET /api/tasks/next-autorun will surface it in resume_tasks once Lead
    clears the halt_reason, or it will appear in next_task if halt_reason is None).
    """
    from src.models.task import Task  # local import to avoid circular

    result = await session.execute(
        select(Task).where(
            Task.blocked_by == question_task_id,
            Task.status == 1,  # active only
        )
    )
    dependents = result.scalars().all()
    for dep in dependents:
        dep.blocked_by = None
        if dep.halt_reason and dep.halt_reason.startswith("Question:"):
            dep.halt_reason = None
