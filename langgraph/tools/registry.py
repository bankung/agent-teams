"""ToolRegistry — central catalog of all specialist tools.

Pattern: class-based + decorator-driven. Tools register themselves at module
import time by applying `@GLOBAL_REGISTRY.register` to their class. The
registry instantiates the tool class once (tools are stateless singletons —
state belongs in `InvokeContext`).

Two consumers:

1. `langgraph/nodes.py` specialist nodes — call `all_tools_as_langchain()` to
   pass into `model.bind_tools(...)`. This is the production path.
2. Tests + future audit logger — call `get(name)` to invoke a tool directly.

Duplicate registration raises `ValueError` rather than silently overwriting —
duplicate `name` attrs are a real source of debugging pain.
"""

from __future__ import annotations

from typing import Any

from .base import Tool


class ToolNotFoundError(LookupError):
    """Raised when `ToolRegistry.get()` is called with an unknown name."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool_cls: type[Tool]) -> type[Tool]:
        """Decorator. Instantiates `tool_cls` and stores it under `tool.name`.

        Returns the class unchanged so it remains importable as the original
        symbol (helpful for tests that want to subclass or inspect).
        """
        tool = tool_cls()
        if tool.name in self._tools:
            existing = self._tools[tool.name]
            raise ValueError(
                f"Duplicate tool name {tool.name!r}: "
                f"{type(existing).__name__} already registered, "
                f"now {tool_cls.__name__} tried to take the same name."
            )
        self._tools[tool.name] = tool
        return tool_cls

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolNotFoundError(
                f"Unknown tool: {name!r}. Registered: {sorted(self._tools)}."
            )
        return self._tools[name]

    def list(self) -> list[str]:
        return sorted(self._tools.keys())

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def all_tools_as_langchain(self) -> list[Any]:
        """Convert every registered tool into a langchain `StructuredTool`.

        The specialist node passes this output into `model.bind_tools(...)` so
        the LLM sees the full tool surface. Tools that aren't allowed for the
        active permission tier are still bound — the permission gate (#979)
        rejects them at invocation time, NOT at bind time. Rationale: the LLM
        should be allowed to PROPOSE a tier-restricted call and then be told
        "halt for approval"; surfacing only the auto-allow set would make the
        engine quietly drop legitimate plan steps.
        """
        return [t.to_langchain() for t in self._tools.values()]

    def clear(self) -> None:
        """Reset the registry. Test-only helper; production code never calls
        this. Useful when a test wants to register a fake tool in isolation
        without polluting the global registry across tests.
        """
        self._tools.clear()


# Module-level singleton. All tool modules import this and register themselves
# via `@GLOBAL_REGISTRY.register`. Importing `langgraph.tools` triggers the
# registration side-effects (see `__init__.py`).
GLOBAL_REGISTRY = ToolRegistry()
