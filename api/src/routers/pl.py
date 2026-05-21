"""HTTP routes for P&L: per-project summary + cross-project rollup.

Per-project endpoints (Kanban #953):
  Mounted at `/api/projects/{project_id}/...`; require X-Project-Id == path id.
  - GET /api/projects/{project_id}/pl     — JSON PLSummary
  - GET /api/projects/{project_id}/export — CSV or JSON ledger dump

Cross-project endpoint (Kanban #1329):
  Mounted at `/api/pnl` (operator-level, no X-Project-Id required).
  - GET /api/pnl — PLCrossProject rollup across all active projects
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_or_404, get_session
from src.models.project import Project
from src.models.transaction import Transaction
from src.schemas.pl import PLCrossProject, PLCrossProjectRow, PLPeriodLiteral, PLSummary
from src.schemas.transaction import TransactionRead
from src.services.pl_calculator import compute_pl
from src.services.session_project import require_project_id_header

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects-finance"])

# Operator-level (cross-project) router — no X-Project-Id header requirement.
# Mounted separately at /api/pnl (see main.py include_router call).
# Conscious choice: this endpoint scans every project the operator has access
# to (effectively all projects with status=1). It is intentionally NOT nested
# under /api/projects/{id} to signal that it is NOT per-project-scoped, and it
# does NOT enforce the X-Project-Id session-scoping used by the per-project
# endpoints. See Kanban #1329 for the design rationale.
pnl_router = APIRouter(prefix="/pnl", tags=["operator-finance"])


# Default since-window per period (when the caller omits `since`). Picks a
# sensible "last N periods" window that matches what a human accountant
# would scan by default. `until` defaults to now() in every case.
_DEFAULT_WINDOW_DAYS_BY_PERIOD: dict[str, int] = {
    "daily": 30,        # ~1 month of daily buckets
    "weekly": 90,       # ~3 months of weekly buckets
    "monthly": 365,     # 12 monthly buckets
    "quarterly": 365 * 2,   # 8 quarterly buckets
    "yearly": 365 * 5,  # 5 yearly buckets
}


# Source-text-locked detail string — pinned by
# test_pl_endpoint.py::test_pl_endpoint_cross_project_returns_404.
_DETAIL_PROJECT_NOT_FOUND_TEMPLATE = "Project id={project_id} not found"


def _resolve_window(
    period: PLPeriodLiteral,
    since: datetime | None,
    until: datetime | None,
) -> tuple[datetime, datetime]:
    """Fill in defaults: until=now-UTC, since = until - default-days(period).

    Caller-supplied values take precedence. If `since > until` → 422.
    """
    if until is None:
        until = datetime.now(timezone.utc)
    if since is None:
        days = _DEFAULT_WINDOW_DAYS_BY_PERIOD.get(period, 365)
        since = until - timedelta(days=days)
    if since > until:
        raise HTTPException(
            status_code=422,
            detail=f"since ({since.isoformat()}) must be <= until ({until.isoformat()})",
        )
    return since, until


@router.get("/{project_id}/pl", response_model=PLSummary)
async def get_project_pl(
    project_id: int,
    session_project_id: int = Depends(require_project_id_header),
    period: PLPeriodLiteral = Query(default="monthly"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PLSummary:
    """Per-project P&L summary grouped by `period`.

    The X-Project-Id header MUST equal the path's `{project_id}` — mismatch
    returns 404 (the project is "invisible" from the bound session's view).

    Default window varies by period (daily=30d, monthly=12 buckets, etc.).
    Caller-supplied `since` / `until` override the defaults; `since > until`
    → 422.

    Multi-currency: per-(currency, period) buckets. Top-level totals
    reflect the FIRST currency observed only — see `PLSummary` docstring.
    """
    if project_id != session_project_id:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=project_id),
        )

    project = await get_or_404(
        session,
        Project,
        detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=project_id),
        id=project_id,
    )

    since, until = _resolve_window(period, since, until)

    stmt = (
        select(Transaction)
        .where(Transaction.project_id == project_id)
        .where(Transaction.occurred_at >= since)
        .where(Transaction.occurred_at < until)
        .order_by(Transaction.occurred_at.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())

    return compute_pl(
        rows,
        period,
        project_currency_default=(project.currency_default or "USD"),
    )


@router.get("/{project_id}/export")
async def export_project_transactions(
    project_id: int,
    session_project_id: int = Depends(require_project_id_header),
    format: Literal["csv", "json"] = Query(default="csv"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Accountant export — CSV or JSON dump of the project's ledger.

    The X-Project-Id header MUST equal the path's `{project_id}` (404 on
    mismatch — same semantics as the P&L endpoint).

    `since` / `until` are OPTIONAL filters (no default window — exports
    typically span the full history). `kind` narrows to a single
    transaction kind when provided.

    CSV columns (in order): id, occurred_at, kind, category, amount_minor,
    currency, source, source_ref, task_id, notes.

    JSON shape: list of TransactionRead objects.

    Content-Disposition: `attachment; filename=transactions-{project_id}-{ts}.{ext}`.
    """
    if project_id != session_project_id:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=project_id),
        )

    # 404 guard mirrors /pl — exists check before query work.
    await get_or_404(
        session,
        Project,
        detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=project_id),
        id=project_id,
    )

    stmt = select(Transaction).where(Transaction.project_id == project_id)
    if since is not None:
        stmt = stmt.where(Transaction.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(Transaction.occurred_at < until)
    if kind is not None:
        stmt = stmt.where(Transaction.kind == kind)
    stmt = stmt.order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
    rows = list((await session.execute(stmt)).scalars().all())

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if format == "json":
        payload = [TransactionRead.model_validate(r).model_dump(mode="json") for r in rows]
        from fastapi.responses import JSONResponse

        return JSONResponse(
            content=payload,
            headers={
                "Content-Disposition": (
                    f"attachment; filename=transactions-{project_id}-{ts}.json"
                ),
            },
        )

    # CSV — build in-memory; ledgers are bounded (per-project, per-window).
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "occurred_at",
            "kind",
            "category",
            "amount_minor",
            "currency",
            "source",
            "source_ref",
            "task_id",
            "notes",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.id,
                r.occurred_at.isoformat() if r.occurred_at else "",
                r.kind,
                r.category or "",
                r.amount_minor,
                r.currency,
                r.source or "",
                r.source_ref or "",
                r.task_id if r.task_id is not None else "",
                r.notes or "",
            ]
        )

    csv_bytes = buf.getvalue().encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=transactions-{project_id}-{ts}.csv"
            ),
        },
    )


# =============================================================================
# Cross-project P&L rollup (Kanban #1329)
# =============================================================================


@pnl_router.get("", response_model=PLCrossProject)
async def get_cross_project_pl(
    period: PLPeriodLiteral = Query(default="monthly"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    include_killed: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> PLCrossProject:
    """Cross-project P&L rollup — one row per project, no bucket breakdown.

    Operator-level endpoint: does NOT require X-Project-Id header. This is a
    conscious choice — the endpoint scans every project in scope (all
    status=1 projects by default). Binding to a specific project header would
    contradict the cross-project intent. See Kanban #1329 for design rationale.

    Default window mirrors per-project /pl defaults (daily=30d, monthly=365d,
    etc.). `since > until` → 422.

    `include_killed=true` extends the scan to soft-deleted projects (status=0)
    — useful for forensic queries (e.g. last 30 days of a killed project).
    Default false keeps the happy-path view clean.

    Amounts are MAJOR units; NO FX conversion is performed. When a project has
    transactions in more than one currency, `mixed_currency=True` and the
    top-level totals reflect the first currency only. `grand_total_net_first_
    currency_only` is null whenever projects span multiple `currency_default`
    values or any row has `mixed_currency=True`.
    """
    # HTTPException (422 from _resolve_window, auth errors) propagates naturally.
    since, until = _resolve_window(period, since, until)

    # Candidate projects — active only by default; include_killed adds
    # soft-deleted rows (status=0). Ordered by name for deterministic JSON.
    if include_killed:
        proj_stmt = select(Project).order_by(Project.name.asc())
    else:
        proj_stmt = select(Project).where(Project.status == 1).order_by(Project.name.asc())

    projects = list((await session.execute(proj_stmt)).scalars().all())

    # N+1 acceptable at current project counts; refactor when projects > ~100
    # (kanban followup — reshape to single SQL GROUP BY project_id, kind,
    # currency, period_label + aggregation).
    rows: list[PLCrossProjectRow] = []
    failed_project_ids: list[int] = []
    for p in projects:
        txn_stmt = (
            select(Transaction)
            .where(Transaction.project_id == p.id)
            .where(Transaction.occurred_at >= since)
            .where(Transaction.occurred_at < until)
            .order_by(Transaction.occurred_at.asc())
        )
        txns = list((await session.execute(txn_stmt)).scalars().all())
        try:
            summary = compute_pl(
                txns, period, project_currency_default=(p.currency_default or "USD")
            )
        except Exception:
            logger.exception(
                "get_cross_project_pl: compute_pl failed for project_id=%d", p.id
            )
            failed_project_ids.append(p.id)
            continue
        # Detect mixed currency: compute_pl returns one bucket per
        # (currency, period-label) pair; distinct currencies = the currency
        # dimension of that set.
        distinct_currencies = {b.currency for b in summary.buckets}
        rows.append(
            PLCrossProjectRow(
                project_id=p.id,
                project_name=p.name,
                team=p.team,
                currency_default=(p.currency_default or "USD"),
                period=period,
                revenue=summary.revenue,
                cost=summary.cost,
                expense=summary.expense,
                refund=summary.refund,
                transfer=summary.transfer,
                net=summary.net,
                transaction_count=summary.transaction_count,
                mixed_currency=len(distinct_currencies) > 1,
                bucket_count=len(summary.buckets),
            )
        )

    # If every project failed, surface the 500 — no partial result to return.
    if failed_project_ids and not rows and projects:
        logger.error(
            "get_cross_project_pl: all %d projects failed compute_pl", len(projects)
        )
        raise HTTPException(status_code=500, detail="Internal server error")

    # Grand total only when every row shares the same currency_default AND
    # no row has mixed_currency (cross-currency sums are meaningless without FX).
    grand_total: Decimal | None = None
    if rows:
        currencies = {r.currency_default for r in rows}
        any_mixed = any(r.mixed_currency for r in rows)
        if len(currencies) == 1 and not any_mixed:
            grand_total = sum((r.net for r in rows), start=Decimal("0.0000"))

    return PLCrossProject(
        period=period,
        since=since,
        until=until,
        rows=rows,
        total_projects=len(rows),
        grand_total_net_first_currency_only=grand_total,
        failed_project_ids=failed_project_ids,
    )
