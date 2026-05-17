"""Multi-provider chat-model factory.

Reads `LANGGRAPH_LLM_PROVIDER` (`anthropic` | `openai` | `ollama`, default
`anthropic`) plus the matching API key (or base URL, for ollama) + optional
model override, returns a langchain `BaseChatModel`. Lifespan in `graph.py`
calls `make_chat_model().invoke("ping")` during boot so any misconfiguration
surfaces BEFORE the container is marked healthy — better than a healthy
container that crashes on first /invoke.

Public surface (kept stable so callers — nodes.py, graph.py lifespan — don't
break across provider swaps):

- `make_chat_model(model: str | None = None) -> BaseChatModel`
- `resolve_provider() -> Literal["anthropic", "openai", "ollama"]`
- `resolve_model(provider: str | None = None) -> str`
- `DEFAULT_ANTHROPIC_MODEL`, `DEFAULT_OPENAI_MODEL`,
  `DEFAULT_OLLAMA_MODEL`, `DEFAULT_OLLAMA_BASE_URL` (module constants)

Provider SDKs (`langchain_anthropic`, `langchain_openai`, `langchain_ollama`)
are imported INSIDE the matching branch so an Anthropic-only deployment does
not pay the OpenAI/Ollama import cost at startup. `max_retries=1` is set for
anthropic + openai — dev wants a fast failure signal; prod can override
per-call via the model's `with_retry()` API without touching this factory.
Ollama runs locally so `max_retries` is left at the langchain default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from langchain_core.language_models import BaseChatModel

# ---------------------------------------------------------------------------
# Safety prelude (Kanban #1116 — L22 prevention)
# ---------------------------------------------------------------------------
#
# Every system message the engine sends to ANY provider (anthropic / openai /
# ollama / future DeepSeek #1086) must be prefixed with the safety prelude.
# Rationale (Phase 9B Ollama injection test 2026-05-17): provider-side RLHF
# safety guards are uneven — local LLMs like llama3.2 default-obey destructive
# task descriptions. With 4-line CLAUDE.md-style rules in the system prompt,
# llama3.2 FLIPPED to refuse. Cheap, high-leverage, provider-agnostic.
#
# File lives at `langgraph/safety_prelude.txt` (provider-agnostic LANGGRAPH-LOCAL
# copy). The same text should ALSO live at `context/standards/llm/safety-prelude.md`
# as the canonical universal-standard source — but that path is humans-only
# (zone rule Q1) so Lead proposes; humans elevate. Until then the langgraph
# local copy is the source of truth for the engine.
#
# Cached after first read — file is small + read-only.

SAFETY_PRELUDE_PATH = Path(__file__).parent / "safety_prelude.txt"
_SAFETY_PRELUDE_CACHE: str | None = None


def _load_safety_prelude() -> str:
    """Read + cache the safety prelude text.

    Raises RuntimeError with a clear message if the file is missing — the
    engine MUST NOT silently fall back to an unprefixed system prompt
    (would defeat the whole purpose of L22 prevention).
    """
    global _SAFETY_PRELUDE_CACHE
    if _SAFETY_PRELUDE_CACHE is None:
        if not SAFETY_PRELUDE_PATH.exists():
            raise RuntimeError(
                f"safety prelude file missing at {SAFETY_PRELUDE_PATH}; "
                "every langgraph LLM call must be prefixed with the prelude "
                "(Kanban #1116). Restore the file or check your container "
                "image."
            )
        _SAFETY_PRELUDE_CACHE = SAFETY_PRELUDE_PATH.read_text(encoding="utf-8")
    return _SAFETY_PRELUDE_CACHE


def build_system_message(role_brief: str) -> str:
    """Prepend the safety prelude to a role-specific system prompt.

    Call this at EVERY langgraph LLM call site that constructs a SystemMessage.
    The `\\n\\n---\\n\\n` separator gives the LLM a visual boundary between
    safety rules + role brief so a malformed role brief (e.g., one that itself
    contains `STRICT RULES — NEVER VIOLATE:`) doesn't fool the LLM into
    re-reading the brief as the rules.

    Args:
        role_brief: the role-specific system-prompt content (e.g.,
            `_SYSTEM_PROMPT` for the backend specialist or
            `_AUDITOR_LLM_SYSTEM_PROMPT` for the auditor).

    Returns:
        The full system-message string with safety prelude prepended.
    """
    return _load_safety_prelude() + "\n\n---\n\n" + role_brief

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"
# Ollama runs locally; `llama3.2` is a small, fast default. Users typically
# `ollama pull qwen2.5:7b` or similar for better quality and set OLLAMA_MODEL.
DEFAULT_OLLAMA_MODEL = "llama3.2"
# host.docker.internal resolves to the host gateway on Docker Desktop (Mac +
# Windows). Linux compose needs `extra_hosts: ["host.docker.internal:host-gateway"]`
# on the langgraph service to make this name resolvable.
DEFAULT_OLLAMA_BASE_URL = "http://host.docker.internal:11434"

_SUPPORTED_PROVIDERS = ("anthropic", "openai", "ollama")
ProviderName = Literal["anthropic", "openai", "ollama"]

# Strict model-name regex for anthropic/openai — lowercase letters, digits,
# dot, hyphen. Catches obvious typos (`claude_sonnet_4_6` with underscores,
# trailing whitespace, capital letters from a stray `ANTHROPIC_MODEL=Claude-Sonnet-4-6`)
# before the SDK call. Permissive on dot to allow `gpt-4o-2024-08-06`-style snapshots.
_MODEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
# Ollama tags use colons (size/quant suffixes: `qwen2.5:7b`,
# `llama3.2:3b-instruct-q4_K_M`), underscores, and uppercase letters in quant
# labels (the `K` and `M` in `q4_K_M` are case-significant in Ollama's
# quantization scheme). We accept `:` + `_` + uppercase ONLY for the ollama
# provider — keeping the anthropic underscore-typo guard intact via the
# stricter `_MODEL_NAME_RE` above. Two regexes is clearer than a runtime
# branch inside one combined pattern. Leading char still lowercase/digit so
# `Llama3.2` (operator typo) is still caught.
_OLLAMA_MODEL_NAME_RE = re.compile(r"^[a-z0-9][a-zA-Z0-9._:\-]*$")


def resolve_provider() -> ProviderName:
    """Return the configured provider name, normalized + validated.

    Raises RuntimeError with a clear message on an unknown value so the
    lifespan fails fast. Also used by `/ok` to report `provider=` in the
    healthcheck body.
    """
    raw = os.getenv("LANGGRAPH_LLM_PROVIDER", "anthropic").strip().lower()
    if raw not in _SUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"Unknown LANGGRAPH_LLM_PROVIDER: {raw!r}; "
            f"expected one of {list(_SUPPORTED_PROVIDERS)}. "
            "Set LANGGRAPH_LLM_PROVIDER=anthropic, openai, or ollama in .env "
            "and restart the container (docker compose restart langgraph)."
        )
    return raw  # type: ignore[return-value]


def _model_re_for(provider: str) -> re.Pattern[str]:
    """Pick the model-name regex for a provider.

    Ollama tags need `:` and `_`; anthropic/openai stay strict so the
    `claude_sonnet_4_6` underscore-typo gotcha still raises.
    """
    return _OLLAMA_MODEL_NAME_RE if provider == "ollama" else _MODEL_NAME_RE


def resolve_model(provider: str | None = None) -> str:
    """Resolve the model name for the given provider.

    Order: explicit override env-var (`ANTHROPIC_MODEL` / `OPENAI_MODEL` /
    `OLLAMA_MODEL`) → `DEFAULT_*` constant. Validates shape via the
    per-provider regex.
    """
    p = (provider or resolve_provider()).lower()
    if p == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip()
        env_var = "ANTHROPIC_MODEL"
    elif p == "openai":
        model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
        env_var = "OPENAI_MODEL"
    elif p == "ollama":
        model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
        env_var = "OLLAMA_MODEL"
    else:
        raise RuntimeError(
            f"Unknown provider {p!r} passed to resolve_model(); "
            f"expected one of {list(_SUPPORTED_PROVIDERS)}."
        )

    if not _model_re_for(p).match(model):
        if p == "ollama":
            raise RuntimeError(
                f"Invalid model name {model!r} for provider {p!r} (via {env_var}). "
                "Expected lowercase letters/digits/dot/hyphen/underscore/colon — e.g., "
                f"{DEFAULT_OLLAMA_MODEL!r} or 'qwen2.5:7b' or 'llama3.2:3b-instruct-q4_K_M'."
            )
        raise RuntimeError(
            f"Invalid model name {model!r} for provider {p!r} (via {env_var}). "
            "Expected lowercase letters/digits/dot/hyphen — e.g., "
            f"{DEFAULT_ANTHROPIC_MODEL!r} or {DEFAULT_OPENAI_MODEL!r}. "
            "Common gotcha: underscores ('_') instead of hyphens ('-')."
        )
    return model


def _require_api_key(provider: ProviderName) -> str:
    """Fetch the API key env-var matching `provider`. Raises on empty/missing.

    Not called for ollama — local runner needs no key.
    """
    env_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    key = os.getenv(env_var, "").strip()
    if not key:
        raise RuntimeError(
            f"LANGGRAPH_LLM_PROVIDER={provider} but {env_var} is unset or empty. "
            f"Set {env_var}=<your-key> in .env and restart the container "
            "(docker compose restart langgraph)."
        )
    return key


def make_chat_model(model: str | None = None) -> BaseChatModel:
    """Construct a chat model for the configured provider.

    Args:
        model: optional override; if None, resolved via `resolve_model()`.

    Returns:
        A langchain `BaseChatModel` (`ChatAnthropic`, `ChatOpenAI`, or `ChatOllama`).

    Raises:
        RuntimeError: provider unknown, API key missing (anthropic/openai
            only), or model name malformed.
    """
    provider = resolve_provider()
    chosen_model = model if model is not None else resolve_model(provider)
    # Re-validate shape even on explicit override — caller bug catches here.
    if not _model_re_for(provider).match(chosen_model):
        raise RuntimeError(
            f"Invalid model name {chosen_model!r} passed to make_chat_model() "
            f"for provider {provider!r}. See resolve_model() for the accepted shape."
        )

    if provider == "anthropic":
        api_key = _require_api_key(provider)
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=chosen_model, api_key=api_key, max_retries=1)

    if provider == "openai":
        api_key = _require_api_key(provider)
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=chosen_model, api_key=api_key, max_retries=1)

    # provider == "ollama" — resolve_provider() guarantees membership, so the
    # final branch is reachable iff the value is "ollama".
    # Ollama runs locally; no API key required.
    from langchain_ollama import ChatOllama

    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip() or DEFAULT_OLLAMA_BASE_URL
    return ChatOllama(model=chosen_model, base_url=base_url)
