"""Unit tests for src.pricing (Kanban #1210 AC#3).

Pure-logic tests — no DB, no fixtures, no async. The pricing module is a
module-level dict + a string-keyed lookup function; tests exercise:

- Bare-name → anthropic default backcompat
- Prefixed `vendor:model` resolution (including model names with `-` / `.`)
- Unknown vendor / unknown model → None
- Direction validation (ValueError on anything except 'input'/'output')
- Case sensitivity (typos return None, do not silently match)
- Coverage sweep: every (vendor, model) combination in MODEL_PRICING has
  non-None float prices for both directions
"""

from __future__ import annotations

import pytest

from src.pricing import MODEL_PRICING, lookup_price


# ----------------------------------------------------------------------
# Bare-name resolution (defaults to anthropic vendor per D3 backcompat)
# ----------------------------------------------------------------------

def test_lookup_bare_opus_input() -> None:
    assert lookup_price("opus", "input") == 15.00


def test_lookup_bare_opus_output() -> None:
    assert lookup_price("opus", "output") == 75.00


def test_lookup_bare_sonnet_input() -> None:
    assert lookup_price("sonnet", "input") == 3.00


def test_lookup_bare_sonnet_output() -> None:
    assert lookup_price("sonnet", "output") == 15.00


def test_lookup_bare_haiku_input() -> None:
    assert lookup_price("haiku", "input") == 0.80


def test_lookup_bare_haiku_output() -> None:
    assert lookup_price("haiku", "output") == 4.00


# ----------------------------------------------------------------------
# Prefixed `vendor:model` resolution
# ----------------------------------------------------------------------

def test_lookup_prefixed_openai_gpt_4o_input() -> None:
    assert lookup_price("openai:gpt-4o", "input") == 2.50


def test_lookup_prefixed_openai_gpt_4o_output() -> None:
    assert lookup_price("openai:gpt-4o", "output") == 10.00


def test_lookup_prefixed_gemini_flash_output() -> None:
    assert lookup_price("gemini:2.5-flash", "output") == 2.50


def test_lookup_prefixed_local_7b_input() -> None:
    assert lookup_price("local:7b", "input") == 0.0002


# ----------------------------------------------------------------------
# Edge cases: hyphens + dots in model names, vendor:model split-on-first
# ----------------------------------------------------------------------

def test_lookup_model_name_with_hyphens_and_dots() -> None:
    """Model `2.5-flash-lite` contains both `.` and `-` — must round-trip
    cleanly via the split-on-first-colon rule."""
    assert lookup_price("gemini:2.5-flash-lite", "input") == 0.10
    assert lookup_price("gemini:2.5-flash-lite", "output") == 0.40


def test_lookup_prefixed_anthropic_explicit() -> None:
    """Explicit `anthropic:opus` works identically to bare `opus`."""
    assert lookup_price("anthropic:opus", "input") == 15.00
    assert lookup_price("anthropic:opus", "output") == 75.00


# ----------------------------------------------------------------------
# Unknown vendor / unknown model → None
# ----------------------------------------------------------------------

def test_lookup_unknown_vendor_returns_none() -> None:
    assert lookup_price("unknownvendor:foo", "input") is None


def test_lookup_unknown_model_known_vendor_returns_none() -> None:
    assert lookup_price("anthropic:nosuchmodel", "input") is None


def test_lookup_bare_unknown_model_returns_none() -> None:
    """Bare name that isn't a known anthropic model resolves to anthropic
    vendor + unknown model → None."""
    assert lookup_price("gpt-4o", "input") is None


def test_lookup_meta_key_as_vendor_returns_none() -> None:
    """`_last_updated` and `_notes` are scalar meta entries on the top
    level — they must NOT be confused for vendor tables."""
    assert lookup_price("_last_updated:foo", "input") is None
    assert lookup_price("_notes:foo", "input") is None


# ----------------------------------------------------------------------
# Case sensitivity — table keys are canonical; typos return None
# ----------------------------------------------------------------------

def test_lookup_uppercase_bare_returns_none() -> None:
    """`OPUS` is NOT a valid key — case-sensitive lookup."""
    assert lookup_price("OPUS", "input") is None


def test_lookup_uppercase_vendor_prefix_returns_none() -> None:
    assert lookup_price("Anthropic:opus", "input") is None


def test_lookup_uppercase_model_after_prefix_returns_none() -> None:
    assert lookup_price("anthropic:OPUS", "input") is None


# ----------------------------------------------------------------------
# Direction validation
# ----------------------------------------------------------------------

def test_lookup_invalid_direction_raises_value_error() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        lookup_price("opus", "wrong")  # type: ignore[arg-type]


def test_lookup_empty_direction_raises_value_error() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        lookup_price("opus", "")  # type: ignore[arg-type]


def test_lookup_uppercase_direction_raises_value_error() -> None:
    """Direction is also case-sensitive — `'Input'` is not `'input'`."""
    with pytest.raises(ValueError, match="direction must be"):
        lookup_price("opus", "Input")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Coverage sweep — every entry resolves to a non-None float in both dirs
# ----------------------------------------------------------------------

def test_pricing_table_has_meta_markers() -> None:
    """The `_last_updated` + `_notes` keys must be present (grep-anchor
    for stale-pricing detection)."""
    assert "_last_updated" in MODEL_PRICING
    assert "_notes" in MODEL_PRICING
    assert isinstance(MODEL_PRICING["_last_updated"], str)
    assert isinstance(MODEL_PRICING["_notes"], str)


def test_pricing_table_last_updated_is_iso_date() -> None:
    """Loose YYYY-MM-DD shape check on the tombstone."""
    val = MODEL_PRICING["_last_updated"]
    assert len(val) == 10 and val[4] == "-" and val[7] == "-"


def test_pricing_table_has_all_vendors() -> None:
    """Canonical set: anthropic, openai, gemini, local. deepseek removed #1838."""
    for vendor in ("anthropic", "openai", "gemini", "local"):
        assert vendor in MODEL_PRICING
        assert isinstance(MODEL_PRICING[vendor], dict)
        assert len(MODEL_PRICING[vendor]) > 0
    assert "deepseek" not in MODEL_PRICING


def test_pricing_table_per_vendor_counts_match_spec() -> None:
    """Locked counts: anthropic=3, openai=5, gemini=3, local=6."""
    assert len(MODEL_PRICING["anthropic"]) == 3
    assert len(MODEL_PRICING["openai"]) == 5
    assert len(MODEL_PRICING["gemini"]) == 3
    assert len(MODEL_PRICING["local"]) == 6


def test_every_entry_has_input_and_output_floats() -> None:
    """Sweep every (vendor, model) — both directions must resolve to a
    non-None positive float via the public helper."""
    for vendor, table in MODEL_PRICING.items():
        if vendor.startswith("_"):
            continue  # meta key
        assert isinstance(table, dict)
        for model in table:
            for direction in ("input", "output"):
                price = lookup_price(f"{vendor}:{model}", direction)  # type: ignore[arg-type]
                assert price is not None, f"{vendor}:{model} {direction} returned None"
                assert isinstance(price, float)
                assert price > 0, f"{vendor}:{model} {direction} non-positive"
