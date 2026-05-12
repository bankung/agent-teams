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
) -> dict[str, Any]:
    """Return a new question_payload dict with the answer appended.

    `existing_payload` is the current task.question_payload (may be None
    for a just-created question task with no payload yet — though Pydantic
    requires it on POST/PATCH for question tasks, so None only happens
    if the row was created before this validation landed).

    The new entry is always appended with is_valid=True. The caller
    serialises to JSON-safe dict (mode='json') before writing to JSONB.
    """
    payload = existing_payload or {"question": "", "options": None, "answer_history": []}
    history: list[dict[str, Any]] = list(payload.get("answer_history") or [])
    history.append(
        {
            "value": value,
            "answered_by": answered_by,
            "answered_at": datetime.now(timezone.utc).isoformat(),
            "is_valid": True,
            "invalidated_reason": None,
        }
    )
    return {**payload, "answer_history": history}


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
