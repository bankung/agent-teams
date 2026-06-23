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
  'claude-opus-4-8' for anthropic — the model interactive Lead sessions
  actually run; aligned with langgraph/llm.py's DEFAULT_ANTHROPIC_MODEL,
  Kanban #1304). For ollama, any local-model name collapses to
  ("ollama","local") in the price card lookup.

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

from src.constants import ResourceKind
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
# case. Kanban #1304: bumped sonnet-4-6 -> opus-4-8 to match the model interactive
# Lead sessions actually run (and langgraph/llm.py's DEFAULT_ANTHROPIC_MODEL).
# Shared with the #944 done-flip estimator — the bump is INTENDED there too.
_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

_ZERO_COST = Decimal("0.0000")

# --- Pre-task forecast constants (Kanban #1304) ----------------------------
# The role-brief (.claude/agents/<role>.md) is bundled into every spawn's
# cached prompt by langgraph/llm.py:_load_cacheable_bundle(). Its token count
# varies by role and is NOT stored in the DB, so V1 uses a single flat estimate.
# ~3000 tokens is a defensible mid-roster value (the dev-* briefs run roughly
# 2k–4k tokens).
# shortcut: flat constant, fine for V1 advisory forecast; upgrade: read the real
# .claude/agents/<role>.md token count per task.assigned_role (FS read or a
# precomputed role->tokens map) so the brief term reflects the actual agent.
ROLE_BRIEF_TOKEN_ESTIMATE = 3000

# Pre-run there are no actuals, so output tokens are a fixed fraction of the
# input estimate. 0.3 is the standard planning heuristic (LOCKED, #1304).
OUTPUT_TOKEN_RATIO = 0.3


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

    Defaults to ('anthropic', 'claude-opus-4-8') for interactive sessions
    that don't set the vars (Kanban #1304). Unknown providers fall back to
    anthropic + opus rather than raising — estimation is advisory, not
    load-bearing.
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


# ---------------------------------------------------------------------------
# Pre-task forecast (Kanban #1304)
# ---------------------------------------------------------------------------


def resolve_forecast_model(task: Any) -> tuple[str, str]:
    """Resolve (provider, model) for a PRE-run forecast.

    `task.model_override` (TEXT, nullable) wins when set: the provider is
    inferred from the string ('claude'->anthropic, 'gpt'->openai,
    'gemini'->google), falling back to the env provider for anything else (e.g.
    a bare tier name like 'opus'). When `model_override` is unset, defer to
    `resolve_provider_model()` (env-driven, same source the #944 done-flip
    estimator uses).

    Pure — reads only `task.model_override`. Never raises; an unknown override
    string is still returned verbatim so the caller's pricing lookup is the one
    place that decides the unknown-model fallback (cost $0 + confidence low).
    """
    override = (getattr(task, "model_override", None) or "").strip()
    if override:
        env_provider, _env_model = resolve_provider_model()
        m = override.lower()
        if "claude" in m:
            provider = "anthropic"
        elif "gpt" in m:
            provider = "openai"
        elif "gemini" in m:
            provider = "google"
        else:
            provider = env_provider
        return (provider, override)
    return resolve_provider_model()


def _derive_confidence(
    resources: list[Any], model_known: bool
) -> str:
    """Map the pinned-resource state + model-known flag to a confidence tier.

    - "low"  : any file resource has `size_bytes IS NULL` (not fully uploaded)
               OR the resolved model is unknown to the price card.
    - "high" : every file resource carries `tags.est_cost_if_full` (fully tagged).
    - "med"  : some file resources are tagged but >= 1 is untagged.

    Only `kind='file'` resources participate — links have no `est_cost_if_full`
    and never gate confidence. With no file resources at all, confidence is
    "high" when the model is known (nothing untagged to drag it down), else "low".
    """
    file_resources = [
        r for r in resources if getattr(r, "kind", None) == ResourceKind.FILE
    ]

    # Any not-fully-uploaded file, or an unknown model -> low (overrides all).
    if not model_known:
        return "low"
    if any(getattr(r, "size_bytes", None) is None for r in file_resources):
        return "low"

    if not file_resources:
        return "high"

    def _is_tagged(r: Any) -> bool:
        tags = getattr(r, "tags", None)
        return bool(tags) and isinstance(tags, dict) and "est_cost_if_full" in tags

    if all(_is_tagged(r) for r in file_resources):
        return "high"
    return "med"


def forecast_task_cost(
    task: Any, resources: list[Any] | None = None
) -> dict[str, Any]:
    """Forecast the USD cost of running `task` BEFORE it is spawned (#1304).

    Pure function — no DB I/O. The caller (the cost-forecast endpoint) pre-fetches
    the task's pinned resources and passes them in, the same way
    `estimate_task_cost` takes a pre-fetched `runs` list.

    Token model:
        prompt_tokens   = heuristic over (title + description + AC texts)
        role_brief_tokens = ROLE_BRIEF_TOKEN_ESTIMATE (flat V1 constant)
        resource_tokens = sum of tags.est_cost_if_full.approx_tokens over resources
        total_input    = prompt + role_brief + resource
        output_tokens  = int(total_input * OUTPUT_TOKEN_RATIO)

    Model resolution via `resolve_forecast_model` (task.model_override first,
    else env). Cost via `cost_tracker.compute_cost`; an unknown model collapses
    cost to $0 AND forces confidence='low'.

    Args:
        task: a `Task` ORM row (or any object exposing `title`, `description`,
            `acceptance_criteria`, `model_override`; missing/None treated empty).
        resources: pre-fetched list of `ProjectResource` rows pinned to the task.
            `None`/`[]` = no attachments.

    Returns:
        {
          "estimated_usd": Decimal (4dp),
          "estimated_tokens": int,            # total INPUT tokens
          "breakdown": {"prompt": int, "role_brief": int,
                        "attached_resources": int, "completion": int},
          "confidence": "low"|"med"|"high",
          "provider": str,
          "model": str,
        }
    """
    resources = resources or []

    title = getattr(task, "title", "") or ""
    description = getattr(task, "description", "") or ""
    acceptance_criteria = getattr(task, "acceptance_criteria", None) or []
    ac_text = " ".join(
        (ac.get("text") or "")
        for ac in acceptance_criteria
        if isinstance(ac, dict)
    )

    prompt_tokens = _heuristic_tokens(title + " " + description + " " + ac_text)
    role_brief_tokens = ROLE_BRIEF_TOKEN_ESTIMATE

    # Sum approx_tokens from each resource's est_cost_if_full tag. Guard r.tags
    # is None (the column is non-null in practice, but stay defensive) and the
    # nested keys being absent / null.
    resource_tokens = 0
    for r in resources:
        tags = getattr(r, "tags", None)
        if not isinstance(tags, dict):
            continue
        est = tags.get("est_cost_if_full")
        if not isinstance(est, dict):
            continue
        resource_tokens += est.get("approx_tokens", 0) or 0
    # Matches the #2361 usage_events le=1e9 token bound; a crafted/corrupted
    # tags.est_cost_if_full.approx_tokens must not overflow NUMERIC(10,4) into
    # a 500 (approx_tokens is normally bounded server-side by resource_verify —
    # this is belt-and-suspenders).
    resource_tokens = min(resource_tokens, 1_000_000_000)

    total_input_tokens = prompt_tokens + role_brief_tokens + resource_tokens
    output_tokens = int(total_input_tokens * OUTPUT_TOKEN_RATIO)

    provider, model = resolve_forecast_model(task)
    model_known = True
    try:
        key_provider, key_model = resolve_pricing_key(provider, model)
        cost = compute_cost(
            key_provider, key_model, total_input_tokens, output_tokens
        )
    except ValueError:
        # Unknown model — zero the cost, flag low confidence (price card miss).
        cost = _ZERO_COST
        model_known = False

    confidence = _derive_confidence(resources, model_known)

    return {
        "estimated_usd": cost.quantize(Decimal("0.0001")),
        "estimated_tokens": total_input_tokens,
        "breakdown": {
            "prompt": prompt_tokens,
            "role_brief": role_brief_tokens,
            "attached_resources": resource_tokens,
            "completion": output_tokens,
        },
        "confidence": confidence,
        "provider": provider,
        "model": model,
    }
