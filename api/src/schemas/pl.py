"""P&L summary schema (Kanban #953).

Returned by `GET /api/projects/{id}/pl`. The service layer
(`src.services.pl_calculator`) groups transactions by `(currency, period)`
and rolls up per-kind sums + net.

Amounts are MAJOR units (USD dollars, THB baht, JPY yen) — not minor —
because the FE renders them and the accountant reads them. The minor →
major conversion happens server-side in the calculator using
`MINOR_DIVISOR_BY_CURRENCY`.

Multi-currency policy (MVP): NO FX conversion. If a project has txns in
multiple currencies, each currency lands in its own bucket. The top-level
`currency` field reflects the FIRST currency observed (or the project's
`currency_default` when the result is empty); `buckets` carries the
per-(currency, period) breakdown.

`buckets` carries one entry per (currency, period-label) pair. Net per
bucket is `revenue - refund - cost - expense` (transfer is neutral —
it represents money moving between accounts, not P&L impact).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

PLPeriodLiteral = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]


class PLBucket(BaseModel):
    """One period bucket inside the P&L summary.

    `label` shape varies by period:
      - daily     → "YYYY-MM-DD"
      - weekly    → "YYYY-Www" (ISO week, e.g. "2026-W20")
      - monthly   → "YYYY-MM"
      - quarterly → "YYYY-Qn" (e.g. "2026-Q2")
      - yearly    → "YYYY"

    All amount fields are MAJOR units (USD, not cents). `net` excludes
    `transfer` per accounting convention (transfer is bookkeeping movement,
    not P&L impact).
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    currency: str
    revenue: Decimal
    cost: Decimal
    expense: Decimal
    refund: Decimal
    transfer: Decimal
    net: Decimal
    transaction_count: int


class PLSummary(BaseModel):
    """Top-level P&L summary response.

    `currency` field: when a project's transactions span multiple currencies,
    this holds the FIRST currency observed (ordered by occurred_at). The
    per-bucket `currency` is authoritative — clients should iterate `buckets`
    to display per-currency rollups.

    `revenue`/`cost`/`expense`/`refund`/`transfer`/`net` at the top level sum
    across the FIRST currency only (so the top-level totals are coherent
    rather than a meaningless cross-currency sum). For full multi-currency
    detail, walk `buckets`. Net = `revenue - refund - cost - expense`.
    """

    model_config = ConfigDict(extra="forbid")

    period: PLPeriodLiteral
    currency: str
    revenue: Decimal
    cost: Decimal
    expense: Decimal
    refund: Decimal
    transfer: Decimal
    net: Decimal
    transaction_count: int
    buckets: list[PLBucket]
