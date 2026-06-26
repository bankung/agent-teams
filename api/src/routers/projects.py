"""HTTP routes for project registry CRUD.

Mounted at `/api/projects` from main.py. All endpoints async, async-SQLAlchemy.
After-create-side-effect: auto-scaffolds the on-disk context/projects/<name>/ folder.

Soft-delete: list endpoints default-filter `WHERE status=1`; opt-in `?include_deleted=true`
returns soft-deleted rows too. DELETE flips `status=0` (and clears `is_active` if true).
Detail endpoints return rows regardless of status (per standards/postgresql/soft-delete.md).

Session-scoped active (Kanban #694, Phase 2): the legacy "single active project"
invariant is gone. `is_active` is a free boolean — multiple rows may carry
`is_active=true` simultaneously. PATCH /api/projects/{id} no longer atomically
clears other rows; GET /api/projects/active returns 410 Gone (use
/api/projects/by-name/{name} or /api/projects?status=1 instead).
"""

from __future__ import annotations

import logging
import shutil
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi import status as http_status
from sqlalchemy import Integer, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import (  # TaskStatus.CANCELLED + TaskType.AUDIT used by stats
    ProjectTeam,
    RecordStatus,
    TaskRunMode,
    TaskStatus,
    TaskType,
)
from src.db import get_active_project_or_404, get_or_404, get_session
from src.middleware.rate_limit import _projects_post_limit, limiter
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionRun
from src.models.task import Task
from src.models.usage_event import UsageEvent
from src.schemas.project import (
    KillProjectRequest,
    KillProjectResponse,
    PauseProjectRequest,
    PauseUnpauseResponse,
    ProjectCreate,
    ProjectGrantConsent,
    ProjectRead,
    ProjectStatsActualInteractiveCost,
    ProjectStatsCostUsage,
    ProjectStatsEntry,
    ProjectStatsEstimatedCost,
    ProjectStatsRunModeBreakdown,
    ProjectUpdate,
    ProgressStatsResponse,
    ReviveProjectRequest,
    ReviveProjectResponse,
    UnpauseProjectRequest,
)
from src.services.budget_gate import reconcile_budget
from src.services.kill_switch import kill_project, revive_project
from src.services.operator_auth import OperatorDecision, require_operator_proof
from src.services.pause_switch import pause_project, unpause_project
from src.services.project_scaffold import scaffold_project_folder
from src.services.session_project import require_project_id_header
from src.services.zero_config_scaffold import (
    scaffold_orchestration,
    substitute_settings_json,
)
from src.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

# Source-text-locked 403 detail for the project kill/revive/consent gate.
_OPERATOR_PROOF_REQUIRED_MSG = (
    "operator_proof_required: project kill/revive/consent is an operator-only action"
)


def _require_operator(operator_proof: OperatorDecision) -> None:
    """Raise 403 unless the request is operator-backed.

    No-op when OPERATOR_ACTION_KEY is unset (`require_operator_proof` returns
    OPERATOR for any request while the key is absent), so these routes stay
    functional on deployments that have not activated the gate.
    """
    if operator_proof is not OperatorDecision.OPERATOR:
        raise HTTPException(status_code=403, detail=_OPERATOR_PROOF_REQUIRED_MSG)


def _substitute_settings_json(target: Path, project: Project) -> None:
    """Read, filter, write settings.json. Failure is non-fatal — DB row is source of truth (#793)."""
    settings_path = target / ".claude" / "settings.json"
    if not settings_path.exists():
        logger.warning(
            "settings.json missing at %s — scaffold may have failed earlier",
            settings_path,
        )
        return

    try:
        content = settings_path.read_bytes()
    except OSError as e:
        logger.warning("failed to read %s: %s", settings_path, e)
        return

    filtered = substitute_settings_json(
        content, project_name=project.name, project_id=project.id
    )

    # No-op write if the filter passed bytes through (unparseable JSON or no
    # permissions block) — saves a syscall and avoids a redundant mtime bump,
    # but functionally a re-write would be identical.
    if filtered == content:
        return

    try:
        settings_path.write_bytes(filtered)
    except OSError as e:
        logger.warning("failed to write %s: %s", settings_path, e)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[Project]:
    stmt = select(Project)
    if not include_deleted:
        stmt = stmt.where(Project.status == RecordStatus.ACTIVE)
    stmt = stmt.order_by(Project.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/stats", response_model=list[ProjectStatsEntry])
async def list_projects_stats(
    project_id: int | None = Query(
        default=None,
        description=(
            "If set, restrict the result to this single project (active or not — "
            "the existing `Project.status == ACTIVE` filter still applies, so a "
            "soft-deleted project id returns `[]`). When unset, returns all active "
            "projects (existing behavior unchanged)."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectStatsEntry]:
    """Batched cross-project stats — powers the dashboard (Kanban #769).

    One entry per active (`status=1`) project in `projects.created_at ASC`
    order (matches GET /api/projects). Each entry carries `counts` (one bucket
    per `tasks.process_status` 1..6, string keys), `run_mode_breakdown`
    (manual / auto_pickup / auto_headless), and `last_activity_at`
    (MAX(updated_at) of active tasks; None when project has zero active tasks).

    Optional `project_id` query param (Kanban #1289): when provided, filters the
    project list to that single id. Returns `[]` (NOT 404) when the id doesn't
    exist or is soft-deleted — "filter returns empty when no rows match" semantics,
    consistent with how `GET /api/projects?status=0` behaves.

    Cross-project read — takes NO `X-Project-Id` header (parity with `""`,
    `/active`, `/by-name/{name}`).

    Kanban #854 (2026-05-13) — CANCELLED (process_status=6) is emitted as
    `counts["6"]` for transparency, but EXCLUDED from `last_activity_at`
    (Option A: cancelled work is dead-end, parity with the soft-delete
    exclusion semantics already applied at `status=0`). The
    `run_mode_breakdown` continues to count every active task regardless of
    process_status — it tells the user how their project's work is
    distributed across execution modes, not which tasks are still alive.

    Query strategy (five-query stitch): one SELECT for the project list,
    one SELECT against `tasks` GROUP BY (project_id, process_status, run_mode)
    with `MAX(updated_at)` aggregate, one SELECT against `session_runs`
    JOIN `sessions` GROUP BY project_id summing cost/token totals (Kanban
    #871), one SELECT against `tasks` GROUP BY project_id summing
    `estimated_cost_usd / estimated_input_tokens / estimated_output_tokens`
    (G1 — non-cancelled tasks with non-null estimated_cost_usd), and one
    SELECT against `usage_events` GROUP BY project_id summing real interactive
    cost/token totals (#2735 — Mode A hook-capture ledger). Soft-deleted
    tasks (`status=0`) and soft-deleted projects excluded at SQL;
    `session_runs` / `sessions` / `usage_events` carry no soft-delete column
    (per db-schema.md: NO audit trigger on those tables) so no filter is needed
    on the cost joins. Python loop stitches the buckets onto the project
    rows. No N+1: exactly five queries regardless of project count.
    """
    # Query 1 — project list in canonical order.
    projects_stmt = (
        select(Project)
        .where(Project.status == RecordStatus.ACTIVE)
        .order_by(Project.created_at.asc(), Project.id.asc())
    )
    # Kanban #1289 — optional single-project filter. Applied AFTER the
    # `status == ACTIVE` filter so soft-deleted ids yield [] (not a 404).
    if project_id is not None:
        projects_stmt = projects_stmt.where(Project.id == project_id)
    projects = list((await session.execute(projects_stmt)).scalars().all())

    # Query 2 — GROUP BY aggregate across active tasks of active projects (join keeps SQL stable when projects is empty)
    agg_stmt = (
        select(
            Task.project_id,
            Task.process_status,
            Task.run_mode,
            func.count().label("n"),
            func.max(Task.updated_at).label("max_updated_at"),
        )
        .join(Project, Project.id == Task.project_id)
        .where(
            Project.status == RecordStatus.ACTIVE,
            Task.status == RecordStatus.ACTIVE,
        )
        .group_by(Task.project_id, Task.process_status, Task.run_mode)
    )
    # Kanban #1289 — mirror the project_id filter on Queries 2 and 3 so the
    # stitch loop only sees rows for the projects that landed in `by_id`.
    if project_id is not None:
        agg_stmt = agg_stmt.where(Task.project_id == project_id)
    agg_rows = (await session.execute(agg_stmt)).all()

    # Query 3 (#871) — per-project cost/token aggregate via session_runs → sessions → projects (GROUP BY session.project_id; task_id is nullable ON DELETE SET NULL)
    cost_stmt = (
        select(
            SessionModel.project_id,
            func.coalesce(func.sum(SessionRun.total_input_tokens), 0).label(
                "sum_input_tokens"
            ),
            func.coalesce(func.sum(SessionRun.total_output_tokens), 0).label(
                "sum_output_tokens"
            ),
            func.coalesce(func.sum(SessionRun.total_context_chars), 0).label(
                "sum_context_chars"
            ),
            func.coalesce(func.sum(SessionRun.total_cost_usd), 0).label(
                "sum_cost_usd"
            ),
            func.sum(
                func.cast(SessionRun.budget_warning, Integer)
            ).label("budget_warning_count"),
            func.count(SessionRun.id).label("session_run_count"),
        )
        .join(SessionModel, SessionModel.id == SessionRun.session_id)
        .join(Project, Project.id == SessionModel.project_id)
        .where(Project.status == RecordStatus.ACTIVE)
        .group_by(SessionModel.project_id)
    )
    # Kanban #1289 — filter cost query to the single project when set.
    if project_id is not None:
        cost_stmt = cost_stmt.where(SessionModel.project_id == project_id)
    cost_rows = (await session.execute(cost_stmt)).all()

    # Query 4 (G1) — per-project heuristic cost aggregate from tasks.
    # Filter: active tasks (status=1), non-cancelled (process_status != 6),
    # and estimated_cost_usd IS NOT NULL (only DONE-flip rows have estimates).
    # COALESCE handles NULLs in the token columns (estimated_* may be NULL
    # independently of estimated_cost_usd if the estimator partially failed).
    est_cost_stmt = (
        select(
            Task.project_id,
            func.coalesce(func.sum(Task.estimated_cost_usd), 0).label(
                "sum_estimated_cost_usd"
            ),
            func.coalesce(func.sum(Task.estimated_input_tokens), 0).label(
                "sum_estimated_input_tokens"
            ),
            func.coalesce(func.sum(Task.estimated_output_tokens), 0).label(
                "sum_estimated_output_tokens"
            ),
        )
        .join(Project, Project.id == Task.project_id)
        .where(
            Project.status == RecordStatus.ACTIVE,
            Task.status == RecordStatus.ACTIVE,
            Task.process_status != TaskStatus.CANCELLED,
            Task.estimated_cost_usd.is_not(None),
        )
        .group_by(Task.project_id)
    )
    if project_id is not None:
        est_cost_stmt = est_cost_stmt.where(Task.project_id == project_id)
    est_cost_rows = (await session.execute(est_cost_stmt)).all()

    # Query 5 (#2735) — per-project real interactive cost aggregate from usage_events.
    # usage_events is append-only with NO soft-delete column; the Project JOIN scopes to
    # active projects only. Rows with project_id IS NULL drop out of the inner join
    # (correct — unattributable events are not per-project). Direct project_id filter
    # (no session hop needed — unlike session_runs).
    # shortcut: SUM over usage_events GROUP BY project_id, ix_usage_events_project_id
    # index exists (model __table_args__) — hash-agg on the index, fine at any scale;
    # upgrade: no action needed (already indexed).
    actual_cost_stmt = (
        select(
            UsageEvent.project_id,
            func.coalesce(func.sum(UsageEvent.cost_usd), 0).label("sum_cost_usd"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label(
                "sum_input_tokens"
            ),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label(
                "sum_output_tokens"
            ),
        )
        .join(Project, Project.id == UsageEvent.project_id)
        .where(Project.status == RecordStatus.ACTIVE)
        .group_by(UsageEvent.project_id)
    )
    if project_id is not None:
        actual_cost_stmt = actual_cost_stmt.where(
            UsageEvent.project_id == project_id
        )
    actual_cost_rows = (await session.execute(actual_cost_stmt)).all()

    # Stitch: per-project all-zero buckets; fold agg_rows + cost_rows + est_cost_rows + actual_cost_rows
    by_id: dict[int, dict] = {
        p.id: {
            "counts": {str(code): 0 for code in TaskStatus.ALL},
            "run_mode_breakdown": {mode: 0 for mode in TaskRunMode.ALL},
            "last_activity_at": None,
            # #871 — zero-filled default; parity with always-emit-all-keys contract
            "cost_usage": {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_context_chars": 0,
                "total_cost_usd": Decimal("0"),
                "budget_warning_count": 0,
                "session_run_count": 0,
            },
            # G1 — zero-filled default; mirrors cost_usage "always-emit-all-keys" contract
            "estimated_cost": {
                "total_cost_usd": Decimal("0"),
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            },
            # #2735 — zero-filled default; mirrors estimated_cost "always-emit-all-keys" contract
            "actual_interactive_cost": {
                "total_cost_usd": Decimal("0"),
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            },
        }
        for p in projects
    }
    for project_id, process_status, run_mode, n, max_updated_at in agg_rows:
        bucket = by_id.get(project_id)
        if bucket is None:
            continue
        bucket["counts"][str(process_status)] += n
        # run_mode is constrained by DB CHECK to TaskRunMode.ALL, but be
        # defensive — an unknown value would KeyError; route the unknown
        # bucket to nothing visible rather than 500 the whole endpoint.
        if run_mode in bucket["run_mode_breakdown"]:
            bucket["run_mode_breakdown"][run_mode] += n
        # Kanban #854: exclude CANCELLED (process_status=6) rows from
        # last_activity_at — parity with the soft-delete exclusion (status=0
        # already filtered at the SQL level above). Cancelled work is
        # dead-end; its updated_at bump on the cancellation flip MUST NOT
        # leak into "last activity" or the FE displays a misleading
        # freshness signal. Counts and run_mode_breakdown still include the
        # row (visibility into how the project's work distributes).
        if process_status == TaskStatus.CANCELLED:
            continue
        cur = bucket["last_activity_at"]
        if max_updated_at is not None and (cur is None or max_updated_at > cur):
            bucket["last_activity_at"] = max_updated_at

    # Fold the cost/token aggregate. project_id from this query MAY be absent
    # from by_id only if a race deletes the project between Query 1 and Query 3;
    # be defensive (skip silently — the DELETE flips status=0 which the join's
    # WHERE clause already filters, so this is paranoia-tier).
    for (
        project_id,
        sum_input_tokens,
        sum_output_tokens,
        sum_context_chars,
        sum_cost_usd,
        budget_warning_count,
        session_run_count,
    ) in cost_rows:
        bucket = by_id.get(project_id)
        if bucket is None:
            continue
        cu = bucket["cost_usage"]
        cu["total_input_tokens"] = int(sum_input_tokens)
        cu["total_output_tokens"] = int(sum_output_tokens)
        cu["total_context_chars"] = int(sum_context_chars)
        # SQL-side COALESCE(SUM(...), 0) on Numeric column → Decimal, never None.
        cu["total_cost_usd"] = sum_cost_usd
        cu["budget_warning_count"] = int(budget_warning_count)
        cu["session_run_count"] = int(session_run_count)

    # Fold the G1 estimated cost aggregate (paranoia-tier skip mirrors cost_rows fold above).
    for (
        project_id,
        sum_estimated_cost_usd,
        sum_estimated_input_tokens,
        sum_estimated_output_tokens,
    ) in est_cost_rows:
        bucket = by_id.get(project_id)
        if bucket is None:
            continue
        ec = bucket["estimated_cost"]
        ec["total_cost_usd"] = sum_estimated_cost_usd
        ec["total_input_tokens"] = int(sum_estimated_input_tokens)
        ec["total_output_tokens"] = int(sum_estimated_output_tokens)

    # Fold the #2735 actual interactive cost aggregate (paranoia-tier skip mirrors est_cost fold above).
    for (
        project_id,
        sum_cost_usd,
        sum_input_tokens,
        sum_output_tokens,
    ) in actual_cost_rows:
        bucket = by_id.get(project_id)
        if bucket is None:
            continue
        ac = bucket["actual_interactive_cost"]
        ac["total_cost_usd"] = sum_cost_usd
        ac["total_input_tokens"] = int(sum_input_tokens)
        ac["total_output_tokens"] = int(sum_output_tokens)

    return [
        ProjectStatsEntry(
            id=p.id,
            name=p.name,
            team=p.team,
            run_mode_breakdown=ProjectStatsRunModeBreakdown(
                **by_id[p.id]["run_mode_breakdown"]
            ),
            counts=by_id[p.id]["counts"],
            last_activity_at=by_id[p.id]["last_activity_at"],
            cost_usage=ProjectStatsCostUsage(**by_id[p.id]["cost_usage"]),
            estimated_cost=ProjectStatsEstimatedCost(**by_id[p.id]["estimated_cost"]),
            actual_interactive_cost=ProjectStatsActualInteractiveCost(
                **by_id[p.id]["actual_interactive_cost"]
            ),
        )
        for p in projects
    ]


# ---------------------------------------------------------------------------
# Kanban #1292 — GET /api/projects/{id}/progress-stats (burndown + velocity)
# ---------------------------------------------------------------------------


def _bucket_starts(window_start: date, today: date, bucket: str) -> list[date]:
    """Walk [window_start, today] in buckets, returning each bucket's start date.

    Week buckets use ISO weeks (Monday start) — the first bucket start is
    snapped back to the Monday on/before `window_start` so a partial first week
    still anchors on its real Monday. Day buckets start at `window_start`.
    Always emits at least one bucket; the final bucket may extend past `today`.
    """
    starts: list[date] = []
    if bucket == "week":
        cur = window_start - timedelta(days=window_start.weekday())  # back to Monday
        step = timedelta(days=7)
    else:  # "day"
        cur = window_start
        step = timedelta(days=1)
    while cur <= today:
        starts.append(cur)
        cur = cur + step
    if not starts:  # window_start > today is impossible (days >= 1), but be safe
        starts.append(window_start)
    return starts


@router.get("/{project_id}/progress-stats", response_model=ProgressStatsResponse)
async def get_project_progress_stats(
    project_id: int,
    session_project_id: int = Depends(require_project_id_header),
    bucket: str = Query(default="week"),
    days: int = Query(default=90, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> ProgressStatsResponse:
    """Burndown + velocity series for one project (Kanban #1292).

    Mirrors the auth gate of the sibling `GET /api/projects/{id}/pl`: requires
    `X-Project-Id` (400 if missing); the header MUST equal the path id (404 on
    mismatch — the project is "invisible" from the bound session). 404 also on
    a missing / soft-deleted project (active-only via `get_active_project_or_404`).

    Query strategy mirrors `GET /stats`: ONE SELECT of the project's active
    task rows (`created_at, completed_at, process_status`), then bucket in
    Python — NO per-bucket query (no N+1).

    Bucket boundaries are UTC. Week buckets use ISO weeks (Monday start);
    `t` is the bucket's start date (YYYY-MM-DD). Both series ascend by `t` and
    are zero-filled (one entry per bucket, never skipped) so the FE has a
    continuous axis.

    burndown[i].remaining = tasks still open as of the END of bucket i:
      created_at <= bucket_end AND status=1 AND process_status != 6 AND
      (completed_at IS NULL OR completed_at > bucket_end).
    velocity[i].completed = tasks completed WITHIN bucket i:
      process_status=5 AND status=1 AND completed_at in [bucket_start, bucket_end).

    v1 reads the `tasks` table only — `completed_at` (set on the DONE-flip) is
    accurate enough for velocity. Exact transition counting from
    `tasks_history` JSONB snapshots is a deferred refinement.
    """
    # 422 on bad bucket — keep the explicit check (Query() is a plain str so we
    # validate the enum here for a precise message). days range is enforced by
    # Query(ge=1, le=365) → FastAPI 422 automatically.
    if bucket not in ("day", "week"):
        raise HTTPException(
            status_code=422,
            detail=f"bucket must be 'day' or 'week' (got {bucket!r})",
        )

    # Auth gate — parity with /pl: header must match the path id.
    if project_id != session_project_id:
        raise HTTPException(
            status_code=404,
            detail=f"Project id={project_id} not found",
        )
    # 404 on missing / soft-deleted (active-only). Same detail string as /pl + /by-name.
    await get_active_project_or_404(
        session, project_id, detail=f"Project id={project_id} not found"
    )

    now = datetime.now(timezone.utc)
    today = now.date()
    window_start = today - timedelta(days=days)

    # Single SELECT of this project's ACTIVE task rows. process_status=6
    # (CANCELLED) rows are NOT excluded at SQL — the velocity filter needs
    # process_status=5 and the burndown filter excludes 6 in Python, so we
    # fetch all active rows once and bucket in memory.
    # is_template + task_type are fetched so the Python loop can exclude
    # recurring templates and audit governance tasks from both remaining and
    # completed counts (Kanban Wave A.2b fix — board hides both, burndown must
    # match).
    stmt = (
        select(
            Task.created_at,
            Task.completed_at,
            Task.process_status,
            Task.is_template,
            Task.task_type,
            Task.title,
        )
        .where(Task.project_id == project_id)
        .where(Task.status == RecordStatus.ACTIVE)
    )
    rows = (await session.execute(stmt)).all()

    starts = _bucket_starts(window_start, today, bucket)
    step = timedelta(days=7 if bucket == "week" else 1)

    burndown: list[dict] = []
    velocity: list[dict] = []
    for start in starts:
        bucket_end_date = start + step  # exclusive upper boundary (date)
        # Compare against the start-of-day UTC datetime of the boundary so a
        # task's tz-aware created_at/completed_at compares cleanly.
        bucket_start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        bucket_end_dt = datetime.combine(bucket_end_date, datetime.min.time(), tzinfo=timezone.utc)

        remaining = 0
        completed = 0
        for created_at, completed_at, process_status, is_template, task_type, title in rows:
            # Mirrors the board's isScheduledNoise + audit filter so remaining
            # == board's visible-open count. Exclude:
            #   - recurring templates (is_template)
            #   - audit governance tasks (task_type == AUDIT)
            #   - scheduled-noise tasks whose title starts with "[schedule:"
            #     (board: t.is_template || t.title.startsWith("[schedule:"))
            if is_template or task_type == TaskType.AUDIT or (title or "").startswith("[schedule:"):
                continue
            # Burndown: open as of bucket_end (exclusive boundary). Exclude CANCELLED.
            if (
                created_at <= bucket_end_dt
                and process_status != TaskStatus.CANCELLED
                and (completed_at is None or completed_at > bucket_end_dt)
            ):
                remaining += 1
            # Velocity: DONE and completed within [bucket_start, bucket_end).
            if (
                process_status == TaskStatus.DONE
                and completed_at is not None
                and bucket_start_dt <= completed_at < bucket_end_dt
            ):
                completed += 1

        t = start.isoformat()
        burndown.append({"t": t, "remaining": remaining})
        velocity.append({"t": t, "completed": completed})

    return ProgressStatsResponse(
        project_id=project_id,
        bucket=bucket,
        window_days=days,
        burndown=burndown,
        velocity=velocity,
        generated_at=now,
    )


@router.get(
    "/active",
    responses={
        410: {
            "description": (
                "Endpoint deprecated. Use /api/projects/by-name/{name} or "
                "/api/projects?status=1 instead."
            )
        },
    },
)
async def get_active_project() -> Response:
    """Deprecated by Kanban #694 (Phase 2 of session-scoped active project shift).

    The legacy "single active project" invariant is gone — each Claude Code
    session binds to a project by name at bootstrap, and multiple rows may
    carry `is_active=true` simultaneously. Returns 410 Gone with a stable
    detail string pointing callers at the replacement endpoints.

    Detail string source-text-locked per the #122 pattern by
    `test_get_active_project_410_detail_pinned_in_router_source` —
    keep in sync.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Endpoint deprecated. Use /api/projects/by-name/{name} or "
            "/api/projects?status=1 instead."
        ),
    )


@router.get("/by-name/{name}", response_model=ProjectRead)
async def get_project_by_name(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> Project:
    # By-name lookup is used by Lead bootstrap and external integrations — they
    # only ever care about active projects. Soft-deleted projects are invisible
    # by name (the partial unique index allows a new project to claim the name).
    return await get_or_404(
        session,
        Project,
        detail=f"Project {name!r} not found",
        name=name,
        status=RecordStatus.ACTIVE,
    )


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project_by_id(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> Project:
    # By-id lookup parity with /by-name/{name} — FE V3 project switcher + external
    # integrations only ever want active rows. Soft-deleted projects 404 by id
    # too; restore is a future admin path. Detail string matches grant-consent
    # / PATCH / DELETE byte-for-byte (source-text-locked, Kanban #691).
    return await get_or_404(
        session,
        Project,
        detail=f"Project id={project_id} not found",
        id=project_id,
        status=RecordStatus.ACTIVE,
    )


@router.post("", response_model=ProjectRead, status_code=http_status.HTTP_201_CREATED)
@limiter.limit(_projects_post_limit)
async def create_project(
    request: Request,  # required by slowapi key_func — not used in handler body
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> Project:
    # Kanban #1620 — validate team against the constants.py registry (the
    # single source of truth now that the DB CHECK is dropped). The TeamCode
    # Literal already 422s at the Pydantic boundary, but this explicit gate
    # gives a precise message and is the load-bearing validation if TeamCode is
    # ever loosened. Before #1620 an unknown team fell through to the
    # IntegrityError handler and returned a WRONG 409 name-conflict message.
    if payload.team not in ProjectTeam.ALL:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown team {payload.team!r}; valid: {sorted(ProjectTeam.ALL)}",
        )

    config = dict(payload.config or {})
    if payload.standards is not None:
        config["standards"] = payload.standards.model_dump()
    data = {
        "name": payload.name,
        "description": payload.description,
        "paths_web": payload.paths.web,
        "paths_api": payload.paths.api,
        "paths_db": payload.paths.db,
        "stack_web": payload.stack.web,
        "stack_api": payload.stack.api,
        "stack_db": payload.stack.db,
        "config": config,
        "is_active": payload.is_active,
        "team": payload.team,
        # Kanban #777: pass-through for the two text fields (None is fine — DB column
        # is nullable). For agent_overrides, OMIT the key when None so the ORM's
        # Python-side `default=dict` fires (DB server_default '{}'::jsonb is the safety
        # net). Without this branch, Project(agent_overrides=None) would explicitly
        # INSERT NULL, bypassing both defaults.
        "working_path": payload.working_path,
        "working_repo": payload.working_repo,
        # Kanban #2300 — per-project effort lever. Plain nullable TEXT, no DB
        # server_default, so passing None lands SQL NULL (= global default off).
        "effort_mode": payload.effort_mode,
        # Kanban #1304 AC5 — plain nullable NUMERIC(10,2). ProjectCreate defaults to
        # Decimal("1.00") so omitting the field from the request lands $1 (gates);
        # an explicit null from the client opts out (no modal). Mirror effort_mode
        # passthrough exactly.
        "cost_forecast_threshold_usd": payload.cost_forecast_threshold_usd,
    }
    if payload.agent_overrides is not None:
        data["agent_overrides"] = payload.agent_overrides

    # #778 — OMIT when None (ORM default=list); model_dump(exclude_none=True) strips null label/kind
    if payload.sources is not None:
        data["sources"] = [
            entry.model_dump(exclude_none=True) for entry in payload.sources
        ]

    # Kanban #979 — OMIT when None so the DB server_default (locked default
    # JSON from migration 0027) fires. An explicit ToolsConfig from the
    # client REPLACES the default; we model_dump() it to a plain dict for
    # JSONB persistence. Pydantic already validated disjoint-tiers + Literal
    # tier strings by this point — server side is just shuttling bytes.
    if payload.tools_config is not None:
        data["tools_config"] = payload.tools_config.model_dump()

    # Kanban #1840 — full-auto decision-policy override. OMIT when None so the
    # DB column lands NULL (= "no policy"; the full-auto Lead uses the hardcoded
    # top-5 matrix). An explicit AutoDecisionPolicy is model_dump()'d to a plain
    # dict for JSONB persistence — exclude_none so partial policies don't store
    # null-valued keys (an unset reviewer_nit etc. stays absent, NOT JSON null).
    # The typed boundary validator (extra="forbid" + per-field Literals) already
    # fired by this point. Unlike approval_policies, the POST path DOES persist
    # this — required for the #1840 POST→GET round-trip AC.
    if payload.auto_decision_policy is not None:
        data["auto_decision_policy"] = payload.auto_decision_policy.model_dump(
            exclude_none=True
        )

    # Kanban #1224 — push-notification targets. OMIT when None so the DB
    # column lands NULL (= "no default configured"; router falls back to
    # local-file write). model_dump() each NotificationTarget to a plain
    # dict for JSONB persistence; the API boundary validator already enforced
    # kind/priority/chat_id/label shape.
    if payload.notification_targets is not None:
        data["notification_targets"] = [
            t.model_dump() for t in payload.notification_targets
        ]

    # Kanban #1800 / #1652 — Mode-B Phase-1 host-binary requirements. OMIT when
    # None so the DB column lands NULL (= "no host-binary requirements"; worker
    # gate skips). Mirrors notification_targets exactly. Each name was already
    # validated against `_BINARY_NAME_RE` at the Pydantic boundary; the list is
    # plain str entries so no model_dump is needed.
    if payload.required_binaries is not None:
        data["required_binaries"] = payload.required_binaries

    # Kanban #694, Phase 2: `is_active` is a free boolean — no atomic-clear of
    # other rows. The legacy `_clear_other_active(keep_id=None)` here was
    # load-bearing on the dropped `ux_projects_active_one` invariant.
    project = Project(**data)
    session.add(project)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Project name {payload.name!r} already exists",
        ) from exc
    await session.refresh(project)

    # Side-effect: scaffold context/projects/<name>/ ONLY for in-repo
    # (working_path=null) projects. working_path projects are scaffolded
    # host-side by bin/agent-teams-init.ps1 (#796) because the API container
    # cannot reach host paths (#795); calling scaffold_project_folder for them
    # just creates orphan dirs in the agent-teams repo (Kanban #1618 —
    # papillon-pod #621 split-brain regression).
    settings = get_settings()
    if not project.working_path:
        scaffold_project_folder(settings.repo_root, project.name, team=project.team)

    # Kanban #793 — second scaffold step: if the project declared a
    # working_path AND that directory already exists, copy the agent-teams
    # orchestration harness (CLAUDE.md, .claude/, context/standards/,
    # context/teams/<team>/) into it. The DB row is the source of truth;
    # any failure below is logged + swallowed so 201 still flies.
    #
    # We explicitly skip when `not target.exists()` — the underlying
    # scaffolder would `mkdir(parents=True, exist_ok=True)`, but we DON'T
    # want POST /api/projects auto-creating filesystem dirs the user didn't
    # ask for. Users create their working_path themselves; we only fill it.
    if project.working_path:
        target = Path(project.working_path)
        # `.exists()` on a pathologically-long path raises OSError
        # (ENAMETOOLONG, ENOENT on bad components, etc). Treat any such
        # filesystem stat failure as "skip the scaffold" — the DB row is
        # still the source of truth.
        try:
            target_is_dir = target.exists() and target.is_dir()
        except OSError as e:
            logger.warning(
                "stat failed on working_path %r: %s — skipping scaffold",
                project.working_path,
                e,
            )
            target_is_dir = False

        if target_is_dir:
            try:
                report = scaffold_orchestration(
                    target_path=target,
                    project_name=project.name,
                    team=project.team,
                    agent_teams_root=settings.repo_root,
                )
                _substitute_settings_json(target, project)
                logger.info(
                    "scaffolded orchestration for %s at %s: "
                    "%d copied, %d skipped, %d errors",
                    project.name,
                    target,
                    len(report.copied),
                    len(report.skipped),
                    len(report.errors),
                )
            except ValueError as e:
                # Path-traversal guard (target is/under agent_teams_root).
                logger.warning("scaffold rejected: %s", e)
            except Exception:  # pragma: no cover — defensive
                logger.exception("scaffold failed for %s", project.name)
        else:
            logger.warning(
                "working_path %r not a dir or missing, skipping scaffold",
                project.working_path,
            )

    return project


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = await get_or_404(
        session, Project, detail=f"Project id={project_id} not found", id=project_id
    )

    updates = payload.model_dump(exclude_unset=True)

    # Kanban #1620 — validate team on PATCH too (only when the key is present).
    # Same registry gate + precise 422 as create_project; fixes the wrong-409
    # IntegrityError mistranslation that the old path produced for unknown teams.
    if "team" in updates and updates["team"] is None:
        raise HTTPException(status_code=422, detail="team cannot be set to null")
    if "team" in updates and updates["team"] not in ProjectTeam.ALL:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown team {updates['team']!r}; valid: {sorted(ProjectTeam.ALL)}",
        )

    # Kanban #777 WARN-1: PATCH explicit-null on agent_overrides means "clear to
    # empty dict", NOT "write SQL NULL". The server_default '{}'::jsonb fires only
    # on INSERT, so without this transform a null-PATCH would land JSONB scalar
    # 'null' in the column (Pydantic surfaces it as None on read). Locked by
    # test_patch_project_agent_overrides_null_clears_to_empty_dict.
    if "agent_overrides" in updates and updates["agent_overrides"] is None:
        updates["agent_overrides"] = {}

    # Kanban #778: PATCH explicit-null on sources means "clear to empty list"
    # (parity with agent_overrides WARN-1 Option A). DB column IS nullable so a
    # SQL NULL would not 500 — but the ProjectRead wire contract is
    # always-a-list, so normalize here to keep response shape consistent. When
    # present, `model_dump(exclude_unset=True)` has already serialized each
    # `SourceEntry` to a plain dict; strip None-valued optional keys
    # (`label`/`kind`) so they don't persist as `null` in JSONB (parity with the
    # POST path's `exclude_none=True` model_dump).
    if "sources" in updates:
        if updates["sources"] is None:
            updates["sources"] = []
        else:
            updates["sources"] = [
                {k: v for k, v in entry.items() if v is not None}
                for entry in updates["sources"]
            ]

    # Kanban #1224: PATCH explicit-null on notification_targets CLEARS to NULL
    # (= "no default configured"; router falls back to local-file write). The
    # DB column IS nullable, and ProjectRead surfaces None as null on the
    # wire — unlike `sources`, we do NOT coerce to []. The "no default" state
    # is semantically distinct from "[] configured" (which has zero priority
    # targets but still triggers the kind filter). `model_dump(exclude_unset=True)`
    # has already serialized each NotificationTarget to a plain dict.

    # Kanban #1800 / #1652: `required_binaries` needs NO special branch here —
    # it mirrors notification_targets' null-stays-null semantics. The DB column
    # is nullable; an explicit-null PATCH surfaces as `None` in
    # `model_dump(exclude_unset=True)` and the generic setattr loop below writes
    # SQL NULL (= "no host-binary requirements"; worker gate skips). Key-absent
    # is dropped by exclude_unset, leaving the column unchanged. An explicit list
    # REPLACES the prior value (no merge). The strict `_BINARY_NAME_RE` validator
    # already fired at the Pydantic boundary.

    # Kanban #1840: PATCH explicit-null on auto_decision_policy CLEARS to NULL
    # (= "no policy"; full-auto Lead falls back to the hardcoded matrix). The DB
    # column IS nullable — null-stays-null, like notification_targets, NOT
    # coerced to {}. When present + non-null, `model_dump(exclude_unset=True)`
    # has already serialized the nested AutoDecisionPolicy to a plain dict, but
    # it does NOT apply exclude_none to that nested model — so unset Literal
    # knobs would persist as JSON `null`. Strip them for POST/PATCH parity
    # (mirrors the `sources` None-key strip above) so a partial PATCH stores
    # only the keys the operator set.
    if updates.get("auto_decision_policy") is not None:
        updates["auto_decision_policy"] = {
            k: v
            for k, v in updates["auto_decision_policy"].items()
            if v is not None
        }

    # M10: cannot reactivate a soft-deleted project via PATCH — restore is a
    # separate (not-yet-built) admin path. Other fields ARE editable on a
    # soft-deleted row (admin edit / metadata correction).
    if updates.get("is_active") is True and project.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=400,
            detail="Cannot activate a soft-deleted project — restore first",
        )

    # #694 Phase 2 — no atomic-clear of other is_active rows

    # Skip writes where the new value equals the existing one — keeps PATCHes
    # that touch only some fields from bumping `updated_at` (and from writing
    # redundant rows once an audit table lands). SQL clause elements bypass the
    # equality check — comparing a ClauseElement with `!=` returns a SQL
    # BinaryExpression (not a bool), so the isinstance guard exists to keep the
    # no-op detector from crashing on dynamic SQL values. Mirrors tasks.py.
    changed = False
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(project, field) != value:
            setattr(project, field, value)
            changed = True

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    if changed:
        project.updated_at = func.now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Mirror create_project — surface a stable detail instead of leaking PG internals.
        orig_text = str(exc.orig)
        if "ux_projects_name_active" in orig_text:
            detail = f"Project name {updates['name']!r} already exists"
        else:
            detail = "Project update conflicts with an existing row"
        raise HTTPException(status_code=409, detail=detail) from exc

    await session.refresh(project)
    return project


@router.post("/{project_id}/grant-consent", response_model=ProjectRead)
async def grant_project_consent(
    project_id: int,
    payload: ProjectGrantConsent,
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> Project:
    """Grant per-project consent for Mode B (auto_headless) tasks (Kanban #481/#483).

    Typed-acknowledgment UX — the user must type the project name verbatim
    (case-sensitive). Idempotent: re-granting on an already-consented project
    returns 200 + the existing row WITHOUT re-stamping `auto_run_consent_at`
    or `updated_at`. The first consent is the legally-significant timestamp.

    404 on missing/soft-deleted project (active-only — `status=1`).
    400 on `confirm_name` mismatch — detail string pinned by source-text-lock
    test in test_routes_smoke.py.
    403 on operator-proof gate active without a valid X-Operator-Token.
    """
    _require_operator(operator_proof)
    project = await get_or_404(
        session,
        Project,
        detail=f"Project id={project_id} not found",
        id=project_id,
        status=RecordStatus.ACTIVE,
    )

    # Case-sensitive exact match — the friction is the point. Stable detail
    # string per #122 source-text-lock pattern.
    if payload.confirm_name != project.name:
        raise HTTPException(
            status_code=400,
            detail="confirm_name must match project name exactly",
        )

    # Idempotent re-grant: return the existing row untouched.
    if project.auto_run_consent_at is not None:
        return project

    # First grant — stamp consent + force updated_at refresh (server_default
    # only fires on INSERT; mirror the PATCH /api/projects/{id} pattern).
    project.auto_run_consent_at = func.now()
    project.updated_at = func.now()
    await session.commit()
    await session.refresh(project)
    return project


@router.post(
    "/{project_id}/kill",
    response_model=KillProjectResponse,
)
async def kill_project_endpoint(
    project_id: int,
    payload: KillProjectRequest,
    force: bool = Query(
        default=False,
        description=(
            "If true, skip the 30s grace on in-flight langgraph runs (AC#6 "
            "emergency path). v1 captures the flag into the audit row; the "
            "langgraph worker contract is where grace lives, not here."
        ),
    ),
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> KillProjectResponse:
    """Hard-pause a project (Kanban #1209, GOV1).

    Operator emergency-stop. Drains recurring tasks (suspends next_fire_at),
    marks in-flight langgraph runs for graceful checkpoint, freezes open
    TODO/BLOCKED tasks via `kill_frozen=true`, and stamps a
    `projects_audit` row with the drain counts.

    Status codes:
    - 200 — kill applied (returns drain_summary + audit_id).
    - 404 — project not found / soft-deleted.
    - 409 — project is already killed (idempotent guard).
    - 422 — `reason` missing / shorter than 10 chars.

    `X-Actor` header (default 'operator') stamps `projects_audit.actor` —
    future project-auditor will read this to disambiguate operator vs system
    kills. The header is optional; the default keeps single-operator dev
    mode (v1 scope) friction-free. Truncated at 200 chars (P1-4 audit on
    #1209) to match the hook-layer precedent — an adversarial / runaway
    caller can otherwise stuff arbitrarily long strings into the audit row.
    403 on operator-proof gate active without a valid X-Operator-Token.
    """
    _require_operator(operator_proof)
    # P1-4: cap at 200 chars; `or "operator"` after the slice handles a
    # purely-whitespace header where .strip() returns empty.
    actor = (x_actor or "operator").strip()[:200] or "operator"
    result = await kill_project(
        project_id=project_id,
        reason=payload.reason,
        force=force,
        actor=actor,
        session=session,
    )
    return KillProjectResponse(**result)


@router.post(
    "/{project_id}/revive",
    response_model=ReviveProjectResponse,
)
async def revive_project_endpoint(
    project_id: int,
    payload: ReviveProjectRequest,  # noqa: ARG001 — schema present for OpenAPI + forward-compat
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> ReviveProjectResponse:
    """Inverse of /kill — restore a project to runnable state (Kanban #1209).

    Clears `is_killed=false` (PRESERVING `killed_at` + `killed_reason` as
    history per D4), recomputes `next_fire_at` for recurring tasks (unless
    the project was killed > REVIVE_MAX_STALENESS_DAYS — those get
    `halt_reason='revive_stale'` and require manual re-arm), and clears
    every `kill_frozen=true` marker.

    Status codes:
    - 200 — revive applied (returns drain_summary + audit_id).
    - 404 — project not found / soft-deleted.
    - 409 — project is NOT currently killed (idempotent guard).

    `X-Actor` truncated at 200 chars (P1-4 audit on #1209) for the same
    reason as the kill endpoint.
    403 on operator-proof gate active without a valid X-Operator-Token.
    """
    _require_operator(operator_proof)
    # P1-4: cap at 200 chars; `or "operator"` after the slice handles a
    # purely-whitespace header where .strip() returns empty.
    actor = (x_actor or "operator").strip()[:200] or "operator"
    result = await revive_project(
        project_id=project_id,
        actor=actor,
        session=session,
    )
    return ReviveProjectResponse(**result)


@router.post(
    "/{project_id}/pause",
    response_model=PauseUnpauseResponse,
)
async def pause_project_endpoint(
    project_id: int,
    payload: PauseProjectRequest,
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    session: AsyncSession = Depends(get_session),
) -> PauseUnpauseResponse:
    """Soft-pause a project (Kanban #1211, GOV3 D3).

    Operator/system soft suspend. Drains recurring tasks (non-templates →
    next_fire_at=NULL; templates → kill_frozen=true) but lets in-flight
    work complete naturally and leaves open TODOs untouched (the resolve-
    flag escape hatch is the mechanism for clearing them). Writes a
    `projects_audit` row with action='pause' + the drain counts.

    Status codes:
    - 200 — pause applied (returns drain_summary + audit_id).
    - 404 — project not found / soft-deleted.
    - 409 — project is already paused OR project is currently killed
            (mutex via app-layer check + DB CHECK ck_projects_kill_pause_mutex).
    - 422 — `reason` missing / shorter than 10 chars.

    `X-Actor` header (default 'operator') stamps `projects_audit.actor`,
    truncated at 200 chars (mirrors the GOV1 P1-4 precedent).
    """
    actor = (x_actor or "operator").strip()[:200] or "operator"
    result = await pause_project(
        project_id=project_id,
        reason=payload.reason,
        actor=actor,
        session=session,
    )
    return PauseUnpauseResponse(**result)


@router.post(
    "/{project_id}/unpause",
    response_model=PauseUnpauseResponse,
)
async def unpause_project_endpoint(
    project_id: int,
    payload: UnpauseProjectRequest,  # noqa: ARG001 — schema for OpenAPI + forward-compat
    x_actor: str | None = Header(default=None, alias="X-Actor"),
    session: AsyncSession = Depends(get_session),
) -> PauseUnpauseResponse:
    """Inverse of /pause — restore a paused project (Kanban #1211).

    Clears `is_paused=false` (PRESERVING `paused_at` + `paused_reason` as
    history per D4), recomputes `next_fire_at` for recurring tasks, and
    clears every `kill_frozen=true` marker in the project.

    Status codes:
    - 200 — unpause applied.
    - 404 — project not found / soft-deleted.
    - 409 — project is NOT currently paused (idempotent guard).

    `X-Actor` truncated at 200 chars (mirrors GOV1 P1-4 precedent).
    """
    actor = (x_actor or "operator").strip()[:200] or "operator"
    result = await unpause_project(
        project_id=project_id,
        actor=actor,
        session=session,
    )
    return PauseUnpauseResponse(**result)


@router.post("/{project_id}/reconcile-budget")
async def reconcile_project_budget(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """On-demand budget reconciliation (Kanban #1194 AC6).

    Recomputes the project's daily spend + projected pct against the daily
    cap. No write — `budget_gate` stores nothing; the answer is derived from
    `tasks.estimated_cost_usd` + `session_runs.total_cost_usd` via the same
    `compute_spend` pipeline that powers the spawn-time gate. Callers that
    want a scheduled reconciliation should arrange to POST this on a cron;
    the scheduled cron half of #1194 is deferred.

    404 on missing / soft-deleted project. 200 with the reconciled numbers
    on success.
    """
    # Cheap pre-check so we can return the canonical 404 (the gate raises
    # ValueError, which would land as 500 without translation).
    project = await get_or_404(
        session,
        Project,
        detail=f"Project id={project_id} not found",
        id=project_id,
        status=RecordStatus.ACTIVE,
    )
    return await reconcile_budget(session, project.id)


@router.delete("/{project_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a project: flip status=0 and defensively clear is_active.

    A soft-deleted row should not advertise itself as active in any list/by-name
    query. Returns 204 No Content. Idempotent — deleting an already-deleted
    project is a no-op (still 204).
    """
    project = await get_or_404(
        session, Project, detail=f"Project id={project_id} not found", id=project_id
    )

    # Idempotent: if already soft-deleted, skip the no-op UPDATE so we don't
    # write a redundant audit row. The is_active clear is also unnecessary —
    # an already-deleted row should not be active, but be defensive and skip.
    if project.status == RecordStatus.DELETED:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    project.status = RecordStatus.DELETED
    if project.is_active:
        # Defensive cleanup: same transaction keeps the row consistent for any
        # concurrent reader (no window where status=0 AND is_active=true).
        project.is_active = False

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    project.updated_at = func.now()

    await session.commit()

    # Kanban #1124 (2026-05-17, L19 prevention) — archive the scaffolded
    # folder instead of leaving it to accumulate. Hammer-test FINDING #11
    # showed soft-delete cleans the DB row but NOT the disk folder; over time
    # `context/projects/` fills with orphaned dirs.
    #
    # Design choice: MOVE to `context/projects/.deleted/<name>-<ts>/` rather
    # than hard `rmtree`. This preserves the on-disk audit trail (operator can
    # recover content, or run a periodic janitor against `.deleted/`). The
    # timestamp suffix ensures successive soft-deletes of a project that was
    # restored + re-deleted (a future capability) don't collide.
    #
    # Side effect — failure is NON-FATAL: the DB row is the source of truth
    # for the soft-delete; a filesystem error must not roll back the commit.
    try:
        src_dir = Path(get_settings().repo_root) / "context" / "projects" / project.name
        if src_dir.exists():
            deleted_dir = (
                Path(get_settings().repo_root) / "context" / "projects" / ".deleted"
            )
            deleted_dir.mkdir(exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dst = deleted_dir / f"{project.name}-{ts}"
            shutil.move(str(src_dir), str(dst))
    except Exception:
        logger.exception(
            "delete_project: failed to archive scaffolded folder for project %d",
            project_id,
        )

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
