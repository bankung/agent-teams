"""Pure ranker for the cross-project next-action recommender (Kanban #1010).

Splits the score math + reason rendering away from DB I/O so the calculator
is unit-testable without a Postgres fixture (mirrors `pl_calculator.py`
precedent). The router lifts the candidate list + per-project budget pct;
this module folds them into ranked items.

Score components (each normalized 0..1, then weighted):

    aging       (40%) = min(hours_since_updated / 168, 1.0)         # 168h = 1 week
    block_count (30%) = min(downstream_block_count / 5, 1.0)        # 5+ downstream = max
    priority    (20%) = (4 - priority) / 3                          # P1=1.0, P4=0.0
    budget      (10%) = clamp(today_spend / today_cap, [0, 1])

Final score = 0.4*aging + 0.3*block_count + 0.2*priority + 0.1*budget;
clamped to [0, 1].

Reason rendering picks the dominant factor (largest WEIGHTED contribution).
Two factors within 5% of each other are concatenated with " and ".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


# Component weights — locked at module level so tests / future tuning happen
# in one place. Sum to 1.0 exactly.
W_AGING = 0.40
W_BLOCK = 0.30
W_PRIORITY = 0.20
W_BUDGET = 0.10

# Normalization bounds.
AGING_HOURS_FULL = 168.0  # 1 week of inactivity = full aging score
BLOCK_COUNT_FULL = 5.0    # 5+ downstream blockers = full block score

# Dominant-factor "tie" band — two components within this fraction of each
# other are co-dominant (concatenated in the reason string).
TIE_BAND = 0.05


@dataclass(frozen=True)
class RankedCandidate:
    """One candidate task with all rank inputs gathered.

    The router builds these from the DB query + the budget fan-out. The pure
    `score_candidates` function below consumes them and emits `ScoredItem`s.
    """

    task_id: int
    project_id: int
    project_name: str
    title: str
    priority: int                          # 1..4 (P1 highest priority)
    updated_at: datetime                   # tz-aware
    downstream_block_count: int            # how many tasks have this.id as blocked_by
    budget_pct: float                      # today_spend / today_cap, 0.0 if unknown


@dataclass(frozen=True)
class ScoredItem:
    """Output of the ranker — what the router emits to the wire."""

    task_id: int
    project_id: int
    project_name: str
    title: str
    reason: str
    score: float


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _aging_component(updated_at: datetime, now: datetime) -> float:
    """Hours since updated, normalized against AGING_HOURS_FULL.

    Naive `updated_at` is treated as UTC (defensive — all DB columns store
    tz-aware UTC, but `datetime.utcnow()` callers occasionally drop the tz).
    """
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta_hours = max(0.0, (now - updated_at).total_seconds() / 3600.0)
    return _clamp01(delta_hours / AGING_HOURS_FULL)


def _block_component(downstream_count: int) -> float:
    if downstream_count <= 0:
        return 0.0
    return _clamp01(downstream_count / BLOCK_COUNT_FULL)


def _priority_component(priority: int) -> float:
    """P1 (URGENT in name but priority code 1) = 1.0; P4 = 0.0.

    Maps the 1..4 code to (4 - p) / 3:
       1 -> 1.0
       2 -> 0.667
       3 -> 0.333
       4 -> 0.0
    Out-of-range values clamp to [0, 1] (defensive — DB CHECK already gates
    1..4 but this keeps the pure function total).
    """
    if priority < 1:
        return 1.0
    if priority > 4:
        return 0.0
    return (4 - priority) / 3.0


def _budget_component(pct: float) -> float:
    return _clamp01(pct)


def _hours_since(updated_at: datetime, now: datetime) -> int:
    """Integer hour delta — used by the reason string."""
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta_seconds = max(0.0, (now - updated_at).total_seconds())
    return int(delta_seconds // 3600)


def _render_reason_for_factor(
    factor: str,
    cand: RankedCandidate,
    now: datetime,
) -> str:
    """One-line human-readable factor description.

    Stable strings — pinned by test_user_next_action.
    """
    if factor == "aging":
        return f"oldest in inbox ({_hours_since(cand.updated_at, now)}h)"
    if factor == "block":
        return f"blocking {cand.downstream_block_count} downstream tasks"
    if factor == "priority":
        # priority code is 1..4 — render the user-facing P-N. Code 1 = P1 (most
        # urgent), code 4 = P4 (least urgent).
        return f"P{cand.priority} priority"
    if factor == "budget":
        # Render as integer percent; clamp display to a sane upper bound for
        # the rare cap=0 / overshoot case (the underlying score is already
        # clamped to [0, 1], so the displayed % is bounded by 100 as well).
        pct_display = int(round(_clamp01(cand.budget_pct) * 100))
        return f"budget hit {pct_display}% on {cand.project_name}"
    # Defensive — unknown factor name shouldn't happen in production code path.
    return ""


def compute_score_and_reason(
    cand: RankedCandidate,
    now: datetime,
) -> tuple[float, str]:
    """Return (score, reason) for one candidate.

    Pure function — no DB I/O, no clocks (caller supplies `now`). Reason
    picker uses dominant WEIGHTED contributions; two factors within
    TIE_BAND of each other are concatenated with " and " in
    contribution-descending order.
    """
    aging = _aging_component(cand.updated_at, now)
    block = _block_component(cand.downstream_block_count)
    priority = _priority_component(cand.priority)
    budget = _budget_component(cand.budget_pct)

    # Weighted contributions — the absolute magnitude each factor adds to the
    # final score. Reason picker walks these, not the raw component values.
    contributions = {
        "aging": aging * W_AGING,
        "block": block * W_BLOCK,
        "priority": priority * W_PRIORITY,
        "budget": budget * W_BUDGET,
    }

    score = sum(contributions.values())
    score = _clamp01(score)

    # Sort factors by contribution descending; pick the top one + any other
    # factor within TIE_BAND of the top.
    ordered = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    top_factor, top_contrib = ordered[0]

    if top_contrib <= 0.0:
        # All-zero contributions — degenerate case (candidate has no aging,
        # no blocks, no priority weight, no budget). Pick aging as a stable
        # default reason so we never emit an empty string.
        return score, _render_reason_for_factor("aging", cand, now)

    parts: list[str] = [_render_reason_for_factor(top_factor, cand, now)]
    for factor, contrib in ordered[1:]:
        if contrib <= 0.0:
            break
        # Within 5% of the TOP contribution -> co-dominant.
        if (top_contrib - contrib) <= (top_contrib * TIE_BAND):
            parts.append(_render_reason_for_factor(factor, cand, now))
    reason = " and ".join(parts)
    return score, reason


def score_candidates(
    candidates: list[RankedCandidate],
    *,
    now: datetime,
    limit: int,
) -> list[ScoredItem]:
    """Score + sort + truncate. Stable ordering on score ties: task_id ASC.

    Pure function — caller (router) supplies the candidates from DB. `limit`
    is applied AFTER sort so we keep the top-N highest-scoring across the
    full candidate set (not the first N in arbitrary order).
    """
    scored: list[ScoredItem] = []
    for cand in candidates:
        score, reason = compute_score_and_reason(cand, now)
        scored.append(
            ScoredItem(
                task_id=cand.task_id,
                project_id=cand.project_id,
                project_name=cand.project_name,
                title=cand.title,
                reason=reason,
                score=score,
            )
        )
    # Sort by score DESC, then task_id ASC for deterministic tie-breaking.
    scored.sort(key=lambda s: (-s.score, s.task_id))
    return scored[:limit]
