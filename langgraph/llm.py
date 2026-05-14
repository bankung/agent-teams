"""Minimal multi-provider chat-model factory (shim for Kanban #850).

The full version with retry/timeout knobs and exhaustive unit tests is Kanban
#853 — keep `make_chat_model() -> BaseChatModel` STABLE so #853 is a drop-in.

Fail-fast contract (Lead-locked 2026-05-14): raise RuntimeError at construction
if the required API key for the chosen provider is unset. We do NOT want the
container to come up only to crash on the first /invoke — that's worse than
refusing to start, because compose healthcheck would mark it healthy.
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel


def make_chat_model() -> BaseChatModel:
    """Construct a chat model based on `LANGGRAPH_LLM_PROVIDER` env-var.

    Defaults: provider=anthropic, model=claude-sonnet-4-6 (anthropic) or gpt-4o
    (openai). Raises RuntimeError if the provider's API key is missing or the
    provider name is unknown.
    """
    provider = os.getenv("LANGGRAPH_LLM_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "LANGGRAPH_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset"
            )
        from langchain_anthropic import ChatAnthropic

        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        # max_retries=1 to fail fast on provider-side hiccups during dev; #853
        # may bump this once we have an opinion on retry policy.
        return ChatAnthropic(model=model, api_key=key, max_retries=1)

    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "LANGGRAPH_LLM_PROVIDER=openai but OPENAI_API_KEY is unset"
            )
        from langchain_openai import ChatOpenAI

        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        return ChatOpenAI(model=model, api_key=key, max_retries=1)

    raise RuntimeError(
        f"Unknown LANGGRAPH_LLM_PROVIDER: {provider!r}; expected 'anthropic' or 'openai'"
    )
