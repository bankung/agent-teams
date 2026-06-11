"""Provider cost rollup endpoint (Kanban #2135).

GET /api/usage/daily?days=N&project_id=<optional>

Cross-project by default (operator-level, no X-Project-Id required). Source
of truth: session_runs only — truthful metering from the worker PATCH.

Rows are grouped by (date, provider, model). NULL provider maps to 'unknown'
in the response. date = finished_at UTC date falling back to started_at.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.middleware.rate_limit import limiter
from src.models.session import Session as SessionModel, SessionRun
from src.schemas.usage import UsageDailyResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["usage"])

# Default and cap for the ?days= parameter.
_DEFAULT_DAYS = 31
_MAX_DAYS = 366


@router.get("/daily", response_model=UsageDailyResponse)
@limiter.limit("30/minute")
async def usage_daily(
    request: Request,  # required by slowapi key_func — not used in handler body
    days: int = Query(
        default=_DEFAULT_DAYS,
        ge=1,
        le=_MAX_DAYS,
        description="Number of calendar days to look back (UTC). Default 31, max 366.",
    ),
    project_id: int | None = Query(
        default=None,
        ge=1,
        description="Optional project filter. When omitted, all projects are included.",
    ),
    db: AsyncSession = Depends(get_session),
) -> UsageDailyResponse:
    """Daily provider cost rollup from session_runs.

    - date = (finished_at FALLBACK started_at) cast to DATE in UTC.
    - NULL provider stored on the run → reported as 'unknown'.
    - Rows sorted date DESC then provider ASC.
    - total_today_usd  = sum over today's UTC date.
    - total_month_usd  = sum over the current UTC calendar month.
    """
    # Derive the run date: coalesce(finished_at, started_at) → DATE.
    run_date = cast(
        func.coalesce(SessionRun.finished_at, SessionRun.started_at),
        Date,
    ).label("run_date")

    # provider NULL → 'unknown' in the grouped output.
    provider_col = func.coalesce(SessionRun.provider, "unknown").label("provider")
    model_col = func.coalesce(SessionRun.model, "").label("model")

    # Window: last <days> calendar days (inclusive of today) in UTC.
    cutoff = func.current_date() - days + 1

    stmt = (
        select(
            run_date,
            provider_col,
            model_col,
            func.sum(SessionRun.total_input_tokens).label("input_tokens"),
            func.sum(SessionRun.total_output_tokens).label("output_tokens"),
            func.sum(SessionRun.total_cost_usd).label("cost_usd"),
        )
        .where(
            cast(
                func.coalesce(SessionRun.finished_at, SessionRun.started_at),
                Date,
            )
            >= cutoff
        )
    )

    if project_id is not None:
        # Join via session_runs → sessions to filter by project_id.
        stmt = stmt.join(SessionModel, SessionRun.session_id == SessionModel.id).where(
            SessionModel.project_id == project_id
        )

    stmt = stmt.group_by(run_date, provider_col, model_col).order_by(
        run_date.desc(), provider_col
    )

    result = await db.execute(stmt)
    rows_raw = result.fetchall()

    # Compute today-UTC and current-month totals from the fetched rows.
    today_stmt = select(func.current_date())
    today_result = await db.execute(today_stmt)
    today = today_result.scalar_one()

    total_today = Decimal("0")
    total_month = Decimal("0")

    rows = []
    for r in rows_raw:
        cost = Decimal(str(r.cost_usd)) if r.cost_usd is not None else Decimal("0")
        rows.append(
            {
                "date": str(r.run_date),
                "provider": r.provider,
                "model": r.model,
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cost_usd": f"{cost:.4f}",
            }
        )
        if r.run_date == today:
            total_today += cost
        if r.run_date.year == today.year and r.run_date.month == today.month:
            total_month += cost

    return UsageDailyResponse(
        days=days,
        today=str(today),
        rows=rows,
        total_today_usd=f"{total_today:.4f}",
        total_month_usd=f"{total_month:.4f}",
    )
