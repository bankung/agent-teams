"""Unit tests for src.services.pl_calculator (Kanban #953).

Pure-function tests — no DB, no HTTP. Uses lightweight test-double objects
that quack like ORM Transaction rows (amount_minor, currency, kind, occurred_at).

Coverage matrix:
  - empty input → zero summary with correct shape + currency default
  - single transaction per kind → correct totals + buckets
  - multi-period bucketing (daily/weekly/monthly/quarterly/yearly)
  - multi-currency: separate buckets, no FX; top-level reflects first currency
  - net formula: revenue - refund - cost - expense; transfer neutral
  - period boundary edges (last second of period, first second of next)
  - minor → major conversion (USD=100, JPY=1, unknown=100)
  - unknown currency falls back to 100-divisor
  - unknown kind silently skipped
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.services.pl_calculator import (
    MINOR_DIVISOR_BY_CURRENCY,
    compute_pl,
    minor_to_major,
    period_label,
)


@dataclass
class _FakeTxn:
    """Test double for an ORM Transaction row (duck-typed)."""

    amount_minor: int
    currency: str
    kind: str
    occurred_at: datetime


# -----------------------------------------------------------------------------
# minor_to_major
# -----------------------------------------------------------------------------


def test_minor_to_major_usd_uses_100_divisor():
    assert minor_to_major(12345, "USD") == Decimal("123.4500")


def test_minor_to_major_jpy_uses_1_divisor_exact_integer():
    assert minor_to_major(12345, "JPY") == Decimal(12345)


def test_minor_to_major_thb_uses_100_divisor():
    assert minor_to_major(100, "THB") == Decimal("1.0000")


def test_minor_to_major_unknown_currency_falls_back_to_100():
    # Unknown ISO code → defaults to 100 (dominant fiat pattern).
    assert minor_to_major(500, "XYZ") == Decimal("5.0000")


def test_minor_to_major_handles_lowercase_currency_input():
    # Service uppercases on lookup.
    assert minor_to_major(100, "usd") == Decimal("1.0000")


def test_minor_divisor_map_includes_core_currencies():
    # Sanity — the curated map must include the project's typical defaults.
    for code in ("USD", "THB", "EUR", "JPY"):
        assert code in MINOR_DIVISOR_BY_CURRENCY


# -----------------------------------------------------------------------------
# period_label
# -----------------------------------------------------------------------------


def test_period_label_daily():
    dt = datetime(2026, 5, 17, 12, 30, tzinfo=timezone.utc)
    assert period_label(dt, "daily") == "2026-05-17"


def test_period_label_weekly_iso_week():
    # 2026-05-17 is a Sunday → ISO week 20 of 2026.
    dt = datetime(2026, 5, 17, 12, 30, tzinfo=timezone.utc)
    assert period_label(dt, "weekly") == "2026-W20"


def test_period_label_monthly():
    dt = datetime(2026, 5, 17, 12, 30, tzinfo=timezone.utc)
    assert period_label(dt, "monthly") == "2026-05"


def test_period_label_quarterly_each_quarter():
    assert period_label(datetime(2026, 1, 1, tzinfo=timezone.utc), "quarterly") == "2026-Q1"
    assert period_label(datetime(2026, 4, 1, tzinfo=timezone.utc), "quarterly") == "2026-Q2"
    assert period_label(datetime(2026, 7, 1, tzinfo=timezone.utc), "quarterly") == "2026-Q3"
    assert period_label(datetime(2026, 10, 1, tzinfo=timezone.utc), "quarterly") == "2026-Q4"


def test_period_label_yearly():
    dt = datetime(2026, 5, 17, tzinfo=timezone.utc)
    assert period_label(dt, "yearly") == "2026"


def test_period_label_unknown_raises():
    with pytest.raises(ValueError):
        period_label(datetime.now(timezone.utc), "fortnightly")  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# compute_pl — empty + zero
# -----------------------------------------------------------------------------


def test_compute_pl_empty_returns_zero_summary_with_default_currency():
    out = compute_pl([], "monthly", project_currency_default="THB")
    assert out.period == "monthly"
    assert out.currency == "THB"
    assert out.revenue == Decimal(0)
    assert out.cost == Decimal(0)
    assert out.expense == Decimal(0)
    assert out.refund == Decimal(0)
    assert out.transfer == Decimal(0)
    assert out.net == Decimal(0)
    assert out.transaction_count == 0
    assert out.buckets == []


def test_compute_pl_empty_default_currency_when_omitted_is_usd():
    out = compute_pl([], "monthly")
    assert out.currency == "USD"


# -----------------------------------------------------------------------------
# compute_pl — single transactions per kind
# -----------------------------------------------------------------------------


def test_compute_pl_single_revenue_lands_in_one_bucket():
    txn = _FakeTxn(
        amount_minor=10000,  # $100.00
        currency="USD",
        kind="revenue",
        occurred_at=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
    )
    out = compute_pl([txn], "monthly")
    assert out.revenue == Decimal("100.0000")
    assert out.cost == Decimal(0)
    assert out.net == Decimal("100.0000")
    assert out.transaction_count == 1
    assert len(out.buckets) == 1
    assert out.buckets[0].label == "2026-05"
    assert out.buckets[0].currency == "USD"
    assert out.buckets[0].transaction_count == 1


def test_compute_pl_single_cost_subtracts_from_net():
    txn = _FakeTxn(5000, "USD", "cost", datetime(2026, 5, 17, tzinfo=timezone.utc))
    out = compute_pl([txn], "monthly")
    assert out.cost == Decimal("50.0000")
    assert out.net == Decimal("-50.0000")


def test_compute_pl_single_refund_subtracts_from_net():
    txn = _FakeTxn(2500, "USD", "refund", datetime(2026, 5, 17, tzinfo=timezone.utc))
    out = compute_pl([txn], "monthly")
    assert out.refund == Decimal("25.0000")
    assert out.net == Decimal("-25.0000")


def test_compute_pl_single_expense_subtracts_from_net():
    txn = _FakeTxn(7500, "USD", "expense", datetime(2026, 5, 17, tzinfo=timezone.utc))
    out = compute_pl([txn], "monthly")
    assert out.expense == Decimal("75.0000")
    assert out.net == Decimal("-75.0000")


def test_compute_pl_transfer_is_neutral_in_net():
    """Transfer represents bookkeeping movement, not P&L impact — must NOT
    affect net even though it appears in totals for visibility."""
    txn = _FakeTxn(50000, "USD", "transfer", datetime(2026, 5, 17, tzinfo=timezone.utc))
    out = compute_pl([txn], "monthly")
    assert out.transfer == Decimal("500.0000")
    assert out.net == Decimal(0)


# -----------------------------------------------------------------------------
# compute_pl — multi-kind net
# -----------------------------------------------------------------------------


def test_compute_pl_net_formula_revenue_minus_refund_cost_expense():
    """net = revenue - refund - cost - expense (transfer neutral)."""
    txns = [
        _FakeTxn(100000, "USD", "revenue", datetime(2026, 5, 1, tzinfo=timezone.utc)),
        _FakeTxn(10000, "USD", "refund", datetime(2026, 5, 2, tzinfo=timezone.utc)),
        _FakeTxn(20000, "USD", "cost", datetime(2026, 5, 3, tzinfo=timezone.utc)),
        _FakeTxn(15000, "USD", "expense", datetime(2026, 5, 4, tzinfo=timezone.utc)),
        _FakeTxn(99999, "USD", "transfer", datetime(2026, 5, 5, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "monthly")
    # 1000 - 100 - 200 - 150 = 550. Transfer ignored.
    assert out.net == Decimal("550.0000")
    assert out.transaction_count == 5
    assert len(out.buckets) == 1


# -----------------------------------------------------------------------------
# compute_pl — period bucketing
# -----------------------------------------------------------------------------


def test_compute_pl_daily_groups_separate_days_into_separate_buckets():
    txns = [
        _FakeTxn(1000, "USD", "revenue", datetime(2026, 5, 17, tzinfo=timezone.utc)),
        _FakeTxn(2000, "USD", "revenue", datetime(2026, 5, 18, tzinfo=timezone.utc)),
        _FakeTxn(3000, "USD", "revenue", datetime(2026, 5, 18, 23, 59, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "daily")
    assert len(out.buckets) == 2
    labels = [b.label for b in out.buckets]
    assert labels == ["2026-05-17", "2026-05-18"]
    assert out.buckets[1].revenue == Decimal("50.0000")
    assert out.buckets[1].transaction_count == 2


def test_compute_pl_monthly_groups_same_month_into_one_bucket():
    txns = [
        _FakeTxn(1000, "USD", "revenue", datetime(2026, 5, 1, tzinfo=timezone.utc)),
        _FakeTxn(2000, "USD", "revenue", datetime(2026, 5, 31, 23, 59, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "monthly")
    assert len(out.buckets) == 1
    assert out.buckets[0].label == "2026-05"
    assert out.buckets[0].revenue == Decimal("30.0000")


def test_compute_pl_quarterly_groups_correctly():
    txns = [
        _FakeTxn(100, "USD", "revenue", datetime(2026, 1, 15, tzinfo=timezone.utc)),
        _FakeTxn(200, "USD", "revenue", datetime(2026, 4, 15, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "quarterly")
    assert [b.label for b in out.buckets] == ["2026-Q1", "2026-Q2"]


def test_compute_pl_yearly_groups_correctly():
    txns = [
        _FakeTxn(100, "USD", "revenue", datetime(2025, 12, 31, tzinfo=timezone.utc)),
        _FakeTxn(200, "USD", "revenue", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "yearly")
    assert [b.label for b in out.buckets] == ["2025", "2026"]


def test_compute_pl_period_boundary_last_second_of_month_lands_in_that_month():
    txns = [
        _FakeTxn(1000, "USD", "revenue", datetime(2026, 5, 31, 23, 59, 59, tzinfo=timezone.utc)),
        _FakeTxn(2000, "USD", "revenue", datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "monthly")
    assert [b.label for b in out.buckets] == ["2026-05", "2026-06"]
    assert out.buckets[0].revenue == Decimal("10.0000")
    assert out.buckets[1].revenue == Decimal("20.0000")


# -----------------------------------------------------------------------------
# compute_pl — multi-currency
# -----------------------------------------------------------------------------


def test_compute_pl_multi_currency_yields_separate_buckets_no_fx():
    txns = [
        _FakeTxn(10000, "USD", "revenue", datetime(2026, 5, 1, tzinfo=timezone.utc)),
        _FakeTxn(35000, "THB", "revenue", datetime(2026, 5, 2, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "monthly")
    # Two buckets — USD and THB separately, no conversion.
    assert len(out.buckets) == 2
    currencies = sorted({b.currency for b in out.buckets})
    assert currencies == ["THB", "USD"]


def test_compute_pl_multi_currency_top_level_reflects_first_currency_only():
    """When multiple currencies are present, top-level totals reflect FIRST
    currency observed (USD wins here by occurred_at ASC). Cross-currency
    sums are meaningless without FX (out of scope MVP)."""
    txns = [
        _FakeTxn(50000, "USD", "revenue", datetime(2026, 5, 1, tzinfo=timezone.utc)),
        _FakeTxn(100000, "THB", "revenue", datetime(2026, 5, 2, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "monthly")
    assert out.currency == "USD"
    assert out.revenue == Decimal("500.0000")  # USD only — THB excluded


# -----------------------------------------------------------------------------
# compute_pl — defensive
# -----------------------------------------------------------------------------


def test_compute_pl_unknown_kind_silently_skipped():
    # DB CHECK should prevent this, but the calculator stays defensive.
    txns = [
        _FakeTxn(1000, "USD", "revenue", datetime(2026, 5, 1, tzinfo=timezone.utc)),
        _FakeTxn(9999, "USD", "wat", datetime(2026, 5, 2, tzinfo=timezone.utc)),
    ]
    out = compute_pl(txns, "monthly")
    assert out.revenue == Decimal("10.0000")
    assert out.transaction_count == 1  # unknown skipped


def test_compute_pl_handles_none_currency_falls_back_to_project_default():
    txn = _FakeTxn(1000, None, "revenue", datetime(2026, 5, 1, tzinfo=timezone.utc))  # type: ignore[arg-type]
    out = compute_pl([txn], "monthly", project_currency_default="EUR")
    assert out.currency == "EUR"
    assert out.buckets[0].currency == "EUR"
