"""HTTP routes for cross-project dashboard surfaces (Kanban #945).

Operator-level endpoints — NO X-Project-Id header required (mirrors the
/api/pnl pattern from Kanban #1329). The endpoints scan every active
(`status=1`) project in scope; binding to a specific project header would
contradict the cross-project intent.

Endpoints:
  - GET /api/dashboard/active-tasks
      Flat list of tasks with `process_status IN (2, 3, 4)` across all
      active projects. Project fields denormalized into each row.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.db import get_session
from src.models.project import Project
from src.models.task import Task
from src.schemas.dashboard import DashboardActiveTaskRow, DashboardActiveTasks

logger = logging.getLogger(__name__)

# Operator-level (cross-project) router — no X-Project-Id requirement.
# Mounted at /api/dashboard in main.py.
router = APIRouter(prefix="/dashboard", tags=["operator-dashboard"])


# In-progress / review / blocked — the "actively going on" subset that the
# dashboard list surfaces. TODO (1) is excluded because it bloats the list
# (most projects carry a long TODO tail); DONE (5) and CANCELLED (6) are
# terminal and belong on the per-project history view instead.
_ACTIVE_TASK_STATUSES: tuple[int, ...] = (
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.BLOCKED,
)


@router.get("/active-tasks", response_model=DashboardActiveTasks)
async def list_active_tasks_cross_project(
    session: AsyncSession = Depends(get_session),
) -> DashboardActiveTasks:
    """Cross-project list of tasks in {IN_PROGRESS, REVIEW, BLOCKED}.

    Operator-level: NO X-Project-Id header required (the endpoint spans
    projects by design). Soft-deleted projects (`status=0`) excluded.
    Soft-deleted tasks (`status=0`) excluded.

    Project info (`project_name`, `team`) denormalized into each row so
    the FE doesn't issue an N-query lookup. Default sort:
    (project_name ASC, updated_at DESC) — same projects cluster together;
    freshest tasks float to the top within each project.

    Single SQL join (Task INNER JOIN Project) — no N+1.
    """
    stmt = (
        select(
            Task.id,
            Task.title,
            Task.project_id,
            Task.process_status,
            Task.run_mode,
            Task.task_kind,
            Task.assigned_role,
            Task.priority,
            Task.updated_at,
            Task.blocked_by,
            Project.name.label("project_name"),
            Project.team.label("team"),
        )
        .join(Project, Project.id == Task.project_id)
        .where(
            Project.status == RecordStatus.ACTIVE,
            Task.status == RecordStatus.ACTIVE,
            Task.process_status.in_(_ACTIVE_TASK_STATUSES),
        )
        .order_by(
            Project.name.asc(),
            Task.updated_at.desc(),
        )
    )
    result = (await session.execute(stmt)).all()

    rows = [
        DashboardActiveTaskRow(
            task_id=r.id,
            title=r.title,
            project_id=r.project_id,
            project_name=r.project_name,
            team=r.team,
            process_status=r.process_status,
            run_mode=r.run_mode,
            task_kind=r.task_kind,
            assigned_role=r.assigned_role,
            priority=r.priority,
            updated_at=r.updated_at,
            blocked_by=r.blocked_by,
        )
        for r in result
    ]
    return DashboardActiveTasks(rows=rows, total_count=len(rows))
