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
# Kanban #944 (2026-05-16): added openai (gpt-4o, gpt-4o-mini) + ollama (local,
# zero-cost). The "anthropic claude-haiku" + "anthropic claude-opus-4-x" alias
# entries are spec'd in #944 with rounded-tier rates ($1/$5 + $15/$75) — they
# coexist with the precise model-tagged keys above (e.g. claude-haiku-4-5-...
# at $0.8/$4 from the V1 CTX-3 lock). The task-cost estimator (services/
# task_cost_estimator.py) resolves the env-supplied model name to the right
# key via a normalizer; both name-shapes are reachable here.
PRICING: dict[tuple[str, str], dict[str, float]] = {
    ("anthropic", "claude-opus-4-7"): {"input": 15.0, "output": 75.0},
    ("anthropic", "claude-sonnet-4-6"): {"input": 3.0, "output": 15.0},
    ("anthropic", "claude-haiku-4-5-20251001"): {"input": 0.8, "output": 4.0},
    # #944 generic tier names (env-var ANTHROPIC_MODEL aliases). Resolver maps
    # any "claude-opus-4-*" / "claude-haiku*" string to these when the precise
    # tag doesn't hit. Rates from the #944 spec.
    ("anthropic", "claude-opus-4-x"): {"input": 15.0, "output": 75.0},
    ("anthropic", "claude-haiku"): {"input": 1.0, "output": 5.0},
    # OpenAI (Kanban #944) — rates locked from the #944 spec; reconfirm when
    # the openai provider abstraction lands.
    ("openai", "gpt-4o"): {"input": 2.50, "output": 10.0},
    ("openai", "gpt-4o-mini"): {"input": 0.15, "output": 0.60},
    # Ollama (Kanban #944) — local inference, $0 by definition. Single
    # placeholder key; specific local model identifiers (llama3, qwen, etc.)
    # collapse to ("ollama", "local") via the estimator's resolver. The
    # compute_cost call returns Decimal('0.0000') exact via the zero-rate.
    ("ollama", "local"): {"input": 0.0, "output": 0.0},
}

_PER_MILLION = Decimal("1000000")
_QUANT = Decimal("0.0001")

# Anthropic prompt-caching multipliers (Kanban #1186).
# Cache writes cost 1.25x the base input rate; cache reads cost 0.10x. Locked
# from Anthropic's public price card 2026-05 (5-min ephemeral TTL). The two
# multipliers are intentionally module constants so future-tier pricing (1-hour
# TTL = 2.0x write, etc.) can land as a parallel constant without disturbing
# the regular-input path.
_CACHE_WRITE_MULTIPLIER = Decimal("1.25")
_CACHE_READ_MULTIPLIER = Decimal("0.10")


def compute_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> Decimal:
    """Return total USD cost for the run, rounded to 4 decimal places.

    Args:
        provider: `"anthropic"` | `"openai"` | `"ollama"`.
        model: model identifier (e.g., `"claude-sonnet-4-6"`).
        input_tokens: regular (non-cached) input tokens billed at the base
            input rate. **Excludes** cache reads + cache writes — the
            Anthropic API reports `usage.input_tokens` as the non-cached
            remainder, so callers should pass that field directly.
        output_tokens: output tokens billed at the base output rate.
        cache_read_input_tokens: tokens served from the prompt cache
            (Kanban #1186). Billed at `0.10x` the base input rate. Defaults
            to 0 — callers that don't pass this field get the pre-#1186
            behavior (backward compatible).
        cache_creation_input_tokens: tokens written into the prompt cache
            on first read (Kanban #1186). Billed at `1.25x` the base input
            rate. Defaults to 0.

    Returns:
        Total USD cost as Decimal, quantized to 4 decimal places.

    Raises:
        ValueError: unknown `(provider, model)` pair — the caller decides
            whether to log + leave the column unchanged or propagate.
    """
    key = (provider, model)
    rates = PRICING.get(key)
    if rates is None:
        raise ValueError(
            f"unknown (provider, model) pair: {provider!r}, {model!r}"
        )
    base_input_rate = Decimal(str(rates["input"]))
    base_output_rate = Decimal(str(rates["output"]))

    input_cost = (base_input_rate * Decimal(input_tokens)) / _PER_MILLION
    output_cost = (base_output_rate * Decimal(output_tokens)) / _PER_MILLION
    cache_write_cost = (
        base_input_rate * _CACHE_WRITE_MULTIPLIER * Decimal(cache_creation_input_tokens)
    ) / _PER_MILLION
    cache_read_cost = (
        base_input_rate * _CACHE_READ_MULTIPLIER * Decimal(cache_read_input_tokens)
    ) / _PER_MILLION

    total = input_cost + output_cost + cache_write_cost + cache_read_cost
    return total.quantize(_QUANT, rounding=ROUND_HALF_UP)
