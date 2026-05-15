"""file_write — happy path, dry_run, refusal when target exists.

Covers (#977 AC1):
- Happy path: writes file, returns size.
- dry_run=True returns size description without writing.
- Existing path → error_code='already_exists' (don't clobber; use file_edit).
- Missing parent dir → error_code='parent_missing' (no implicit mkdir).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY


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
