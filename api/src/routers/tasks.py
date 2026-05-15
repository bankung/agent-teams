"""HTTP routes for Kanban tasks.

Mounted at `/api/tasks`. Process-status transitions stamp `started_at` /
`completed_at` on the way to in_progress / done — clients shouldn't set those directly.

Soft-delete: list endpoint default-filters `WHERE status=1`; opt-in `?include_deleted=true`
returns soft-deleted rows. DELETE /api/tasks/{id} flips `status=0`. Detail endpoint
returns the row regardless of soft-delete status (per standards/postgresql/soft-delete.md).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import RecordStatus, TaskInteractionKind, TaskRunMode, TaskStatus
from src.db import get_or_404, get_session
from src.models.session import SessionRun
from src.models.task import Task
from src.schemas.ai_task import ParseRequest, ParseResponse
from src.schemas.task import NextAutorunResponse, TaskCreate, TaskRead, TaskReorder, TaskUpdate
from src.services.ai_task_parser import (
    AiCallFailed,
    AiCallTimeout,
    AiUnparseable,
    MissingApiKey as AiMissingApiKey,
    parse_task_text,
)
from src.services.is_pending import assert_is_pending_with_process_status
from src.services.recurrence import fire_template, next_cron_fire
from src.services.budget_enforcer import check_budget
from src.services.run_mode import assert_consent_for_run_mode
from src.services.task_cost_estimator import estimate_task_cost
from src.services.task_interaction import (
    append_answer,
    auto_unblock_dependents,
    invalidate_last_answer as _invalidate_last_answer,
)
from src.services.task_kind import (
    assert_run_mode_for_kind,
    coerce_task_kind_for_interaction,
)
from src.services.session_project import (
    assert_body_matches_session,
    assert_task_belongs_to_session,
    require_project_id_header,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])

logger = logging.getLogger(__name__)

# Source-text-locked (#122). Pinned by test_post_task_400_detail_strings + test_tasks_scheduled_at
_DETAIL_SCHEDULED_XOR_TEMPLATE = (
    "scheduled_at is incompatible with is_template=true "
    "(use recurrence_rule for templates)"
)

# Source-text-locked (#122). Pinned by test_fire_now_detail_string_pinned_in_router_source
_DETAIL_FIRE_NOW_NOT_TEMPLATE_TEMPLATE = (
    "Task id={task_id} is not a template; fire-now only applies to is_template=true"
)

# #771 cross-row rejections → 422; parent_task_id legacy → 400 (do not migrate)

# Kanban #771: maximum depth for the PATCH-time blocked_by cycle walk. Pins a
# defensive upper bound — real chains are expected to be 1-3 deep. Hitting 10
# without resolving raises 422 (defensive; should not occur in practice).
_BLOCKED_BY_MAX_CHAIN_DEPTH = 10

# Kanban #772: maximum chain depth for the blocker-order constraint walk used
# by both POST /api/tasks/{id}/reorder and PATCH /api/tasks/{id} (when
# sort_order or blocked_by is in the body). Reused as a sibling of the cycle
# walk's budget — real blocker chains stay 1-3 deep. Hitting depth 10
# without resolving raises 422 defensively.
_REORDER_BLOCKER_CHAIN_DEPTH = 10

# Kanban #819: minimum gap between float sort_orders before re-densification
# is triggered. Float-64 midpoint arithmetic exhausts after ~52 same-interval
# halvings; when (a+b)/2 lands within this threshold of either anchor we
# re-densify the lane with integer floors (1.0, 2.0, …) and recompute.
_SORT_ORDER_MIN_GAP = 1e-9


def _opt_int_str(v: int | None) -> str:
    """None → 'null' (JSON), int → str. For wire-contract detail strings."""
    return "null" if v is None else str(v)

# Auto-stamp started_at / completed_at on ps=2 / ps=5 transitions
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
    include_cancelled: bool = Query(
        default=False,
        description=(
            "If true, include CANCELLED (process_status=6) rows. By default "
            "cancelled rows are excluded from the list (parity with the "
            "soft-delete default-filter pattern — cancelled work is dead-end "
            "and not relevant to most board / Lead-bootstrap queries). Kanban "
            "#854. Silently ignored when an explicit `process_status=N` is "
            "provided (explicit filter wins)."
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
        # Note: `pending=true` returns ps != 5; CANCELLED (ps=6) is also a
        # "non-done" code, but cancelled work is dead-end and excluded below
        # via the `include_cancelled` gate unless explicitly opted in.
        stmt = stmt.where(Task.process_status != TaskStatus.DONE)
    # Kanban #854: cancelled rows (process_status=6) are excluded by default
    # — parity with soft-delete semantics for dead-end work. Skipped when an
    # explicit `process_status=N` filter is provided (the explicit filter is
    # more specific and wins, same precedence pattern as `pending`).
    if process_status is None and not include_cancelled:
        stmt = stmt.where(Task.process_status != TaskStatus.CANCELLED)
    if assigned_role is not None:
        stmt = stmt.where(Task.assigned_role == assigned_role)
    if top_level_only:
        stmt = stmt.where(Task.parent_task_id.is_(None))
    elif parent_task_id is not None:
        stmt = stmt.where(Task.parent_task_id == parent_task_id)
    stmt = stmt.order_by(Task.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/next-autorun", response_model=NextAutorunResponse)
async def get_next_autorun(
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> NextAutorunResponse:
    """Kanban #833: read-only snapshot for the headless auto-run loop.

    Returns four fields in a single round-trip so the loop can decide
    whether to pick up work, resume a halted task, or surface a pending
    question — without issuing four separate queries.

    All four queries share the session-bound project_id from the header.
    No side effects; purely SELECT.
    """
    project_id = session_project_id

    # Alias for the blocker row so we can outerjoin Task → blocker Task.
    blocker = aliased(Task)

    # --- next_task -----------------------------------------------------------
    # Highest-priority runnable TODO task: auto_pickup or auto_headless,
    # not halted, not blocked by an in-progress/todo blocker.
    next_task_stmt = (
        select(Task)
        .outerjoin(blocker, Task.blocked_by == blocker.id)
        .where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.process_status == TaskStatus.TODO,
            Task.run_mode.in_([TaskRunMode.AUTO_PICKUP, TaskRunMode.AUTO_HEADLESS]),
            Task.halt_reason.is_(None),
            or_(Task.blocked_by.is_(None), blocker.process_status == TaskStatus.DONE),
        )
        .order_by(
            Task.priority.desc(),
            Task.sort_order.asc().nulls_last(),
            Task.created_at.asc(),
        )
        .limit(1)
    )
    next_task_row = (await session.execute(next_task_stmt)).scalars().first()

    # --- budget enforcement gate (Kanban #951) -------------------------------
    # Manual-mode tasks are already excluded by the run_mode filter above —
    # the bypass requirement ("run_mode=manual tasks bypass enforcement")
    # is satisfied implicitly here: only AUTO_PICKUP / AUTO_HEADLESS rows
    # ever reach this gate.
    #
    # When the project is over its hard-halt cap, we:
    #   1. Stamp halt_reason='budget_exceeded:<period>' on the candidate row
    #      so the operator sees the gate on the board.
    #   2. Drop the candidate from next_task (return None).
    #
    # When over the soft-warn band (80-100%), we log a structured WARNING
    # line and proceed with the pickup — soft warns are informational. The
    # FE banner reads `check_budget` results via a future endpoint.
    if next_task_row is not None:
        verdict = await check_budget(session, project_id)
        if verdict.hard_halt:
            halt_msg = f"budget_exceeded:{verdict.exceeded_cap}"
            next_task_row.halt_reason = halt_msg
            await session.commit()
            logger.warning(
                "budget_hard_halt: project=%d task=%d cap=%s "
                "daily_pct=%s monthly_pct=%s total_pct=%s",
                project_id,
                next_task_row.id,
                verdict.exceeded_cap,
                verdict.daily_pct,
                verdict.monthly_pct,
                verdict.total_pct,
            )
            next_task_row = None
        elif verdict.soft_warn:
            logger.warning(
                "budget_soft_warn: project=%d task=%d "
                "daily_pct=%s monthly_pct=%s total_pct=%s",
                project_id,
                next_task_row.id,
                verdict.daily_pct,
                verdict.monthly_pct,
                verdict.total_pct,
            )

    # --- resume_tasks --------------------------------------------------------
    # HALTED tasks (halt_reason IS NOT NULL) whose blocker question/decision is DONE.
    # Tasks halted without a blocker (old-style "Option A/B" halts) are excluded —
    # they have no resolved answer and require manual unhalt by the user.
    resume_stmt = (
        select(Task)
        .join(blocker, Task.blocked_by == blocker.id)
        .where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.halt_reason.is_not(None),
            Task.blocked_by.is_not(None),
            blocker.process_status == TaskStatus.DONE,
        )
        .order_by(Task.priority.desc(), Task.created_at.asc())
    )
    resume_rows = list((await session.execute(resume_stmt)).scalars().all())

    # --- pending_questions ---------------------------------------------------
    # Active question/decision tasks not yet DONE — awaiting user input.
    questions_stmt = (
        select(Task)
        .where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.interaction_kind.in_([
                TaskInteractionKind.QUESTION,
                TaskInteractionKind.DECISION,
            ]),
            Task.process_status != TaskStatus.DONE,
        )
        .order_by(Task.created_at.asc())
    )
    question_rows = list((await session.execute(questions_stmt)).scalars().all())

    # --- blocked_count -------------------------------------------------------
    # Count of active TODO/IN_PROGRESS tasks that have a non-DONE blocker.
    blocked_stmt = (
        select(func.count())
        .select_from(Task)
        .outerjoin(blocker, Task.blocked_by == blocker.id)
        .where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.process_status.in_([TaskStatus.TODO, TaskStatus.IN_PROGRESS]),
            Task.blocked_by.is_not(None),
            blocker.process_status != TaskStatus.DONE,
        )
    )
    blocked_count = (await session.execute(blocked_stmt)).scalar_one()

    return NextAutorunResponse(
        next_task=next_task_row,
        resume_tasks=resume_rows,
        pending_questions=question_rows,
        blocked_count=blocked_count,
    )


@router.post("/ai-parse", response_model=ParseResponse)
async def ai_parse_task(
    payload: ParseRequest,
    session_project_id: int = Depends(require_project_id_header),
) -> ParseResponse:
    """Parse free-text into a proposed TaskCreate body (Kanban #856).

    Read-only: does NOT create a row. The FE (Kanban #857) renders the
    proposal in an editable pre-fill form; user confirms via the existing
    POST /api/tasks.

    Provider chosen by LANGGRAPH_LLM_PROVIDER env var (shared with the
    langgraph service so ops sets it once). API scope is anthropic +
    openai; ollama is rejected here (langgraph-only in this release).

    Error contract:
    - 422 — Pydantic validation (empty / oversized `text`, unknown keys)
            OR LLM returned a structurally invalid proposal.
    - 502 — provider call failed (network / 5xx / malformed response).
    - 503 — provider not configured (api key env var unset).
    - 504 — provider exceeded the 10s wall budget.
    """
    try:
        proposed = await parse_task_text(
            text=payload.text, project_id=session_project_id
        )
    except AiMissingApiKey as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AiCallTimeout as exc:
        raise HTTPException(
            status_code=504, detail="AI provider timeout"
        ) from exc
    except AiUnparseable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AiCallFailed as exc:
        raise HTTPException(
            status_code=502, detail=f"AI provider error: {exc}"
        ) from exc

    return ParseResponse(proposed=proposed)


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


@router.get("/{task_id}/blocks", response_model=list[TaskRead])
async def list_tasks_blocked_by(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> list[Task]:
    """Reverse-lookup for Kanban #771: list active tasks that point AT this
    task via `blocked_by` (i.e., the dependents this task is currently
    blocking). 404 if `task_id` itself does not exist — mirrors the detail
    endpoint's "row must exist for sub-resource queries" convention. Returns
    `[]` when no dependents reference it. Soft-deleted dependents are
    excluded (status=1 filter). Same-project is implicit by FK semantics."""
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    stmt = (
        select(Task)
        .where(Task.blocked_by == task_id, Task.status == RecordStatus.ACTIVE)
        .order_by(Task.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _enforce_blocker_order_constraint(
    session: AsyncSession,
    target_id: int,
    target_blocked_by: int | None,
    target_process_status: int,
    target_sort_order: float | None,
) -> None:
    """#772 — walk blocker chain (depth ≤ _REORDER_BLOCKER_CHAIN_DEPTH); enforce
    target.sort_order >= B.sort_order for same-lane (TODO) blockers with non-null
    sort_orders. Violation → 422 with (target, B) pair. Detail strings pinned by
    test_reorder_detail_strings_pinned_in_router_source.
    """
    # No blocker chain → nothing to enforce.
    if target_blocked_by is None or target_sort_order is None:
        return
    # Out-of-lane target → blocker-order rule does not apply.
    if target_process_status != TaskStatus.TODO:
        return

    cursor: int | None = target_blocked_by
    # Range is N+2 (not N+1) so a chain of EXACTLY N blockers terminates via
    # the `cursor is None: break` path on iteration N+1 instead of falsely
    # tripping the for-else. The constant N is the budget for "blockers
    # walked"; the +1 sentinel iteration exists solely to break cleanly
    # when the chain ends at the budget edge (WARN-3 fix).
    for depth in range(1, _REORDER_BLOCKER_CHAIN_DEPTH + 2):
        if cursor is None:
            break
        blocker = await session.get(Task, cursor)
        if blocker is None:
            break
        # Only check when the blocker shares the lane AND has a sort_order.
        if (
            blocker.process_status == TaskStatus.TODO
            and blocker.sort_order is not None
            and target_sort_order < blocker.sort_order
        ):
            raise HTTPException(
                status_code=422,
                detail=f"task #{target_id} cannot be ordered before its blocker #{blocker.id}",
            )
        cursor = blocker.blocked_by
    else:
        # Loop exited via exhausting `range` without break — chain strictly
        # longer than the budget (depth > N). Defensive guard. Mirrors the
        # cycle-walk pattern below (#771).
        raise HTTPException(
            status_code=422,
            detail=f"reorder blocker chain exceeds maximum depth of {_REORDER_BLOCKER_CHAIN_DEPTH}",
        )


async def _materialize_null_sort_orders_in_lane(
    session: AsyncSession,
    project_id: int,
    process_status: int,
    exclude_task_id: int | None = None,
) -> None:
    """#772 — first-reorder densifier. Fills NULL sort_orders in the lane with
    floor floats starting at (max non-null + 1.0). Existing non-null values are
    preserved. `exclude_task_id` skips a row about to be set by the caller.
    """
    stmt = (
        select(Task)
        .where(
            Task.project_id == project_id,
            Task.process_status == process_status,
            Task.status == RecordStatus.ACTIVE,
        )
        .order_by(Task.sort_order.asc().nulls_last(), Task.created_at.asc())
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    # Determine the starting floor: max existing non-null sort_order in lane.
    existing_max = max(
        (r.sort_order for r in rows if r.sort_order is not None),
        default=0.0,
    )
    next_value = existing_max + 1.0
    for row in rows:
        if exclude_task_id is not None and row.id == exclude_task_id:
            continue
        if row.sort_order is None:
            row.sort_order = next_value
            next_value += 1.0


async def _redensify_lane(
    session: AsyncSession,
    project_id: int,
    process_status: int,
) -> None:
    """#819 — overwrite all sort_orders: 1.0, 2.0, … preserving relative position.
    ORM identity map propagates; no session.refresh() needed.
    """
    stmt = (
        select(Task)
        .where(
            Task.project_id == project_id,
            Task.process_status == process_status,
            Task.status == RecordStatus.ACTIVE,
        )
        .order_by(Task.sort_order.asc().nulls_last(), Task.created_at.asc())
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    for i, row in enumerate(rows, start=1):
        row.sort_order = float(i)


@router.post(
    "/{task_id}/reorder",
    response_model=TaskRead,
    status_code=http_status.HTTP_200_OK,
)
async def reorder_task(
    task_id: int,
    payload: TaskReorder,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    """#772 — anchor-based within-lane reorder.

    Body: `{before_id?: int, after_id?: int}` (≥1 required). Both → averaged.
    Before only → averaged between before_id and the largest smaller sort_order
    in lane (or before_id - 1.0). After only → mirrored.

    Same-lane invariant: target + anchors share process_status (else 422).
    NULL anchor sort_order → densify lane first (floor floats, atomic).

    Detail strings pinned by test_reorder_detail_strings_pinned_in_router_source.
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    if task.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=404, detail=f"Task id={task_id} not found"
        )

    # Resolve anchors in TWO passes so all 422 branches fire before any
    # write happens (densification is the only mutation pre-commit; we
    # rollback on any failure below).
    #
    # Pass 1: existence + same-project + not-deleted. Pass 2 (after) is the
    # lane-mismatch check — done after both anchors are loaded so the 422
    # detail can include both anchors' process_status values without an
    # inline-await in an f-string.
    async def _resolve_anchor_pass1(anchor_id: int) -> Task:
        anchor = await session.get(Task, anchor_id)
        if anchor is None:
            raise HTTPException(
                status_code=422,
                detail=f"reorder anchor #{anchor_id} not found in project",
            )
        if anchor.project_id != task.project_id:
            raise HTTPException(
                status_code=422,
                detail=f"reorder anchor #{anchor_id} not found in project",
            )
        if anchor.status == RecordStatus.DELETED:
            raise HTTPException(
                status_code=422,
                detail=f"reorder anchor #{anchor_id} is deleted",
            )
        return anchor

    before_anchor: Task | None = None
    after_anchor: Task | None = None
    if payload.before_id is not None:
        before_anchor = await _resolve_anchor_pass1(payload.before_id)
    if payload.after_id is not None:
        after_anchor = await _resolve_anchor_pass1(payload.after_id)

    # Pass 2: same-lane invariant. The 422 detail surfaces BOTH anchors'
    # process_status values (or None for an anchor not supplied) so the
    # client can see exactly which side is off.
    def _lane_mismatch(anchor: Task) -> bool:
        return anchor.process_status != task.process_status

    if (before_anchor is not None and _lane_mismatch(before_anchor)) or (
        after_anchor is not None and _lane_mismatch(after_anchor)
    ):
        before_status = before_anchor.process_status if before_anchor else None
        after_status = after_anchor.process_status if after_anchor else None
        raise HTTPException(
            status_code=422,
            detail=(
                f"reorder requires moved task #{task_id} and anchor(s) to "
                f"share the same process_status; moved={task.process_status} "
                f"before_id_status={_opt_int_str(before_status)} "
                f"after_id_status={_opt_int_str(after_status)}"
            ),
        )

    # Materialize NULL sort_orders in the lane upfront so anchor.sort_order
    # is guaranteed non-null below. Exclude the moved task itself — we'll
    # set its sort_order explicitly. NO-OP on lanes already fully densified.
    # This runs AFTER all validation so a 422 doesn't leave a partial
    # densification mid-transaction.
    await _materialize_null_sort_orders_in_lane(
        session,
        project_id=task.project_id,
        process_status=task.process_status,
        exclude_task_id=task_id,
    )
    # NOTE: the materializer above mutates `Task.sort_order` on the SAME
    # ORM-managed instances in the session's identity map; before_anchor /
    # after_anchor reflect the new floor floats directly. Do NOT call
    # session.refresh() here — that would re-read from the DB and clobber
    # the pre-commit mutation.

    # Both anchors → average. before only → below before_id. after only → above after_id (#772)
    async def _compute_sort_order() -> float:
        if before_anchor is not None and after_anchor is not None:
            # both anchors. The smaller is after_anchor.sort_order; the larger
            # is before_anchor.sort_order. Average. (Server does NOT validate
            # they are currently adjacent — trust client.)
            assert before_anchor.sort_order is not None  # materialized above
            assert after_anchor.sort_order is not None
            return (after_anchor.sort_order + before_anchor.sort_order) / 2.0
        elif before_anchor is not None:
            # Place just above (smaller than) before_anchor.
            assert before_anchor.sort_order is not None
            # Find the largest sort_order strictly less than before_anchor's
            # in the same lane (excluding the moved task itself).
            smaller_stmt = (
                select(func.max(Task.sort_order))
                .where(
                    Task.project_id == task.project_id,
                    Task.process_status == task.process_status,
                    Task.status == RecordStatus.ACTIVE,
                    Task.sort_order < before_anchor.sort_order,
                    Task.id != task_id,
                )
            )
            largest_smaller = await session.scalar(smaller_stmt)
            if largest_smaller is None:
                return before_anchor.sort_order - 1.0
            else:
                return (largest_smaller + before_anchor.sort_order) / 2.0
        else:
            # after_anchor only — place just below (larger than) it.
            assert after_anchor is not None
            assert after_anchor.sort_order is not None
            larger_stmt = (
                select(func.min(Task.sort_order))
                .where(
                    Task.project_id == task.project_id,
                    Task.process_status == task.process_status,
                    Task.status == RecordStatus.ACTIVE,
                    Task.sort_order > after_anchor.sort_order,
                    Task.id != task_id,
                )
            )
            smallest_larger = await session.scalar(larger_stmt)
            if smallest_larger is None:
                return after_anchor.sort_order + 1.0
            else:
                return (after_anchor.sort_order + smallest_larger) / 2.0

    new_sort_order = await _compute_sort_order()

    # #819 — float gap collapse: re-densify + recompute atomically
    anchor_sort_orders = [
        a.sort_order
        for a in (before_anchor, after_anchor)
        if a is not None and a.sort_order is not None
    ]
    if any(abs(new_sort_order - v) < _SORT_ORDER_MIN_GAP for v in anchor_sort_orders):
        await _redensify_lane(session, task.project_id, task.process_status)
        new_sort_order = await _compute_sort_order()

    # Enforce the blocker-order constraint on the resolved final value
    # BEFORE writing. If the check fires, ORM session is rolled back so
    # the densification we did above doesn't leak.
    try:
        await _enforce_blocker_order_constraint(
            session,
            target_id=task_id,
            target_blocked_by=task.blocked_by,
            target_process_status=task.process_status,
            target_sort_order=new_sort_order,
        )
    except HTTPException:
        await session.rollback()
        raise

    task.sort_order = new_sort_order
    task.updated_at = func.now()
    await session.commit()
    await session.refresh(task)
    return task


@router.post("", response_model=TaskRead, status_code=http_status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    # #695 — header is canonical project; body project_id is defense-in-depth (must match)
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

    # Kanban #771: blocked_by validation. Same-project enforcement is app-layer
    # (no DB trigger). POST has no row id yet, so neither self-reference nor
    # transitive cycle is reachable; only existence + same-project checks fire
    # here. Stable detail strings pinned by
    # test_blocked_by_detail_strings_pinned_in_router_source — keep in sync.
    if payload.blocked_by is not None:
        blocker = await session.get(Task, payload.blocked_by)
        if blocker is None or blocker.status == RecordStatus.DELETED:
            raise HTTPException(
                status_code=422,
                detail=f"blocked_by {payload.blocked_by} does not exist or is deleted",
            )
        if blocker.project_id != payload.project_id:
            raise HTTPException(
                status_code=422,
                detail=f"blocked_by {payload.blocked_by} belongs to a different project",
            )

    # Kanban #858 (2026-05-13): when interaction_kind IN ('question','decision'),
    # force task_kind='human' AND run_mode='manual' regardless of caller input.
    # Silent server-side coerce (Option A) — atomic so the HUMAN↔MANUAL
    # invariant below doesn't fire on the same call. Reverse 'question'→'work'
    # PATCHes do NOT auto-revert task_kind (handled separately in update_task).
    coerced_task_kind, coerced_run_mode = coerce_task_kind_for_interaction(
        payload.interaction_kind, payload.task_kind, payload.run_mode
    )

    # V3+ T1 (Kanban #706) cross-table validator: task_kind='human' is
    # incompatible with run_mode != 'manual'. Pure function (no DB I/O) so
    # fires BEFORE the consent gate (cheaper check first; both are app-layer
    # cross-validators on the resolved final values). Detail string pinned by
    # source-text-lock test in test_task_kind_recurrence.py — keep in sync with
    # services/task_kind.py. Runs on the POST-coerce values so a caller-supplied
    # task_kind='ai' + interaction_kind='question' lands at ('human','manual')
    # without tripping the assertion.
    assert_run_mode_for_kind(coerced_task_kind, coerced_run_mode)

    # Kanban #750 cross-state validator: is_pending=true requires
    # process_status=2 (in_progress). Pure function (no DB I/O) — fires after
    # task_kind (also pure) and BEFORE the consent gate (DB I/O). Default-case
    # (is_pending=false) returns trivially. Detail string source-text-locked
    # in services/is_pending.py.
    assert_is_pending_with_process_status(payload.is_pending, payload.process_status)

    # Cross-table consent gate (Kanban #481/#483). Only fires when run_mode is
    # auto_headless; otherwise no-op. Detail string pinned by the source-text-lock
    # test in test_routes_smoke.py — keep in sync with services/run_mode.py.
    await assert_consent_for_run_mode(session, payload.project_id, payload.run_mode)

    # #801 — model_dump(mode='json') coerces datetime → str for JSONB writes; pattern reused for sibling fields below. See standards/sqlalchemy/orm.md.
    payload_dict = payload.model_dump()
    # #858 — persist post-coerce values (no-op when interaction_kind='work')
    payload_dict["task_kind"] = coerced_task_kind
    payload_dict["run_mode"] = coerced_run_mode
    if payload_dict.get("acceptance_criteria") is not None:
        payload_dict["acceptance_criteria"] = [
            c.model_dump(mode="json") for c in payload.acceptance_criteria
        ]
    # same #801 pattern
    payload_dict["subagent_models"] = [
        e.model_dump(mode="json") for e in payload.subagent_models
    ]
    # same #801 pattern
    if payload_dict.get("question_payload") is not None:
        payload_dict["question_payload"] = payload.question_payload.model_dump(mode="json")
    if payload_dict.get("resume_context") is not None:
        payload_dict["resume_context"] = payload.model_dump(mode="json")["resume_context"]

    task = Task(**payload_dict)
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
        elif "ck_tasks_task_type_valid" in orig_text:
            detail = "task_type violates ck_tasks_task_type_valid"
        elif "ck_tasks_interaction_kind_valid" in orig_text:
            detail = "interaction_kind violates ck_tasks_interaction_kind_valid"
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

    # same #801 pattern (explicit-null PATCH skips re-dumping)
    if (
        "acceptance_criteria" in updates
        and updates["acceptance_criteria"] is not None
        and payload.acceptance_criteria is not None
    ):
        updates["acceptance_criteria"] = [
            c.model_dump(mode="json") for c in payload.acceptance_criteria
        ]
    # same #801 pattern
    if "subagent_models" in updates:
        updates["subagent_models"] = [
            e.model_dump(mode="json") for e in payload.subagent_models
        ]
    # same #801 pattern
    if (
        "question_payload" in updates
        and updates["question_payload"] is not None
        and payload.question_payload is not None
    ):
        updates["question_payload"] = payload.question_payload.model_dump(mode="json")
    if (
        "resume_context" in updates
        and updates["resume_context"] is not None
    ):
        updates["resume_context"] = payload.model_dump(mode="json")["resume_context"]

    # Kanban #832: pop action-only fields before writing to ORM.
    # These are not DB columns — they trigger interaction logic below.
    new_answer = updates.pop("new_answer", None)
    new_answer_by = updates.pop("new_answer_by", None) or "user"
    do_invalidate = updates.pop("invalidate_last_answer", None)
    invalidated_reason = updates.pop("invalidated_reason", None)

    # Kanban #832: answer append for question/decision tasks.
    if new_answer is not None:
        resolved_interaction_kind = (
            updates.get("interaction_kind") if "interaction_kind" in updates
            else task.interaction_kind
        )
        if resolved_interaction_kind not in (
            TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
        ):
            raise HTTPException(
                status_code=422,
                detail="new_answer is only valid for interaction_kind 'question' or 'decision'",
            )
        updates["question_payload"] = append_answer(
            task.question_payload, new_answer, new_answer_by
        )

    # Kanban #832: invalidate last valid answer. Use updates["question_payload"]
    # if new_answer already updated it in this same PATCH; else fall back to DB value.
    if do_invalidate:
        _payload_for_invalidate = updates.get("question_payload") or task.question_payload
        try:
            updates["question_payload"] = _invalidate_last_answer(
                _payload_for_invalidate, invalidated_reason or ""
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    # Kanban #858: server-side coerce based on the resolved interaction_kind.
    # If the resolved value is 'question' or 'decision', force task_kind='human'
    # + run_mode='manual' (Option A — atomic; keeps the HUMAN↔MANUAL invariant
    # below from firing on the same call). Reverse 'question'/'decision' → 'work'
    # is NOT auto-reverted (spawn brief edge case #3) — task_kind stays at the
    # existing 'human' until the caller explicitly PATCHes it back to 'ai'.
    resolved_interaction_kind = (
        updates.get("interaction_kind") if "interaction_kind" in updates
        else task.interaction_kind
    )
    coerced_task_kind, coerced_run_mode = coerce_task_kind_for_interaction(
        resolved_interaction_kind, resolved_task_kind, resolved_run_mode
    )
    # Only write back into `updates` when the coerced value diverges from the
    # existing row's column — the no-op skip below already detects equality but
    # we keep `updates` clean so audit-row noise / explicit PATCH semantics stay
    # tight. Re-pin the resolved values for the assertion + consent gate below.
    if coerced_task_kind != task.task_kind:
        updates["task_kind"] = coerced_task_kind
    if coerced_run_mode != task.run_mode:
        updates["run_mode"] = coerced_run_mode
    resolved_task_kind = coerced_task_kind
    resolved_run_mode = coerced_run_mode

    assert_run_mode_for_kind(resolved_task_kind, resolved_run_mode)

    # Kanban #750 resolved-final cross-state: is_pending=true requires
    # process_status=2. Both fields resolve via PATCH-supplied if present,
    # else the existing row's value — asymmetric drift fails (PATCH only
    # is_pending=true on a ps=3 row → 400; PATCH only ps=3 on a ps=2 +
    # is_pending=true row → 400). Pure function — fires before consent
    # (DB I/O). Detail source-text-locked in services/is_pending.py.
    resolved_is_pending = (
        updates["is_pending"] if "is_pending" in updates else task.is_pending
    )
    resolved_process_status = (
        updates["process_status"]
        if "process_status" in updates
        else task.process_status
    )
    assert_is_pending_with_process_status(
        resolved_is_pending, resolved_process_status
    )

    # Kanban #771: blocked_by validation on PATCH. Differs from POST in two ways:
    #   1. Self-reference IS structurally possible (target row has an id), so
    #      reject blocked_by == task_id at 422.
    #   2. Cycle detection: walk the new blocker's chain up to depth=10. If we
    #      hit task_id anywhere in the chain → cycle → 422. Setting to None
    #      is always allowed (clears the blocker; no checks needed).
    # Soft-deleted blockers are rejected. Same-project enforcement mirrors POST.
    # Stable detail strings pinned by
    # test_blocked_by_detail_strings_pinned_in_router_source — keep in sync.
    if "blocked_by" in updates:
        new_blocked_by = updates["blocked_by"]
        if new_blocked_by is not None:
            if new_blocked_by == task_id:
                raise HTTPException(
                    status_code=422,
                    detail="blocked_by cannot reference self",
                )
            blocker = await session.get(Task, new_blocked_by)
            if blocker is None or blocker.status == RecordStatus.DELETED:
                raise HTTPException(
                    status_code=422,
                    detail=f"blocked_by {new_blocked_by} does not exist or is deleted",
                )
            if blocker.project_id != task.project_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"blocked_by {new_blocked_by} belongs to a different project",
                )
            # Cycle walk: starting from the new blocker, follow blocked_by
            # links. If we hit task_id → cycle (the target transitively
            # depends on itself). Exhaust within depth budget → OK. Exceed
            # budget → defensive 422 (should not occur in practice).
            # Range is N+2 (not N+1) so a chain of EXACTLY N blockers
            # terminates via the `cursor is None: break` path on iteration
            # N+1 instead of falsely tripping the for-else. The constant N
            # is the budget for "blockers walked"; the +1 sentinel
            # iteration exists solely to break cleanly when the chain ends
            # (or cycle closes) at the budget edge. Mirrors the
            # _enforce_blocker_order_constraint fix (#772 / Kanban #820).
            cursor: int | None = blocker.blocked_by
            for depth in range(1, _BLOCKED_BY_MAX_CHAIN_DEPTH + 2):
                if cursor is None:
                    break
                if cursor == task_id:
                    raise HTTPException(
                        status_code=422,
                        detail=f"blocked_by {new_blocked_by} would create a cycle (depth {depth})",
                    )
                next_row = await session.get(Task, cursor)
                if next_row is None:
                    break
                cursor = next_row.blocked_by
            else:
                # Loop exited via exhausting `range` without break — chain
                # longer than the budget. Defensive guard.
                raise HTTPException(
                    status_code=422,
                    detail=f"blocked_by chain exceeds maximum depth of {_BLOCKED_BY_MAX_CHAIN_DEPTH}",
                )

    # Kanban #772 resolved-final blocker-order constraint. Fires when EITHER
    # `sort_order` or `blocked_by` is in the PATCH body — the constraint
    # touches both columns and a change to either side can violate the rule
    # T.sort_order >= B.sort_order (where T.blocked_by transitively walks
    # to B, B in same lane as T, both ps=TODO, both sort_orders non-null).
    # This is a SEPARATE walk from the cycle walk above — two concerns,
    # two detail-string templates. Skipped silently when neither field is
    # in the body (no chance of violating).
    if "sort_order" in updates or "blocked_by" in updates:
        resolved_sort_order = (
            updates["sort_order"] if "sort_order" in updates else task.sort_order
        )
        resolved_blocked_by_for_order = (
            updates["blocked_by"]
            if "blocked_by" in updates
            else task.blocked_by
        )
        await _enforce_blocker_order_constraint(
            session,
            target_id=task_id,
            target_blocked_by=resolved_blocked_by_for_order,
            target_process_status=resolved_process_status,
            target_sort_order=resolved_sort_order,
        )

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

    # V3+ T2 (Kanban #707): if a template's recurrence_rule or timezone changes,
    # recompute next_fire_at from now() unless the client explicitly supplied
    # one in the same PATCH (cron is TZ-sensitive — even a TZ-only flip means
    # the next slot moves). Recompute only when the resolved row is/will be a
    # template — otherwise the recurrence fields are noise.
    if (
        resolved_is_template is True
        and ("recurrence_rule" in updates or "recurrence_timezone" in updates)
        and "next_fire_at" not in updates
    ):
        resolved_rule = (
            updates["recurrence_rule"]
            if "recurrence_rule" in updates
            else task.recurrence_rule
        )
        resolved_tz = (
            updates["recurrence_timezone"]
            if "recurrence_timezone" in updates
            else task.recurrence_timezone
        )
        if resolved_rule:
            updates["next_fire_at"] = next_cron_fire(resolved_rule, resolved_tz or "UTC")

    # Kanban #832: capture resolved interaction_kind before the setattr loop
    # so the auto-unblock check after commit can read it without touching an
    # expired ORM attribute.
    _resolved_interaction_kind_for_done = (
        updates.get("interaction_kind") if "interaction_kind" in updates
        else task.interaction_kind
    )
    _resolved_ps_for_done = (
        updates.get("process_status") if "process_status" in updates
        else task.process_status
    )

    # Process-status-transition side effects — only stamp if not already set /
    # explicitly provided. We use the DB now() so the value matches the
    # audit-trigger snapshot.
    new_process_status = updates.get("process_status")
    if new_process_status is not None and new_process_status != task.process_status:
        field = _STATUS_TIMESTAMP_FIELDS.get(new_process_status)
        if field is not None and getattr(task, field) is None:
            updates.setdefault(field, func.now())

    # Kanban #944 (2026-05-16): per-task LLM-cost estimation on done-flip.
    # Fires only when the PATCH transitions process_status from <5 to 5 AND
    # the task has never been estimated before (idempotent re-flip: a row
    # whose estimated_cost_usd is non-null preserves the first-close values).
    # Estimator failures (unknown model, etc.) are swallowed + logged so a
    # cost-estimation bug never blocks a done flip. The status_change_reason
    # for output-char counting is the resolved value (payload if present, else
    # the existing row's stored value).
    if (
        new_process_status == TaskStatus.DONE
        and task.process_status < TaskStatus.DONE
        and task.estimated_cost_usd is None
    ):
        try:
            runs_result = await session.execute(
                select(SessionRun).where(SessionRun.task_id == task_id)
            )
            runs = list(runs_result.scalars())
            # Build a snapshot object that reflects the resolved-final values
            # for the heuristic — the PATCH may set status_change_reason in
            # the SAME body that closes the task (the typical use-case).
            resolved_reason = (
                updates.get("status_change_reason")
                if "status_change_reason" in updates
                else task.status_change_reason
            )

            class _Snap:
                title = task.title
                description = task.description
                status_change_reason = resolved_reason

            est = estimate_task_cost(_Snap(), runs)
            updates.setdefault("estimated_input_tokens", est["tokens_in"])
            updates.setdefault("estimated_output_tokens", est["tokens_out"])
            updates.setdefault("estimated_cost_usd", est["cost_usd"])
        except Exception as exc:  # noqa: BLE001 - swallow + log; never crash the PATCH
            logger.warning(
                "task %s: cost estimation failed (%s); leaving estimate fields NULL",
                task_id,
                exc,
            )

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
        elif "ck_tasks_task_type_valid" in orig_text:
            detail = "task_type violates ck_tasks_task_type_valid"
        elif "ck_tasks_interaction_kind_valid" in orig_text:
            detail = "interaction_kind violates ck_tasks_interaction_kind_valid"
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

    # Kanban #832: auto-unblock dependents when a question/decision task is marked DONE.
    if (
        _resolved_ps_for_done == TaskStatus.DONE
        and _resolved_interaction_kind_for_done in (
            TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
        )
    ):
        await auto_unblock_dependents(session, task_id)
        await session.commit()  # second commit for the unblock writes

    await session.refresh(task)
    return task


@router.post(
    "/{task_id}/fire-now",
    response_model=TaskRead,
    status_code=http_status.HTTP_200_OK,
)
async def fire_now(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    """Manual trigger for a recurrence template (Kanban #707, T2).

    Bypasses the `next_fire_at <= now()` check. Spawns a child row + advances
    the template's `next_fire_at` to the next future cron slot. Returns the new
    child as `TaskRead` (200, not 201, since the template existed; the child is
    a side-effect resource).

    404 if id not found / soft-deleted. 400 if not is_template=true. 400 on
    cross-project header mismatch.
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    if task.status == RecordStatus.DELETED:
        # 404 vs 400: get_or_404 returns soft-deleted rows by id (per
        # standards/postgresql/soft-delete.md detail endpoint convention). For
        # fire-now, treat soft-deleted as "not found" — a hard cousin of the
        # is-template check below.
        raise HTTPException(
            status_code=404, detail=f"Task id={task_id} not found"
        )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    if not task.is_template:
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_FIRE_NOW_NOT_TEMPLATE_TEMPLATE.format(task_id=task_id),
        )

    child = await fire_template(session, task)
    return child


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
