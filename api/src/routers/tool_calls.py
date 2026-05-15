"""HTTP routes for tool_calls (Kanban #980).

Mounted at `/api/tasks/{task_id}/tool-calls` (read-only sub-resource of
tasks). Clients cannot POST/PATCH/DELETE — the audit table is written
exclusively by `services.tool_call_writer.record_tool_call`, invoked
from inside the langgraph specialist tool-use loop (#981 wires it).

Endpoint contract:

  GET /api/tasks/{task_id}/tool-calls
    Headers:  X-Project-Id (required — sub-resource of /api/tasks/*)
    Response: [ToolCallRead, ...]  ordered by invoked_at DESC
    Errors:
      400  X-Project-Id missing OR task belongs to a different project
      404  task not found
      410  task soft-deleted (status=0) — sub-resource is Gone with the
           parent

Soft-delete semantics: the bare `GET /api/tasks/{id}` returns soft-
deleted rows per the standards convention (the caller already knows
the id). But the audit-timeline sub-resource is different — the
timeline UI fetches it to render "what did the agent do?" — once the
parent is deleted that context is gone; 410 is the right signal so
the FE stops polling.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.task import Task
from src.models.tool_call import ToolCall
from src.schemas.tool_call import ToolCallRead
from src.services.session_project import (
    assert_task_belongs_to_session,
    require_project_id_header,
)

router = APIRouter(prefix="/tasks", tags=["tool-calls"])


@router.get("/{task_id}/tool-calls", response_model=list[ToolCallRead])
async def list_tool_calls(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> list[ToolCall]:
    """List audit rows for one task — ordered by `invoked_at DESC`.

    See module docstring for the full contract. The query lands on the
    `ix_tool_calls_task_id_invoked_at` composite index — single B-tree
    walk, no separate sort.
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    # Kanban #695: cross-check the session-bound project against the row.
    # Fires AFTER get_or_404 so 404 still wins on a missing id.
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    # 410 Gone — the parent task is soft-deleted, sub-resource has no
    # audit-timeline meaning. Distinct from 404 (id never existed).
    if task.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=410,
            detail=f"Task id={task_id} is deleted; tool-call audit is gone with the parent",
        )

    stmt = (
        select(ToolCall)
        .where(ToolCall.task_id == task_id)
        .order_by(ToolCall.invoked_at.desc(), ToolCall.id.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
