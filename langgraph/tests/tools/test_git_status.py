"""git_status — porcelain output shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY
from tools.base import InvokeContext


async def test_clean_repo_only_branch_header(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_status")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke({}, context=ctx)
    assert result.success is True
    # `-b` adds a branch header like `## main`. Clean tree means only that line.
    lines = (result.output or "").strip().splitlines()
    assert lines and lines[0].startswith("##")
    assert "main" in lines[0]


async def test_dirty_repo_lists_changes(tmp_git_repo: Path):
    (tmp_git_repo / "new.txt").write_text("hello\n", encoding="utf-8")
    (tmp_git_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    tool = GLOBAL_REGISTRY.get("git_status")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke({}, context=ctx)
    assert result.success is True
    out = result.output or ""
    # Modified tracked file: ` M README.md`
    assert " M README.md" in out
    # Untracked file: `?? new.txt`
    assert "?? new.txt" in out


async def test_tier_is_read():
    tool = GLOBAL_REGISTRY.get("git_status")
    assert tool.tier.value == "read"
