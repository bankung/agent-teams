"""Tests for services.cost_tracker (CTX-3, Kanban #718).

Pricing table is locked from Anthropic's public price card 2026-05.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


def test_compute_cost_opus_1m_in_1m_out_is_90_dollars() -> None:
    """1M input @ $15 + 1M output @ $75 = $90.0000 exactly."""
    from src.services.cost_tracker import compute_cost

    result = compute_cost("anthropic", "claude-opus-4-7", 1_000_000, 1_000_000)
    assert result == Decimal("90.0000")


def test_compute_cost_sonnet_500k_in_100k_out() -> None:
    """500k input @ $3/1M = $1.5; 100k output @ $15/1M = $1.5; total = $3.0000."""
    from src.services.cost_tracker import compute_cost

    result = compute_cost("anthropic", "claude-sonnet-4-6", 500_000, 100_000)
    assert result == Decimal("3.0000")


def test_compute_cost_haiku_zero_tokens_is_zero() -> None:
    from src.services.cost_tracker import compute_cost

    result = compute_cost("anthropic", "claude-haiku-4-5-20251001", 0, 0)
    assert result == Decimal("0.0000")


def test_compute_cost_returns_decimal_with_4_places() -> None:
    """numeric(10,4) — quantized to 4 decimal places."""
    from src.services.cost_tracker import compute_cost

    result = compute_cost("anthropic", "claude-opus-4-7", 1, 1)
    # 15/1M + 75/1M = 0.000015 + 0.000075 = 0.00009 → quantized to 0.0001
    assert result.as_tuple().exponent == -4
    assert result == Decimal("0.0001")


def test_compute_cost_unknown_model_raises_value_error() -> None:
    from src.services.cost_tracker import compute_cost

    with pytest.raises(ValueError, match="unknown"):
        compute_cost("anthropic", "claude-mythical-99", 1000, 1000)


def test_compute_cost_unknown_provider_raises_value_error() -> None:
    from src.services.cost_tracker import compute_cost

    with pytest.raises(ValueError, match="unknown"):
        compute_cost("openai", "gpt-99", 1000, 1000)


def test_pricing_table_has_three_locked_models() -> None:
    """Spec lock: the core Anthropic exact keys are present.

    V1 CTX-3 locked three keys (opus-4-7, sonnet-4-6, haiku-4-5-20251001).
    Kanban #2301 added claude-opus-4-8 as a 4th exact key.
    Kanban #944 added claude-opus-4-x and claude-haiku alias entries.
    This test pins all exact-key presences so regressions are caught immediately.
    """
    from src.services.cost_tracker import PRICING

    assert ("anthropic", "claude-opus-4-7") in PRICING
    assert ("anthropic", "claude-sonnet-4-6") in PRICING
    assert ("anthropic", "claude-haiku-4-5-20251001") in PRICING
    assert ("anthropic", "claude-opus-4-8") in PRICING
    assert PRICING[("anthropic", "claude-opus-4-7")] == {"input": 15.0, "output": 75.0}


# ---------------------------------------------------------------------------
# Cache-aware pricing (Kanban #1186)
# ---------------------------------------------------------------------------


def test_compute_cost_backward_compat_no_cache_fields() -> None:
    """Callers passing only (input, output) tokens — no cache fields — must
    get the same result as before #1186. This is the load-bearing backward
    compat invariant: every existing caller in the codebase predates cache
    fields and must keep working unchanged.
    """
    from src.services.cost_tracker import compute_cost

    # Identical to test_compute_cost_sonnet_500k_in_100k_out (existing test).
    result_no_cache = compute_cost(
        "anthropic", "claude-sonnet-4-6", 500_000, 100_000
    )
    # Same call, but explicit zero cache fields.
    result_zero_cache = compute_cost(
        "anthropic", "claude-sonnet-4-6", 500_000, 100_000,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    assert result_no_cache == Decimal("3.0000")
    assert result_zero_cache == Decimal("3.0000")


def test_compute_cost_cache_read_priced_at_0_10x_input() -> None:
    """Cache read = 0.10x base input rate.

    Sonnet input = $3/1M. 1M cache-read tokens at 0.10x = $0.30. Zero regular
    input + zero output → total $0.3000.
    """
    from src.services.cost_tracker import compute_cost

    result = compute_cost(
        "anthropic",
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=0,
    )
    # 1M * $3 * 0.10 / 1M = $0.30 exactly.
    assert result == Decimal("0.3000")


def test_compute_cost_cache_creation_priced_at_1_25x_input() -> None:
    """Cache write = 1.25x base input rate. 1M write tokens on Sonnet input
    $3/1M at 1.25x = $3.75.
    """
    from src.services.cost_tracker import compute_cost

    result = compute_cost(
        "anthropic",
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=1_000_000,
    )
    # 1M * $3 * 1.25 / 1M = $3.75 exactly.
    assert result == Decimal("3.7500")


def test_compute_cost_combined_cache_read_write_plus_regular() -> None:
    """All four cost components add together.

    Sonnet: input $3/1M, output $15/1M.
    - 100k regular input  → 100_000 * 3 / 1M       = $0.30
    - 10k output          → 10_000 * 15 / 1M       = $0.15
    - 10k cache_creation  → 10_000 * 3 * 1.25 / 1M = $0.0375
    - 50k cache_read      → 50_000 * 3 * 0.10 / 1M = $0.015
    Total = $0.5025
    """
    from src.services.cost_tracker import compute_cost

    result = compute_cost(
        "anthropic",
        "claude-sonnet-4-6",
        input_tokens=100_000,
        output_tokens=10_000,
        cache_read_input_tokens=50_000,
        cache_creation_input_tokens=10_000,
    )
    assert result == Decimal("0.5025")


def test_compute_cost_cache_savings_vs_uncached_amortization() -> None:
    """The whole point of caching: after the first call, cache-read cost is
    10% of the regular-input cost it replaces.

    Simulate a 10-iteration loop with a 10k stable prefix.
    - WITHOUT cache: 10 * 10k * $3 / 1M = $0.30 input
    - WITH cache: iter 1 writes (1.25x) + iters 2-10 read (0.10x).
      Write: 10_000 * 3 * 1.25 / 1M = $0.0375
      Read:  9 * 10_000 * 3 * 0.10 / 1M = $0.027
      Total cached input = $0.0645
    - Savings = ($0.30 - $0.0645) / $0.30 = 78.5%
    """
    from src.services.cost_tracker import compute_cost

    # Uncached: 10 calls each charging 10k input tokens at base rate, no output.
    uncached_total = Decimal("0")
    for _ in range(10):
        uncached_total += compute_cost(
            "anthropic", "claude-sonnet-4-6", 10_000, 0
        )
    # Cached: first call writes the cache (creation=10k); next 9 calls read it.
    cached_total = compute_cost(
        "anthropic", "claude-sonnet-4-6",
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=10_000,
    )
    for _ in range(9):
        cached_total += compute_cost(
            "anthropic", "claude-sonnet-4-6",
            input_tokens=0, output_tokens=0,
            cache_read_input_tokens=10_000,
            cache_creation_input_tokens=0,
        )

    # Savings must exceed the AC3 floor of 40%.
    savings_pct = (uncached_total - cached_total) / uncached_total * 100
    assert savings_pct > Decimal("40"), (
        f"cache savings {savings_pct}% under 40% floor; expected ~78.5%"
    )
    # Exact arithmetic check.
    assert uncached_total == Decimal("0.3000")
    assert cached_total == Decimal("0.0645")


# ---------------------------------------------------------------------------
# Kanban #2301 — claude-opus-4-8 exact key + updated rates
# ---------------------------------------------------------------------------


def test_resolve_pricing_key_opus_4_8_returns_exact_key_not_alias() -> None:
    """claude-opus-4-8 must hit the exact key, not the claude-opus-4-x alias.

    The exact key carries $5/$25; the alias carries legacy $15/$75. If the
    alias fires instead, cost accounting is 3x too high.
    """
    from src.services.cost_tracker import PRICING, resolve_pricing_key

    key = resolve_pricing_key("anthropic", "claude-opus-4-8")
    assert key == ("anthropic", "claude-opus-4-8"), f"expected exact key, got {key}"
    assert PRICING[key] == {"input": 5.0, "output": 25.0}


def test_compute_cost_opus_4_8_uses_5_25_rates() -> None:
    """1M input @ $5 + 1M output @ $25 = $30.0000 (not legacy $90)."""
    from src.services.cost_tracker import compute_cost

    result = compute_cost("anthropic", "claude-opus-4-8", 1_000_000, 1_000_000)
    assert result == Decimal("30.0000"), f"expected $30.0000 at $5/$25 rates, got {result}"


def test_haiku_4_5_rate_updated_to_1_5() -> None:
    """claude-haiku-4-5-20251001 updated to $1/$5 (Kanban #2301 verified 2026-06-11)."""
    from src.services.cost_tracker import PRICING

    rates = PRICING[("anthropic", "claude-haiku-4-5-20251001")]
    assert rates == {"input": 1.0, "output": 5.0}, f"expected {{input:1.0, output:5.0}}, got {rates}"


# ---------------------------------------------------------------------------
# Opus-4-8 versioned-id pricing guard (2026-06-13, Fix 2).
# ---------------------------------------------------------------------------


def test_resolve_pricing_key_opus_4_8_versioned_id_uses_5_25_rates() -> None:
    """A versioned model id like 'claude-opus-4-8-20250514' must resolve to
    ('anthropic', 'claude-opus-4-8') at $5/$25, NOT the generic 'claude-opus-4-x'
    alias at $15/$75 (which would be 3x overcount).

    The guard 'if "opus-4-8" in m' must fire BEFORE the generic 'if "opus" in m'.
    """
    from src.services.cost_tracker import PRICING, resolve_pricing_key

    key = resolve_pricing_key("anthropic", "claude-opus-4-8-20250514")
    assert key == ("anthropic", "claude-opus-4-8"), (
        f"versioned opus-4-8 id resolved to {key!r}; expected exact claude-opus-4-8 key"
    )
    # Confirm the resolved key carries the correct $5/$25 rates, not legacy $15/$75.
    assert PRICING[key] == {"input": 5.0, "output": 25.0}
