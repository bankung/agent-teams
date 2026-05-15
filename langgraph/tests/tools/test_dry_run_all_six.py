"""Cross-tool consistency: dry_run accepted by every WRITE-tier tool and
produces NO side effects.

Per the locked design (§8): tools that can have side effects MUST accept a
`dry_run` flag and, when True, return a ToolResult describing what WOULD
happen without doing it. Read-only tools (git_diff, git_status) and the
shell tool (which can't be safely dry-run for arbitrary commands) are out of
scope here.

This test is the cross-cutting consistency contract — any future write-tier
tool must add itself to WRITE_TOOLS_WITH_DRY_RUN to confirm participation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY


WRITE_TOOLS_WITH_DRY_RUN = ["file_edit", "file_write"]


@pytest.mark.parametrize("tool_name", WRITE_TOOLS_WITH_DRY_RUN)
async def test_write_tool_accepts_dry_run_field(tool_name: str):
    """Every WRITE-tier tool in this batch has a `dry_run` field on its
    input_schema. Static schema check (no invocation)."""
    tool = GLOBAL_REGISTRY.get(tool_name)
    assert "dry_run" in tool.input_schema.model_fields, (
        f"{tool_name} input_schema is missing `dry_run`"
    )


async def test_file_edit_dry_run_does_not_modify(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("hello\n", encoding="utf-8")
    before_mtime = p.stat().st_mtime_ns
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({
        "path": str(p),
        "old_string": "hello",
        "new_string": "world",
        "dry_run": True,
    })
    assert result.success is True
    assert "Dry-run" in (result.output or "")
    # mtime unchanged
    assert p.stat().st_mtime_ns == before_mtime
    assert p.read_text(encoding="utf-8") == "hello\n"


async def test_file_write_dry_run_does_not_create(tmp_path: Path):
    target = tmp_path / "x.txt"
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({
        "path": str(target),
        "content": "data",
        "dry_run": True,
    })
    assert result.success is True
    assert "Dry-run" in (result.output or "")
    assert not target.exists()


async def test_dry_run_output_is_descriptive():
    """The dry-run output must mention the operation + target so the LLM /
    Kanban UI can render a meaningful preview."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "f.txt"
        p.write_text("old\n", encoding="utf-8")
        tool = GLOBAL_REGISTRY.get("file_edit")
        result = await tool.invoke({
            "path": str(p),
            "old_string": "old",
            "new_string": "new",
            "dry_run": True,
        })
        # Must describe the file + the change
        assert str(p) in (result.output or "")
        assert "old" in (result.output or "") or "new" in (result.output or "")
