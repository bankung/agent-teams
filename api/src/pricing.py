"""Multi-vendor LLM pricing snapshot + lookup helper (Kanban #1210 D3).

Module-level `MODEL_PRICING` is a manual snapshot of public per-million-token
prices for the LLM vendors / sizes we currently care about, plus a synthetic
`local` category for self-hosted consumer-GPU runs (electricity + amortized
hardware). It is consumed by GOV2 project-auditor's budget-burn metric as a
fallback when a task row has no `estimated_cost_usd`.

`_last_updated` + `_notes` are tombstone markers on the table itself — grep
for either when refreshing prices. Refresh cadence: on demand when an
auditor report's drift between fallback and actual >10%, or when a vendor
publishes new pricing.

Vendor / model name resolution
==============================

`lookup_price(vendor_model, direction)` accepts two name shapes:

- **Bare name** (no `:` separator) — defaults to the `anthropic` vendor for
  backward compatibility with the `subagent_models[].model` field, which
  historically stored bare Anthropic names ("opus", "sonnet", "haiku").
- **Prefixed name** (`vendor:model`) — explicit vendor selector. The split
  is on the FIRST `:` only, so model names may contain `-` and `.`
  (`"openai:gpt-4o"`, `"gemini:2.5-flash-lite"`, `"local:7b"`).

Both vendor and model names are **case-sensitive** by design — the table
keys are the canonical spellings and we don't want silent acceptance of
typos like `"OPUS"` or `"Anthropic:opus"`. Lookups for unknown vendor or
unknown model return `None` (the auditor surfaces this via `coverage_pct`).

`local:*` cost note
===================

The `local` entries are placeholder estimates for self-hosted consumer-GPU
inference (electricity + amortized hardware over expected lifetime). They
are deliberately tiny and conservative. GOV2 / GOV5 will read a per-project
override from `projects.config.local_llm_cost_override` (a JSONB blob with
the same `{size: {input_per_M, output_per_M}}` shape) when present; that
override consumption is NOT implemented in this module — it is the
caller's responsibility to merge before invoking `lookup_price`.

Out of scope here (Kanban #1210 + future AA work)
-------------------------------------------------

- Cache pricing nuance (Anthropic input cache 90% off, OpenAI prompt
  caching) — would distort fallback math; revisit in GOV5 if material.
- Real-time pricing fetch from vendor APIs — table is a manual snapshot.
- Per-project override merging — caller's job.
"""

from __future__ import annotations

from typing import Literal

MODEL_PRICING: dict = {
    "anthropic": {
        "opus":   {"input_per_M":  5.00, "output_per_M": 25.00},
        "sonnet": {"input_per_M":  3.00, "output_per_M": 15.00},
        "haiku":  {"input_per_M":  1.00, "output_per_M":  5.00},
    },
    "openai": {
        "gpt-4.1":     {"input_per_M": 5.00,  "output_per_M": 15.00},
        "gpt-4o":      {"input_per_M": 2.50,  "output_per_M": 10.00},
        "gpt-4o-mini": {"input_per_M": 0.15,  "output_per_M":  0.60},
        "o1":          {"input_per_M": 15.00, "output_per_M": 60.00},
        "o3-mini":     {"input_per_M": 1.10,  "output_per_M":  4.40},
    },
    "gemini": {
        "2.5-pro":        {"input_per_M": 1.25, "output_per_M": 10.00},
        "2.5-flash":      {"input_per_M": 0.30, "output_per_M":  2.50},
        "2.5-flash-lite": {"input_per_M": 0.10, "output_per_M":  0.40},
    },
    "local": {
        # electricity + amortized consumer-GPU hardware; operator-tunable per-project
        # via projects.config.local_llm_cost_override JSONB
        "3b":  {"input_per_M": 0.0001, "output_per_M": 0.0001},
        "4b":  {"input_per_M": 0.0001, "output_per_M": 0.0001},
        "7b":  {"input_per_M": 0.0002, "output_per_M": 0.0002},
        "8b":  {"input_per_M": 0.0002, "output_per_M": 0.0002},
        "13b": {"input_per_M": 0.0005, "output_per_M": 0.0005},
        "70b": {"input_per_M": 0.005,  "output_per_M": 0.005},
    },
    "_last_updated": "2026-06-11",
    "_notes": (
        "Public pricing snapshot. Local LLM = electricity+amortized hardware "
        "estimate for consumer-GPU self-hosted; operator-tunable per project "
        "via projects.config.local_llm_cost_override."
    ),
}


def lookup_price(
    vendor_model: str,
    direction: Literal["input", "output"],
) -> float | None:
    """Resolve per-million-token price for a vendor/model + direction.

    Args:
        vendor_model: Either a bare model name (e.g. `"opus"` → defaults to
            anthropic vendor) or a prefixed `vendor:model` form (e.g.
            `"openai:gpt-4o"`, `"local:7b"`). Split on the FIRST `:` only —
            model names may legally contain `-` and `.`.
        direction: `"input"` or `"output"`. Any other value raises ValueError.

    Returns:
        The per-million-token USD price as a float, or `None` if the vendor
        or model is unknown. Vendor + model names are case-sensitive — typos
        return `None` rather than silently coercing.

    Raises:
        ValueError: if `direction` is not exactly `"input"` or `"output"`.
    """
    if direction not in ("input", "output"):
        raise ValueError(
            f"direction must be 'input' or 'output'; got {direction!r}"
        )

    if ":" in vendor_model:
        vendor, model = vendor_model.split(":", 1)
    else:
        vendor, model = "anthropic", vendor_model

    vendor_table = MODEL_PRICING.get(vendor)
    # Guard against the meta keys (`_last_updated`, `_notes`) returning a
    # str/scalar instead of a dict if a caller passes a prefixed lookup like
    # `"_last_updated:foo"` — defensive type check, not a hot path.
    if not isinstance(vendor_table, dict):
        return None

    entry = vendor_table.get(model)
    if not isinstance(entry, dict):
        return None

    key = f"{direction}_per_M"
    price = entry.get(key)
    if not isinstance(price, (int, float)):
        return None
    return float(price)
