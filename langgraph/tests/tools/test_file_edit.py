"""file_edit — happy path, dry_run, error cases.

Covers (#977 AC1):
- Happy path: unique old_string is replaced; output is a unified diff.
- dry_run=True returns the diff but DOESN'T modify the file.
- 0 matches → error_code='match_ambiguous'.
- >1 matches → error_code='match_ambiguous'.
- Path doesn't exist → error_code='not_found'.
- Path is a directory → error_code='not_a_file'.

Kanban #2837 additions:
- A relative path anchors at ctx.working_path, never the process CWD.
- A hung to_thread() read/write is cut off by asyncio.wait_for →
  error_code='timeout' (read and write phases tested separately — they carry
  different retry_safe values).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY, InvokeContext
from tools.fs import _common


@pytest.fixture
def tmp_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.py"
    p.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n", encoding="utf-8")
    return p


async def test_happy_path_replaces_unique_match(tmp_file: Path):
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({
        "path": str(tmp_file),
        "old_string": "return 1",
        "new_string": "return 11",
    })
    assert result.success is True
    assert result.error_code is None
    # File on disk is changed
    content = tmp_file.read_text(encoding="utf-8")
    assert "return 11" in content
    assert "return 1\n" not in content  # the original line is gone
    # Output is a unified diff
    assert "-    return 1" in (result.output or "")
    assert "+    return 11" in (result.output or "")


async def test_dry_run_returns_diff_without_writing(tmp_file: Path):
    tool = GLOBAL_REGISTRY.get("file_edit")
    before = tmp_file.read_text(encoding="utf-8")
    result = await tool.invoke({
        "path": str(tmp_file),
        "old_string": "return 1",
        "new_string": "return 11",
        "dry_run": True,
    })
    assert result.success is True
    assert "Dry-run" in (result.output or "")
    assert "-    return 1" in (result.output or "")
    # File on disk is UNCHANGED
    assert tmp_file.read_text(encoding="utf-8") == before


async def test_zero_matches_halts(tmp_file: Path):
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({
        "path": str(tmp_file),
        "old_string": "this_text_does_not_exist",
        "new_string": "anything",
    })
    assert result.success is False
    assert result.error_code == "match_ambiguous"
    assert "0 matches" in (result.error_msg or "")


async def test_multiple_matches_halts(tmp_path: Path):
    """`return` appears twice in the fixture above (`return 1` and `return 2`),
    so a bare `return ` substring matches twice."""
    p = tmp_path / "dup.py"
    p.write_text("x = 1\nx = 1\n", encoding="utf-8")
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({
        "path": str(p),
        "old_string": "x = 1",
        "new_string": "x = 99",
    })
    assert result.success is False
    assert result.error_code == "match_ambiguous"
    assert "2 matches" in (result.error_msg or "")
    # File untouched
    assert p.read_text(encoding="utf-8") == "x = 1\nx = 1\n"


async def test_missing_file_returns_not_found(tmp_path: Path):
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({
        "path": str(tmp_path / "no_such.py"),
        "old_string": "x",
        "new_string": "y",
    })
    assert result.success is False
    assert result.error_code == "not_found"


async def test_directory_target_rejected(tmp_path: Path):
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke({
        "path": str(tmp_path),  # a directory, not a file
        "old_string": "x",
        "new_string": "y",
    })
    assert result.success is False
    assert result.error_code == "not_a_file"


async def test_tier_is_write():
    tool = GLOBAL_REGISTRY.get("file_edit")
    assert tool.tier.value == "write"


# ---------------------------------------------------------------------------
# Kanban #2837 — path anchoring
# ---------------------------------------------------------------------------


async def test_relative_path_resolves_inside_working_path(tmp_path: Path):
    """A relative path anchors at ctx.working_path — never the process CWD
    (/repo/langgraph). Mirrors the same fix in file_write."""
    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "e.txt"
    target.write_text("hello\n", encoding="utf-8")
    ctx = InvokeContext(working_path=str(tmp_path))
    tool = GLOBAL_REGISTRY.get("file_edit")
    result = await tool.invoke(
        {"path": "sub/e.txt", "old_string": "hello", "new_string": "world"}, ctx
    )
    assert result.success is True, result.error_msg
    assert target.read_text(encoding="utf-8") == "world\n"


# ---------------------------------------------------------------------------
# Kanban #2837 — enforced timeout (read + write phases)
# ---------------------------------------------------------------------------


async def test_timeout_on_read_returns_timeout_result(monkeypatch, tmp_path: Path):
    """A hung to_thread() read is cut off by asyncio.wait_for. Nothing has
    been mutated yet, so this is retry_safe=True."""
    p = tmp_path / "e.txt"
    p.write_text("hello\n", encoding="utf-8")

    async def _hang(func, *args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(asyncio, "to_thread", _hang)
    tool = GLOBAL_REGISTRY.get("file_edit")
    monkeypatch.setattr(tool, "timeout_sec", 1)

    result = await tool.invoke(
        {"path": str(p), "old_string": "hello", "new_string": "world"}
    )

    assert result.success is False
    assert result.error_code == "timeout"
    assert result.retry_safe is True
    # Negative: the file is untouched — the read never completed.
    assert p.read_text(encoding="utf-8") == "hello\n"


async def test_timeout_on_write_returns_timeout_result(monkeypatch, tmp_path: Path):
    """A hung to_thread() write (read succeeds first) is cut off by
    asyncio.wait_for. A write may be partial, so retry_safe=False."""
    p = tmp_path / "e.txt"
    p.write_text("hello\n", encoding="utf-8")
    real_to_thread = asyncio.to_thread

    async def _hang_write_only(func, *args, **kwargs):
        if func is _common.write_text:
            await asyncio.sleep(5)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _hang_write_only)
    tool = GLOBAL_REGISTRY.get("file_edit")
    monkeypatch.setattr(tool, "timeout_sec", 1)

    result = await tool.invoke(
        {"path": str(p), "old_string": "hello", "new_string": "world"}
    )

    assert result.success is False
    assert result.error_code == "timeout"
    assert result.retry_safe is False
