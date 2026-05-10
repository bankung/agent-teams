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
    """Spec lock: V1 ships exactly the three Anthropic models."""
    from src.services.cost_tracker import PRICING

    assert ("anthropic", "claude-opus-4-7") in PRICING
    assert ("anthropic", "claude-sonnet-4-6") in PRICING
    assert ("anthropic", "claude-haiku-4-5-20251001") in PRICING
    assert PRICING[("anthropic", "claude-opus-4-7")] == {"input": 15.0, "output": 75.0}
