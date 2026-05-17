"""HTTP routes for the per-project P&L summary + accountant export (Kanban #953).

Mounted at `/api/projects/{project_id}/...` so the resource hierarchy mirrors
the per-project scoping. Both endpoints require X-Project-Id == {project_id}
in the path (cross-project access surfaces as 404 — parity with the
transactions PATCH semantics).

Two endpoints:
  - GET /api/projects/{project_id}/pl     — JSON PLSummary (see schemas/pl.py)
  - GET /api/projects/{project_id}/export — CSV or JSON ledger dump for the
                                            accountant. Content-Disposition
                                            attachment + filename.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_or_404, get_session
from src.models.project import Project
from src.models.transaction import Transaction
from src.schemas.pl import PLPeriodLiteral, PLSummary
from src.schemas.transaction import TransactionRead
from src.services.pl_calculator import compute_pl
from src.services.session_project import require_project_id_header

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects-finance"])


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
