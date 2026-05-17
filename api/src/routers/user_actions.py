"""Cross-project next-action recommender (Kanban #1010).

Mounted at `/api/user/...`. USER-scoped, NOT project-scoped — the endpoint
walks every active project the operator owns and returns the top-N
highest-impact pending interactions (questions / decisions). Powers:

  - #1009 digest section 5 ("next-action queue")
  - #1003 mobile home tile
  - #1000 inbox empty-state hint

No `X-Project-Id` header required — parity with `GET /api/projects` and
`GET /api/projects/by-name/{name}` (the resource IS the user, not a single
project).

Ranking
-------
A pure ranker (`services/next_action_ranker.py`) scores each candidate on
four weighted, normalized factors:

   aging      40%   hours_since_updated / 168, clamped to 1.0
   block      30%   downstream_block_count / 5, clamped to 1.0
   priority   20%   (4 - priority) / 3
   budget     10%   today_spend / today_cap (per-project, fan-out)

The budget component requires a per-project P&L (`/api/projects/{id}/pl`)
fan-out. Each fan-out is bounded by `BUDGET_TIMEOUT_SECONDS`; on timeout or
error, the contribution falls back to 0.0 (the rest of the row still
surfaces). Implementation walks `services.budget_enforcer.compute_spend`
directly to avoid an HTTP round-trip back into our own ASGI app.

Filter
------
A task qualifies when ALL hold:

   interaction_kind IN ('question', 'decision')
   process_status NOT IN (5, 6, 7)              # done / cancelled / (reserved)
   blocked_by IS NULL
   status = 1 (active — soft-delete excluded)
   project.status = 1 (active — soft-deleted project's tasks excluded)

Empty fallback
--------------
When zero candidates survive the filter, `items: []` + a one-line
`fallback_hint` with cross-project counts:

   "No action needed - N tasks running, M completed today"

If even the counts query fails (truly catastrophic), the hint degrades to
the bare `"No action needed."` string — the endpoint NEVER 500s on the
fallback path.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.constants import RecordStatus, TaskInteractionKind, TaskStatus
from src.db import get_session
from src.models.project import Project
from src.models.task import Task
from src.schemas.user_actions import NextActionItem, NextActionResponse
from src.services.budget_enforcer import compute_spend
from src.services.next_action_ranker import (
    RankedCandidate,
    score_candidates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user-actions"])


# Per-(project) P&L fan-out timeout. The brief locks 2s — slightly generous so
# a single slow project doesn't tank the whole response, but small enough that
# the p95 stays under the 200ms budget for the common case (most projects
# either have no cap, in which case the helper short-circuits, or a small
# index-scan worth of session_runs / tasks rows).
BUDGET_TIMEOUT_SECONDS = 2.0

# Soft cap on candidate set size for ranking — we ORDER BY updated_at DESC at
# the SQL layer to ensure the freshest interactions land in the candidate
# window when the operator has more than this many pending HITLs. Picked at
# 100 (= 20x the typical limit=5) so the ranker still has comfortable headroom
# without paying for huge sorts. Bumpable — not a wire-contract value.
_CANDIDATE_FETCH_CAP = 100


async def _utc_midnight_now() -> tuple[datetime, datetime]:
    """Return (now, midnight_today) both tz-aware UTC."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now, midnight


async def _compute_budget_pct(
    db: AsyncSession, project_id: int, cap_daily: Decimal | None, since: datetime
) -> float:
    """Return today_spend / today_cap as a float in [0, +inf).

    Returns 0.0 when `cap_daily` is None / 0 (no cap configured = no budget
    pressure). Errors and timeouts also return 0.0 — the ranker treats budget
    as a tie-breaker, not a hard input, so any failure mode is safe to
    degrade.

    Wrapped in `asyncio.wait_for(..., BUDGET_TIMEOUT_SECONDS)` per the brief.
    """
    if cap_daily is None or cap_daily <= 0:
        return 0.0
    try:
        spend = await asyncio.wait_for(
            compute_spend(db, project_id, since=since),
            timeout=BUDGET_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "next-action: budget fan-out timed out for project_id=%s after %.1fs",
            project_id, BUDGET_TIMEOUT_SECONDS,
        )
        return 0.0
    except Exception:
        # Catch-all — DB hiccup / connection drop / unexpected. The ranker
        # tolerates 0.0; never let one project's spend query fail the whole
        # response.
        logger.exception(
            "next-action: budget fan-out failed for project_id=%s", project_id
        )
        return 0.0
    if spend is None:
        return 0.0
    # Decimal / Decimal -> Decimal -> float. Defensive: spend may come back as
    # int 0 from COALESCE(SUM, 0) on an empty range.
    spend_d = spend if isinstance(spend, Decimal) else Decimal(spend)
    return float(spend_d / cap_daily)


async def _compute_fallback_hint(db: AsyncSession, midnight: datetime) -> str:
    """Cross-project counts string for the empty-items case.

    Two queries:
      - running = COUNT(tasks WHERE process_status=2 AND status=1 AND project.status=1)
      - completed = COUNT(tasks WHERE process_status=5 AND completed_at>=midnight AND status=1 AND project.status=1)

    Any failure mode degrades to the bare "No action needed." string.
    """
    try:
        running_stmt = (
            select(func.count(Task.id))
            .join(Project, Project.id == Task.project_id)
            .where(
                Task.process_status == TaskStatus.IN_PROGRESS,
                Task.status == RecordStatus.ACTIVE,
                Project.status == RecordStatus.ACTIVE,
            )
        )
        completed_stmt = (
            select(func.count(Task.id))
            .join(Project, Project.id == Task.project_id)
            .where(
                Task.process_status == TaskStatus.DONE,
                Task.completed_at.is_not(None),
                Task.completed_at >= midnight,
                Task.status == RecordStatus.ACTIVE,
                Project.status == RecordStatus.ACTIVE,
            )
        )
        running = int((await db.execute(running_stmt)).scalar_one() or 0)
        completed = int((await db.execute(completed_stmt)).scalar_one() or 0)
    except Exception:
        logger.exception("next-action: fallback hint counts query failed")
        return "No action needed."
    return f"No action needed - {running} tasks running, {completed} completed today"


@router.get("/next-action", response_model=NextActionResponse)
async def get_next_action(
    limit: int = Query(default=5, ge=1, le=50),
    db: AsyncSession = Depends(get_session),
) -> NextActionResponse:
    """Top-N highest-impact pending operator actions across every active project.

    USER-scoped — no `X-Project-Id` header required. See module docstring for
    the filter + ranking contract.

    Performance: one candidate query (joined with downstream-block-count
    subquery, LIMIT 100 worst-case), then one Project fetch per distinct
    candidate project for the budget fan-out (concurrent via `asyncio.gather`,
    2s timeout each). Empty-items fast path emits the fallback hint via two
    COUNT queries; both wrapped in a try/except that degrades to a bare hint
    rather than 500ing.
    """
    now, midnight = await _utc_midnight_now()

    # Downstream-block-count: COUNT of OTHER tasks whose blocked_by points at
    # this candidate. Correlated scalar subquery keeps it a single round-trip
    # (no N+1). Soft-deleted downstream tasks don't count (consistent with the
    # soft-delete-excluded-everywhere policy). Aliased so the inner Task
    # reference is distinct from the outer (correlated) Task.
    DownTask = aliased(Task)
    downstream_count_subq = (
        select(func.count(DownTask.id))
        .where(
            DownTask.blocked_by == Task.id,
            DownTask.status == RecordStatus.ACTIVE,
        )
        .correlate(Task)
        .scalar_subquery()
    )

    # Candidate query — filter + downstream count + project name in one shot.
    # ORDER BY updated_at DESC isn't quite right (we want OLDER first for
    # ranking) BUT we sort in Python after scoring anyway, so the SQL ORDER BY
    # is only the candidate-window selector (which we soft-cap at 100). Order
    # by updated_at ASC so the OLDEST candidates land in the window when more
    # than 100 exist (the right bias — they're the ones the aging factor will
    # rank highest).
    stmt = (
        select(
            Task.id,
            Task.project_id,
            Task.title,
            Task.priority,
            Task.updated_at,
            downstream_count_subq.label("downstream_count"),
            Project.name.label("project_name"),
            Project.budget_daily_usd.label("budget_daily_usd"),
        )
        .join(Project, Project.id == Task.project_id)
        .where(
            Task.interaction_kind.in_(
                (TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION)
            ),
            # NOT IN (5, 6, 7) per spec. TaskStatus.DONE=5, CANCELLED=6.
            # Code 7 isn't currently allocated (see TaskStatus.ALL) but the
            # filter mentions it for forward-compat; an explicit IN-NOT of the
            # five live actionable codes (1..4 + any future code) is safer.
            Task.process_status.notin_((5, 6, 7)),
            Task.blocked_by.is_(None),
            Task.status == RecordStatus.ACTIVE,
            Project.status == RecordStatus.ACTIVE,
        )
        .order_by(Task.updated_at.asc())
        .limit(_CANDIDATE_FETCH_CAP)
    )

    rows = (await db.execute(stmt)).all()

    if not rows:
        hint = await _compute_fallback_hint(db, midnight)
        return NextActionResponse(items=[], fallback_hint=hint)

    # Per-project budget cap cache — one project may have many candidate
    # tasks; fan-out the spend query at most once per distinct project_id.
    # The cap comes back on the candidate row (we selected
    # Project.budget_daily_usd above) so the cache key is just project_id ->
    # spend_pct.
    distinct_projects: dict[int, Decimal | None] = {}
    for row in rows:
        if row.project_id not in distinct_projects:
            distinct_projects[row.project_id] = row.budget_daily_usd

    # Concurrent fan-out — each call is wrapped in its own timeout. The
    # gather collects results in the same order as the input list.
    project_ids = list(distinct_projects.keys())
    budget_pcts_raw = await asyncio.gather(
        *(
            _compute_budget_pct(db, pid, distinct_projects[pid], midnight)
            for pid in project_ids
        ),
        return_exceptions=False,  # _compute_budget_pct swallows internally
    )
    budget_pct_by_project: dict[int, float] = dict(zip(project_ids, budget_pcts_raw))

    candidates = [
        RankedCandidate(
            task_id=row.id,
            project_id=row.project_id,
            project_name=row.project_name,
            title=row.title,
            priority=row.priority,
            updated_at=row.updated_at,
            downstream_block_count=int(row.downstream_count or 0),
            budget_pct=budget_pct_by_project.get(row.project_id, 0.0),
        )
        for row in rows
    ]

    scored = score_candidates(candidates, now=now, limit=limit)

    items = [
        NextActionItem(
            task_id=s.task_id,
            project_id=s.project_id,
            project_name=s.project_name,
            title=s.title,
            reason=s.reason,
            score=s.score,
        )
        for s in scored
    ]
    return NextActionResponse(items=items, fallback_hint=None)
