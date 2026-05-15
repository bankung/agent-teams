"""Specialist tool layer — public surface.

Importing this package triggers registration of all batch-1 tools on the
`GLOBAL_REGISTRY` singleton via each submodule's `@GLOBAL_REGISTRY.register`
decorator. After import, callers can do:

    from tools import GLOBAL_REGISTRY
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({"path": "/repo/...", "old_string": "x", "new_string": "y"})

The specialist node wires the registry into the LLM via:

    model.bind_tools(GLOBAL_REGISTRY.all_tools_as_langchain())

#977 ships batch 1: file_edit, file_write, git_diff, git_status, git_commit,
shell_run. Batch 2 (http_get, http_post) lands in #978; permission gate in
#979; audit trail in #980; sandbox enforcement in #981.
"""

from .base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from .registry import GLOBAL_REGISTRY, ToolNotFoundError, ToolRegistry

# Trigger registration side-effects. The order doesn't matter (each tool's
# `name` attr is unique by design), but we list them for readability.
from . import fs  # noqa: F401  — registers file_edit, file_write
from . import vcs  # noqa: F401  — registers git_diff, git_status, git_commit
from . import shell  # noqa: F401  — registers shell_run

__all__ = [
    "GLOBAL_REGISTRY",
    "InvokeContext",
    "Tier",
    "Tool",
    "ToolInput",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
]
