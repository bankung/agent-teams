"""P&L summary schema (Kanban #953).

Returned by `GET /api/projects/{id}/pl`. The service layer
(`src.services.pl_calculator`) groups transactions by `(currency, period)`
and rolls up per-kind sums + net.

Amounts are MAJOR units (USD dollars, THB baht, JPY yen) — not minor —
because the FE renders them and the accountant reads them. The minor →
major conversion happens server-side in the calculator using
`MINOR_DIVISOR_BY_CURRENCY`.

Multi-currency policy: NO FX conversion. If a project has txns in
multiple currencies, each currency lands in its own bucket. The top-level
`currency` field reflects the FIRST currency observed (or the project's
`currency_default` when the result is empty); `buckets` carries the
per-(currency, period) breakdown.

`buckets` carries one entry per (currency, period-label) pair. Net per
bucket is `revenue - refund - cost - expense` (transfer is neutral —
it represents money moving between accounts, not P&L impact).

Cross-project rollup (Kanban #1329):
  - `PLCrossProjectRow` — one row per project; top-level totals mirror
    PLSummary first-currency semantics.
  - `PLCrossProject` — wrapper returned by `GET /api/pnl`.
  - `grand_total_net_first_currency_only` is non-null only when every row
    shares the same `currency_default` AND no row has `mixed_currency=True`.
    Otherwise the FE should render per-row breakdowns instead.
"""

from __future__ import annotations

from datetime import datetime
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


class PLCrossProjectRow(BaseModel):
    """One row in the cross-project P&L rollup (Kanban #1329).

    Amounts reflect the project's `currency_default` (first-currency semantics
    from PLSummary — NO FX conversion). When `mixed_currency=True` the
    displayed totals are first-currency-only; the FE should show a badge so
    the operator knows to click through for per-currency detail.

    `bucket_count` is the total number of (currency, period-label) buckets
    compute_pl produced — signals richness of per-project detail available via
    the existing `GET /api/projects/{id}/pl` endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int
    project_name: str
    team: str
    currency_default: str
    period: PLPeriodLiteral
    revenue: Decimal
    cost: Decimal
    expense: Decimal
    refund: Decimal
    transfer: Decimal
    net: Decimal
    transaction_count: int
    mixed_currency: bool
    bucket_count: int


class PLCrossProject(BaseModel):
    """Cross-project P&L rollup response for `GET /api/pnl` (Kanban #1329).

    `rows` contains one entry per scanned project (status=1 by default;
    include_killed=true also returns soft-deleted projects).

    `grand_total_net_first_currency_only` is non-null ONLY when every row
    shares the same `currency_default` AND no row reports `mixed_currency=True`.
    Otherwise null — the FE should render per-row breakdowns rather than a
    misleading aggregate.

    `failed_project_ids` is non-empty when one or more per-project compute_pl
    calls raised an unexpected error (Kanban #1381). The response is still 200
    so partial results are usable; callers should surface a warning when this
    list is non-empty. An all-projects failure (every project raised) still
    returns 500.

    Amounts are MAJOR units; no FX conversion is performed anywhere in this
    endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    period: PLPeriodLiteral
    since: datetime
    until: datetime
    rows: list[PLCrossProjectRow]
    total_projects: int
    grand_total_net_first_currency_only: Decimal | None
    failed_project_ids: list[int] = []
