"""Specialist tool layer — public surface.

Importing this package triggers registration of all batch-1 tools on the
`GLOBAL_REGISTRY` singleton via each submodule's `@GLOBAL_REGISTRY.register`
decorator. After import, callers can do:

    from tools import GLOBAL_REGISTRY
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({"path": "/repo/...", "old_string": "x", "new_string": "y"})

The specialist node wires the registry into the LLM via:

    model.bind_tools(GLOBAL_REGISTRY.all_tools_as_langchain())

#977 shipped batch 1: file_edit, file_write, git_diff, git_status, git_commit,
shell_run. #978 added batch 2: http_get, http_post — registered only when the
active LLM provider supports tool-use (Anthropic + OpenAI; ollama is
feature-flagged OFF, see `tools/http/__init__.py`). Permission gate in
#979; audit trail in #980; sandbox enforcement in #981.
"""

from .base import (
    InvokeContext,
    MAX_TOOL_LOOP_ITERATIONS,
    TOOL_LOOP_HALT_REASON,
    Tier,
    Tool,
    ToolInput,
    ToolResult,
)
from .permission_gate import PermissionDecision, check_permission
from .registry import GLOBAL_REGISTRY, ToolNotFoundError, ToolRegistry
from .sandbox import (
    OUTPUT_CAP_BYTES,
    OUTPUT_CAP_MARKER,
    WORKING_PATH_UNSET_CODE,
    _allowlist_cache_clear,
    apply_output_cap,
    apply_sandbox,
    check_hard_kill_drift,
    fs_boundary_check,
)

# Trigger registration side-effects. The order doesn't matter (each tool's
# `name` attr is unique by design), but we list them for readability.
from . import fs  # noqa: F401  — registers file_edit, file_write
from . import vcs  # noqa: F401  — registers git_diff, git_status, git_commit
from . import shell  # noqa: F401  — registers shell_run
from . import http  # noqa: F401  — registers http_get, http_post (unless provider=ollama)

__all__ = [
    "GLOBAL_REGISTRY",
    "InvokeContext",
    "MAX_TOOL_LOOP_ITERATIONS",
    "OUTPUT_CAP_BYTES",
    "OUTPUT_CAP_MARKER",
    "PermissionDecision",
    "TOOL_LOOP_HALT_REASON",
    "Tier",
    "Tool",
    "ToolInput",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "WORKING_PATH_UNSET_CODE",
    "apply_output_cap",
    "apply_sandbox",
    "check_hard_kill_drift",
    "check_permission",
    "fs_boundary_check",
]
