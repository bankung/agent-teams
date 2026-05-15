"""file_edit — exact-string find-and-replace on an existing file.

Matches the design doc §3.1. The contract mirrors Claude Code's own `Edit`
tool: `old_string` must match exactly ONCE in the file. Zero matches and
multiple matches both halt — duplicate matches are a real footgun because a
naive replace would clobber the wrong occurrence. The LLM is expected to widen
`old_string` with surrounding context to disambiguate.

Dry-run mode returns a unified diff describing what WOULD change, without
touching the file. Useful for the engine to preview before committing (and for
#979's halt-for-approval flow to surface a diff in the Kanban UI).
"""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path

from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY


class FileEditInput(ToolInput):
    path: str = Field(..., description="Absolute or repo-relative path to the file to edit.")
    old_string: str = Field(
        ...,
        description=(
            "Exact string to find. Must match exactly ONCE in the file. "
            "Include surrounding context to disambiguate when the literal "
            "text appears multiple times."
        ),
    )
    new_string: str = Field(..., description="Replacement string.")
    dry_run: bool = Field(
        False,
        description=(
            "When True, return a unified diff of the proposed edit without "
            "modifying the file."
        ),
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _build_diff(path: Path, before: str, after: str) -> str:
    """Unified diff suitable for direct display to the LLM / Kanban UI."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
            n=3,
        )
    )


@GLOBAL_REGISTRY.register
class FileEditTool(Tool):
    name = "file_edit"
    description = (
        "Edit a file by exact string replacement. The old_string must match "
        "exactly ONCE in the file (zero or multiple matches halt the call — "
        "widen old_string with surrounding context to disambiguate). Set "
        "dry_run=True to preview the diff without writing."
    )
    tier = Tier.WRITE
    input_schema = FileEditInput

    async def _run(
        self, input_obj: ToolInput, context: InvokeContext
    ) -> ToolResult:
        assert isinstance(input_obj, FileEditInput)
        path = Path(input_obj.path)
        if not path.exists():
            return ToolResult(
                success=False,
                error_code="not_found",
                error_msg=f"File does not exist: {path}",
                retry_safe=True,
            )
        if not path.is_file():
            return ToolResult(
                success=False,
                error_code="not_a_file",
                error_msg=f"Path is not a regular file: {path}",
                retry_safe=True,
            )

        try:
            before = await asyncio.to_thread(_read_text, path)
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="io_error",
                error_msg=f"Failed to read {path}: {exc}",
                retry_safe=True,
            )

        # Count occurrences. The design doc calls this `match_ambiguous`.
        count = before.count(input_obj.old_string)
        if count == 0:
            return ToolResult(
                success=False,
                error_code="match_ambiguous",
                error_msg=(
                    f"old_string not found in {path} (0 matches). "
                    "Check whitespace/indentation and try a smaller, more "
                    "specific snippet."
                ),
                retry_safe=True,
            )
        if count > 1:
            return ToolResult(
                success=False,
                error_code="match_ambiguous",
                error_msg=(
                    f"old_string is not unique in {path} ({count} matches). "
                    "Widen old_string with surrounding context to make it "
                    "match exactly once."
                ),
                retry_safe=True,
            )

        after = before.replace(input_obj.old_string, input_obj.new_string, 1)
        diff = _build_diff(path, before, after)

        if input_obj.dry_run:
            return ToolResult(
                success=True,
                output=(
                    f"Dry-run: file_edit({path})\n"
                    f"Would apply this diff:\n{diff}"
                    if diff
                    else f"Dry-run: file_edit({path})\nNo changes (old == new)."
                ),
                retry_safe=True,
            )

        try:
            await asyncio.to_thread(_write_text, path, after)
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="io_error",
                error_msg=f"Failed to write {path}: {exc}",
                retry_safe=False,  # partial write possible — caller should verify.
            )

        return ToolResult(
            success=True,
            output=diff or f"Applied edit to {path} (zero-line diff: old == new).",
            retry_safe=True,
        )
