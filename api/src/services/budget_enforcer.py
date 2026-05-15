"""Per-project budget enforcement (Kanban #951).

Builds on the cost foundation laid by #944 (`tasks.estimated_cost_usd`) and
#871 (`session_runs.total_cost_usd`). The headless auto-run worker
(langgraph/worker.py) polls `/api/tasks/next-autorun`; that endpoint
filters out runnable tasks for projects that are over their hard-halt
cap, and stamps `halt_reason='budget_exceeded:<period>'` on the
candidate task so the operator sees the gate in the UI.

Public API
==========

`compute_spend(db, project_id, since=None) -> Decimal`
    Sum of cost dollars for the project over `[since, +infinity)`. When
    `since` is None, returns lifetime cost (no time filter). Combines
    two source tables WITHOUT double-counting:

      1. `session_runs.total_cost_usd` — server-authoritative real metering
         (preferred source). Aggregated via `sessions.project_id` (FK chain
         `session_runs → sessions → projects`; the alternate `session_runs.task_id`
         path is NULL-able on `ON DELETE SET NULL` and unreliable).
      2. `tasks.estimated_cost_usd` — heuristic fallback for tasks that
         never had a linked `session_run` (the typical interactive-Claude
         case). To avoid double-counting tasks that DO have a linked
         session_run, the task estimate is summed only for tasks whose
         `id` is NOT referenced by any `session_runs.task_id` for this
         project.

    The "since" filter applies to:
      - `tasks.completed_at >= since`  (when present)
      - `session_runs.created_at >= since`

    Both filters are strict `>=` (mirror the cron / recurrence semantics
    elsewhere — boundary point is INSIDE the window).

`check_budget(db, project_id) -> BudgetVerdict`
    Evaluates all three caps (daily / monthly / total) against the
    current spend. Returns a `BudgetVerdict` dataclass whose fields are
    safe-to-render on the API boundary (Decimals + bools + Literal).

      - `daily_pct` / `monthly_pct` / `total_pct`: Decimal in the range
        [0, +inf]. `Decimal("0")` when the corresponding cap is NULL
        (treated as unlimited; the FE renders no progress bar).
      - `soft_warn`: True when ANY non-null cap is `> 80% AND <= 100%`.
      - `hard_halt`: True when ANY non-null cap is `> 100%`.
      - `exceeded_cap`: Literal["daily","monthly","total"] | None — the
        FIRST cap to exceed 100% in (total, monthly, daily) priority
        order. None when `hard_halt=False`. Priority order is deliberate:
        a `total` cap is the loudest signal (lifetime overspend), then
        `monthly` (the calendar window), then `daily` (rolling).

Reset cadence
=============

NO scheduled job — reset is FREE via on-demand `compute_spend(since=...)`.
The daily anchor is "midnight UTC of the current date"; the monthly
anchor is "first-of-month UTC, 00:00". When the calendar tips to a new
day or month, the next `check_budget` call automatically returns a
fresh-window verdict because the WHERE clauses use `>=` on the new
anchor. No rollover table, no APScheduler hook, no catch-up logic.

TZ caveat: this slice uses UTC for both anchors. The brief flagged a
"recurrence_timezone" anchor but that column lives on `tasks`, not
`projects`. If per-project TZ becomes load-bearing for caps later, add
`projects.tz` in a follow-up migration; the enforcer's `since=` argument
already takes any tz-aware datetime, so the service signature is
forward-compatible.

Hook semantics
==============

`run_mode='manual'` tasks BYPASS enforcement entirely — the user is
making an explicit per-action choice, the enforcer is a guardrail for
automated burn, not a user-decision gate. The bypass is enforced at the
hook callsite (router), NOT inside `check_budget` itself, so the service
remains a pure compute and `check_budget` can be reused by any caller
(e.g., the FE-facing GET /api/projects/{id}/budget endpoint in a future
slice).

Soft-warn behavior in the hook: log a WARNING-level structured line
("budget_soft_warn: project=X period=Y pct=Z") and proceed with pickup.
NO write to the task row — soft warns are informational, not a state
change. The FE banner reads `check_budget` on poll.

Hard-halt behavior in the hook: refuse to surface the task as
`next_task` AND set `halt_reason='budget_exceeded:<period>'` on the
candidate row so the operator sees the gate on the board. The row's
process_status stays TODO — when the operator raises the cap (or the
calendar window rolls), the next-autorun poll surfaces the row again
(halt_reason is auto-cleared on cap-raise via a separate slice; for
this slice, the operator clears it via PATCH).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionRun
from src.models.task import Task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public verdict shape — kept as a plain dataclass (not Pydantic) so it can be
# returned from any service / dependency without importing schemas. The router
# / FE will map this to a Pydantic schema if it ever exposes a public endpoint.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetVerdict:
    """Result of `check_budget` — read-only snapshot at one moment in time.

    All three pct fields use `Decimal("0")` (NOT None) when the
    corresponding cap is NULL — keeps downstream arithmetic simple and
    mirrors the always-emit-zero-when-unset pattern used elsewhere in the
    codebase (ProjectStatsCostUsage, ProjectStatsRunModeBreakdown).
    """

    daily_pct: Decimal
    monthly_pct: Decimal
    total_pct: Decimal
    soft_warn: bool
    hard_halt: bool
    exceeded_cap: Literal["daily", "monthly", "total"] | None


# Thresholds — locked at the module level so they're testable + adjustable
# in one place. The 80% boundary is INCLUSIVE-lower / EXCLUSIVE-upper for the
# soft_warn band, matching the brief's "> 80% AND <= 100%". The 100% boundary
# is STRICTLY GREATER for hard_halt (=100% spend exactly is the "spent it all
# but didn't overshoot" case → soft_warn only, no halt).
SOFT_WARN_THRESHOLD_PCT = Decimal("80")
HARD_HALT_THRESHOLD_PCT = Decimal("100")

# Zero-pct constant — returned for caps whose cap value is NULL (unlimited),
# so the verdict struct shape is uniform regardless of cap presence.
_ZERO_PCT = Decimal("0")
_ZERO_SPEND = Decimal("0")


# ---------------------------------------------------------------------------
# Time anchors
# ---------------------------------------------------------------------------


def _utc_midnight(now: datetime) -> datetime:
    """Return 00:00:00 UTC of the same calendar date as `now`.

    `now` may be naive; we treat naive as UTC (the API/DB store everything
    in UTC tz-aware, so callers in the codebase will always pass tz-aware).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _utc_first_of_month(now: datetime) -> datetime:
    """Return 00:00:00 UTC on the 1st of `now`'s month."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    n = now.astimezone(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# compute_spend
# ---------------------------------------------------------------------------


async def compute_spend(
    db: AsyncSession,
    project_id: int,
    since: datetime | None = None,
) -> Decimal:
    """Sum cost (USD) for project_id over `[since, +inf)` (or lifetime if None).

    Combines `session_runs.total_cost_usd` (real-metering source) with the
    subset of `tasks.estimated_cost_usd` that is NOT shadowed by a linked
    session_run. Result is `Decimal` quantized to 4 decimal places (parity
    with the underlying NUMERIC(10,4) columns).

    Excludes:
      - soft-deleted tasks (`tasks.status = 0`) — they don't burn budget.
      - tasks with NULL `estimated_cost_usd` (no estimate captured yet).
      - sessions / session_runs are NEVER soft-deleted (no status column);
        all rows count.

    Double-count avoidance: for the task-sum branch, we exclude any task
    whose `id` is referenced by `session_runs.task_id` for THIS project's
    sessions. That subset is covered by the session_runs branch already.
    """
    # --- session_runs branch ------------------------------------------------
    # Aggregate via sessions.project_id (the FK chain
    # session_runs.session_id → sessions.id → sessions.project_id is
    # always-present; session_runs.task_id is nullable + unreliable).
    sr_stmt = (
        select(func.coalesce(func.sum(SessionRun.total_cost_usd), 0))
        .select_from(SessionRun)
        .join(SessionModel, SessionRun.session_id == SessionModel.id)
        .where(SessionModel.project_id == project_id)
    )
    if since is not None:
        sr_stmt = sr_stmt.where(SessionRun.created_at >= since)
    sr_sum = (await db.execute(sr_stmt)).scalar_one() or _ZERO_SPEND

    # --- tasks branch -------------------------------------------------------
    # Sum estimated_cost_usd for project's tasks, EXCLUDING tasks that already
    # have a linked session_run (those are covered by sr_sum). The subquery
    # collects the set of task_ids that have ANY session_run for this project
    # — joining session_runs to sessions ensures we only look at runs scoped
    # to the same project.
    metered_task_ids_subq = (
        select(SessionRun.task_id)
        .join(SessionModel, SessionRun.session_id == SessionModel.id)
        .where(
            SessionModel.project_id == project_id,
            SessionRun.task_id.is_not(None),
        )
    )
    t_stmt = (
        select(func.coalesce(func.sum(Task.estimated_cost_usd), 0))
        .where(
            Task.project_id == project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.estimated_cost_usd.is_not(None),
            Task.id.not_in(metered_task_ids_subq),
        )
    )
    if since is not None:
        # Task cost is captured at done-flip time, so completed_at is the
        # right anchor (started_at would over-count a task that started in
        # the window but completed outside, and vice versa).
        t_stmt = t_stmt.where(Task.completed_at >= since)
    t_sum = (await db.execute(t_stmt)).scalar_one() or _ZERO_SPEND

    # SQLAlchemy may return python int 0 from COALESCE(SUM,0) when the col is
    # NUMERIC and the sum is empty; coerce both legs to Decimal for the add.
    sr_d = sr_sum if isinstance(sr_sum, Decimal) else Decimal(sr_sum)
    t_d = t_sum if isinstance(t_sum, Decimal) else Decimal(t_sum)
    return (sr_d + t_d).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------


def _pct(spent: Decimal, cap: Decimal | None) -> Decimal:
    """Return (spent / cap) * 100 as Decimal; 0 when cap is None.

    Quantized to 4 places — small enough to display, large enough to round-trip
    a 0.01-USD spend against a 100-USD cap without floor-collapsing to 0%.
    """
    if cap is None:
        return _ZERO_PCT
    if cap <= 0:
        # A cap of 0 means "no spend allowed"; any non-zero spend lands at
        # +infinity logically. We return a sentinel large value so the
        # hard_halt branch fires. (DB CHECK rejects negative caps; cap=0 is
        # allowed and means "all autorun blocked" — an emergency-stop knob.)
        return Decimal("99999999.9999") if spent > 0 else _ZERO_PCT
    return ((spent / cap) * Decimal("100")).quantize(Decimal("0.0001"))


async def check_budget(db: AsyncSession, project_id: int) -> BudgetVerdict:
    """Evaluate daily / monthly / total caps and return a structured verdict.

    Reads the project row + computes three spend windows on-demand. Returns
    the all-clear verdict (zeros + False + False + None) when no caps are
    set (the typical pre-#951 case).

    Raises no exceptions for the all-NULL-cap path — that's the
    short-circuit. For an unknown `project_id`, the SELECT returns None and
    we surface a `ValueError` (caller decides whether to convert to a 404).
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError(f"project_id={project_id} not found")

    cap_daily = project.budget_daily_usd
    cap_monthly = project.budget_monthly_usd
    cap_total = project.budget_total_usd

    # Short-circuit: all-NULL caps → no enforcement at all. Skip the three
    # spend queries (each is a small index scan but unnecessary).
    if cap_daily is None and cap_monthly is None and cap_total is None:
        return BudgetVerdict(
            daily_pct=_ZERO_PCT,
            monthly_pct=_ZERO_PCT,
            total_pct=_ZERO_PCT,
            soft_warn=False,
            hard_halt=False,
            exceeded_cap=None,
        )

    now = datetime.now(timezone.utc)

    # Only compute the windows we actually need — saves one query per
    # NULL cap. The total cap is the cheapest (no time filter), and is
    # also the priority-1 exceeded_cap (see exceeded_cap selection below).
    spend_daily = (
        await compute_spend(db, project_id, since=_utc_midnight(now))
        if cap_daily is not None
        else _ZERO_SPEND
    )
    spend_monthly = (
        await compute_spend(db, project_id, since=_utc_first_of_month(now))
        if cap_monthly is not None
        else _ZERO_SPEND
    )
    spend_total = (
        await compute_spend(db, project_id, since=None)
        if cap_total is not None
        else _ZERO_SPEND
    )

    daily_pct = _pct(spend_daily, cap_daily)
    monthly_pct = _pct(spend_monthly, cap_monthly)
    total_pct = _pct(spend_total, cap_total)

    # Soft-warn: ANY non-null cap in the band (80, 100]. Hard-halt: ANY
    # non-null cap strictly > 100. (At exactly 100% spent = full burn but
    # not over → soft_warn only.)
    def _in_soft_band(pct: Decimal, cap: Decimal | None) -> bool:
        return cap is not None and SOFT_WARN_THRESHOLD_PCT < pct <= HARD_HALT_THRESHOLD_PCT

    def _over_hard(pct: Decimal, cap: Decimal | None) -> bool:
        return cap is not None and pct > HARD_HALT_THRESHOLD_PCT

    soft_warn = (
        _in_soft_band(daily_pct, cap_daily)
        or _in_soft_band(monthly_pct, cap_monthly)
        or _in_soft_band(total_pct, cap_total)
    )
    hard_halt = (
        _over_hard(daily_pct, cap_daily)
        or _over_hard(monthly_pct, cap_monthly)
        or _over_hard(total_pct, cap_total)
    )

    # Exceeded-cap selection: total > monthly > daily priority order. The
    # "loudest" signal wins so the operator sees the most damning cap on
    # the FE banner. None when hard_halt is False (including the soft_warn
    # only case).
    exceeded_cap: Literal["daily", "monthly", "total"] | None = None
    if hard_halt:
        if _over_hard(total_pct, cap_total):
            exceeded_cap = "total"
        elif _over_hard(monthly_pct, cap_monthly):
            exceeded_cap = "monthly"
        elif _over_hard(daily_pct, cap_daily):
            exceeded_cap = "daily"

    # If both warn and halt fire on the same row (one cap at 85%, another at
    # 105%), favor halt — the gating signal is the more conservative one. The
    # struct still exposes soft_warn=True for completeness so a future caller
    # could surface "warn AND halt" in the UI, but the hook reads hard_halt
    # first and stops.
    return BudgetVerdict(
        daily_pct=daily_pct,
        monthly_pct=monthly_pct,
        total_pct=total_pct,
        soft_warn=soft_warn,
        hard_halt=hard_halt,
        exceeded_cap=exceeded_cap,
    )
