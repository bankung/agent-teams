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
from src.services.run_mode import assert_consent_for_run_mode
from src.services.task_kind import assert_run_mode_for_kind
from src.services.session_project import (
    assert_body_matches_session,
    assert_task_belongs_to_session,
    require_project_id_header,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Source-text-locked detail string (#122 pattern). Wire contract — drift
# breaks any FE that string-matches it. Used by both:
#  - the IntegrityError fallback for `ck_tasks_scheduled_xor_template` (POST + PATCH)
#  - the resolved-final XOR application-layer guard on PATCH
# Pinned by test_post_task_400_detail_strings_are_pinned_in_router_source +
# the new tests in test_tasks_scheduled_at.py.
_DETAIL_SCHEDULED_XOR_TEMPLATE = (
    "scheduled_at is incompatible with is_template=true "
    "(use recurrence_rule for templates)"
)

# Process-status transitions that auto-stamp a lifecycle timestamp (when not
# already set). Order doesn't matter — at most one entry fires per PATCH
# (process_status is a single value).
_STATUS_TIMESTAMP_FIELDS: dict[int, str] = {
    TaskStatus.IN_PROGRESS: "started_at",
    TaskStatus.DONE: "completed_at",
}


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    session_project_id: int = Depends(require_project_id_header),
    process_status: int | None = Query(
        default=None, description="Filter by tasks.process_status (1..5)"
    ),
    assigned_role: int | None = Query(
        default=None, description="Filter by tasks.assigned_role"
    ),
    parent_task_id: int | None = Query(
        default=None,
        ge=1,
        description="Filter to direct children of the given task id (Kanban #238).",
    ),
    top_level_only: bool = Query(
        default=False,
        description=(
            "If true, return only tasks with parent_task_id IS NULL (top-level "
            "umbrellas). Cleaner than coercing the literal string 'null' through "
            "Query type-narrowing. When both are provided, top_level_only takes "
            "precedence and parent_task_id is ignored."
        ),
    ),
    pending: bool = Query(
        default=False,
        description=(
            "If true, return only rows with process_status != 5 (i.e., todo + "
            "in_progress + review + blocked). Convenience shortcut for the "
            "Lead-bootstrap 'list pending tasks' query. When both `pending=true` "
            "and `process_status=N` are provided, `process_status` wins (more "
            "specific) and `pending` is silently ignored."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[Task]:
    # Kanban #695: project scoping comes from the X-Project-Id header (session-
    # bound). The legacy `?project_id=` query param was removed — header is the
    # canonical channel; missing/non-int → 400 via require_project_id_header.
    stmt = select(Task).where(Task.project_id == session_project_id)
    if not include_deleted:
        stmt = stmt.where(Task.status == RecordStatus.ACTIVE)
    if process_status is not None:
        stmt = stmt.where(Task.process_status == process_status)
    elif pending:
        # Kanban #697: convenience shortcut for the Lead-bootstrap "list pending
        # tasks" query. `elif` enforces precedence — explicit `process_status`
        # wins (more specific); `pending` is silently ignored on conflict.
        stmt = stmt.where(Task.process_status != TaskStatus.DONE)
    if assigned_role is not None:
        stmt = stmt.where(Task.assigned_role == assigned_role)
    if top_level_only:
        stmt = stmt.where(Task.parent_task_id.is_(None))
    elif parent_task_id is not None:
        stmt = stmt.where(Task.parent_task_id == parent_task_id)
    stmt = stmt.order_by(Task.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    # Kanban #695: cross-check the session-bound project against the row.
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    return task


@router.post("", response_model=TaskRead, status_code=http_status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    # Kanban #695: header is the canonical session-bound project. Body's
    # project_id is defense-in-depth — must match the header (header wins on
    # conflict; mismatch → 400 with locked detail). This fires BEFORE the
    # parent-task / consent / FK validations so a stale body is rejected
    # immediately.
    assert_body_matches_session(payload.project_id, session_project_id)

    # Subtask parent validation (Kanban #238). Same-project enforcement is
    # app-layer (no DB trigger). Stable detail strings are pinned by
    # test_post_task_400_detail_strings_are_pinned_in_router_source — keep in sync.
    if payload.parent_task_id is not None:
        parent = await session.get(Task, payload.parent_task_id)
        if parent is None or parent.status == RecordStatus.DELETED:
            raise HTTPException(
                status_code=400,
                detail=f"parent_task_id {payload.parent_task_id} does not exist or is deleted",
            )
        if parent.project_id != payload.project_id:
            raise HTTPException(
                status_code=400,
                detail=f"parent_task_id {payload.parent_task_id} belongs to a different project",
            )

    # V3+ T1 (Kanban #706) cross-table validator: task_kind='human' is
    # incompatible with run_mode != 'manual'. Pure function (no DB I/O) so
    # fires BEFORE the consent gate (cheaper check first; both are app-layer
    # cross-validators on the resolved final values). Detail string pinned by
    # source-text-lock test in test_task_kind_recurrence.py — keep in sync with
    # services/task_kind.py.
    assert_run_mode_for_kind(payload.task_kind, payload.run_mode)

    # Cross-table consent gate (Kanban #481/#483). Only fires when run_mode is
    # auto_headless; otherwise no-op. Detail string pinned by the source-text-lock
    # test in test_routes_smoke.py — keep in sync with services/run_mode.py.
    await assert_consent_for_run_mode(session, payload.project_id, payload.run_mode)

    task = Task(**payload.model_dump())
    session.add(task)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Translate well-known constraint names to stable details; mirror update_task M5.
        # Strings pinned by test_post_task_400_detail_strings_are_pinned_in_router_source — keep the test in sync.
        orig_text = str(exc.orig)
        if "tasks_project_id_fkey" in orig_text:
            detail = f"project_id {payload.project_id} does not exist"
        elif "ck_tasks_process_status_valid" in orig_text:
            detail = "process_status violates ck_tasks_process_status_valid"
        elif "ck_tasks_priority_valid" in orig_text:
            detail = "priority violates ck_tasks_priority_valid"
        elif "ck_tasks_status_valid" in orig_text:
            detail = "status violates ck_tasks_status_valid"
        elif "ck_tasks_task_kind_valid" in orig_text:
            detail = "task_kind violates ck_tasks_task_kind_valid"
        elif "ck_tasks_template_recurrence_complete" in orig_text:
            detail = (
                "template fields incomplete violates "
                "ck_tasks_template_recurrence_complete"
            )
        elif "ck_tasks_scheduled_xor_template" in orig_text:
            detail = _DETAIL_SCHEDULED_XOR_TEMPLATE
        else:
            detail = "Task creation violates a database constraint"
        raise HTTPException(status_code=400, detail=detail) from exc
    await session.refresh(task)
    return task


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    # Kanban #695: cross-check the session-bound project against the row.
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    updates = payload.model_dump(exclude_unset=True)

    # Cross-table consent gate (Kanban #481/#483). Resolve run_mode = the
    # value AFTER this PATCH would land — payload value if present, else the
    # existing row's run_mode. V1 forbids re-parenting so project_id is always
    # the existing row's. Only fires when the resolved value is auto_headless;
    # downgrading auto_headless → manual is always allowed.
    resolved_run_mode = updates.get("run_mode") if "run_mode" in updates else task.run_mode

    # V3+ T1 (Kanban #706) cross-table validator on RESOLVED final values:
    # task_kind='human' is incompatible with run_mode != 'manual'. Resolve
    # task_kind the same way as run_mode. Fires BEFORE the consent check
    # (cheaper — pure function, no DB I/O). Detail string source-text-locked
    # in services/task_kind.py.
    resolved_task_kind = (
        updates.get("task_kind") if "task_kind" in updates else task.task_kind
    )
    assert_run_mode_for_kind(resolved_task_kind, resolved_run_mode)

    # Kanban #723 resolved-final XOR: scheduled_at and is_template are mutually
    # exclusive. The Pydantic validator catches the both-fields-in-payload case;
    # this app-layer check catches the cross-state case (PATCH one field on a
    # row where the other is already set). Returns 422 with the same locked
    # detail before the DB CHECK trips the IntegrityError 400 fallback.
    resolved_is_template = (
        updates["is_template"] if "is_template" in updates else task.is_template
    )
    resolved_scheduled_at = (
        updates["scheduled_at"]
        if "scheduled_at" in updates
        else task.scheduled_at
    )
    if resolved_is_template is True and resolved_scheduled_at is not None:
        raise HTTPException(
            status_code=422,
            detail=_DETAIL_SCHEDULED_XOR_TEMPLATE,
        )

    await assert_consent_for_run_mode(session, task.project_id, resolved_run_mode)

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
    # N7 parity with projects.py — Kanban #120.
    changed = False
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(task, field) != value:
            setattr(task, field, value)
            changed = True

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    if changed:
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
        elif "ck_tasks_task_kind_valid" in orig_text:
            detail = "task_kind violates ck_tasks_task_kind_valid"
        elif "ck_tasks_template_recurrence_complete" in orig_text:
            detail = (
                "template fields incomplete violates "
                "ck_tasks_template_recurrence_complete"
            )
        elif "ck_tasks_scheduled_xor_template" in orig_text:
            detail = _DETAIL_SCHEDULED_XOR_TEMPLATE
        else:
            detail = "Task update violates a database constraint"
        raise HTTPException(status_code=400, detail=detail) from exc
    await session.refresh(task)
    return task


@router.delete("/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a task: flip status=0. Returns 204 No Content. Idempotent —
    deleting an already-deleted task is a no-op (still 204).
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    # Kanban #695: cross-check the session-bound project against the row.
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    # Idempotent: skip the no-op UPDATE so we don't write a redundant audit row.
    if task.status == RecordStatus.DELETED:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    # Block soft-delete when active children reference this task (Kanban #238).
    # Detail string pinned by test_delete_task_409_detail_strings_are_pinned_in_router_source.
    active_children_count = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.parent_task_id == task_id, Task.status == RecordStatus.ACTIVE)
    )
    if active_children_count and active_children_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete task — {active_children_count} active subtask(s) reference this task",
        )

    task.status = RecordStatus.DELETED
    # Force `updated_at` to refresh — server_default only fires on INSERT. Kanban #120.
    task.updated_at = func.now()
    await session.commit()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
