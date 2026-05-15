"""git_diff — invoke against a temp git repo, verify diff output shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY
from tools.base import InvokeContext


async def test_no_changes_returns_empty_output(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_diff")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke({"paths": None}, context=ctx)
    assert result.success is True
    assert result.output == ""


async def test_modified_file_shows_in_diff(tmp_git_repo: Path):
    seed = tmp_git_repo / "README.md"
    seed.write_text("# fixture repo\n\nnew line\n", encoding="utf-8")
    tool = GLOBAL_REGISTRY.get("git_diff")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke({"paths": None}, context=ctx)
    assert result.success is True
    assert "+new line" in (result.output or "")
    assert "README.md" in (result.output or "")


async def test_diff_scoped_to_paths(tmp_git_repo: Path):
    """Two modified files; passing paths=[one] scopes the diff."""
    (tmp_git_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    other = tmp_git_repo / "other.txt"
    other.write_text("untracked file\n", encoding="utf-8")
    # other.txt is untracked, so won't show in `git diff` regardless; create a
    # second tracked file first.
    import subprocess

    subprocess.run(["git", "add", "other.txt"], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add other"],
        cwd=tmp_git_repo,
        check=True,
        env={"GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid",
             "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
             "HOME": str(tmp_git_repo), "PATH": "/usr/bin:/bin"},
    )
    other.write_text("modified\n", encoding="utf-8")

    tool = GLOBAL_REGISTRY.get("git_diff")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke({"paths": ["README.md"]}, context=ctx)
    assert result.success is True
    assert "README.md" in (result.output or "")
    assert "other.txt" not in (result.output or "")


async def test_tier_is_read():
    tool = GLOBAL_REGISTRY.get("git_diff")
    assert tool.tier.value == "read"
