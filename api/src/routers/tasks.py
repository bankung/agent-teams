"""HTTP routes for Kanban tasks.

Mounted at `/api/tasks`. Process-status transitions stamp `started_at` /
`completed_at` on the way to in_progress / done — clients shouldn't set those directly.

Soft-delete: list endpoint default-filters `WHERE status=1`; opt-in `?include_deleted=true`
returns soft-deleted rows. DELETE /api/tasks/{id} flips `status=0`. Detail endpoint
returns the row regardless of soft-delete status (per standards/postgresql/soft-delete.md).
"""

from __future__ import annotations

import logging
import types as _types
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi import status as http_status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import RecordStatus, TaskInteractionKind, TaskRunMode, TaskStatus, TaskType
from src.db import get_or_404, get_session
from src.models.handoff_template import HandoffTemplate
from src.models.project import Project
from src.models.session import SessionRun
from src.models.task import Task
from src.models.transaction import Transaction
from src.schemas.ai_task import ParseRequest, ParseResponse
from src.schemas.project import ResolveFlagRequest, ResolveFlagResponse
from src.schemas.task import (
    AcceptanceCriterion,
    DecisionRequest,
    HitlResolveRequest,
    HitlResolveResponse,
    NextAutorunResponse,
    SnoozeRequest,
    TaskCreate,
    TaskRead,
    TaskReorder,
    TaskUpdate,
)
from src.middleware.rate_limit import limiter
from src.services.ai_task_parser import (
    AiCallFailed,
    AiCallTimeout,
    AiUnparseable,
    MissingApiKey as AiMissingApiKey,
    parse_task_text,
)
from src.services.content_moderation import scan_task_payload
from src.services.is_pending import assert_is_pending_with_process_status
from src.services.recurrence import fire_template, next_cron_fire
from src.services.budget_enforcer import check_budget
from src.services.budget_gate import check_budget as check_spawn_budget
from src.services.run_mode import assert_consent_for_run_mode
from src.services.task_cost_estimator import estimate_task_cost, resolve_provider_model
from src.services.task_interaction import (
    _validate_answer,
    append_answer,
    auto_unblock_dependents,
    invalidate_last_answer as _invalidate_last_answer,
    validate_decision_payload,
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
from src.services.action_templates import get_template
from src.services.handoff_spawn import spawn_child_from_handoff

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

# Kanban #1125 (L21 prevention): fire-now must respect the same cap as the
# scheduler tick. When fire_template returns None (cap reached), surface
# a 409 Conflict to the operator with the resolved cap so they know what
# to do (resolve children OR raise max_active_children on the template).
_DETAIL_FIRE_NOW_MAX_CHILDREN_TEMPLATE = (
    "Task id={task_id} is at max_active_children cap; template halted. "
    "Resolve open children or raise max_active_children to resume."
)

# Kanban #1122 (L15 prevention): a template that wants to run unattended
# (run_mode=auto_headless AND is_template=true) must be explicitly confirmed
# by a human via POST /api/tasks/{id}/confirm-template-auto-run BEFORE its
# next fire. Resolved-final 422 surfaces on POST (Pydantic) and on the PATCH
# router-side check below. Source-text-locked by
# test_template_auto_run_confirm — keep both in sync.
_DETAIL_TEMPLATE_AUTO_RUN_NEEDS_CONFIRM = (
    "is_template=true AND run_mode='auto_headless' requires "
    "template_auto_run_confirmed_at to be set (per-template confirmation, "
    "Kanban #1122 L15). POST /api/tasks/{task_id}/confirm-template-auto-run first."
)

# Kanban #1121 (L14 prevention): a task whose author-supplied content matched
# a destructive-intent pattern in services/content_moderation.py carries
# requires_human_review=true. The auto-headless gate below refuses any PATCH
# that resolves run_mode=auto_headless on such a row. Source-text-locked by
# test_content_moderation — keep both in sync.
_DETAIL_REQUIRES_HUMAN_REVIEW = (
    "task requires human review before auto-run (matched fields: {matched}). "
    "PATCH requires_human_review=false explicitly to unblock."
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


def _translate_task_integrity_error(exc: "IntegrityError", context: str) -> str:
    """Translate well-known PG constraint names to stable HTTP 400 detail strings.

    Shared by create_task and update_task IntegrityError handlers (Kanban #1682
    Phase 1 dedup). The `context` arg ("creation" or "update") fills the fallback
    phrase — all other branches are identical. Strings are source-text-locked by
    test_post_task_400_detail_strings_are_pinned_in_router_source and
    test_patch_task_400_detail_strings_are_pinned_in_router_source; keep in sync.

    NOTE: the `tasks_project_id_fkey` FK branch (create-only) and the project_id
    literal (`"project_id {payload.project_id} does not exist"`) stay inline in
    create_task so the source-text-lock scan finds them verbatim.
    """
    orig_text = str(exc.orig)
    if "ck_tasks_process_status_valid" in orig_text:
        return "process_status violates ck_tasks_process_status_valid"
    elif "ck_tasks_priority_valid" in orig_text:
        return "priority violates ck_tasks_priority_valid"
    elif "ck_tasks_status_valid" in orig_text:
        return "status violates ck_tasks_status_valid"
    elif "ck_tasks_task_kind_valid" in orig_text:
        return "task_kind violates ck_tasks_task_kind_valid"
    elif "ck_tasks_task_type_valid" in orig_text:
        return "task_type violates ck_tasks_task_type_valid"
    elif "ck_tasks_interaction_kind_valid" in orig_text:
        return "interaction_kind violates ck_tasks_interaction_kind_valid"
    elif "ck_tasks_template_recurrence_complete" in orig_text:
        return (
            "template fields incomplete violates "
            "ck_tasks_template_recurrence_complete"
        )
    elif "ck_tasks_scheduled_xor_template" in orig_text:
        return _DETAIL_SCHEDULED_XOR_TEMPLATE
    elif "ck_tasks_pause_reason_length" in orig_text:
        return (
            "allow_during_pause=true requires allow_during_pause_reason "
            "(>=10 chars) violates ck_tasks_pause_reason_length"
        )
    # Fallback — context distinguishes "creation" vs "update" in the wire detail.
    # Literals below are source-text-locked (see test_post/patch_400_detail_strings).
    if context == "update":
        return "Task update violates a database constraint"
    return "Task creation violates a database constraint"


def _apply_jsonb_serialization(payload: object, updates: dict) -> None:
    """Serialize Pydantic JSONB fields to JSON-safe dicts in-place (#801 pattern).

    Applies model_dump(mode='json') coercion for the four JSONB columns that
    require it before being stored. Guards mirror the update_task PATCH shape
    (field present in updates AND value not None). Called from update_task only
    (create_task serializes inline). Kanban #1682 Phase 1 dedup.
    """
    if (
        "acceptance_criteria" in updates
        and updates["acceptance_criteria"] is not None
        and getattr(payload, "acceptance_criteria", None) is not None
    ):
        updates["acceptance_criteria"] = [
            c.model_dump(mode="json") for c in payload.acceptance_criteria  # type: ignore[union-attr]
        ]
    if "subagent_models" in updates:
        updates["subagent_models"] = [
            e.model_dump(mode="json") for e in payload.subagent_models  # type: ignore[union-attr]
        ]
    if (
        "question_payload" in updates
        and updates["question_payload"] is not None
        and getattr(payload, "question_payload", None) is not None
    ):
        updates["question_payload"] = payload.question_payload.model_dump(mode="json")  # type: ignore[union-attr]
    if (
        "resume_context" in updates
        and updates["resume_context"] is not None
    ):
        updates["resume_context"] = payload.model_dump(mode="json")["resume_context"]  # type: ignore[union-attr]


def _fire_hitl_push(task_id: int, title: str, question_payload: dict | None) -> None:
    """Kanban #1450: fire ntfy push when a task transitions into HITL state.

    Soft-fail — exceptions are caught and logged; never raises to the caller.
    The push gate (PUSH_ENABLED, NTFY_TOPIC) is handled inside send_push itself
    so this function needs no extra guard — it simply passes through.

    Args:
        task_id:         Row id, used to construct the click_url.
        title:           Resolved task title (post-PATCH value, captured before
                         the ORM object expires after commit).
        question_payload: Resolved question_payload dict or None.  Used to
                          extract a short message snippet for the push body.
    """
    import os
    from src.services.notify_ntfy import send_push

    try:
        # Compose the message body: prefer question text, fall back to title.
        qp = question_payload or {}
        question_text = qp.get("question") if isinstance(qp, dict) else None
        if question_text:
            body = str(question_text)[:120]
        else:
            body = "Tap to view"

        # click_url: WEB_BASE_URL + /approve/<id> (Kanban #1452 — phone HITL
        # push-tap flow). Phone operator lands on the mobile approve page which
        # POSTs back to /api/tasks/{id}/decide with the HitlResolveRequest body
        # shape. Was /tasks/<id> pre-#1452 (#955.B), pointing at the desktop
        # task focus view — wrong target for phone push.
        base_url = os.environ.get("WEB_BASE_URL", "http://localhost:5431").rstrip("/")
        click_url = f"{base_url}/approve/{task_id}"

        truncated_title = title[:50] if title else ""
        push_title = f"Agent-Teams: {truncated_title}"

        result = send_push(
            body,
            title=push_title,
            priority=4,
            click_url=click_url,
            tags="warning,robot",
        )
        if not result.ok:
            logger.warning(
                "hitl_push: task=%d send_push returned ok=False detail=%s",
                task_id,
                result.detail,
            )
        else:
            logger.info("hitl_push: task=%d push sent", task_id)
    except Exception:  # noqa: BLE001 — never crash the API response
        logger.exception("hitl_push: unexpected error on task=%d; push skipped", task_id)

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

    # --- HITL timeout gate (Kanban #989) -------------------------------------
    # On-demand enforcement (Q2 → A, design lock #950 — mirrors the #951
    # budget-cap pattern): no APScheduler / cron — we stamp on every
    # /next-autorun poll. `projects.hitl_timeout_hours` NULL = indefinite
    # pause (preserves pre-#989 behavior); non-null = stamp
    # `halt_reason='hitl_timeout'` on any BLOCKED HITL task
    # (halt_reason literally 'question' or 'decision') whose updated_at is
    # older than the threshold. Halt-only — task stays BLOCKED so the user
    # decides cancel/retry/re-prompt. This must precede the pending_questions
    # / resume_tasks enumeration below so timed-out rows reflect their new
    # halt_reason in the same response.
    session_project = await session.get(Project, project_id)
    if session_project is not None and session_project.hitl_timeout_hours is not None:
        timeout_hours = session_project.hitl_timeout_hours
        now = datetime.now(timezone.utc)
        threshold = timedelta(hours=timeout_hours)
        paused_q = select(Task).where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.halt_reason.in_(("question", "decision")),
            Task.process_status == TaskStatus.BLOCKED,
        )
        paused = (await session.execute(paused_q)).scalars().all()
        stamped_any = False
        for t in paused:
            if t.updated_at is not None and (now - t.updated_at) > threshold:
                t.halt_reason = "hitl_timeout"
                stamped_any = True
                logger.warning(
                    "task %d HITL timeout exceeded (project %d, elapsed_h=%.1f, "
                    "limit_h=%d) — halt_reason stamped 'hitl_timeout'",
                    t.id,
                    project_id,
                    (now - t.updated_at).total_seconds() / 3600,
                    timeout_hours,
                )
        if stamped_any:
            await session.commit()

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
            if before_anchor.sort_order is None:  # materialized above
                raise RuntimeError("before_anchor.sort_order unexpectedly None")
            if after_anchor.sort_order is None:
                raise RuntimeError("after_anchor.sort_order unexpectedly None")
            return (after_anchor.sort_order + before_anchor.sort_order) / 2.0
        elif before_anchor is not None:
            # Place just above (smaller than) before_anchor.
            if before_anchor.sort_order is None:
                raise RuntimeError("before_anchor.sort_order unexpectedly None")
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
            if after_anchor is None:
                raise RuntimeError("after_anchor unexpectedly None in else branch")
            if after_anchor.sort_order is None:
                raise RuntimeError("after_anchor.sort_order unexpectedly None")
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

    # Kanban #1006 (2026-05-20): action template pre-fill.
    # Look up the named template BEFORE any DB I/O — unknown name → 422.
    # Apply default values only for fields the caller did NOT explicitly supply
    # (detected via model_fields_set).  acceptance_criteria merging is handled
    # AFTER payload_dict construction further below.
    _action_template = None
    if payload.action_template_id is not None:
        _action_template = get_template(payload.action_template_id)
        if _action_template is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"action_template_id {payload.action_template_id!r} not found; "
                    "check GET /api/templates/actions for available templates"
                ),
            )

    # Kanban #1209 (GOV1 hard kill switch): refuse new task POSTs against a
    # killed project. 423 Locked (per AC#4) distinguishes "project state
    # blocks this action" from 409 (resource conflict on kill/revive) and
    # from 422 (validation). The detail surfaces killed_at + killed_reason
    # so the FE can render an actionable banner ("project N killed since X
    # because Y; revive to enable POST"). Skipped on the missing-project
    # path — the FK violation downstream gives a stable detail for that.
    # Soft-delete filter (P1-2, dev-reviewer audit on #1209): only ACTIVE
    # projects guard via the kill gate. A soft-deleted (status=0) project
    # falls through to the downstream FK violation, which is the right shape
    # for "this project no longer exists" rather than 423 "this project is
    # locked". (kill_switch.py service-layer already enforces the same
    # filter via _get_active_project_or_404.)
    proj_row = (
        await session.execute(
            select(
                Project.id,
                Project.is_killed,
                Project.killed_at,
                Project.killed_reason,
                Project.is_paused,
                Project.paused_at,
                Project.paused_reason,
            )
            .where(
                Project.id == payload.project_id,
                Project.status == RecordStatus.ACTIVE,
            )
        )
    ).first()
    if proj_row is not None and proj_row.is_killed:
        # P1-1 (dev-reviewer audit on #1209): killed_reason can be up to 2000
        # chars; embedding it in `message` doubled the payload size AND made
        # the message field unbounded. Keep the full reason in the dedicated
        # `killed_reason` field (where consumers expect to find it) and let
        # `message` stay a fixed-length pointer.
        raise HTTPException(
            status_code=423,
            detail={
                "message": (
                    f"Project {payload.project_id} is killed. "
                    f"POST blocked. See killed_reason field for details."
                ),
                "killed_at": (
                    proj_row.killed_at.isoformat() if proj_row.killed_at else None
                ),
                "killed_reason": proj_row.killed_reason,
            },
        )

    # Kanban #1211 (GOV3 soft-pause D3): refuse new task POSTs against a paused
    # project UNLESS the per-task escape hatch is engaged. Order matters:
    # the kill check above is stricter (mutex constraint guarantees only
    # one can fire), so kill takes precedence on the rare race window.
    #
    # Escape hatch: body carries `allow_during_pause=true` + a reason
    # >=10 chars (Pydantic enforces). When both conditions land, we
    # ALLOW + log a `projects_audit` row with action='pause_override' so
    # operators can review override frequency (D6 + GOV5 callout: "if used
    # >X times/week, threshold is wrong"). The audit row is written here
    # at the router after we know the override fired but BEFORE the task
    # INSERT — same session, same transaction, atomic with the task INSERT.
    pause_override_audit_pending: dict | None = None
    if proj_row is not None and proj_row.is_paused:
        if not (
            payload.allow_during_pause
            and payload.allow_during_pause_reason
            and len(payload.allow_during_pause_reason) >= 10
        ):
            raise HTTPException(
                status_code=423,
                detail={
                    "message": (
                        f"Project {payload.project_id} is paused. "
                        "POST blocked unless allow_during_pause=true with "
                        "allow_during_pause_reason set (>=10 chars). "
                        "See paused_reason field for context."
                    ),
                    "paused_at": (
                        proj_row.paused_at.isoformat()
                        if proj_row.paused_at
                        else None
                    ),
                    "paused_reason": proj_row.paused_reason,
                },
            )
        # Escape hatch engaged — stage the audit row for the same commit as
        # the INSERT. Captured as a dict (not the ORM object yet) so we can
        # add it AFTER the task INSERT and let the audit row reference the
        # new task's id in drain_summary for GOV4 deep-linking.
        pause_override_audit_pending = {
            "reason": payload.allow_during_pause_reason,
        }

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

    # Kanban #1004: handoff_template_id existence + project-scope validation.
    # Mirrors the blocked_by posture above. A template's project_id must be
    # NULL (global) OR equal to the task's project_id; cross-project pointers
    # are rejected at 422. Soft-deleted templates (status=0) are rejected.
    if payload.handoff_template_id is not None:
        ht = await session.get(HandoffTemplate, payload.handoff_template_id)
        if ht is None or ht.status == RecordStatus.DELETED:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"handoff_template_id {payload.handoff_template_id} "
                    "does not exist or is deleted"
                ),
            )
        if ht.project_id is not None and ht.project_id != payload.project_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"handoff_template_id {payload.handoff_template_id} "
                    "belongs to a different project"
                ),
            )

    # Kanban #858 (2026-05-13): when interaction_kind IN ('question','decision'),
    # force task_kind='human' AND run_mode='manual' regardless of caller input.
    # Silent server-side coerce (Option A) — atomic so the HUMAN↔MANUAL
    # invariant below doesn't fire on the same call. Reverse 'question'→'work'
    # PATCHes do NOT auto-revert task_kind (handled separately in update_task).
    coerced_task_kind, coerced_run_mode = coerce_task_kind_for_interaction(
        payload.interaction_kind, payload.task_kind, payload.run_mode
    )

    # Kanban #1194 AC4 (2026-05-19): spawn-time hard cap gate. Only fires for
    # AI tasks — human tasks (interaction_kind in {question,decision} or
    # explicit task_kind='human') don't burn LLM budget. Override hatch:
    # body carries `budget_override_authorized_by` + `budget_override_reason`
    # (pair-validated in the Pydantic model_validator); when present, the
    # gate is bypassed AND the override is recorded as a structured footer
    # on the task description for auditability.
    #
    # Skip the gate entirely when the project row is missing (proj_row is None)
    # — letting the downstream IntegrityError handler surface the canonical
    # `project_id N does not exist` 400 string (source-text-locked by
    # test_post_task_returns_stable_detail_on_fk_violation +
    # test_post_task_auto_headless_with_missing_project_returns_project_does_not_exist).
    # The gate's "project not found" ValueError would otherwise surface as 500.
    if coerced_task_kind == "ai" and proj_row is not None:
        bc = await check_spawn_budget(
            session, payload.project_id, payload.estimated_cost_usd
        )
        if not bc.allowed:
            override_ok = (
                payload.budget_override_authorized_by is not None
                and payload.budget_override_reason is not None
            )
            if not override_ok:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "message": (
                            "Spawn would exceed project's daily budget cap. "
                            "Supply budget_override_authorized_by + "
                            "budget_override_reason to bypass."
                        ),
                        "used_today_usd": str(bc.used_today_usd),
                        "cap_daily_usd": (
                            str(bc.cap_daily_usd) if bc.cap_daily_usd else None
                        ),
                        "projected_usd": str(bc.projected_usd),
                        "pct_used": (
                            str(bc.pct_used) if bc.pct_used is not None else None
                        ),
                        "reason": bc.reason,
                        "override_hint": (
                            "set budget_override_authorized_by + "
                            "budget_override_reason to bypass"
                        ),
                    },
                )
            # Override engaged — log the structured audit line. We mutate the
            # caller's description (or seed one) with a stable footer that
            # operators can grep for; this is the audit signal since AC5
            # already covers the operational notification on the threshold.
            logger.warning(
                "budget_gate_override: project=%d authorized_by=%s "
                "used_today=%s cap=%s projected=%s reason=%s",
                payload.project_id,
                payload.budget_override_authorized_by,
                bc.used_today_usd,
                bc.cap_daily_usd,
                bc.projected_usd,
                payload.budget_override_reason,
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

    # Kanban #1121 (L14 prevention): scan author-supplied fields for
    # destructive-intent patterns. The scanner is pure (no DB I/O) — runs here
    # AFTER the consent gate so a clearly-bad POST surfaces consent issues
    # first (consent is the bigger blast-radius gate). A match TAGS the row
    # via requires_human_review=true; downstream auto-pickup paths (worker
    # L17, auto-headless PATCH gate below) honor the tag. Empty list = clean,
    # falsy in Python.
    moderation_matches = scan_task_payload(
        title=payload.title,
        description=payload.description,
        acceptance_criteria=payload.acceptance_criteria,
        halt_reason=payload.halt_reason,
        status_change_reason=payload.status_change_reason,
    )

    # #801 — model_dump(mode='json') coerces Pydantic objects to JSON-safe dicts
    # for JSONB columns. See standards/sqlalchemy/orm.md.
    payload_dict = payload.model_dump()
    # Kanban #1194 (AC4): the budget-gate override pair is request-only metadata
    # — not Task columns. Strip BEFORE the Task(**payload_dict) construction
    # so SQLAlchemy doesn't see the extras. Logged + structured audit lives in
    # the gate-evaluation block above.
    payload_dict.pop("budget_override_authorized_by", None)
    payload_dict.pop("budget_override_reason", None)
    # Kanban #1006 (2026-05-20): action_template_id is request-only metadata —
    # not a Task column.  Strip it so SQLAlchemy doesn't see it.
    payload_dict.pop("action_template_id", None)

    # Kanban #1006 (AC4 + AC6): apply template pre-fill if a template was resolved.
    if _action_template is not None:
        # task_kind: applied AFTER the coerce assignment below (step marked ①)
        # because coerce_task_kind_for_interaction runs before payload_dict and
        # its result is written to payload_dict["task_kind"] at step ①; we
        # must hook in there to avoid being overwritten.

        # task_type — no coerce path touches this; apply now.
        if "task_type" not in payload.model_fields_set:
            payload_dict["task_type"] = _action_template.default_task_type
        # priority — no coerce path touches this; apply now.
        if "priority" not in payload.model_fields_set:
            payload_dict["priority"] = _action_template.default_priority

        # AC4 acceptance_criteria: template ac_outline items as AcceptanceCriterion.
        # Build the template-derived entries (status='pending').
        template_ac = [
            AcceptanceCriterion(text=text).model_dump(mode="json")
            for text in _action_template.ac_outline
        ]
        if "acceptance_criteria" not in payload.model_fields_set or payload.acceptance_criteria is None:
            # Caller omitted acceptance_criteria → use template list as-is.
            payload_dict["acceptance_criteria"] = template_ac if template_ac else None
        else:
            # Caller supplied acceptance_criteria → MERGE: template first, then caller.
            caller_ac = [
                c.model_dump(mode="json") for c in payload.acceptance_criteria
            ]
            payload_dict["acceptance_criteria"] = template_ac + caller_ac

        # AC6: record template provenance in resume_context so history doesn't
        # change retroactively when the YAML is updated.
        existing_rc: dict = payload_dict.get("resume_context") or {}
        existing_rc["action_template"] = {
            "id": _action_template.name,
            "version": _action_template.version,
        }
        payload_dict["resume_context"] = existing_rc
    # #858 — persist post-coerce values (no-op when interaction_kind='work')
    payload_dict["task_kind"] = coerced_task_kind
    payload_dict["run_mode"] = coerced_run_mode
    # Kanban #1006 step ①: apply template task_kind default AFTER the coerce
    # assignment so we don't stomp on the coerce.  Only fires when:
    #   (a) a template was resolved, AND
    #   (b) the caller did NOT explicitly supply task_kind (model_fields_set),
    #       AND
    #   (c) the interaction_kind coerce did not change the value (i.e. the
    #       coerce was a no-op — coerced_task_kind equals payload.task_kind
    #       which is the Pydantic default 'ai').  We check (b) which covers (c):
    #       if the caller didn't supply task_kind and the coerce also didn't
    #       force it, we can apply the template default.
    if _action_template is not None and "task_kind" not in payload.model_fields_set:
        # If coerce_task_kind_for_interaction forced 'human' (question/decision),
        # keep that — the coerce is a higher-level invariant than the template.
        if coerced_task_kind == payload.task_kind:
            # coerce was a no-op; safe to apply template default
            payload_dict["task_kind"] = _action_template.default_task_kind
    # L14: stamp the flag iff the scanner matched. A clean POST leaves the
    # column at its DB DEFAULT (false). Note we do NOT raise here — the tag
    # is non-blocking by design; the operator may legitimately FILE
    # destructive work, only auto-headless is gated.
    if moderation_matches:
        payload_dict["requires_human_review"] = True
    # #801 — JSONB serialization for JSONB columns. acceptance_criteria and
    # resume_context require the _action_template guard (the pre-fill block
    # already built the correct serialized form when a template was active).
    # subagent_models and question_payload have no template dependency.
    if _action_template is None and payload_dict.get("acceptance_criteria") is not None:
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
    # #801 pattern — resume_context: re-serialize from payload when no template.
    if _action_template is None and payload_dict.get("resume_context") is not None:
        payload_dict["resume_context"] = payload.model_dump(mode="json")["resume_context"]

    task = Task(**payload_dict)
    session.add(task)

    # Kanban #1211 (GOV3 D6): if the pause-override hatch fired above, stage
    # a projects_audit row with action='pause_override' in the SAME
    # transaction as the INSERT — so a failed INSERT also rolls back the
    # audit row (no orphan signals). flush() materializes task.id so we
    # can reference it in drain_summary for GOV4 deep-linking.
    if pause_override_audit_pending is not None:
        from src.models.projects_audit import ProjectsAudit
        try:
            await session.flush()  # surfaces task.id for the audit row's drain_summary
        except IntegrityError as exc:
            # Same translation pattern as the commit-time block below; the
            # flush surfaces the same constraint violations the commit would,
            # so we mirror the detail mapping here.
            await session.rollback()
            orig_text = str(exc.orig)
            if "tasks_project_id_fkey" in orig_text:
                detail = f"project_id {payload.project_id} does not exist"
            else:
                detail = "Task creation violates a database constraint"
            raise HTTPException(status_code=400, detail=detail) from exc
        session.add(
            ProjectsAudit(
                project_id=payload.project_id,
                actor="operator",
                action="pause_override",
                reason=pause_override_audit_pending["reason"],
                drain_summary={
                    "task_id": task.id,
                    "task_title": task.title[:200],
                },
            )
        )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Translate well-known constraint names to stable details; mirror update_task M5.
        # Strings pinned by test_post_task_400_detail_strings_are_pinned_in_router_source — keep the test in sync.
        # FK branch (create-only) handled inline so the source-text-lock scan
        # finds `"project_id {payload.project_id} does not exist"` verbatim.
        orig_text = str(exc.orig)
        if "tasks_project_id_fkey" in orig_text:
            detail = f"project_id {payload.project_id} does not exist"
        else:
            detail = _translate_task_integrity_error(exc, context="creation")
        raise HTTPException(status_code=400, detail=detail) from exc
    await session.refresh(task)

    # Kanban #1450: fire ntfy push when a task is created with interaction_kind
    # in ('question', 'decision').  Fires once per creation; no idempotency
    # concern on POST (each POST is a new row).  Soft-fail: push error does NOT
    # block the 201 response.
    if payload.interaction_kind in (
        TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
    ):
        _post_qp = (
            payload.question_payload.model_dump(mode="json")
            if payload.question_payload is not None
            else None
        )
        _fire_hitl_push(task.id, payload.title or "", _post_qp)

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

    # Kanban #955.B: capture pre-PATCH state for push-notification transition
    # detection. Must be captured before any mutation so the "was X before this
    # PATCH" check in the post-commit hooks is accurate.
    _pre_patch_process_status = task.process_status
    _pre_patch_interaction_kind = task.interaction_kind

    updates = payload.model_dump(exclude_unset=True)

    # #801 — model_dump(mode='json') coercion for JSONB columns. Kanban #1682
    # Phase 1 dedup: extracted to _apply_jsonb_serialization (defined above).
    _apply_jsonb_serialization(payload, updates)

    # Kanban #832: pop action-only fields before writing to ORM.
    # These are not DB columns — they trigger interaction logic below.
    new_answer = updates.pop("new_answer", None)
    new_answer_by = updates.pop("new_answer_by", None) or "user"
    do_invalidate = updates.pop("invalidate_last_answer", None)
    invalidated_reason = updates.pop("invalidated_reason", None)

    # Kanban #832: answer append for question/decision tasks.
    # Kanban #987: strict answer validation gate (Q3=A) + invalid-attempt
    # audit trail (Q6=A). Invalid answers append to history with
    # is_valid=False + invalidated_reason, persist in one transaction,
    # then raise 422 — task stays BLOCKED (no resume).
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
        # Resolve question_payload the same way (PATCH-supplied wins).
        resolved_question_payload = (
            updates.get("question_payload") if "question_payload" in updates
            else task.question_payload
        )
        is_valid, reason = _validate_answer(
            resolved_interaction_kind, resolved_question_payload, new_answer
        )
        if not is_valid:
            # Append the invalid attempt then 422. Commit the audit-trail
            # write before raising so the answer_history grew even though
            # the rest of the PATCH is rejected.
            updates["question_payload"] = append_answer(
                resolved_question_payload, new_answer, new_answer_by,
                is_valid=False, invalidated_reason=reason,
            )
            # Persist ONLY the question_payload audit trail; discard
            # other patch fields so the 422 carries clean rejection
            # semantics (status / process_status etc. don't sneak in).
            task.question_payload = updates["question_payload"]
            await session.commit()
            raise HTTPException(
                status_code=422,
                detail=f"invalid_answer: {reason}",
            )
        updates["question_payload"] = append_answer(
            resolved_question_payload, new_answer, new_answer_by,
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

    # Kanban #1004: handoff_template_id PATCH validation. Same posture as
    # blocked_by — existence + project-scope checks, plus the global-template
    # exception (project_id IS NULL on the template). Setting to None is
    # always allowed (clears the auto-handoff opt-in).
    if "handoff_template_id" in updates:
        new_handoff_template_id = updates["handoff_template_id"]
        if new_handoff_template_id is not None:
            ht = await session.get(HandoffTemplate, new_handoff_template_id)
            if ht is None or ht.status == RecordStatus.DELETED:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"handoff_template_id {new_handoff_template_id} "
                        "does not exist or is deleted"
                    ),
                )
            if ht.project_id is not None and ht.project_id != task.project_id:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"handoff_template_id {new_handoff_template_id} "
                        "belongs to a different project"
                    ),
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

    # Kanban #1122 (L15 prevention) resolved-final check: a row that lands at
    # is_template=true AND run_mode='auto_headless' MUST also have a non-null
    # template_auto_run_confirmed_at. Resolve all three the same way as the
    # other resolved-final gates above (PATCH-supplied if present, else
    # existing row's value). Fires AFTER consent (which is the broader gate —
    # project-level consent must be granted first; then the per-template L15
    # confirm refines it). Detail string source-text-locked above.
    resolved_template_auto_run_confirmed_at = (
        updates["template_auto_run_confirmed_at"]
        if "template_auto_run_confirmed_at" in updates
        else task.template_auto_run_confirmed_at
    )
    if (
        resolved_is_template is True
        and resolved_run_mode == TaskRunMode.AUTO_HEADLESS
        and resolved_template_auto_run_confirmed_at is None
    ):
        raise HTTPException(
            status_code=422,
            detail=_DETAIL_TEMPLATE_AUTO_RUN_NEEDS_CONFIRM.format(task_id=task_id),
        )

    # Kanban #1121 (L14 prevention) — scan + auto-headless gate.
    #
    # Step 1: scan the PATCH-supplied content fields (title / description /
    # acceptance_criteria / halt_reason / status_change_reason) for
    # destructive intent. We pull from `updates` rather than `payload`
    # because:
    #   (a) `updates` already has `exclude_unset=True` applied — fields the
    #       caller didn't touch are absent, so the scanner doesn't waste
    #       cycles on the row's stored value (which by definition was
    #       already scanned on its OWN POST/PATCH).
    #   (b) The acceptance_criteria + question_payload entries in `updates`
    #       are already model_dump'd to dicts (see #801 pattern above) when
    #       the caller supplied them, so the scanner's dict-or-model
    #       fallback handles them cleanly.
    #
    # The PATCH-only scan deliberately differs from POST's full-payload scan:
    # POST has no prior row state, so every author-field is in scope. PATCH
    # only inspects the diff — a row that landed flagged on a prior scan
    # stays flagged via the resolved-final logic below regardless of whether
    # the current PATCH touches the originally-matched field.
    patch_moderation_matches = scan_task_payload(
        title=updates.get("title"),
        description=updates.get("description"),
        acceptance_criteria=updates.get("acceptance_criteria"),
        halt_reason=updates.get("halt_reason"),
        status_change_reason=updates.get("status_change_reason"),
    )

    # Step 2: resolve the final requires_human_review value.
    #   (a) Caller-supplied (in `updates`) wins — that's the reviewer-ack
    #       channel (PATCH `requires_human_review=false` clears the flag).
    #   (b) Otherwise, sticky-on-match: a fresh PATCH-scan hit escalates
    #       false → true; an unmatched scan does NOT auto-clear (one-way).
    #   (c) Otherwise, the row's existing stored value carries forward.
    if "requires_human_review" in updates:
        resolved_requires_human_review = updates["requires_human_review"]
    elif patch_moderation_matches:
        resolved_requires_human_review = True
        # Stamp into `updates` so the value persists alongside the rest of
        # the PATCH. The no-op skip a few blocks below will silently drop
        # the field if it equals the row's current value, so this doesn't
        # generate audit-row noise when a previously-flagged task gets
        # another flagged PATCH.
        updates["requires_human_review"] = True
    else:
        resolved_requires_human_review = task.requires_human_review

    # Step 3: auto-headless gate. If the row would land at
    # run_mode='auto_headless' AND requires_human_review is True, refuse
    # the PATCH with 422 and the source-text-locked detail. This is the
    # primary enforcement point — the scanner TAGS, this gate BLOCKS auto-
    # pickup. Note the gate fires REGARDLESS of whether the caller is
    # PATCHing run_mode in this body (a flipped flag + an existing
    # auto_headless row is the same risk surface as an explicit flip).
    if (
        resolved_run_mode == TaskRunMode.AUTO_HEADLESS
        and resolved_requires_human_review is True
    ):
        # Build the matched-fields list for the error detail. Prefer the
        # patch-scan result (fresh signal); if empty (the flag came from an
        # earlier scan), say "previously flagged" so the operator knows the
        # gate is firing on stored state rather than the current PATCH.
        matched_for_detail = (
            ", ".join(patch_moderation_matches)
            if patch_moderation_matches
            else "previously flagged"
        )
        raise HTTPException(
            status_code=422,
            detail=_DETAIL_REQUIRES_HUMAN_REVIEW.format(matched=matched_for_detail),
        )

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

    # Kanban #955.B: capture notification payload values before the setattr
    # loop. After session.commit() the ORM object is expired (async sessions
    # lazy-load on attribute access → MissingGreenlet). We derive the final
    # values here using the same "updates wins over row" pattern as #832.
    _notify_task_title = (
        updates.get("title") if "title" in updates else task.title
    )
    _notify_status_change_reason = (
        updates.get("status_change_reason")
        if "status_change_reason" in updates
        else task.status_change_reason
    )
    _notify_question_payload = (
        updates.get("question_payload")
        if "question_payload" in updates
        else task.question_payload
    )

    # Kanban #1007 (AC2): when a decision task is being flipped to DONE via PATCH,
    # enforce that chosen_id is set and matches an option id. This mirrors the
    # `/decide` endpoint's own validation so both paths share the invariant.
    # Fires before the status-stamp side effects — a 422 here is a clean rejection.
    if (
        _resolved_ps_for_done == TaskStatus.DONE
        and _resolved_interaction_kind_for_done == TaskInteractionKind.DECISION
        and task.process_status != TaskStatus.DONE  # skip already-done idempotent case
    ):
        resolved_qp = updates.get("question_payload") or task.question_payload
        try:
            validate_decision_payload(resolved_qp)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

            _snap = _types.SimpleNamespace(
                title=task.title,
                description=task.description,
                status_change_reason=resolved_reason,
            )
            est = estimate_task_cost(_snap, runs)
            updates.setdefault("estimated_input_tokens", est["tokens_in"])
            updates.setdefault("estimated_output_tokens", est["tokens_out"])
            updates.setdefault("estimated_cost_usd", est["cost_usd"])

            # Kanban #953: mirror the cost estimate into the transactions
            # ledger so per-project P&L stays complete without manual
            # reconciliation. Idempotent via the same precondition that
            # gates the cost write itself (task.estimated_cost_usd is None
            # before this block) — re-flipping a previously-done task does
            # NOT double-insert. Skip when cost is zero (no ledger noise
            # for unmetered work) and when project_id is missing (defensive).
            cost_usd = est["cost_usd"]
            if cost_usd and cost_usd > 0 and task.project_id is not None:
                provider, _model = resolve_provider_model()
                # USD minor units (cents). The estimator returns USD
                # Decimals — we hard-code USD here in lockstep. Localizing
                # to project.currency_default is a future slice (the cost
                # is denominated in USD upstream regardless).
                amount_minor = int(cost_usd * 100)
                session.add(
                    Transaction(
                        project_id=task.project_id,
                        amount_minor=amount_minor,
                        currency="USD",
                        kind="cost",
                        category=f"llm_{provider}",
                        # task.completed_at hasn't resolved yet (it's a func.now()
                        # ClauseElement in `updates`). Stamp explicit UTC now()
                        # so the ledger row carries a concrete TZ-aware datetime.
                        occurred_at=datetime.now(timezone.utc),
                        source="estimated",
                        source_ref=f"task-{task_id}-close",
                        task_id=task_id,
                        notes=f"Auto-inserted on task close (est. {cost_usd} USD)",
                    )
                )
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

    # =====================================================================
    # POST-PATCH cross-resource side-effect hooks (4 sites below)
    # ---------------------------------------------------------------------
    # All 4 hooks below follow the codified pattern in
    # `context/standards/fastapi/atomic-mutations.md` § "Post-PATCH
    # cross-resource side effects":
    #
    #   (1) Kanban #1004 — handoff_template spawn (in-transaction, line ~1823)
    #   (2) Kanban #832  — auto-unblock dependents       (line ~1895)
    #   (3) Kanban #1211 — audit-flag pipeline           (line ~1905)
    #   (4) Kanban #955.B — push-notification event hooks (line ~1958)
    #
    # Shared invariants per the standard:
    # - Transition detection via "`field` in updates AND old != new" → gives
    #   idempotent re-PATCH semantics for free.
    # - In-transaction hooks (#1004) fire BEFORE session.commit() so atomic
    #   rollback works; errors raise HTTPException, not swallow.
    # - Post-commit hooks (#832, #1211, #955.B) fire AFTER the durable write
    #   so any payload they ship reflects the persisted state.
    #
    # If you're adding a 5th hook here, pattern-match the shape exactly —
    # don't invent a new style. If the pattern has structurally diverged
    # at n>=6 sites, that's the point to extract a mini-framework (see the
    # "Generalizes to" section of the standards doc).
    # =====================================================================

    # Kanban #1004: auto-handoff spawn hook. When this PATCH transitions
    # process_status from `!= 5` to `= 5` AND the task carries a non-null
    # handoff_template_id, spawn a child task derived from that template in
    # the SAME transaction. The parent flip + child INSERT commit together;
    # a template-render failure (422) atomically rolls both back so the
    # operator never sees a half-spawned state.
    #
    # We compute `_was_done_before` from the cached pre-PATCH process_status
    # captured earlier (_resolved_ps_for_done is the post-PATCH value). The
    # "transitioned to DONE" condition mirrors #944's cost-estimation gate
    # so a re-PATCH of an already-DONE task does NOT re-spawn (idempotence).
    #
    # Reads `task.handoff_template_id` AFTER the setattr loop so a same-PATCH
    # update to the field is honored (e.g. PATCH {handoff_template_id: T,
    # process_status: 5} on a TODO row — sets the pointer AND triggers
    # spawn in one call).
    if (
        _resolved_ps_for_done == TaskStatus.DONE
        and task.process_status == TaskStatus.DONE  # setattr loop applied it
        and task.handoff_template_id is not None
    ):
        # The parent row in `task` was just set to DONE in-memory. We need to
        # know if this is a TRANSITION (re-PATCHing an already-DONE row must
        # not re-spawn). `_resolved_ps_for_done` is the post-PATCH value
        # (always DONE here); the actual pre-PATCH process_status lives on
        # the row at session-load time — but SQLAlchemy has mutated the
        # attribute, so we use the `process_status` key presence in
        # `updates` as the signal: if process_status is in `updates`, the
        # caller actually flipped it (the no-op skip above would have left
        # it out otherwise → still a transition signal absent). When
        # process_status is NOT in updates, the task was already DONE before
        # this PATCH — skip the spawn.
        if "process_status" in updates:
            # spawn_child_from_handoff raises HTTPException(422) on
            # template-render failure; the surrounding try/except below
            # (commit IntegrityError handler) does NOT swallow HTTPException,
            # so the 422 propagates up cleanly with the parent flip rolled
            # back (we haven't commit'd yet).
            await spawn_child_from_handoff(session, task)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Translate well-known CHECK names to stable details; fall through for
        # unknown constraints so the failure is still surfaced (without leaking
        # raw PG text into the wire response).
        # Strings pinned by test_patch_task_400_detail_strings_are_pinned_in_router_source — keep the test in sync.
        detail = _translate_task_integrity_error(exc, context="update")
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

    # Kanban #1211 (GOV3 AC#3): post-PATCH hook — if the patched task is an
    # audit task (task_type='audit') that just transitioned to DONE, invoke
    # the flag pipeline. The hook is surgical: it only fires on the
    # DONE-flip of an 'audit' task, leaving every other PATCH path
    # unaffected.
    #
    # Why here (not in the audit-task DONE flow above): the audit-task
    # transition is a regular PATCH, not a question/decision answer. We
    # detect it post-commit so the audit_report (which the same PATCH may
    # have written) is already persisted before the helper reads it.
    #
    # Errors from apply_flag_from_audit_report are LOGGED but NOT raised —
    # an audit-flag pipeline failure must not crash the audit-task DONE
    # PATCH (the data-quality issue is downstream tooling's responsibility
    # to clean up). The helper itself is defensive (returns no-op summary
    # on malformed input rather than raising).
    if (
        _resolved_ps_for_done == TaskStatus.DONE
        and task.task_type == TaskType.AUDIT
    ):
        from src.services.audit_flag import apply_flag_from_audit_report
        try:
            flag_summary = await apply_flag_from_audit_report(
                audit_task_id=task_id,
                actor="system",
                session=session,
            )
            await session.commit()  # commit flag-pipeline side effects
            logger.info(
                "GOV3 flag pipeline: audit_task=%d summary=%s",
                task_id,
                flag_summary,
            )
        except HTTPException:
            # Defensive re-raise pattern from pause_project (already-killed
            # 409 etc.) propagates HTTPException through the helper. Roll
            # back the flag-pipeline side effects, log, and continue — the
            # audit-task DONE flip itself already committed above so the
            # caller's response is still 200.
            await session.rollback()
            logger.exception(
                "GOV3 flag pipeline raised HTTPException on audit_task=%d; "
                "audit-task DONE flip stands but flag pipeline rolled back",
                task_id,
            )
        except Exception:  # noqa: BLE001 — defensive: never crash the PATCH
            await session.rollback()
            logger.exception(
                "GOV3 flag pipeline crashed on audit_task=%d; "
                "audit-task DONE flip stands but flag pipeline rolled back",
                task_id,
            )

    # Kanban #955.B: push-notification event hooks. Fire AFTER all PATCH commits
    # so the mutation is durable before any delivery attempt. Three transitions:
    #
    #   (1) HITL needed — interaction_kind transitions from 'work'/'None' →
    #       'question' or 'decision'. Does NOT fire on reverse transition.
    #   (2) Task done  — process_status transitions to 5 (DONE).
    #   (3) Task failed — process_status transitions to 6 (CANCELLED/FAIL).
    #
    # Pattern: "field in updates AND old value differs from new value" — same
    # idempotent-re-PATCH guard used by #1007, #1211, #1004.
    #
    # deliver() is fire-and-await but adapter failures return {ok:False, detail}
    # and do NOT raise — a push delivery failure never crashes the PATCH.
    # When no push subscriptions match, deliver() is a no-op (empty target list).
    try:
        from src.services.notification_router import deliver as _push_deliver

        # HITL-needed hook — fires when interaction_kind transitions from
        # 'work' (or NULL) → 'question' or 'decision'. Uses pre-captured
        # values (_pre_patch_interaction_kind, _notify_*) since the ORM
        # object is expired after commit (async-session lazy-load guard).
        if (
            "interaction_kind" in updates
            and _resolved_interaction_kind_for_done in (
                TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
            )
            and _pre_patch_interaction_kind not in (
                TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
            )
        ):
            _hitl_qp = _notify_question_payload or {}
            _hitl_body = (
                _hitl_qp.get("question") if isinstance(_hitl_qp, dict) else None
            ) or _notify_task_title
            await _push_deliver(
                task_id=task_id,
                payload={
                    "title": f"HITL needed: {_notify_task_title}",
                    "body": str(_hitl_body),
                    "url": f"/tasks/{task_id}",
                },
                kind="web_push",
                event_kind="hitl_needed",
                session=session,
            )

        # Task done hook — fires when process_status transitions to 5.
        elif (
            "process_status" in updates
            and _resolved_ps_for_done == TaskStatus.DONE
            and _pre_patch_process_status != TaskStatus.DONE
        ):
            _done_reason = _notify_status_change_reason or "Completed"
            await _push_deliver(
                task_id=task_id,
                payload={
                    "title": f"Task done: {_notify_task_title}",
                    "body": str(_done_reason),
                    "url": f"/tasks/{task_id}",
                },
                kind="web_push",
                event_kind="task_done",
                session=session,
            )

        # Task failed hook — fires when process_status transitions to 6.
        elif (
            "process_status" in updates
            and _resolved_ps_for_done == TaskStatus.CANCELLED
            and _pre_patch_process_status != TaskStatus.CANCELLED
        ):
            _fail_reason = _notify_status_change_reason or "Failed"
            await _push_deliver(
                task_id=task_id,
                payload={
                    "title": f"Task failed: {_notify_task_title}",
                    "body": str(_fail_reason),
                    "url": f"/tasks/{task_id}",
                },
                kind="web_push",
                event_kind="task_failed",
                session=session,
            )
    except Exception:  # noqa: BLE001 — defensive: push hook failure never crashes PATCH
        logger.exception(
            "955.B push hook failed on task_id=%d; PATCH stands",
            task_id,
        )

    await session.refresh(task)

    # Kanban #1450: fire ntfy push when interaction_kind transitions INTO HITL
    # state via PATCH.  Idempotency rule: only fire when `interaction_kind` is
    # IN the patch body (i.e., the caller is explicitly setting the value —
    # not just patching an unrelated field on an already-HITL task).
    # Pre-PATCH value must NOT already be question/decision (transition-in guard).
    # Soft-fail: push error does NOT block the 200 response.
    if (
        "interaction_kind" in updates
        and _resolved_interaction_kind_for_done in (
            TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
        )
        and _pre_patch_interaction_kind not in (
            TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION
        )
    ):
        _fire_hitl_push(task_id, _notify_task_title or "", _notify_question_payload)

    return task


@router.post(
    "/{task_id}/resolve-flag",
    response_model=ResolveFlagResponse,
    status_code=http_status.HTTP_200_OK,
)
async def resolve_flag_endpoint(
    task_id: int,
    payload: ResolveFlagRequest,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> ResolveFlagResponse:
    """Atomic resolve handler for an GOV3 audit flag (Kanban #1211 D4).

    Body shape: `{action, adjustments?}` — action is one of
    'continue' / 'adjust_continue' / 'keep_paused' / 'terminate'.
    adjustments is required (and non-empty) only for 'adjust_continue';
    only allowlisted keys are applied
    (services/pause_switch.ADJUST_CONTINUE_ALLOWED_KEYS).

    Single-transaction atomicity: flag-DONE + side effects commit together.
    'terminate' splits across two commits (kill_project commits independently);
    no rollback risk in the second commit (single column flip on the flag).

    Status codes:
    - 200 — resolve applied (returns shape varies by branch — see ResolveFlagResponse).
    - 400 — cross-project header mismatch.
    - 404 — flag task not found / soft-deleted.
    - 422 — action invalid OR flag is not an GOV3 audit flag OR
            adjust_continue with empty/non-allowlisted adjustments.

    `X-Actor` (default 'operator') stamps `projects_audit.actor` on any
    audit rows the service writes; truncated at 200 chars (GOV1 P1-4 precedent).
    """
    from src.services.pause_switch import resolve_flag

    actor = (x_actor or "operator").strip()[:200] or "operator"

    # Pre-fetch the flag to assert cross-project session-header parity.
    # 404 here matches the service's behavior; we'd rather fail at the
    # header check than leak project information via 422 from the service.
    flag = await session.get(Task, task_id)
    if flag is None or flag.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=404,
            detail=f"Flag task id={task_id} not found",
        )
    assert_task_belongs_to_session(task_id, flag.project_id, session_project_id)

    result = await resolve_flag(
        flag_id=task_id,
        action=payload.action,
        adjustments=payload.adjustments,
        actor=actor,
        session=session,
    )
    return ResolveFlagResponse(**result)


@router.post(
    "/{task_id}/confirm-template-auto-run",
    status_code=http_status.HTTP_200_OK,
)
async def confirm_template_auto_run(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Kanban #1122 (L15 prevention): stamp the per-template auto-headless
    confirmation timestamp.

    Idempotent — re-POSTing on an already-confirmed template overwrites the
    timestamp with `now()` (intentionally; a re-confirm signals the operator
    has re-reviewed the template). Returns
    `{"task_id": int, "confirmed_at": ISO8601}`.

    Errors:
    - 404 if task not found or soft-deleted.
    - 400 on cross-project header mismatch.
    - 422 if the task is not a template (`is_template=false`).

    NOTE: does NOT require the task to already be `run_mode='auto_headless'`.
    Operators can pre-confirm a template (run_mode='auto_pickup') before
    flipping it to auto_headless — the resolved-final check on PATCH enforces
    the actual cross-column rule.
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    if task.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=404, detail=f"Task id={task_id} not found"
        )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    if not task.is_template:
        raise HTTPException(
            status_code=422,
            detail=(
                "template_auto_run_confirmed_at only meaningful for templates "
                "(is_template=true)"
            ),
        )

    # Use a Python-side UTC datetime so the return value is a real datetime
    # (not a SQL func expression) — easier to consume on the wire + in tests.
    now = datetime.now(timezone.utc)
    task.template_auto_run_confirmed_at = now
    task.updated_at = func.now()
    await session.commit()
    await session.refresh(task)
    return {
        "task_id": task_id,
        "confirmed_at": task.template_auto_run_confirmed_at,
    }


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
    if child is None:
        # L21 (#1125): cap reached — template was halted in-place by fire_template
        # (process_status flipped to BLOCKED, halt_reason set). Surface 409
        # Conflict (not 400) since the request was syntactically valid but
        # the resource state forbids the action.
        raise HTTPException(
            status_code=409,
            detail=_DETAIL_FIRE_NOW_MAX_CHILDREN_TEMPLATE.format(task_id=task_id),
        )
    return child


def _extract_option_ids(question_payload: dict | None) -> list[str]:
    """Extract the list of valid option IDs from a question_payload.

    Supports both shapes:
      - legacy: `options: list[str]` — each string IS the option id
      - new (#1007): `options: list[{id, label, ...}]` — `id` is the option id

    Returns [] when payload is None / has no options / options is empty.
    """
    if not question_payload:
        return []
    options = question_payload.get("options") or []
    ids: list[str] = []
    for opt in options:
        if isinstance(opt, str):
            ids.append(opt)
        elif isinstance(opt, dict) and "id" in opt:
            ids.append(opt["id"])
    return ids


@router.post(
    "/{task_id}/decide",
    status_code=http_status.HTTP_200_OK,
)
@limiter.limit("10/minute")
async def decide_task(
    request: Request,  # required by slowapi key_func
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/tasks/{id}/decide — dual-contract endpoint.

    Two distinct callers, discriminated by request-body shape:

    1. Kanban #1007 (DecisionRequest body — `{chosen_id, rationale?, chosen_by?}`)
       — the Inbox/DecisionInteractionView FE component finalises a decision
       task. Mutates `question_payload` (merges chosen_id/rationale/...),
       flips `process_status=5` (DONE), stamps `completed_at`, calls
       `auto_unblock_dependents`. Returns the full TaskRead.

    2. Kanban #1452 (HitlResolveRequest body — `{action, selected_option?,
       custom_text?}`) — phone HITL push-tap flow (operator taps push,
       lands on `/approve/<task_id>`, posts here). Mutates `resume_context`
       (records action + selected_option/custom_text + decided_at +
       decided_via='phone'), clears `is_pending=false`. Does NOT flip
       process_status — Lead resumes the in-flight (ps=2) task from the
       resume_context via the row_changed SSE stream. Returns
       HitlResolveResponse (slim — task_id, process_status, resume_context,
       decided_at).

    Routing rule: body MUST validate cleanly against EXACTLY ONE schema.
    A body with `action` falls through to the HITL path; a body with
    `chosen_id` falls through to the legacy path. Ambiguous bodies (no
    discriminator field, both fields, unknown fields) → 400.

    Rate limit: 10/minute/IP (slowapi).

    Lead-resume signal (Kanban #1452 AC3): no new event type. The PATCH
    naturally fires the `notify_row_changed` PG trigger → broadcasts a
    `row_changed` event on the SSE stream (GET /api/events/stream filtered
    by project_id). Lead's session is already a subscriber. Design call:
    reuse beats invent — minimum-viable-change per Karpathy lane.

    Error codes (HITL path):
      - 404 — task not found.
      - 409 — task is not in HITL waiting state (interaction_kind in
              {question,decision} AND is_pending=true) — covers
              already-resolved, wrong-kind, never-pending.
      - 400 — invalid body (Pydantic validation; also: selected_option
              not in question_payload.options).
      - 429 — rate-limited.

    Error codes (legacy #1007 path):
      - 404 — task not found.
      - 409 — task is already DONE.
      - 422 — task is not `interaction_kind='decision'`, or `chosen_id`
              is not in the option list.
    """
    # --- Step 1: load raw body + route by shape -----------------------------
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    # Discriminator: explicit `action` key → HITL path; `chosen_id` → legacy.
    # `action` wins on a tie (the HITL path is the new locked wire contract).
    if "action" in raw:
        try:
            hitl_payload = HitlResolveRequest.model_validate(raw)
        except Exception as exc:  # Pydantic ValidationError → 400
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await _decide_hitl(task_id, hitl_payload, session_project_id, session)

    if "chosen_id" in raw:
        try:
            legacy_payload = DecisionRequest.model_validate(raw)
        except Exception as exc:  # Pydantic ValidationError → 400
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        legacy_task = await _decide_legacy_1007(
            task_id, legacy_payload, session_project_id, session
        )
        # Serialize via TaskRead so the wire contract matches the prior
        # `response_model=TaskRead`. The dual-shape handler can't carry a
        # single response_model decoration — we materialise per branch.
        return TaskRead.model_validate(legacy_task, from_attributes=True).model_dump(mode="json")

    raise HTTPException(
        status_code=400,
        detail=(
            "request body must carry either 'action' (HITL phone-tap, #1452) "
            "or 'chosen_id' (decision-task finalize, #1007)"
        ),
    )


async def _decide_hitl(
    task_id: int,
    payload: HitlResolveRequest,
    session_project_id: int,
    session: AsyncSession,
) -> HitlResolveResponse:
    """Kanban #1452 — phone HITL push-tap resolver. See `decide_task` docstring."""
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    # Guard: task must be in HITL waiting state. Single 409 covers all
    # not-resolvable cases (already-resolved, wrong-kind, never-pending) —
    # the FE re-poll on a 409 surfaces the current state to the operator.
    if task.interaction_kind not in (
        TaskInteractionKind.QUESTION,
        TaskInteractionKind.DECISION,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task id={task_id} is not awaiting HITL resolution "
                f"(interaction_kind='{task.interaction_kind}')"
            ),
        )
    if not task.is_pending:
        raise HTTPException(
            status_code=409,
            detail=f"Task id={task_id} is already resolved (is_pending=false)",
        )

    # Validate selected_option (approve/reject) against the option list.
    if payload.action in ("approve", "reject"):
        valid_ids = _extract_option_ids(task.question_payload)
        if valid_ids and payload.selected_option not in valid_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"selected_option '{payload.selected_option}' not in "
                    f"question_payload.options: {valid_ids}"
                ),
            )

    # Build the resume_context entry. PATCH semantics: merge into any
    # existing resume_context (Lead may have stored mid-task state); the
    # new keys win on conflict. decided_via='phone' marks the channel —
    # future channels (web, telegram, etc.) carry their own values.
    now_utc = datetime.now(timezone.utc)
    existing_rc: dict = task.resume_context or {}
    rc_entry: dict = {
        "action": payload.action,
        "decided_at": now_utc.isoformat(),
        "decided_via": "phone",
    }
    if payload.action in ("approve", "reject"):
        rc_entry["selected_option"] = payload.selected_option
    else:  # action == "custom"
        rc_entry["custom_text"] = payload.custom_text
    task.resume_context = {**existing_rc, **rc_entry}

    # Clear the HITL-waiting flag. process_status stays unchanged — Lead
    # resumes the in-flight task from where it halted (typically ps=2
    # IN_PROGRESS, but the gate is is_pending not the ps value).
    task.is_pending = False
    task.updated_at = func.now()

    await session.commit()
    await session.refresh(task)

    # The PATCH above fires the notify_row_changed PG trigger automatically;
    # Lead's SSE subscriber (GET /api/events/stream?project_id=N) receives
    # the row_changed event and refetches the task to read resume_context.
    # No explicit pg_notify call needed.

    return HitlResolveResponse(
        task_id=task.id,
        process_status=task.process_status,
        resume_context=task.resume_context,
        decided_at=now_utc,
    )


async def _decide_legacy_1007(
    task_id: int,
    payload: DecisionRequest,
    session_project_id: int,
    session: AsyncSession,
) -> Task:
    """Kanban #1007 (AC4) — record a human decision on a decision task.

    Atomically:
      (a) Validates `chosen_id` against `question_payload.options[].id`.
      (b) Merges `chosen_id`, `rationale`, `chosen_at=now()`, and `chosen_by`
          into `question_payload`.
      (c) Flips `process_status=5` (DONE) and stamps `completed_at`.
      (d) Calls `auto_unblock_dependents` (same as the PATCH done-flip path).
      (e) The existing `tasks_audit_trg` PG trigger captures the full row
          snapshot automatically — no separate audit plumbing needed.

    Error codes:
      - 404 — task not found.
      - 409 — task is already DONE.
      - 422 — task is not `interaction_kind='decision'`, or `chosen_id` is
              not in the option list.
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    # Guard: wrong interaction kind.
    if task.interaction_kind != TaskInteractionKind.DECISION:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Task id={task_id} is not a decision task "
                f"(interaction_kind='{task.interaction_kind}')"
            ),
        )

    # Guard: already decided.
    if task.process_status == TaskStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"Task id={task_id} is already DONE",
        )

    # Validate chosen_id against the existing option list.
    try:
        validate_decision_payload({
            **(task.question_payload or {}),
            "chosen_id": payload.chosen_id,
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Merge decision fields into question_payload.
    now_utc = datetime.now(timezone.utc)
    updated_payload = {
        **(task.question_payload or {}),
        "chosen_id": payload.chosen_id,
        "rationale": payload.rationale,
        "chosen_at": now_utc.isoformat(),
        "chosen_by": payload.chosen_by,
    }
    task.question_payload = updated_payload

    # Flip to DONE + stamp timestamps.
    task.process_status = TaskStatus.DONE
    task.completed_at = func.now()
    task.updated_at = func.now()

    await session.commit()

    # Auto-unblock any tasks blocked by this decision task (mirrors the PATCH path).
    await auto_unblock_dependents(session, task_id)
    await session.commit()

    await session.refresh(task)
    return task


@router.post(
    "/{task_id}/snooze",
    response_model=TaskRead,
    status_code=http_status.HTTP_200_OK,
)
async def snooze_task(
    task_id: int,
    payload: SnoozeRequest,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Task:
    """Kanban #1011 (AC5): snooze the HITL aging nudge for a task.

    Sets `last_nudge_at = now() + (hours - 24) * interval '1 hour'` so that
    the next eligible nudge (last_nudge_at + 24h) fires exactly `hours` from
    now.  Example: hours=4 → next eligible nudge is 4h from now.

    Request body: `{hours: int}` — default 4, range 1..168 (max 1 week).

    Returns the updated TaskRead.  404 on missing task_id.
    422 on hours out of range (Pydantic Field validation).
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    # Compute the shifted last_nudge_at: now() + (hours - 24) hours.
    # When hours=24, last_nudge_at = now() → next eligible = 24h from now.
    # When hours=4, last_nudge_at = now()-20h → next eligible = 4h from now.
    # When hours=168 (1 week), last_nudge_at = now()+144h → next eligible = 168h from now.
    now = datetime.now(timezone.utc)
    shift = timedelta(hours=payload.hours - 24)
    task.last_nudge_at = now + shift
    task.updated_at = func.now()

    await session.commit()
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
