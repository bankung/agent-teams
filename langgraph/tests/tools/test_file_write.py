"""file_write — happy path, dry_run, refusal when target exists.

Covers (#977 AC1):
- Happy path: writes file, returns size.
- dry_run=True returns size description without writing.
- Existing path → error_code='already_exists' (don't clobber; use file_edit).
- Missing parent dir → error_code='parent_missing' (no implicit mkdir).

Kanban #2837 additions:
- A relative path anchors at ctx.working_path, never the process CWD.
- An absolute path is unaffected by working_path (regression guard).
- A hung to_thread() write is cut off by asyncio.wait_for → error_code='timeout'.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY, InvokeContext


async def test_happy_path_creates_file(tmp_path: Path):
    target = tmp_path / "new.txt"
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({"path": str(target), "content": "hello\n"})
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert "6 bytes" in (result.output or "")


async def test_dry_run_does_not_write(tmp_path: Path):
    target = tmp_path / "new.txt"
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({
        "path": str(target),
        "content": "hello\n",
        "dry_run": True,
    })
    assert result.success is True
    assert "Dry-run" in (result.output or "")
    assert "6 bytes" in (result.output or "")
    assert not target.exists()


async def test_refuses_existing_path(tmp_path: Path):
    target = tmp_path / "exists.txt"
    target.write_text("original", encoding="utf-8")
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({"path": str(target), "content": "new"})
    assert result.success is False
    assert result.error_code == "already_exists"
    # Original content preserved
    assert target.read_text(encoding="utf-8") == "original"


async def test_refuses_missing_parent(tmp_path: Path):
    target = tmp_path / "missing-dir" / "file.txt"
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({"path": str(target), "content": "x"})
    assert result.success is False
    assert result.error_code == "parent_missing"
    assert not target.exists()


async def test_tier_is_write():
    tool = GLOBAL_REGISTRY.get("file_write")
    assert tool.tier.value == "write"


# ---------------------------------------------------------------------------
# Kanban #2837 — path anchoring
# ---------------------------------------------------------------------------


async def test_relative_path_resolves_inside_working_path(tmp_path: Path):
    """A relative path anchors at ctx.working_path — never the process CWD
    (/repo/langgraph). This is the exact divergence #2837 closes: the old
    code did `Path(input_obj.path)` directly, which resolved a relative path
    against the process's CWD when the file was actually opened."""
    (tmp_path / "sub").mkdir()
    ctx = InvokeContext(working_path=str(tmp_path))
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({"path": "sub/out.txt", "content": "hi"}, ctx)
    assert result.success is True, result.error_msg
    assert (tmp_path / "sub" / "out.txt").read_text(encoding="utf-8") == "hi"


async def test_absolute_path_still_works_with_working_path_set(tmp_path: Path):
    """An absolute path resolves as-is regardless of working_path (regression
    guard — the #2837 fix must not re-anchor an already-absolute path)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / "out.txt"
    working = tmp_path / "sub"
    working.mkdir()
    ctx = InvokeContext(working_path=str(working))
    tool = GLOBAL_REGISTRY.get("file_write")
    result = await tool.invoke({"path": str(target), "content": "hi"}, ctx)
    assert result.success is True, result.error_msg
    assert target.read_text(encoding="utf-8") == "hi"


# ---------------------------------------------------------------------------
# Kanban #2837 — enforced timeout
# ---------------------------------------------------------------------------


async def test_timeout_returns_timeout_result(monkeypatch, tmp_path: Path):
    """A hung to_thread() write is cut off by asyncio.wait_for, not left to
    freeze the (serial) worker forever."""

    async def _hang(func, *args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(asyncio, "to_thread", _hang)
    tool = GLOBAL_REGISTRY.get("file_write")
    monkeypatch.setattr(tool, "timeout_sec", 1)

    target = tmp_path / "hang.txt"
    result = await tool.invoke({"path": str(target), "content": "x"})

    assert result.success is False
    assert result.error_code == "timeout"
    assert result.retry_safe is False
    # Negative: the hung write never actually landed on disk.
    assert not target.exists()
