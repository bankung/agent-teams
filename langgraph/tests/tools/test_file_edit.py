"""file_edit — happy path, dry_run, error cases.

Covers (#977 AC1):
- Happy path: unique old_string is replaced; output is a unified diff.
- dry_run=True returns the diff but DOESN'T modify the file.
- 0 matches → error_code='match_ambiguous'.
- >1 matches → error_code='match_ambiguous'.
- Path doesn't exist → error_code='not_found'.
- Path is a directory → error_code='not_a_file'.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import GLOBAL_REGISTRY


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
