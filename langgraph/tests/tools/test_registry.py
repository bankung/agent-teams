"""ToolRegistry — register/get/list + duplicate-name behavior.

Covers (Kanban #977 AC1):
- All 6 batch-1 tools register on package import.
- `get()` returns the right Tool instance; unknown name → ToolNotFoundError.
- `list()` is deterministic (sorted).
- Duplicate registration raises ValueError (not a silent overwrite).
- `all_tools_as_langchain()` yields one BaseTool per registered tool, with
  matching name + args_schema.
"""

from __future__ import annotations

import pytest

from tools import GLOBAL_REGISTRY
from tools.base import Tier, Tool, ToolInput, ToolResult
from tools.registry import ToolNotFoundError, ToolRegistry


EXPECTED_BATCH_1 = {
    "file_edit",
    "file_write",
    "git_diff",
    "git_status",
    "git_commit",
    "shell_run",
}

# Batch 2 (Kanban #978) — registered when the active LLM provider supports
# tool-use. The test runtime defaults to no provider env var → these register.
EXPECTED_BATCH_2 = {
    "http_get",
    "http_post",
}

EXPECTED_REGISTERED = EXPECTED_BATCH_1 | EXPECTED_BATCH_2


def test_global_registry_has_all_batch_1_tools():
    """Pin the batch-1 subset (so a future refactor that drops one is loud)."""
    assert EXPECTED_BATCH_1.issubset(set(GLOBAL_REGISTRY.list()))


def test_global_registry_has_batch_2_when_provider_supports_tool_use():
    """HTTP tools register when LANGGRAPH_LLM_PROVIDER != ollama. The test
    runtime doesn't set the env var (or sets it to anthropic), so batch 2
    should be present."""
    import os
    assert os.environ.get("LANGGRAPH_LLM_PROVIDER", "").lower() != "ollama", (
        "Test runtime should not have provider=ollama for this assertion."
    )
    assert EXPECTED_BATCH_2.issubset(set(GLOBAL_REGISTRY.list()))


def test_list_is_sorted():
    listed = GLOBAL_REGISTRY.list()
    assert listed == sorted(listed)


def test_get_returns_tool_instance():
    tool = GLOBAL_REGISTRY.get("file_edit")
    assert tool.name == "file_edit"
    assert tool.tier == Tier.WRITE


def test_get_unknown_raises():
    with pytest.raises(ToolNotFoundError, match="Unknown tool"):
        GLOBAL_REGISTRY.get("nope")


def test_duplicate_registration_raises():
    """Registering two tools with the same name is a real footgun (silent
    overwrite would hide a copy-paste bug). Force ValueError."""

    class _SchemaA(ToolInput):
        pass

    local = ToolRegistry()

    @local.register
    class ToolA(Tool):
        name = "dupe"
        description = "first"
        tier = Tier.READ
        input_schema = _SchemaA

        async def _run(self, input_obj, context):
            return ToolResult(success=True)

    with pytest.raises(ValueError, match="Duplicate tool name"):

        @local.register
        class ToolB(Tool):
            name = "dupe"
            description = "second"
            tier = Tier.READ
            input_schema = _SchemaA

            async def _run(self, input_obj, context):
                return ToolResult(success=True)


def test_all_tools_as_langchain_count_and_names():
    lc_tools = GLOBAL_REGISTRY.all_tools_as_langchain()
    lc_names = {t.name for t in lc_tools}
    # Batch 1 is the immutable minimum; batch 2 piggybacks when provider != ollama.
    assert EXPECTED_BATCH_1.issubset(lc_names)
    assert len(lc_tools) == len(lc_names), "duplicate names in langchain export"


def test_all_tools_as_langchain_preserves_args_schema():
    """Each StructuredTool carries the original Pydantic ToolInput subclass
    so the LLM sees the exact field set we declared."""
    lc_tools = {t.name: t for t in GLOBAL_REGISTRY.all_tools_as_langchain()}
    file_edit_args = list(lc_tools["file_edit"].args_schema.model_fields.keys())
    assert file_edit_args == ["path", "old_string", "new_string", "dry_run"]
    git_commit_args = list(lc_tools["git_commit"].args_schema.model_fields.keys())
    assert git_commit_args == ["message", "paths"]


def test_clear_resets_registry():
    local = ToolRegistry()

    class _S(ToolInput):
        pass

    @local.register
    class T1(Tool):
        name = "t1"
        description = "x"
        tier = Tier.READ
        input_schema = _S

        async def _run(self, input_obj, context):
            return ToolResult(success=True)

    assert local.list() == ["t1"]
    local.clear()
    assert local.list() == []
