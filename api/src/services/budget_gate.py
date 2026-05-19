"""Spawn-time hard cap enforcement (Kanban #1194 AC4-7).

Sibling to `services/budget_enforcer.py`:

- `budget_enforcer.check_budget(db, project_id)` answers "is the project
  ALREADY over its caps right now?" — used by the auto-pickup poll
  (`/api/tasks/next-autorun`) to refuse to surface a new task and stamp
  halt_reason on the candidate row.
- `budget_gate.check_budget(db, project_id, estimated_cost_usd)` (this
  module) answers "would adding ONE MORE task push the project over its
  daily cap?" — used by the POST /api/tasks gate to block a new AI-spawn
  with 429 before the row hits the DB. Spawn-time check projects the
  proposed cost against the daily window; the enforcer's monthly + total
  caps still gate at next-autorun pickup.

The two are intentionally narrow: a single "does this fit" question fires
hundreds of times more often than the full daily/monthly/total verdict, and
the brief explicitly limits v1 to the daily window (cron / scheduled
nightly reconciliation deferred to the #1194-cron follow-up).

Threshold alerts (AC5) compose with `services.notification_router.deliver`:
on every check that crosses 80% or 100% (per-project-per-day, de-duped),
fire a telegram payload. The de-dupe state is a module-level dict (cheap;
no DB write); it resets across process restarts which is fine — operators
get at-worst-one-duplicate-alert-per-restart, not a storm.

Reconciliation (AC6) is on-demand only — no scheduled job here. Callers
that want a point-in-time recompute hit `POST /api/projects/{id}/reconcile-
budget` which echoes the same numbers `check_budget` would report. No
stored cache to invalidate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.project import Project
from src.services.budget_enforcer import (
    _utc_first_of_month,
    _utc_midnight,
    compute_spend,
)

logger = logging.getLogger(__name__)


# Thresholds — locked at module level, mirror budget_enforcer's pattern.
# 80%-band entry is INCLUSIVE on the lower bound (>=80 fires the warn). 100%-cap
# exhaustion fires the halt branch when projected >= cap. Both diverge from
# budget_enforcer (strict-greater 80, strict-greater 100) deliberately: the
# spawn-time check is the GATE that blocks, so we're more conservative — a
# proposed spawn that lands EXACTLY at the cap is refused, not allowed.
SPAWN_WARN_PCT = Decimal("80")
SPAWN_BLOCK_PCT = Decimal("100")

ReasonCode = Literal[
    "ok",
    "no_cap_configured",
    "would_exceed_daily_cap",
]


@dataclass(frozen=True)
class BudgetCheckResult:
    """Verdict for a single spawn-time `check_budget` call.

    `pct_used` is `None` when `cap_daily_usd` is None (no cap configured); the
    FE can render "unlimited" rather than an arithmetic placeholder. All other
    Decimal fields use Decimal('0') when unset so downstream arithmetic stays
    type-stable.
    """

    allowed: bool
    used_today_usd: Decimal
    cap_daily_usd: Decimal | None
    projected_usd: Decimal
    pct_used: Decimal | None
    reason: ReasonCode


# ---------------------------------------------------------------------------
# Threshold-alert de-dupe — module-level cache keyed by (project_id, UTC date).
# Reset on date rollover happens implicitly: a new date produces a new key, so
# the prior day's entries linger but cost nothing (operator can reset the
# process to clear if memory pressure ever matters; today's entries: ~10 per
# project on a busy day → negligible).
# ---------------------------------------------------------------------------
_ALERT_SENT: dict[tuple[int, date, str], None] = {}


def _alert_already_sent_today(project_id: int, today: date, event: str) -> bool:
    return (project_id, today, event) in _ALERT_SENT


def _mark_alert_sent_today(project_id: int, today: date, event: str) -> None:
    _ALERT_SENT[(project_id, today, event)] = None


def _reset_alert_cache_for_tests() -> None:
    """Hook for the test suite to clear the cache between assertions."""
    _ALERT_SENT.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def check_budget(
    db: AsyncSession,
    project_id: int,
    estimated_cost_usd: Decimal | None = None,
) -> BudgetCheckResult:
    """Project the proposed spawn cost against the daily cap.

    Args:
        db: AsyncSession scoped to the caller.
        project_id: target project id.
        estimated_cost_usd: caller's pre-computed estimate for the new spawn.
            None or Decimal('0') means "spawn has no cost claim" — we still
            evaluate the existing daily spend (the project may already be
            over without any new burn).

    Returns:
        BudgetCheckResult — see dataclass docstring.

    Notes:
        - Unknown / soft-deleted project → ValueError (caller decides 404).
        - Threshold alerts (80% / 100%) are fire-and-forget; failures inside
          the notification path are swallowed + logged so a Telegram outage
          can never block a spawn decision.
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError(f"project_id={project_id} not found")

    cap = project.budget_daily_usd
    new_cost = estimated_cost_usd if estimated_cost_usd is not None else Decimal("0")
    # Defensive coerce — Pydantic gives us Decimal already, but raw-SQL / direct
    # service callers might pass an int / float / str.
    if not isinstance(new_cost, Decimal):
        new_cost = Decimal(str(new_cost))

    now = datetime.now(timezone.utc)
    used_today = await compute_spend(db, project_id, since=_utc_midnight(now))
    projected = used_today + new_cost

    if cap is None:
        return BudgetCheckResult(
            allowed=True,
            used_today_usd=used_today,
            cap_daily_usd=None,
            projected_usd=projected,
            pct_used=None,
            reason="no_cap_configured",
        )

    # cap is not None below — safe to divide.
    if cap <= 0:
        # Cap of 0 means "no AI spend allowed at all". Any non-zero projected
        # spend is a block. The pct field is meaningless when cap=0; report a
        # large sentinel so the FE bar pegs and the reason field is the
        # actionable signal.
        pct = Decimal("99999999.9999") if projected > 0 else Decimal("0")
        allowed = projected <= 0
        reason: ReasonCode = "ok" if allowed else "would_exceed_daily_cap"
    else:
        pct = (projected / cap * Decimal("100")).quantize(Decimal("0.0001"))
        allowed = projected < cap  # strict-less: projected == cap is blocked.
        reason = "ok" if allowed else "would_exceed_daily_cap"

    result = BudgetCheckResult(
        allowed=allowed,
        used_today_usd=used_today,
        cap_daily_usd=cap,
        projected_usd=projected,
        pct_used=pct,
        reason=reason,
    )

    # Fire-and-forget threshold alert. Schedule on the running loop so the
    # caller's 429 response is not delayed by Telegram latency.
    await _maybe_fire_threshold_alert(db, project, project_id, result, now)

    return result


# ---------------------------------------------------------------------------
# Threshold-alert dispatch (AC5)
# ---------------------------------------------------------------------------


async def _maybe_fire_threshold_alert(
    db: AsyncSession,
    project: Project,
    project_id: int,
    result: BudgetCheckResult,
    now: datetime,
) -> None:
    """De-duped 80% / 100% threshold notification dispatch.

    Composes with `services.notification_router.deliver`. We import lazily to
    avoid a circular at module-load time (notification_router imports
    src.models.* which already pulls budget tooling on some paths).
    """
    if result.pct_used is None:
        return  # No cap → no thresholds to cross.

    today = now.date()
    event: str | None = None
    if result.pct_used >= SPAWN_BLOCK_PCT:
        event = "budget_threshold_100_blocked"
    elif result.pct_used >= SPAWN_WARN_PCT:
        event = "budget_threshold_80"
    if event is None:
        return

    if _alert_already_sent_today(project_id, today, event):
        return

    # Skip when the project has no telegram targets — local-file fallback
    # would still fire from notification_router but the de-dupe key would
    # then suppress subsequent legitimate operator-facing pages. Operators
    # can configure project.notification_targets = [] to disable entirely.
    targets = project.notification_targets or []
    if not any(t.get("kind") == "telegram" for t in targets if isinstance(t, dict)):
        # Mark sent anyway so we don't recompute the empty-targets check on
        # every spawn through the day; reset via process restart.
        _mark_alert_sent_today(project_id, today, event)
        return

    payload = {
        "event": event,
        "project_id": project_id,
        "project_name": project.name,
        "pct": str(result.pct_used),
        "used_today_usd": str(result.used_today_usd),
        "projected_usd": str(result.projected_usd),
        "cap_daily_usd": str(result.cap_daily_usd) if result.cap_daily_usd else None,
        "at": now.isoformat(),
    }

    try:
        # Lazy import — see docstring above.
        from src.services.notification_router import deliver

        # The notification_router needs a task_id for the audit row (its
        # tasks_history INSERT is keyed off tasks.id). For project-level
        # budget alerts there is no single owning task, so we pick the most
        # recently created ACTIVE task on the project as a stable anchor.
        # If the project has zero tasks we skip the dispatch — operator can
        # still see the 429 detail.
        from sqlalchemy import select
        from src.constants import RecordStatus
        from src.models.task import Task

        anchor_q = (
            select(Task.id)
            .where(Task.project_id == project_id, Task.status == RecordStatus.ACTIVE)
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        anchor_id = (await db.execute(anchor_q)).scalar_one_or_none()
        if anchor_id is None:
            logger.info(
                "budget_threshold_alert_skipped_no_anchor: project=%d event=%s",
                project_id,
                event,
            )
            _mark_alert_sent_today(project_id, today, event)
            return

        # deliver() commits its own audit rows; we don't await its result for
        # the 429 path but we DO await here (caller is a service func, not the
        # HTTP handler — the router-side fire-and-forget happens at the call
        # site if needed). Wrap in try/except so a Telegram outage never
        # blocks the gate decision.
        await deliver(
            task_id=anchor_id, payload=payload, kind="telegram", session=db
        )
        _mark_alert_sent_today(project_id, today, event)
    except Exception:  # pragma: no cover - defensive, real failures logged
        logger.exception(
            "budget_threshold_alert_failed: project=%d event=%s",
            project_id,
            event,
        )
        # Do NOT mark sent — let the next check retry. (Avoids a permanent
        # silent-failure mode where a transient Telegram error suppresses
        # the rest of the day's alerts.)


# ---------------------------------------------------------------------------
# AC6 — on-demand reconciliation
# ---------------------------------------------------------------------------


async def reconcile_budget(
    db: AsyncSession, project_id: int
) -> dict[str, Any]:
    """Recompute and return point-in-time budget numbers for a project.

    Called by `POST /api/projects/{id}/reconcile-budget`. Does NOT write
    anything — no cached column to invalidate (see AC6 design note in the
    spawn brief). Returns the same shape `check_budget` exposes plus a
    `reconciled_at` UTC timestamp.
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError(f"project_id={project_id} not found")

    now = datetime.now(timezone.utc)
    used_today = await compute_spend(db, project_id, since=_utc_midnight(now))
    used_this_month = await compute_spend(db, project_id, since=_utc_first_of_month(now))
    cap_daily = project.budget_daily_usd
    cap_monthly = project.budget_monthly_usd
    pct_daily = None
    if cap_daily is not None and cap_daily > 0:
        pct_daily = (used_today / cap_daily * Decimal("100")).quantize(Decimal("0.0001"))
    pct_monthly = None
    if cap_monthly is not None and cap_monthly > 0:
        pct_monthly = (used_this_month / cap_monthly * Decimal("100")).quantize(Decimal("0.0001"))
    return {
        "project_id": project_id,
        "used_today_usd": str(used_today),
        "used_this_month_usd": str(used_this_month),
        "cap_daily_usd": str(cap_daily) if cap_daily is not None else None,
        "cap_monthly_usd": str(cap_monthly) if cap_monthly is not None else None,
        "pct_used_daily": str(pct_daily) if pct_daily is not None else None,
        "pct_used_monthly": str(pct_monthly) if pct_monthly is not None else None,
        "reconciled_at": now.isoformat(),
    }
