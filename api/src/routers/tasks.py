"""HTTP routes for Kanban tasks.

Mounted at `/api/tasks`. Status transitions stamp `started_at` / `completed_at`
on the way to in_progress / done — clients shouldn't set those directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.constants import TaskStatus
from src.db import get_or_404, get_session
from src.models.task import Task
from src.schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Status transitions that auto-stamp a lifecycle timestamp (when not already set).
# Order doesn't matter — at most one entry fires per PATCH (status is a single value).
_STATUS_TIMESTAMP_FIELDS: dict[int, str] = {
    TaskStatus.IN_PROGRESS: "started_at",
    TaskStatus.DONE: "completed_at",
}


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    project_id: int = Query(..., description="Required — scope tasks to one project"),
    status: int | None = Query(default=None, description="Filter by tasks.status"),
    assigned_role: int | None = Query(default=None, description="Filter by tasks.assigned_role"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Task]:
    stmt = select(Task).where(Task.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
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

    # Status-transition side effects — only stamp if not already set / explicitly
    # provided. We use the DB now() so the value matches the audit-trigger snapshot.
    new_status = updates.get("status")
    if new_status is not None and new_status != task.status:
        field = _STATUS_TIMESTAMP_FIELDS.get(new_status)
        if field is not None and getattr(task, field) is None:
            updates.setdefault(field, func.now())

    for field, value in updates.items():
        setattr(task, field, value)

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    task.updated_at = func.now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig)) from exc
    await session.refresh(task)
    return task
