"""Shared fixtures for tool tests.

`tmp_git_repo` — a fresh `git init`'d directory with one initial commit and a
deterministic user.name/email config. Used by git_diff / git_status /
git_commit tests so we don't pollute the worktree's actual repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run(cmd: list[str], cwd: Path) -> None:
    """Synchronous git invoke for fixture setup. Async tools-under-test use
    asyncio; the fixtures themselves are simpler in sync form."""
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """A git repo at tmp_path with one initial commit on `main`."""
    _run(["git", "init", "-q", "-b", "main"], cwd=tmp_path)
    _run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path)
    _run(["git", "config", "user.name", "Test User"], cwd=tmp_path)
    # commit.gpgsign would block non-interactive commit on machines with GPG
    # default-signing set globally. Force off for the test repo.
    _run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path)
    seed = tmp_path / "README.md"
    seed.write_text("# fixture repo\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=tmp_path)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path)
    return tmp_path
