"""Provider cost rollup endpoints (Kanban #2135, #2356).

GET /api/usage/daily?days=N&project_id=<optional>
    Source of truth: session_runs only — truthful metering from the worker PATCH.
    Rows grouped by (date, provider, model). NULL provider → 'unknown'.
    date = finished_at UTC date falling back to started_at.

GET /api/usage/monthly?months=N&cycle_day=D&project_id=<optional>
    Billing-cycle (cut-off day D) cost rollup combining BOTH cost modes:
    Mode A = usage_events (interactive Claude-Code hook capture) and
    Mode B = session_runs (headless langgraph metering). See usage_monthly().

Both are cross-project by default (operator-level, no X-Project-Id required).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.middleware.rate_limit import limiter
from src.models.session import Session as SessionModel, SessionRun
from src.models.task import Task
from src.models.usage_event import UsageEvent
from src.schemas.usage import (
    UsageDailyResponse,
    UsageMonthlyCycle,
    UsageMonthlyResponse,
    UsageMonthlyTaskRow,
)
from src.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["usage"])

# Default and cap for the ?days= parameter.
_DEFAULT_DAYS = 31
_MAX_DAYS = 366

# Default and caps for the monthly ?months= parameter.
_DEFAULT_MONTHS = 6
_MAX_MONTHS = 36

_MONEY_QUANT = Decimal("0.0001")


def _fmt_money(d: Decimal) -> str:
    """Serialise a Decimal as a 4dp string (matches usage_daily)."""
    return f"{d:.4f}"


def _prev_month(year: int, month: int) -> tuple[int, int]:
    """Return (year, month) for the month before the given one (Jan → Dec)."""
    if month == 1:
        return (year - 1, 12)
    return (year, month - 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    """Return (year, month) for the month after the given one (Dec → Jan)."""
    if month == 12:
        return (year + 1, 1)
    return (year, month + 1)


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


def _cycle_starts(now: datetime, cycle_day: int, months: int) -> list[date]:
    """Return cycle-start dates, most-recent first (length == months).

    The CURRENT cycle's start is the most recent day-`cycle_day` boundary at or
    before `now`: if `now.day >= cycle_day` it's day-D of this month, else day-D
    of the previous month (with Jan→Dec year rollover). Each earlier start is the
    previous month's day-D, walking back `months-1` further.
    """
    if now.day >= cycle_day:
        y, m = now.year, now.month
    else:
        y, m = _prev_month(now.year, now.month)

    starts: list[date] = []
    for _ in range(months):
        starts.append(date(y, m, cycle_day))
        y, m = _prev_month(y, m)
    return starts  # already most-recent first


@router.get("/monthly", response_model=UsageMonthlyResponse)
@limiter.limit("30/minute")
async def usage_monthly(
    request: Request,  # required by slowapi key_func — not used in handler body
    months: int = Query(
        default=_DEFAULT_MONTHS,
        ge=1,
        le=_MAX_MONTHS,
        description="Number of billing cycles to return (current + prior), most-recent first.",
    ),
    cycle_day: int | None = Query(
        default=None,
        ge=1,
        le=28,
        description=(
            "Override the billing cut-off day (1..28). When omitted, falls back "
            "to the COST_CYCLE_DAY setting (default 1). Capped at 28 so cycle "
            "boundaries never hit the Feb/30/31 edge."
        ),
    ),
    project_id: int | None = Query(
        default=None,
        ge=1,
        description="Optional project filter. When omitted, all projects are included.",
    ),
    db: AsyncSession = Depends(get_session),
) -> UsageMonthlyResponse:
    """Billing-cycle cost rollup combining BOTH cost modes.

    A billing cycle with cut-off day D covers the half-open window
    ``[day D of month M 00:00 UTC, day D of month M+1 00:00 UTC)``. The
    "current" cycle contains ``now()``. An event exactly at day-D 00:00 belongs
    to the NEW cycle; the instant before belongs to the prior cycle.

    Per cycle, two DISJOINT sources are reported and summed:
      - **Mode A (estimated)** = ``usage_events`` rows (interactive Claude-Code
        hook capture), bucketed by ``occurred_at``.
      - **Mode B (metered)** = ``session_runs`` rows (headless langgraph
        metering), bucketed by ``coalesce(finished_at, started_at)``.

    These do NOT double-count: Mode A and Mode B are SEPARATE execution paths
    (interactive vs headless), so ``total_cost_usd = mode_a + mode_b`` is the
    intended total spend across both modes — not an overlap.

    Per-cycle drilldown groups by ``task_id`` across BOTH sources; rows with no
    task_id collapse into one ``task_id: null`` ("unattributed") bucket. Tasks
    within a cycle are ordered by total_cost_usd desc.

    cycle_day precedence: ``?cycle_day=`` query value → ``COST_CYCLE_DAY``
    setting → default 1. The resolved value is echoed in the response.

    The response is zero-filled: one entry per requested cycle, most-recent
    first, even cycles with zero spend. Money is 4dp strings.
    """
    resolved_cycle_day = (
        cycle_day if cycle_day is not None else get_settings().cost_cycle_day
    )

    now = datetime.now(timezone.utc)
    starts = _cycle_starts(now, resolved_cycle_day, months)

    # Cycle windows: start_ts (inclusive) .. end_ts (exclusive = next start).
    # starts is most-recent first; the newest cycle's exclusive end is the next
    # day-D boundary AFTER its start.
    def _start_ts(d: date) -> datetime:
        return datetime.combine(d, time.min, tzinfo=timezone.utc)

    newest_start = starts[0]
    ny, nm = _next_month(newest_start.year, newest_start.month)
    newest_end_exclusive = _start_ts(date(ny, nm, resolved_cycle_day))
    oldest_start_ts = _start_ts(starts[-1])

    # Pre-compute per-cycle (start_ts, end_ts_exclusive, cycle_end_display),
    # oldest first (index 0 = oldest cycle). Built oldest→newest so accumulator
    # indices align; the response reverses to most-recent first at the end.
    starts_asc = list(reversed(starts))  # oldest first
    boundaries: list[tuple[datetime, datetime, date]] = []
    for i, s in enumerate(starts_asc):
        s_ts = _start_ts(s)
        if i + 1 < len(starts_asc):
            e_ts = _start_ts(starts_asc[i + 1])
        else:
            e_ts = newest_end_exclusive
        # Display end = inclusive last day = next-cycle-start date minus 1 day.
        cycle_end_display = (e_ts - timedelta(days=1)).date()
        boundaries.append((s_ts, e_ts, cycle_end_display))

    # ---- Two window queries (Mode A + Mode B) -----------------------------
    # shortcut: in-Python bucketing of the whole window in two queries, fine
    # <100k ledger rows (Mode B date filter unindexed = seq scan, ok at scale); upgrade: SQL width_bucket on a cycle table + Mode B functional index.

    # Mode A — usage_events by occurred_at, project filter on the column itself.
    ev_stmt = select(
        UsageEvent.occurred_at,
        UsageEvent.task_id,
        UsageEvent.cost_usd,
        UsageEvent.input_tokens,
        UsageEvent.output_tokens,
    ).where(
        UsageEvent.occurred_at >= oldest_start_ts,
        UsageEvent.occurred_at < newest_end_exclusive,
    )
    if project_id is not None:
        ev_stmt = ev_stmt.where(UsageEvent.project_id == project_id)
    ev_stmt = ev_stmt.order_by(UsageEvent.occurred_at)

    # Mode B — session_runs by coalesce(finished_at, started_at); project filter
    # via JOIN session_runs → sessions (same join usage_daily uses).
    run_date_expr = func.coalesce(SessionRun.finished_at, SessionRun.started_at)
    run_stmt = select(
        run_date_expr.label("run_at"),
        SessionRun.task_id,
        SessionRun.total_cost_usd,
        SessionRun.total_input_tokens,
        SessionRun.total_output_tokens,
    ).where(
        run_date_expr >= oldest_start_ts,
        run_date_expr < newest_end_exclusive,
    )
    if project_id is not None:
        run_stmt = run_stmt.join(
            SessionModel, SessionRun.session_id == SessionModel.id
        ).where(SessionModel.project_id == project_id)
    run_stmt = run_stmt.order_by(run_date_expr)

    ev_rows = (await db.execute(ev_stmt)).fetchall()
    run_rows = (await db.execute(run_stmt)).fetchall()

    # ---- Per-cycle accumulators -------------------------------------------
    n = len(boundaries)
    mode_a_cost = [Decimal("0")] * n
    mode_a_in = [0] * n
    mode_a_out = [0] * n
    mode_b_cost = [Decimal("0")] * n
    mode_b_in = [0] * n
    mode_b_out = [0] * n
    # Per cycle: task_id -> {"a": Decimal, "b": Decimal} (task_id None = unattributed).
    per_task: list[dict[int | None, dict[str, Decimal]]] = [
        defaultdict(lambda: {"a": Decimal("0"), "b": Decimal("0")}) for _ in range(n)
    ]
    task_ids: set[int] = set()

    def _bucket_index(ts: datetime) -> int | None:
        """Return the cycle index for a timestamp, or None if outside the window.

        Half-open per cycle: start <= ts < end. The window query already bounds
        ts to [oldest_start, newest_end_exclusive), so a None here would only
        arise from a boundary rounding edge — guarded defensively.
        """
        for idx, (s_ts, e_ts, _disp) in enumerate(boundaries):
            if s_ts <= ts < e_ts:
                return idx
        return None

    for r in ev_rows:
        ts = r.occurred_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        idx = _bucket_index(ts)
        if idx is None:
            continue
        cost = Decimal(str(r.cost_usd)) if r.cost_usd is not None else Decimal("0")
        mode_a_cost[idx] += cost
        mode_a_in[idx] += int(r.input_tokens or 0)
        mode_a_out[idx] += int(r.output_tokens or 0)
        per_task[idx][r.task_id]["a"] += cost
        if r.task_id is not None:
            task_ids.add(r.task_id)

    for r in run_rows:
        ts = r.run_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        idx = _bucket_index(ts)
        if idx is None:
            continue
        cost = (
            Decimal(str(r.total_cost_usd))
            if r.total_cost_usd is not None
            else Decimal("0")
        )
        mode_b_cost[idx] += cost
        mode_b_in[idx] += int(r.total_input_tokens or 0)
        mode_b_out[idx] += int(r.total_output_tokens or 0)
        per_task[idx][r.task_id]["b"] += cost
        if r.task_id is not None:
            task_ids.add(r.task_id)

    # ---- Resolve task titles in one query (LEFT-join semantics) -----------
    titles: dict[int, str] = {}
    if task_ids:
        title_rows = (
            await db.execute(
                select(Task.id, Task.title).where(Task.id.in_(task_ids))
            )
        ).fetchall()
        titles = {tid: title for tid, title in title_rows}

    # ---- Build the response (most-recent first → reverse the asc lists) ----
    grand_total = Decimal("0")
    cycles_out: list[UsageMonthlyCycle] = []
    for idx in range(n - 1, -1, -1):  # newest cycle first
        s_ts, _e_ts, disp_end = boundaries[idx]
        cycle_total = mode_a_cost[idx] + mode_b_cost[idx]
        grand_total += cycle_total

        task_rows: list[UsageMonthlyTaskRow] = []
        for tid, sub in per_task[idx].items():
            a = sub["a"]
            b = sub["b"]
            task_rows.append(
                UsageMonthlyTaskRow(
                    task_id=tid,
                    task_title=(titles.get(tid) if tid is not None else None),
                    mode_a_cost_usd=_fmt_money(a),
                    mode_b_cost_usd=_fmt_money(b),
                    total_cost_usd=_fmt_money(a + b),
                )
            )
        # Order tasks by total_cost_usd desc (dominant key). Tie-break within an
        # equal-cost tier: task_id asc, unattributed (None) bucket last. Done as
        # two stable sorts: (is_None, task_id) asc FIRST, then cost desc, so that
        # cost dominates and None-last applies only among equal-cost rows.
        task_rows.sort(key=lambda tr: (tr.task_id is None, tr.task_id or 0))
        task_rows.sort(key=lambda tr: Decimal(tr.total_cost_usd), reverse=True)

        cycles_out.append(
            UsageMonthlyCycle(
                cycle_start=s_ts.date().isoformat(),
                cycle_end=disp_end.isoformat(),
                mode_a_cost_usd=_fmt_money(mode_a_cost[idx]),
                mode_a_input_tokens=mode_a_in[idx],
                mode_a_output_tokens=mode_a_out[idx],
                mode_b_cost_usd=_fmt_money(mode_b_cost[idx]),
                mode_b_input_tokens=mode_b_in[idx],
                mode_b_output_tokens=mode_b_out[idx],
                total_cost_usd=_fmt_money(cycle_total),
                tasks=task_rows,
            )
        )

    return UsageMonthlyResponse(
        months=months,
        cycle_day=resolved_cycle_day,
        cycles=cycles_out,
        total_cost_usd=_fmt_money(grand_total),
    )
