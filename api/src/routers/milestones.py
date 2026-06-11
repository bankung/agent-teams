"""Per-project Milestones CRUD + rollup router (Kanban #1868, Phase 1).

Mounted at `/api/milestones`. A milestone groups tasks for release planning;
it belongs to one project and is X-Project-Id scoped (the session-bound project
header is canonical — mirrors the tasks router).

Column naming (#1868): `milestone_status` is the LIFECYCLE field (planned /
active / released / cancelled); `status` (0/1) is the uniform soft-delete flag
(RecordStatus), never exposed on the wire. Parity with tasks' process_status
(lifecycle) vs status (soft-delete).

Soft-delete: list endpoint default-filters `WHERE status=1`; opt-in
`?include_deleted=true` returns soft-deleted rows. DELETE /api/milestones/{id}
flips `status=0` AND sets every child task's `milestone_id=NULL` in the SAME
transaction. Detail endpoint returns the row regardless of soft-delete status
(per standards/postgresql/soft-delete.md).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import RecordStatus, TaskStatus
from src.db import get_or_404, get_session
from src.models.milestone import Milestone
from src.models.task import Task
from src.schemas.milestone import (
    MilestoneCreate,
    MilestoneDetail,
    MilestoneRead,
    MilestoneRollup,
    MilestoneStatusLiteral,
    MilestoneUpdate,
)
from src.services.session_project import (
    assert_body_matches_session,
    require_project_id_header,
)

router = APIRouter(prefix="/milestones", tags=["milestones"])

logger = logging.getLogger(__name__)


async def _get_milestone_in_session_or_404(
    session: AsyncSession, milestone_id: int, session_project_id: int
) -> Milestone:
    """Fetch a milestone by id, 404 if missing OR if it belongs to a different
    project than the session-bound header.

    The cross-project case 404s (not 403) — the milestone is "invisible" from
    a session bound to another project (parity with how tasks 404 a row from
    a foreign project at the detail endpoint via assert_task_belongs_to_session,
    here folded into a single 404 since milestones are always project-scoped).
    """
    milestone = await get_or_404(
        session,
        Milestone,
        detail=f"Milestone id={milestone_id} not found",
        id=milestone_id,
    )
    if milestone.project_id != session_project_id:
        raise HTTPException(
            status_code=404,
            detail=f"Milestone id={milestone_id} not found",
        )
    return milestone


async def _compute_rollup(
    session: AsyncSession, milestone_id: int, project_id: int
) -> MilestoneRollup:
    """Aggregate active (status=1) tasks pointing at `milestone_id`.

    ONE GROUP BY query over (process_status) → buckets. progress_pct uses
    done / (total excluding cancelled) with a div-by-zero guard.

    `project_id` is defense-in-depth: the WHERE clause pins the rollup to
    tasks that also belong to the same project, so isolation holds regardless
    of call-site validation ordering.
    """
    stmt = (
        select(Task.process_status, func.count().label("n"))
        .where(
            Task.milestone_id == milestone_id,
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.is_active.is_(True),
        )
        .group_by(Task.process_status)
    )
    rows = (await session.execute(stmt)).all()

    by_process_status = {str(code): 0 for code in TaskStatus.ALL}
    for process_status, n in rows:
        # process_status is constrained by DB CHECK to TaskStatus.ALL; be
        # defensive against an out-of-set value (route to nothing rather than
        # KeyError the whole endpoint).
        key = str(process_status)
        if key in by_process_status:
            by_process_status[key] += int(n)

    total = sum(by_process_status.values())
    done = by_process_status[str(TaskStatus.DONE)]
    cancelled = by_process_status[str(TaskStatus.CANCELLED)]
    denominator = total - cancelled
    progress_pct = round(done / denominator * 100, 1) if denominator > 0 else 0.0

    return MilestoneRollup(
        total=total,
        by_process_status=by_process_status,
        done=done,
        progress_pct=progress_pct,
    )


@router.get("", response_model=list[MilestoneRead])
async def list_milestones(
    session_project_id: int = Depends(require_project_id_header),
    milestone_status: MilestoneStatusLiteral | None = Query(
        default=None,
        alias="status",
        description=(
            "Filter by milestone_status (planned/active/released/cancelled). "
            "Aliased as `status` on the query string per spec; the value maps "
            "to the lifecycle column milestone_status, NOT the soft-delete flag."
        ),
    ),
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Milestone]:
    """List milestones for the session-bound project.

    Soft-delete-aware: default returns only `status=1` (active) rows. The
    `?status=` query param filters the LIFECYCLE column (milestone_status).
    Ordering: `sort_order ASC NULLS LAST, id ASC` (mirror of the tasks lane-sort
    rule; NULL sort_order falls back to id order).
    """
    stmt = select(Milestone).where(Milestone.project_id == session_project_id)
    if not include_deleted:
        stmt = stmt.where(Milestone.status == RecordStatus.ACTIVE)
    if milestone_status is not None:
        stmt = stmt.where(Milestone.milestone_status == milestone_status)
    stmt = (
        stmt.order_by(Milestone.sort_order.asc().nulls_last(), Milestone.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{milestone_id}", response_model=MilestoneDetail)
async def get_milestone(
    milestone_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> MilestoneDetail:
    """Detail endpoint WITH the task rollup.

    Returns the row regardless of soft-delete status (caller already has the id),
    but 404s when the milestone belongs to a different project than the session.
    """
    milestone = await _get_milestone_in_session_or_404(
        session, milestone_id, session_project_id
    )
    rollup = await _compute_rollup(session, milestone_id, milestone.project_id)
    return MilestoneDetail(
        **MilestoneRead.model_validate(milestone).model_dump(),
        rollup=rollup,
    )


@router.post("", response_model=MilestoneRead, status_code=http_status.HTTP_201_CREATED)
async def create_milestone(
    payload: MilestoneCreate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Milestone:
    """Create a milestone scoped to the session-bound project.

    The X-Project-Id header is canonical; the body `project_id` is
    defense-in-depth (must match — 400 on mismatch, mirrors create_task).

    Errors:
    - 400 — body project_id != header, OR FK violation (project does not exist).
    - 422 — Pydantic validation (start_date>target_date, bad milestone_status).
    """
    assert_body_matches_session(payload.project_id, session_project_id)

    milestone = Milestone(
        project_id=payload.project_id,
        title=payload.title,
        description=payload.description,
        milestone_status=payload.milestone_status,
        start_date=payload.start_date,
        target_date=payload.target_date,
        sort_order=payload.sort_order,
    )
    session.add(milestone)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "milestones_project_id_fkey" in orig_text:
            raise HTTPException(
                status_code=400,
                detail=f"project_id {payload.project_id} does not exist",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="Milestone creation violates a database constraint",
        ) from exc

    await session.refresh(milestone)
    return milestone


@router.patch("/{milestone_id}", response_model=MilestoneRead)
async def update_milestone(
    milestone_id: int,
    payload: MilestoneUpdate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Milestone:
    """Partial update.

    Re-scoping a milestone between projects is NOT supported (project_id absent
    from the surface). Soft-delete is via DELETE, not PATCH.

    Resolved-final date check: if exactly one of start_date / target_date is in
    the PATCH body, validate it against the existing row's other date (the
    schema validator only sees the payload, so the cross-row case is checked
    here — 422 on violation).

    Errors:
    - 404 — milestone not found / belongs to a different project.
    - 422 — start_date > target_date (resolved-final).
    """
    milestone = await _get_milestone_in_session_or_404(
        session, milestone_id, session_project_id
    )

    updates = payload.model_dump(exclude_unset=True)

    # Resolved-final cross-field date check. Resolve each date = the PATCH value
    # if present, else the existing row's value. 422 if both resolve non-null
    # and start > target. (The schema validator already caught the
    # both-in-payload case; this catches the one-field PATCH against the stored
    # row.) Detail string is verbatim with the schema validator (one wire
    # contract for both create + patch).
    if "start_date" in updates or "target_date" in updates:
        resolved_start = (
            updates["start_date"] if "start_date" in updates else milestone.start_date
        )
        resolved_target = (
            updates["target_date"]
            if "target_date" in updates
            else milestone.target_date
        )
        if (
            resolved_start is not None
            and resolved_target is not None
            and resolved_start > resolved_target
        ):
            raise HTTPException(
                status_code=422,
                detail="start_date must be on or before target_date",
            )

    # No-op skip + updated_at bump pattern (mirrors tasks / projects / handoff
    # templates PATCH). ClauseElement guard keeps the equality check from
    # crashing on dynamic SQL values.
    changed = False
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(milestone, field) != value:
            setattr(milestone, field, value)
            changed = True
    if changed:
        milestone.updated_at = func.now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Milestone update violates a database constraint",
        ) from exc

    await session.refresh(milestone)
    return milestone


@router.delete("/{milestone_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_milestone(
    milestone_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a milestone: flip `status=0` AND detach every child task
    (set `tasks.milestone_id = NULL`) in the SAME transaction.

    Idempotent: deleting an already-deleted milestone is a no-op (still 204),
    and the child-detach UPDATE is skipped on the already-deleted path.
    Returns 204 No Content.
    """
    milestone = await _get_milestone_in_session_or_404(
        session, milestone_id, session_project_id
    )

    if milestone.status == RecordStatus.ACTIVE:
        # Detach children first (same transaction). Bulk UPDATE over the FK
        # column — covers both active + soft-deleted child tasks so no row
        # keeps a dangling pointer to a soft-deleted milestone.
        await session.execute(
            update(Task)
            .where(Task.milestone_id == milestone_id)
            .values(milestone_id=None)
        )
        milestone.status = RecordStatus.DELETED
        milestone.updated_at = func.now()
        await session.commit()

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
