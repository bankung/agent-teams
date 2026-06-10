"""Per-task LLM-cost estimator (Kanban #944).

Estimates input/output token counts and USD cost for a single task at
done-flip time (`process_status` transition <5 → 5). Two code paths:

1. Real-metering branch — if the task has linked `session_runs` rows, sum
   their `total_input_tokens` / `total_output_tokens` and use the rows'
   already-computed `total_cost_usd`. This is the truthful path and is
   preferred whenever data is available.

2. Heuristic branch — when no `session_runs` are linked (the typical case
   for tasks driven from an interactive Claude Code session without
   `session_runs` tracking), approximate from the task's textual fields:
       input_chars  = len(title) + len(description)
       output_chars = len(status_change_reason)
   Token count = chars / chars_per_token where chars_per_token is 2 for
   Thai/CJK-dominant text (>30% of chars in those Unicode blocks) and 4
   otherwise. USD cost = `compute_cost(provider, model, tokens_in, tokens_out)`
   from `cost_tracker`.

Provider/model resolution:
- env `LANGGRAPH_LLM_PROVIDER` (default 'anthropic')
- env `ANTHROPIC_MODEL` / `OPENAI_MODEL` per provider (default
  'claude-sonnet-4-6' for anthropic — the typical interactive-session
  baseline). For ollama, any local-model name collapses to ("ollama","local")
  in the price card lookup.

Public API:
    estimate_task_cost(task, runs) -> dict
        returns {"tokens_in": int, "tokens_out": int, "cost_usd": Decimal}

`runs` is a pre-fetched list of `SessionRun` rows (or any iterable of
objects exposing total_input_tokens / total_output_tokens / total_cost_usd).
The caller (PATCH handler) lifts the query — keeps this function pure and
unit-testable without a DB.

Idempotency contract lives in the PATCH handler — this function is a
pure compute. Repeated calls return identical results given identical input.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from src.services.cost_tracker import PRICING, compute_cost, resolve_pricing_key

# Thai Unicode block: U+0E00-U+0E7F.
# CJK Unified Ideographs (core): U+4E00-U+9FFF (covers Hangul-free Chinese +
# JP Kanji used by Anthropic tokenizers' dense-token range). Hiragana
# (U+3040-309F), Katakana (U+30A0-30FF) and the CJK Symbols + Punct block
# (U+3000-303F) round out the "dense" range — token compression there
# resembles Han characters. Lifted into module constants so the boundary
# is testable / amendable in one place.
_THAI_START, _THAI_END = 0x0E00, 0x0E7F
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
)

# Threshold: if dense-script chars exceed 30% of total, treat the string as
# Thai/CJK-dominant (2 chars/token). Spec value from #944.
_DENSE_RATIO_THRESHOLD = 0.30

# Chars-per-token approximations from the #944 spec.
_CPT_ASCII = 4
_CPT_DENSE = 2

# Default model resolution — interactive Claude Code sessions don't set these
# env vars, so the default needs to be sensible for the typical Lead-drove-it
# case. Spec: anthropic / claude-sonnet-4-6.
_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

_ZERO_COST = Decimal("0.0000")


def _is_dense_char(ch: str) -> bool:
    """True if ch is in the Thai or CJK ranges (chars/token ≈ 2 there)."""
    cp = ord(ch)
    if _THAI_START <= cp <= _THAI_END:
        return True
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def chars_per_token(text: str) -> int:
    """Return 2 if the text is Thai/CJK-dominant (>30%), else 4.

    Pure function — no I/O, no model state. Empty text returns 4 (the ASCII
    default) so the divisor exists; the caller multiplies by 0 chars anyway.
    """
    if not text:
        return _CPT_ASCII
    dense = sum(1 for ch in text if _is_dense_char(ch))
    total = len(text)
    return _CPT_DENSE if (dense / total) > _DENSE_RATIO_THRESHOLD else _CPT_ASCII


def _heuristic_tokens(text: str) -> int:
    """Approximate token count: max(0, chars // chars_per_token(text))."""
    if not text:
        return 0
    return len(text) // chars_per_token(text)


def resolve_provider_model() -> tuple[str, str]:
    """Read LANGGRAPH_LLM_PROVIDER + ANTHROPIC_MODEL / OPENAI_MODEL env vars.

    Defaults to ('anthropic', 'claude-sonnet-4-6') for interactive sessions
    that don't set the vars. Unknown providers fall back to anthropic +
    sonnet rather than raising — estimation is advisory, not load-bearing.
    """
    provider = (os.environ.get("LANGGRAPH_LLM_PROVIDER") or _DEFAULT_PROVIDER).lower()
    if provider == "openai":
        model = os.environ.get("OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL
        return ("openai", model)
    if provider == "ollama":
        # Specific local model name doesn't affect pricing (all $0). Collapse
        # to the placeholder key so PRICING lookup hits.
        return ("ollama", "local")
    # Default + anthropic branch.
    model = os.environ.get("ANTHROPIC_MODEL") or _DEFAULT_ANTHROPIC_MODEL
    return ("anthropic", model)


def _resolve_pricing_key(provider: str, model: str) -> tuple[str, str]:
    """Delegate to cost_tracker.resolve_pricing_key (Kanban #2135).

    Kept as a private wrapper here so existing internal callers in this module
    continue to work unchanged. The canonical implementation now lives in
    cost_tracker so sessions.py can import it without a cross-dependency.
    """
    return resolve_pricing_key(provider, model)


def _empty_result() -> dict[str, Any]:
    return {"tokens_in": 0, "tokens_out": 0, "cost_usd": _ZERO_COST}


def estimate_task_cost(task: Any, runs: list[Any] | None = None) -> dict[str, Any]:
    """Compute estimated tokens + USD cost for a single task.

    Args:
        task: a `Task` ORM row (or any object exposing `title`, `description`,
            `status_change_reason` attributes; missing/None attrs treated as
            empty strings).
        runs: pre-fetched list of `SessionRun` rows linked to this task. Pass
            `None` or `[]` to force the heuristic path. When non-empty, the
            sum-of-runs values shadow the heuristic entirely.

    Returns:
        `{"tokens_in": int, "tokens_out": int, "cost_usd": Decimal}`.
        - Real-metering: sums runs' totals; cost = sum of total_cost_usd.
        - Heuristic: tokens from chars/cpt; cost via cost_tracker.compute_cost.
        - Degenerate (no description, no runs): all-zero result.

    Never raises — unknown-model errors collapse to ZERO_COST so the PATCH
    handler can complete the done-flip without a 500. The caller (router)
    logs the underlying error.
    """
    # --- Real-metering branch -------------------------------------------------
    if runs:
        tokens_in = sum(int(r.total_input_tokens or 0) for r in runs)
        tokens_out = sum(int(r.total_output_tokens or 0) for r in runs)
        cost = sum(
            (Decimal(r.total_cost_usd) if r.total_cost_usd is not None else _ZERO_COST)
            for r in runs
        ) or _ZERO_COST
        # `sum` over Decimals starts at int 0; coerce explicitly to keep the
        # column-shape contract (Decimal with 4-place exponent).
        if not isinstance(cost, Decimal):
            cost = Decimal(cost)
        return {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost.quantize(Decimal("0.0001")),
        }

    # --- Heuristic branch -----------------------------------------------------
    title = getattr(task, "title", "") or ""
    description = getattr(task, "description", "") or ""
    output_text = getattr(task, "status_change_reason", "") or ""
    input_text = title + description

    # Degenerate case: nothing to count and no runs.
    if not input_text and not output_text:
        return _empty_result()

    tokens_in = _heuristic_tokens(input_text)
    tokens_out = _heuristic_tokens(output_text)

    provider, model = resolve_provider_model()
    try:
        key_provider, key_model = _resolve_pricing_key(provider, model)
        cost = compute_cost(key_provider, key_model, tokens_in, tokens_out)
    except ValueError:
        # Unknown provider/model — preserve token counts, zero the cost.
        cost = _ZERO_COST

    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost}
