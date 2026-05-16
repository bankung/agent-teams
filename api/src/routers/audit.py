"""HTTP routes for cross-project auditor rollups (Kanban #1082).

Mounted at `/api/audit` from main.py. Read-only — every endpoint here is an
aggregation over `tasks.audit_report` (migration 0030, populated by
`langgraph/nodes.py::auditor_node`).

Cross-project endpoint — takes NO `X-Project-Id` header (parity with
`/api/projects/stats` precedent).
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.db import get_session
from src.models.project import Project
from src.models.task import Task
from src.schemas.audit import AuditDailyCounts, AuditDailyRollupEntry

router = APIRouter(prefix="/audit", tags=["audit"])


# Default window when query params are omitted — 7 days back from today (UTC),
# inclusive on both ends. The "ending today" semantics matches the FE widget
# label ("last 7 days").
_DEFAULT_WINDOW_DAYS = 7


@router.get("/daily-rollup", response_model=list[AuditDailyRollupEntry])
async def list_audit_daily_rollup(
    from_date: date | None = Query(
        default=None,
        alias="from",
        description=(
            "Inclusive start of the window (UTC). Defaults to "
            "`today - 7 days` when omitted."
        ),
    ),
    to_date: date | None = Query(
        default=None,
        alias="to",
        description=(
            "Inclusive end of the window (UTC). Defaults to `today` when "
            "omitted."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> list[AuditDailyRollupEntry]:
    """Per-project, per-day audit verdict rollup over a date window.

    Aggregates every active task whose `audit_report` JSONB is non-null and
    whose `updated_at` falls in `[from_date, to_date]` (inclusive). One row
    per (project, day) — soft-deleted tasks and soft-deleted projects are
    both excluded. Empty response (`[]`) when nothing matches; never 500.

    Verdict → bucket mapping (see `src.schemas.audit` module docstring):
      - `halt_reason = 'auditor_giveup'` → `failed_giveup` (overrides verdict).
      - `verdict = 'pass'` → `pass`.
      - `verdict = 'auto_resolve'` → `auto_resolved`.
      - `verdict = 'escalate'` + `process_status = 5 (DONE)` → `escalated`.
      - `verdict = 'escalate'` + `process_status in (1..4)` → `pending_escalation`.
      - anything else → unbucketed (skipped silently — paranoia for legacy
        rows pre-dating the locked verdict vocabulary).

    Window validation: `from > to` → 422. Defaults: `from = today - 7 days`,
    `to = today` (UTC date). Single SQL pass — no row-by-row Python folding.

    Ordering: `project_id ASC, day DESC` — newest day first per project
    matches the FE widget's render order ("today's activity at the top").
    """
    today = date.today()
    if from_date is None:
        from_date = today - timedelta(days=_DEFAULT_WINDOW_DAYS)
    if to_date is None:
        to_date = today

    if from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail="from must be <= to",
        )

    # The window upper bound is INCLUSIVE on `to_date` — a task touched at
    # 23:59:59 on `to_date` UTC must be counted. Compare `updated_at` against
    # `to_date + 1 day` exclusive (i.e. start of the next day) to capture
    # the entire day on the boundary without faffing with timezones.
    upper_exclusive = to_date + timedelta(days=1)

    # JSONB verdict extraction — `audit_report->>'verdict'` returns the raw
    # text value, NULL if the key is missing. Wrap as a SQL expression so
    # SQLAlchemy renders the operator literally rather than treating the
    # string as a column.
    verdict_expr = Task.audit_report.op("->>")("verdict")

    # `date_trunc('day', updated_at)::date` floors the timestamptz to a UTC
    # calendar date. Cast to SQLAlchemy `Date` so Pydantic receives a
    # `datetime.date` instance directly (not a `datetime` with zeroed time).
    day_expr = func.date_trunc("day", Task.updated_at).cast(Date)

    # Mapping each task row to a bucket — `case()` returns NULL for rows
    # that fall through (legacy / unknown verdict). NULL-valued buckets are
    # excluded from the `count() FILTER` clauses below by definition.
    pass_filter = (
        (Task.halt_reason.is_(None) | (Task.halt_reason != "auditor_giveup"))
        & (verdict_expr == "pass")
    )
    auto_resolved_filter = (
        (Task.halt_reason.is_(None) | (Task.halt_reason != "auditor_giveup"))
        & (verdict_expr == "auto_resolve")
    )
    escalated_filter = (
        (Task.halt_reason.is_(None) | (Task.halt_reason != "auditor_giveup"))
        & (verdict_expr == "escalate")
        & (Task.process_status == TaskStatus.DONE)
    )
    pending_escalation_filter = (
        (Task.halt_reason.is_(None) | (Task.halt_reason != "auditor_giveup"))
        & (verdict_expr == "escalate")
        & (Task.process_status != TaskStatus.DONE)
    )
    # `failed_giveup` is the dominant gate — applies whenever the auditor's
    # giveup halt_reason is stamped, regardless of the captured verdict at
    # the moment of giveup (which is always 'auto_resolve' today, but the
    # mapping shouldn't depend on that staying true).
    failed_giveup_filter = Task.halt_reason == "auditor_giveup"

    # Use SQL `FILTER (WHERE ...)` aggregate clauses — PG-native, one pass.
    # SQLAlchemy renders `func.count().filter(expr)` as
    # `count(*) FILTER (WHERE expr)` on the PostgreSQL dialect.
    stmt = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            day_expr.label("day"),
            func.count().filter(pass_filter).label("pass_count"),
            func.count().filter(auto_resolved_filter).label("auto_resolved_count"),
            func.count().filter(escalated_filter).label("escalated_count"),
            func.count().filter(failed_giveup_filter).label("failed_giveup_count"),
            func.count()
            .filter(pending_escalation_filter)
            .label("pending_escalation_count"),
        )
        .join(Task, Task.project_id == Project.id)
        .where(
            Task.audit_report.isnot(None),
            Task.status == RecordStatus.ACTIVE,
            Project.status == RecordStatus.ACTIVE,
            Task.updated_at >= from_date,
            Task.updated_at < upper_exclusive,
        )
        .group_by(Project.id, Project.name, day_expr)
        .order_by(Project.id.asc(), day_expr.desc())
    )

    rows = (await session.execute(stmt)).all()

    return [
        AuditDailyRollupEntry(
            project_id=row.project_id,
            project_name=row.project_name,
            day=row.day,
            counts=AuditDailyCounts(
                # field name `pass_` (Python keyword reserved); alias on the
                # wire is `pass`. `populate_by_name=True` lets us pass either.
                pass_=row.pass_count,
                auto_resolved=row.auto_resolved_count,
                escalated=row.escalated_count,
                failed_giveup=row.failed_giveup_count,
                pending_escalation=row.pending_escalation_count,
            ),
        )
        for row in rows
    ]


