"""Shared sync IO helpers for fs tools (file_edit, file_write).

Both `_run` methods wrap these via `asyncio.to_thread(...)` so the helpers
themselves remain plain sync functions — the threading happens at the call
site to keep the helper testable in isolation.

Any future change (atomic-write via temp + rename, BOM stripping, etc.)
happens here ONCE and propagates to both tools.
"""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    """Read a UTF-8 text file. Caller wraps in `asyncio.to_thread`."""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Write a UTF-8 text file. Caller wraps in `asyncio.to_thread`."""
    path.write_text(content, encoding="utf-8")
