"""git_commit — happy path + refusal of forbidden flags and ref-like paths.

Important: every commit happens in a TEMP repo (`tmp_git_repo` fixture) so
the worktree's actual git history is never polluted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY
from tools.base import InvokeContext


async def test_happy_path_stages_and_commits(tmp_git_repo: Path):
    new = tmp_git_repo / "feature.txt"
    new.write_text("feature\n", encoding="utf-8")
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "feat: add feature.txt", "paths": ["feature.txt"]},
        context=ctx,
    )
    assert result.success is True, result.error_msg
    # Verify the commit landed
    log = subprocess.run(
        ["git", "log", "--format=%s", "-n", "1"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert log == "feat: add feature.txt"


async def test_refuses_force_in_message(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "please --force this", "paths": ["README.md"]},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "forbidden_flag"


async def test_refuses_push_in_message(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "do a --push after this", "paths": ["README.md"]},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "forbidden_flag"


async def test_refuses_amend_in_message(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "--amend this please", "paths": ["README.md"]},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "forbidden_flag"


async def test_refuses_ref_like_path(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "boom", "paths": ["HEAD"]},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "path_looks_like_ref"


async def test_refuses_refs_heads_main_path(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "x", "paths": ["refs/heads/main"]},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "path_looks_like_ref"


async def test_refuses_remote_branch_pattern(tmp_git_repo: Path):
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "x", "paths": ["origin/main"]},
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "path_looks_like_ref"


async def test_nothing_to_commit_returns_git_error(tmp_git_repo: Path):
    """If `paths` matches no actual changes, git commit fails — we surface
    that as `git_error` (the LLM can read the message and adjust)."""
    tool = GLOBAL_REGISTRY.get("git_commit")
    ctx = InvokeContext(repo_root=str(tmp_git_repo))
    result = await tool.invoke(
        {"message": "empty", "paths": ["README.md"]},  # README unchanged
        context=ctx,
    )
    assert result.success is False
    assert result.error_code == "git_error"


async def test_tier_is_write():
    tool = GLOBAL_REGISTRY.get("git_commit")
    assert tool.tier.value == "write"


async def test_retry_safe_is_false():
    """git_commit is not idempotent; the engine should NOT auto-retry."""
    tool = GLOBAL_REGISTRY.get("git_commit")
    # Construct a result by invoking with a forbidden message — that's a
    # synchronous reject so it returns retry_safe=False per code path.
    ctx = InvokeContext()
    result = await tool.invoke(
        {"message": "--push", "paths": ["x"]},
        context=ctx,
    )
    assert result.retry_safe is False
