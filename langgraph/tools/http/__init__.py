"""HTTP tools — http_get, http_post (Kanban #978).

These are NETWORK-tier tools. Each call is gated by a per-project host
allowlist sourced from `projects.tools_config.http_hosts` (the read path lands
in #979's permission gate; here we expect `InvokeContext.host_allowlist` to
already carry the resolved list). An empty allowlist halts everything — the
default is fail-closed.

Provider feature-flag (locked decision Kanban #978 Q11 → A): tool-use is
supported on Anthropic + OpenAI providers only. When the host process runs
with `LANGGRAPH_LLM_PROVIDER=ollama`, the HTTP tools are NOT registered on
`GLOBAL_REGISTRY` — `register_http_tools()` early-returns. The Tool classes
themselves stay importable so unit tests can exercise them without depending
on the active provider. The specialist node will additionally halt
auto_pickup tasks at runtime if an ollama-bound LLM proposes a tool call
(wired by #981 integration; out of scope here).
"""

from __future__ import annotations

import os

from ..registry import ToolRegistry, GLOBAL_REGISTRY
from .http_get import HttpGetTool
from .http_post import HttpPostTool

__all__ = [
    "HttpGetTool",
    "HttpPostTool",
    "register_http_tools",
]


def _resolve_provider(explicit: str | None) -> str:
    """Provider name comes from the explicit arg if given (tests), else env."""
    if explicit is not None:
        return explicit.strip().lower()
    return os.environ.get("LANGGRAPH_LLM_PROVIDER", "").strip().lower()


def register_http_tools(
    registry: ToolRegistry, *, provider: str | None = None
) -> bool:
    """Register HttpGetTool + HttpPostTool on `registry` unless provider=ollama.

    Returns True if registration happened, False if skipped (provider=ollama).
    Tests pass `provider='ollama'` explicitly + a fresh ToolRegistry to verify
    the skip path without mutating GLOBAL_REGISTRY.
    """
    if _resolve_provider(provider) == "ollama":
        return False
    registry.register(HttpGetTool)
    registry.register(HttpPostTool)
    return True


# Package-import side-effect: register on the global singleton using the
# current env's provider. Imported by `tools/__init__.py` so the standard
# `from tools import GLOBAL_REGISTRY` path picks up the http tools when the
# active provider supports tool-use.
register_http_tools(GLOBAL_REGISTRY)
