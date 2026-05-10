"""Pure-Python tests for `services.session_store` (CTX-2, Kanban #717).

No HTTP, no DB — drives the writer/reader API directly with `tmp_path`
as the repo_root. HTTP-layer tests live in `test_sessions.py`.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.services.session_store import (
    SECTION_COMPACTED_HISTORY,
    SECTION_RECENT_ACTIVITY,
    append_recent_activity,
    create_card_log_skeleton,
    create_session_files,
    format_activity_block,
    get_section_text,
    read_session_for_prompt,
    replace_section,
    write_card_log,
)


# =============================================================================
# Skeleton creation
# =============================================================================


def test_create_session_files_creates_layout(tmp_path: Path) -> None:
    sdir = create_session_files(42, tmp_path)
    assert sdir == tmp_path / "_sessions" / "42"
    assert (sdir / "session.md").is_file()
    assert (sdir / "archive").is_dir()
    assert (sdir / "cards").is_dir()
    md = (sdir / "session.md").read_text(encoding="utf-8")
    assert SECTION_COMPACTED_HISTORY in md
    assert SECTION_RECENT_ACTIVITY in md


def test_create_session_files_idempotent_on_existing_folder(
    tmp_path: Path,
) -> None:
    """Calling twice must not raise and must not overwrite existing content."""
    create_session_files(7, tmp_path)
    sess_md = tmp_path / "_sessions" / "7" / "session.md"
    # Mutate the file.
    sess_md.write_text("custom content\n", encoding="utf-8")

    # Re-create — should NOT clobber custom content.
    create_session_files(7, tmp_path)
    assert sess_md.read_text(encoding="utf-8") == "custom content\n"


def test_create_card_log_skeleton_idempotent(tmp_path: Path) -> None:
    create_session_files(1, tmp_path)
    p1 = create_card_log_skeleton(1, 99, tmp_path)
    assert p1.is_file()
    original = p1.read_text(encoding="utf-8")
    p2 = create_card_log_skeleton(1, 99, tmp_path)
    assert p2 == p1
    assert p2.read_text(encoding="utf-8") == original


# =============================================================================
# Recent Activity append
# =============================================================================


def test_append_recent_activity_replaces_placeholder_on_first_append(
    tmp_path: Path,
) -> None:
    create_session_files(1, tmp_path)
    block = append_recent_activity(
        1,
        summary="kicked off task",
        task_id=99,
        role="dev-backend",
        kind="spawn",
        repo_root=tmp_path,
    )
    assert "task #99" in block
    assert "dev-backend:spawn" in block

    body = get_section_text(1, SECTION_RECENT_ACTIVITY, tmp_path)
    # Placeholder gone; appended block present.
    assert "_(empty — session just started)_" not in body
    assert "kicked off task" in body
    assert "task #99" in body


def test_append_recent_activity_multiple_entries_in_order(
    tmp_path: Path,
) -> None:
    create_session_files(2, tmp_path)
    append_recent_activity(2, summary="first", repo_root=tmp_path)
    append_recent_activity(2, summary="second", repo_root=tmp_path)
    append_recent_activity(2, summary="third", repo_root=tmp_path)

    body = get_section_text(2, SECTION_RECENT_ACTIVITY, tmp_path)
    i1 = body.find("first")
    i2 = body.find("second")
    i3 = body.find("third")
    assert 0 <= i1 < i2 < i3


def test_append_recent_activity_preserves_compacted_history_section(
    tmp_path: Path,
) -> None:
    """Appending to Recent Activity must not touch the Compacted History body."""
    create_session_files(3, tmp_path)
    replace_section(
        3,
        SECTION_COMPACTED_HISTORY,
        "compact summary text here\n",
        repo_root=tmp_path,
    )
    append_recent_activity(3, summary="entry", repo_root=tmp_path)
    ch = get_section_text(3, SECTION_COMPACTED_HISTORY, tmp_path)
    assert "compact summary text here" in ch


# =============================================================================
# Section read / replace
# =============================================================================


def test_replace_section_changes_body_only(tmp_path: Path) -> None:
    create_session_files(4, tmp_path)
    replace_section(
        4,
        SECTION_COMPACTED_HISTORY,
        "rewritten compact body\n",
        repo_root=tmp_path,
    )
    md = (tmp_path / "_sessions" / "4" / "session.md").read_text(encoding="utf-8")
    # Both markers still present.
    assert SECTION_COMPACTED_HISTORY in md
    assert SECTION_RECENT_ACTIVITY in md
    # New body present, old placeholder gone.
    assert "rewritten compact body" in md
    assert "_(empty — no compacts yet)_" not in md
    # Recent Activity placeholder still there (we only replaced one section).
    assert "_(empty — session just started)_" in md


def test_get_section_text_returns_empty_for_missing_section(
    tmp_path: Path,
) -> None:
    sdir = create_session_files(5, tmp_path)
    # Manually clobber to a one-section file.
    (sdir / "session.md").write_text(
        f"{SECTION_COMPACTED_HISTORY}\nbody\n", encoding="utf-8"
    )
    assert get_section_text(5, SECTION_RECENT_ACTIVITY, tmp_path) == ""


def test_replace_section_rejects_unknown_section(tmp_path: Path) -> None:
    create_session_files(6, tmp_path)
    with pytest.raises(ValueError, match="unknown section"):
        replace_section(6, "## Bogus", "x", repo_root=tmp_path)  # type: ignore[arg-type]


# =============================================================================
# Card heartbeat
# =============================================================================


def test_write_card_log_append_five_blocks_in_order(tmp_path: Path) -> None:
    create_session_files(8, tmp_path)
    for i in range(5):
        write_card_log(
            8, 100, f"heartbeat-{i}", mode="append", repo_root=tmp_path
        )
    card = (tmp_path / "_sessions" / "8" / "cards" / "100.md").read_text(
        encoding="utf-8"
    )
    # All five blocks present, in source order.
    indices = [card.find(f"heartbeat-{i}") for i in range(5)]
    assert all(i >= 0 for i in indices)
    assert indices == sorted(indices)


def test_write_card_log_replace_overwrites(tmp_path: Path) -> None:
    create_session_files(9, tmp_path)
    write_card_log(9, 200, "first", mode="append", repo_root=tmp_path)
    write_card_log(9, 200, "snapshot only", mode="replace", repo_root=tmp_path)
    card = (tmp_path / "_sessions" / "9" / "cards" / "200.md").read_text(
        encoding="utf-8"
    )
    assert card == "snapshot only"
    assert "first" not in card


def test_write_card_log_rejects_unknown_mode(tmp_path: Path) -> None:
    create_session_files(10, tmp_path)
    with pytest.raises(ValueError, match="unknown mode"):
        write_card_log(10, 1, "x", mode="weird", repo_root=tmp_path)  # type: ignore[arg-type]


# =============================================================================
# Prompt-ready read
# =============================================================================


def test_read_session_for_prompt_returns_markdown_and_charcount(
    tmp_path: Path,
) -> None:
    create_session_files(11, tmp_path)
    append_recent_activity(11, summary="hello", repo_root=tmp_path)
    md, n = read_session_for_prompt(11, tmp_path)
    assert md.startswith("# Session context")
    assert SECTION_COMPACTED_HISTORY in md
    assert SECTION_RECENT_ACTIVITY in md
    assert "hello" in md
    assert n == len(md)


def test_read_session_for_prompt_with_include_card_id_appends_card(
    tmp_path: Path,
) -> None:
    create_session_files(12, tmp_path)
    write_card_log(12, 555, "card-body-text", mode="append", repo_root=tmp_path)
    md, _ = read_session_for_prompt(12, tmp_path, include_card_id=555)
    assert "## Current card detail (task #555)" in md
    assert "card-body-text" in md


def test_read_session_for_prompt_missing_card_silently_omits(
    tmp_path: Path,
) -> None:
    """If the card file doesn't exist, omit the section rather than 500."""
    create_session_files(13, tmp_path)
    md, _ = read_session_for_prompt(13, tmp_path, include_card_id=9999)
    assert "## Current card detail" not in md


# =============================================================================
# Format helper
# =============================================================================


def test_format_activity_block_elides_missing_fields() -> None:
    block = format_activity_block(summary="x", timestamp="2026-05-10T12:00:00Z")
    assert block.startswith("### 2026-05-10T12:00:00Z\n")
    block2 = format_activity_block(
        summary="x", task_id=1, timestamp="2026-05-10T12:00:00Z"
    )
    assert "task #1" in block2
    block3 = format_activity_block(
        summary="x", role="dev", timestamp="2026-05-10T12:00:00Z"
    )
    assert "dev" in block3
    block4 = format_activity_block(
        summary="x", role="dev", kind="spawn", timestamp="2026-05-10T12:00:00Z"
    )
    assert "dev:spawn" in block4


# =============================================================================
# Concurrency — file lock prevents corrupt writes
# =============================================================================


def test_file_lock_serializes_concurrent_appends(tmp_path: Path) -> None:
    """N threads each call append_recent_activity — final file has N entries.

    Without the lock, interleaved read-modify-writes would lose entries
    (last-writer-wins on the file). With the lock, the file ends with all
    N markers present.
    """
    create_session_files(20, tmp_path)
    n = 16

    def worker(i: int) -> None:
        append_recent_activity(
            20, summary=f"thread-{i}", repo_root=tmp_path
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    body = get_section_text(20, SECTION_RECENT_ACTIVITY, tmp_path)
    for i in range(n):
        assert f"thread-{i}" in body, (
            f"thread-{i} missing from Recent Activity — file lock failed"
        )


def test_file_lock_serializes_concurrent_card_appends(tmp_path: Path) -> None:
    create_session_files(21, tmp_path)
    n = 12

    def worker(i: int) -> None:
        write_card_log(
            21, 1, f"hb-{i}", mode="append", repo_root=tmp_path
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    card = (tmp_path / "_sessions" / "21" / "cards" / "1.md").read_text(
        encoding="utf-8"
    )
    for i in range(n):
        assert f"hb-{i}" in card
