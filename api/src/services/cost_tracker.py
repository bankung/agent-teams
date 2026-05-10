"""Cost computation from token totals + provider/model price card (CTX-3, #718).

V1 ships a hardcoded `PRICING` dict. When provider abstraction lands (out of
CTX scope), this module flips to a DB-backed lookup. Prices are USD per
million tokens, locked from Anthropic's public price card 2026-05.

`session_runs.total_cost_usd` is `numeric(10,4)` — we round to 4 decimal
places so the value lands cleanly in the column.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# USD per 1M tokens. Mirror of the spec's locked direction (CTX-3).
PRICING: dict[tuple[str, str], dict[str, float]] = {
    ("anthropic", "claude-opus-4-7"): {"input": 15.0, "output": 75.0},
    ("anthropic", "claude-sonnet-4-6"): {"input": 3.0, "output": 15.0},
    ("anthropic", "claude-haiku-4-5-20251001"): {"input": 0.8, "output": 4.0},
}

_PER_MILLION = Decimal("1000000")
_QUANT = Decimal("0.0001")


def compute_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> Decimal:
    """Return total USD cost for the run, rounded to 4 decimal places.

    Raises `ValueError` for unknown `(provider, model)` pairs — the caller
    decides whether to log + leave the column unchanged or propagate.
    """
    key = (provider, model)
    rates = PRICING.get(key)
    if rates is None:
        raise ValueError(
            f"unknown (provider, model) pair: {provider!r}, {model!r}"
        )
    input_cost = (Decimal(str(rates["input"])) * Decimal(input_tokens)) / _PER_MILLION
    output_cost = (
        Decimal(str(rates["output"])) * Decimal(output_tokens)
    ) / _PER_MILLION
    total = input_cost + output_cost
    return total.quantize(_QUANT, rounding=ROUND_HALF_UP)
