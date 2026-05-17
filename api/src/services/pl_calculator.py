"""P&L period-bucketing pure service (Kanban #953).

Groups a list of `Transaction` rows by `(currency, period-label)`, sums per
`kind`, computes `net = revenue - refund - cost - expense` (transfer is
neutral in P&L — represents bookkeeping movement, not income/expense).

Multi-currency policy (MVP): NO FX conversion. If transactions span multiple
currencies, each currency lands in its own bucket. The top-level summary
totals reflect the FIRST currency observed (ordered by occurred_at); per-
currency detail lives in `buckets`.

Period bucketing:
  - daily     → "YYYY-MM-DD"
  - weekly    → "YYYY-Www" (ISO week per `isocalendar()`)
  - monthly   → "YYYY-MM"
  - quarterly → "YYYY-Qn" where n = (month-1)//3 + 1
  - yearly    → "YYYY"

Pure function — no DB I/O. The caller (router) lifts the SQL query; this
keeps the calculator unit-testable without a Postgres fixture.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from src.schemas.pl import PLBucket, PLPeriodLiteral, PLSummary

# Minor-unit divisors per currency. Covers the common cases; default to 100
# for unknown currencies (the dominant pattern — most fiat is 100 minor per
# major). JPY / KRW / etc. with zero decimal places land in the explicit map.
# Extend here when new currencies show up — keeping the map in one place
# means the FE / accountant flow doesn't have to know about every currency.
MINOR_DIVISOR_BY_CURRENCY: dict[str, int] = {
    "USD": 100,
    "THB": 100,
    "EUR": 100,
    "GBP": 100,
    "AUD": 100,
    "CAD": 100,
    "CNY": 100,
    "HKD": 100,
    "SGD": 100,
    "JPY": 1,
    "KRW": 1,
    "VND": 1,
    "IDR": 1,
    "TWD": 1,
}

# Default for currencies not in the map. 100 covers ~90% of fiat — most
# zero-decimal currencies need an explicit entry above (operator-correctable).
_DEFAULT_MINOR_DIVISOR = 100

# Zero in 4-place quant — matches `tasks.estimated_cost_usd` and the rest of
# the cost surface. The P&L view is 2-place at the boundary but we keep the
# intermediate calculation in 4-place for accumulation precision.
_ZERO = Decimal("0.0000")


def minor_to_major(amount_minor: int, currency: str) -> Decimal:
    """Convert BIGINT minor units to major-unit Decimal using the currency
    divisor map. Unknown currencies fall back to 100 (the dominant pattern).
    """
    divisor = MINOR_DIVISOR_BY_CURRENCY.get(currency.upper(), _DEFAULT_MINOR_DIVISOR)
    if divisor == 1:
        # Zero-decimal currency — exact integer Decimal, no division noise.
        return Decimal(amount_minor)
    return (Decimal(amount_minor) / Decimal(divisor)).quantize(Decimal("0.0001"))


def period_label(dt: datetime, period: PLPeriodLiteral) -> str:
    """Return the bucket label string for `dt` under `period`.

    `dt` is treated as-is — the caller is responsible for TZ normalization
    (the router passes through `tasks.completed_at` / `transactions.occurred_at`
    which are TIMESTAMPTZ; ISO week / month / etc. are computed in the
    datetime's own TZ). For UTC-anchored buckets, pass UTC-tz datetimes.
    """
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    if period == "weekly":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "monthly":
        return dt.strftime("%Y-%m")
    if period == "quarterly":
        quarter = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{quarter}"
    if period == "yearly":
        return str(dt.year)
    # Defensive — Pydantic Literal at the boundary should have caught this.
    raise ValueError(f"unknown period: {period!r}")


def _empty_kind_dict() -> dict[str, Decimal]:
    return {
        "revenue": _ZERO,
        "cost": _ZERO,
        "expense": _ZERO,
        "refund": _ZERO,
        "transfer": _ZERO,
    }


def _compute_net(kinds: dict[str, Decimal]) -> Decimal:
    """Net = revenue - refund - cost - expense. Transfer is neutral."""
    return kinds["revenue"] - kinds["refund"] - kinds["cost"] - kinds["expense"]


def compute_pl(
    transactions: Iterable[Any],
    period: PLPeriodLiteral,
    *,
    project_currency_default: str = "USD",
) -> PLSummary:
    """Roll a list of `Transaction` rows into a PLSummary.

    Args:
        transactions: any iterable of objects exposing `amount_minor`,
            `currency`, `kind`, `occurred_at`. ORM `Transaction` rows OR
            test-double objects both work.
        period: bucketing granularity (Literal — Pydantic-validated at the
            router boundary).
        project_currency_default: used as the top-level `currency` when the
            transactions list is empty (so the response shape stays
            renderable on the FE even for new projects).

    Returns:
        PLSummary with one bucket per (currency, period-label) pair. Top-
        level totals reflect the FIRST currency observed (ordered by
        occurred_at ASC for determinism). Empty input → all-zero summary
        with currency=project_currency_default.

    Net formula: `revenue - refund - cost - expense` (transfer excluded).
    """
    # Materialize so we can sort + iterate twice deterministically. Callers
    # usually pass small lists (per-project, per-window) so the memory cost
    # is bounded. SQL ORDER BY occurred_at ASC at the caller would let us
    # skip the sort here, but doing it locally keeps the contract simple.
    txns = sorted(
        transactions,
        key=lambda t: (getattr(t, "occurred_at", None) or datetime.min),
    )

    if not txns:
        zero_kinds = _empty_kind_dict()
        return PLSummary(
            period=period,
            currency=project_currency_default.upper(),
            revenue=zero_kinds["revenue"],
            cost=zero_kinds["cost"],
            expense=zero_kinds["expense"],
            refund=zero_kinds["refund"],
            transfer=zero_kinds["transfer"],
            net=_ZERO,
            transaction_count=0,
            buckets=[],
        )

    # First-observed currency (deterministic — uppercase for display parity).
    first_currency = str(txns[0].currency or project_currency_default).upper()

    # bucket_acc: {(currency, label) → {kind → Decimal, "count" → int}}
    bucket_acc: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {**_empty_kind_dict(), "count": 0}
    )

    for t in txns:
        currency = str(t.currency or project_currency_default).upper()
        label = period_label(t.occurred_at, period)
        major = minor_to_major(int(t.amount_minor), currency)
        acc = bucket_acc[(currency, label)]
        # Unknown kind → skip silently (DB CHECK should prevent; defensive).
        if t.kind in acc and t.kind != "count":
            acc[t.kind] = acc[t.kind] + major
            acc["count"] += 1

    # Build per-bucket PLBucket list, ordered by (currency, label) ASC for
    # deterministic JSON output. Labels sort lexicographically in the natural
    # period order (YYYY-MM-DD / YYYY-Www / YYYY-MM / YYYY-Qn / YYYY).
    buckets: list[PLBucket] = []
    top_kinds = _empty_kind_dict()
    top_count = 0
    for (currency, label) in sorted(bucket_acc.keys()):
        acc = bucket_acc[(currency, label)]
        bucket_kinds = {k: acc[k] for k in ("revenue", "cost", "expense", "refund", "transfer")}
        bucket = PLBucket(
            label=label,
            currency=currency,
            revenue=bucket_kinds["revenue"],
            cost=bucket_kinds["cost"],
            expense=bucket_kinds["expense"],
            refund=bucket_kinds["refund"],
            transfer=bucket_kinds["transfer"],
            net=_compute_net(bucket_kinds),
            transaction_count=acc["count"],
        )
        buckets.append(bucket)
        # Roll into top-level only for the first-observed currency. Cross-
        # currency sums are meaningless without FX (out of scope MVP).
        if currency == first_currency:
            for k in top_kinds:
                top_kinds[k] = top_kinds[k] + bucket_kinds[k]
            top_count += acc["count"]

    return PLSummary(
        period=period,
        currency=first_currency,
        revenue=top_kinds["revenue"],
        cost=top_kinds["cost"],
        expense=top_kinds["expense"],
        refund=top_kinds["refund"],
        transfer=top_kinds["transfer"],
        net=_compute_net(top_kinds),
        transaction_count=top_count,
        buckets=buckets,
    )
