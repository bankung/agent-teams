"""HTTP routes for Kanban tasks.

Mounted at `/api/tasks`. Process-status transitions stamp `started_at` /
`completed_at` on the way to in_progress / done — clients shouldn't set those directly.

Soft-delete: list endpoint default-filters `WHERE status=1`; opt-in `?include_deleted=true`
returns soft-deleted rows. DELETE /api/tasks/{id} flips `status=0`. Detail endpoint
returns the row regardless of soft-delete status (per standards/postgresql/soft-delete.md).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import RecordStatus, TaskStatus
from src.db import get_or_404, get_session
from src.models.task import Task
from src.schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Process-status transitions that auto-stamp a lifecycle timestamp (when not
# already set). Order doesn't matter — at most one entry fires per PATCH
# (process_status is a single value).
_STATUS_TIMESTAMP_FIELDS: dict[int, str] = {
    TaskStatus.IN_PROGRESS: "started_at",
    TaskStatus.DONE: "completed_at",
}


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    project_id: int = Query(..., description="Required — scope tasks to one project"),
    process_status: int | None = Query(
        default=None, description="Filter by tasks.process_status (1..5)"
    ),
    assigned_role: int | None = Query(
        default=None, description="Filter by tasks.assigned_role"
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[Task]:
    stmt = select(Task).where(Task.project_id == project_id)
    if not include_deleted:
        stmt = stmt.where(Task.status == RecordStatus.ACTIVE)
    if process_status is not None:
        stmt = stmt.where(Task.process_status == process_status)
    if assigned_role is not None:
        stmt = stmt.where(Task.assigned_role == assigned_role)
    stmt = stmt.order_by(Task.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
) -> Task:
    return await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )


@router.post("", response_model=TaskRead, status_code=http_status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = Task(**payload.model_dump())
    session.add(task)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # FK violation on project_id, or CHECK constraint failure
        raise HTTPException(status_code=400, detail=str(exc.orig)) from exc
    await session.refresh(task)
    return task


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )

    updates = payload.model_dump(exclude_unset=True)

    # Process-status-transition side effects — only stamp if not already set /
    # explicitly provided. We use the DB now() so the value matches the
    # audit-trigger snapshot.
    new_process_status = updates.get("process_status")
    if new_process_status is not None and new_process_status != task.process_status:
        field = _STATUS_TIMESTAMP_FIELDS.get(new_process_status)
        if field is not None and getattr(task, field) is None:
            updates.setdefault(field, func.now())

    # Skip writes where the new value equals the existing one — reduces audit-row
    # noise on PATCHes that touch only some fields. The lifecycle stamping above
    # already runs only when process_status actually changes, so the no-op skip
    # here doesn't bypass started_at / completed_at logic. SQL clause elements
    # (e.g., func.now()) bypass the equality check — comparing a ClauseElement
    # with `!=` returns a SQL BinaryExpression (not a bool), so the isinstance
    # guard exists to keep the no-op detector from crashing on dynamic SQL values.
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(task, field) != value:
            setattr(task, field, value)

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    task.updated_at = func.now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Translate well-known CHECK names to stable details; fall through for
        # unknown constraints so the failure is still surfaced (without leaking
        # raw PG text into the wire response).
        orig_text = str(exc.orig)
        # Strings pinned by test_patch_task_400_detail_strings_are_pinned_in_router_source — keep the test in sync.
        if "ck_tasks_process_status_valid" in orig_text:
            detail = "process_status violates ck_tasks_process_status_valid"
        elif "ck_tasks_priority_valid" in orig_text:
            detail = "priority violates ck_tasks_priority_valid"
        elif "ck_tasks_status_valid" in orig_text:
            detail = "status violates ck_tasks_status_valid"
        else:
            detail = "Task update violates a database constraint"
        raise HTTPException(status_code=400, detail=detail) from exc
    await session.refresh(task)
    return task


@router.delete("/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a task: flip status=0. Returns 204 No Content. Idempotent —
    deleting an already-deleted task is a no-op (still 204).
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    # Idempotent: skip the no-op UPDATE so we don't write a redundant audit row.
    if task.status == RecordStatus.DELETED:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    task.status = RecordStatus.DELETED
    await session.commit()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
