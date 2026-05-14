"""Multi-provider chat-model factory.

Reads `LANGGRAPH_LLM_PROVIDER` (`anthropic` | `openai`, default `anthropic`)
plus the matching API key + optional model override, returns a langchain
`BaseChatModel`. Lifespan in `graph.py` calls `make_chat_model().invoke("ping")`
during boot so any misconfiguration surfaces BEFORE the container is marked
healthy — better than a healthy container that crashes on first /invoke.

Public surface (kept stable so callers — nodes.py, graph.py lifespan — don't
break across provider swaps):

- `make_chat_model(model: str | None = None) -> BaseChatModel`
- `resolve_provider() -> Literal["anthropic", "openai"]`
- `resolve_model(provider: str | None = None) -> str`
- `DEFAULT_ANTHROPIC_MODEL`, `DEFAULT_OPENAI_MODEL` (module constants)

Provider SDKs (`langchain_anthropic`, `langchain_openai`) are imported INSIDE
the matching branch so an Anthropic-only deployment does not pay the OpenAI
import cost at startup. `max_retries=1` is set for both — dev wants a fast
failure signal; prod can override per-call via the model's `with_retry()` API
without touching this factory.
"""

from __future__ import annotations

import os
import re
from typing import Literal

from langchain_core.language_models import BaseChatModel

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"

_SUPPORTED_PROVIDERS = ("anthropic", "openai")
ProviderName = Literal["anthropic", "openai"]

# Model name shape — lowercase letters, digits, dot, hyphen. Catches obvious
# typos (`claude_sonnet_4_6` with underscores, trailing whitespace, capital
# letters from a stray `ANTHROPIC_MODEL=Claude-Sonnet-4-6`) before the SDK
# call. Permissive on dot to allow `gpt-4o-2024-08-06`-style snapshots.
_MODEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")


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
            "Set LANGGRAPH_LLM_PROVIDER=anthropic or LANGGRAPH_LLM_PROVIDER=openai in .env "
            "and restart the container (docker compose restart langgraph)."
        )
    return raw  # type: ignore[return-value]


def resolve_model(provider: str | None = None) -> str:
    """Resolve the model name for the given provider.

    Order: explicit override env-var (`ANTHROPIC_MODEL` / `OPENAI_MODEL`) →
    `DEFAULT_*` constant. Validates shape via `_MODEL_NAME_RE`.
    """
    p = (provider or resolve_provider()).lower()
    if p == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip()
    elif p == "openai":
        model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
    else:
        raise RuntimeError(
            f"Unknown provider {p!r} passed to resolve_model(); "
            f"expected one of {list(_SUPPORTED_PROVIDERS)}."
        )

    if not _MODEL_NAME_RE.match(model):
        env_var = "ANTHROPIC_MODEL" if p == "anthropic" else "OPENAI_MODEL"
        raise RuntimeError(
            f"Invalid model name {model!r} for provider {p!r} (via {env_var}). "
            "Expected lowercase letters/digits/dot/hyphen — e.g., "
            f"{DEFAULT_ANTHROPIC_MODEL!r} or {DEFAULT_OPENAI_MODEL!r}. "
            "Common gotcha: underscores ('_') instead of hyphens ('-')."
        )
    return model


def _require_api_key(provider: ProviderName) -> str:
    """Fetch the API key env-var matching `provider`. Raises on empty/missing."""
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
        A langchain `BaseChatModel` (`ChatAnthropic` or `ChatOpenAI`).

    Raises:
        RuntimeError: provider unknown, API key missing, or model name malformed.
    """
    provider = resolve_provider()
    api_key = _require_api_key(provider)
    chosen_model = model if model is not None else resolve_model(provider)
    # Re-validate shape even on explicit override — caller bug catches here.
    if not _MODEL_NAME_RE.match(chosen_model):
        raise RuntimeError(
            f"Invalid model name {chosen_model!r} passed to make_chat_model(). "
            "Expected lowercase letters/digits/dot/hyphen."
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=chosen_model, api_key=api_key, max_retries=1)

    # provider == "openai" — resolve_provider() guarantees membership in
    # _SUPPORTED_PROVIDERS, so no `else` branch is reachable.
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=chosen_model, api_key=api_key, max_retries=1)
