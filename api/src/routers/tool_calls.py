"""HTTP routes for tool_calls (Kanban #980 + #981).

Mounted at `/api/tasks/{task_id}/tool-calls`. Sub-resource of tasks.

Endpoints:

  GET /api/tasks/{task_id}/tool-calls
    Public — the FE timeline UI consumes this.
    Headers:  X-Project-Id (required — sub-resource of /api/tasks/*)
    Response: [ToolCallRead, ...]  ordered by invoked_at DESC
    Errors:
      400  X-Project-Id missing OR task belongs to a different project
      404  task not found
      410  task soft-deleted (status=0) — sub-resource is Gone with the
           parent

  POST /api/tasks/{task_id}/tool-calls       (Kanban #981 — internal only)
    Producer: langgraph specialist tool-use loop. NOT advertised to FE
    clients. Delegates to `services.tool_call_writer.record_tool_call`
    after the same X-Project-Id + soft-delete gates as GET.
    Body:     ToolCallCreate
    Response: 201 + ToolCallRead
    Errors:
      400  X-Project-Id missing OR task belongs to a different project
      404  task not found
      410  task soft-deleted

Soft-delete semantics: the bare `GET /api/tasks/{id}` returns soft-
deleted rows per the standards convention (the caller already knows
the id). But the audit-timeline sub-resource is different — the
timeline UI fetches it to render "what did the agent do?" — once the
parent is deleted that context is gone; 410 is the right signal so
the FE stops polling. POST mirrors this — once the parent is gone the
agent has no business appending more rows.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.task import Task
from src.models.tool_call import ToolCall
from src.schemas.tool_call import (
    LeadActivityCreate,
    ToolCallCreate,
    ToolCallRead,
)
from src.services.session_project import (
    assert_task_belongs_to_session,
    require_project_id_header,
)
from src.services.tool_call_writer import record_lead_activity, record_tool_call

router = APIRouter(prefix="/tasks", tags=["tool-calls"])


@router.get("/{task_id}/tool-calls", response_model=list[ToolCallRead])
async def list_tool_calls(
    task_id: int,
    limit: int | None = Query(default=None, ge=1, le=50),
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> list[ToolCall]:
    """List audit rows for one task — ordered by `invoked_at DESC`.

    See module docstring for the full contract. The query lands on the
    `ix_tool_calls_task_id_invoked_at` composite index — single B-tree
    walk, no separate sort.

    Optional `limit` (1..50): caps the number of rows returned. Omitted →
    full list (byte-identical to previous behavior). Kanban #2334.
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
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/{task_id}/tool-calls",
    response_model=ToolCallRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_tool_call(
    task_id: int,
    body: dict[str, Any] = Body(...),
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> ToolCall:
    """Append one row to the activity rail — dual-contract (#981 + #2320).

    Two write shapes share this URL; dispatch is by the `source` discriminator
    (the #2124 same-URL body-shape lock):

      * body `source == 'lead'`  → `LeadActivityCreate` (Lead checkpoint;
        kind + summary required, engine-only columns left NULL).
      * body without `source` (or `source == 'engine'`) → `ToolCallCreate`
        (the #981 engine path — byte-identical behavior, never sends `source`).

    422 fires with a clear `loc` on the appropriate path: invalid kind /
    missing summary on the lead path; the unchanged engine 422s otherwise.

    Internal endpoint — engine path consumed by
    `langgraph/audit.py::record_tool_invocation`; lead path by the `zb-report`
    skill. The audit table is append-only on the wire (no PATCH / DELETE) and
    the writer service owns the only persistence path.

    Gate order mirrors GET (run BEFORE the writer):
      1. require_project_id_header (400 on missing)
      2. get_or_404 (404 on unknown task)
      3. assert_task_belongs_to_session (400 on cross-project header)
      4. RecordStatus.DELETED → 410 (audit closed with the parent)
      5. writer → 201 + persisted row

    Failure isolation: the writer commits synchronously (#949 Q9 lock).
    On a write error the writer re-raises; the langgraph-side caller MUST treat
    a non-2xx response as a sandbox-invariant violation and halt the task.
    """
    # Dispatch by the `source` discriminator. Validate the chosen shape and
    # re-raise as RequestValidationError so FastAPI returns the standard 422
    # body with a proper `loc` (engine-path 422s are byte-unchanged).
    is_lead = isinstance(body, dict) and body.get("source") == "lead"
    try:
        if is_lead:
            lead = LeadActivityCreate.model_validate(body)
        else:
            engine = ToolCallCreate.model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    if task.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=410,
            detail=f"Task id={task_id} is deleted; tool-call audit is gone with the parent",
        )

    if is_lead:
        return await record_lead_activity(
            task_id=task_id,
            kind=lead.kind,
            summary=lead.summary,
            success=lead.success,
            tool_name=lead.tool_name,
            db=session,
        )

    return await record_tool_call(
        task_id=task_id,
        tool_name=engine.tool_name,
        tier=engine.tier,
        input_args=engine.input_args,
        result=engine.result.model_dump(),
        permission_decision=engine.permission_decision,
        db=session,
    )
