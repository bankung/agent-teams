"""file_edit — exact-string find-and-replace on an existing file.

Matches the design doc §3.1. The contract mirrors Claude Code's own `Edit`
tool: `old_string` must match exactly ONCE in the file. Zero matches and
multiple matches both halt — duplicate matches are a real footgun because a
naive replace would clobber the wrong occurrence. The LLM is expected to widen
`old_string` with surrounding context to disambiguate.

Dry-run mode returns a unified diff describing what WOULD change, without
touching the file. Useful for the engine to preview before committing (and for
#979's halt-for-approval flow to surface a diff in the Kanban UI).

Kanban #2837: path resolution mirrors `sandbox.fs_boundary_check` exactly
(via `resolve_fs_path`) so the boundary-checked path and the path actually
read/written are provably identical — a relative path anchors at
working_path/repo_root, never the process CWD. Both the read and the write
are wrapped in `asyncio.wait_for` (self.timeout_sec) so a hung disk I/O
can't freeze the serial worker loop forever, mirroring how
shell_run/git_*/http_* already self-enforce a timeout.
"""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path

from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY
from ..sandbox import resolve_fs_path
from ._common import read_text, write_text


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
        self, input_obj: FileEditInput, context: InvokeContext
    ) -> ToolResult:
        # Resolve via the SAME base fs_boundary_check uses (#2837) so a
        # relative path anchors at working_path/repo_root — never the
        # process CWD (/repo/langgraph) — and the gate's checked path is
        # provably the path touched below.
        path = Path(resolve_fs_path(context, input_obj.path))
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
            # shortcut: wait_for() unblocks the caller on timeout but can't
            # force-kill the to_thread() worker (Python threads aren't
            # cancellable) — see file_write.py's write wait_for for the full
            # rationale; the same tradeoff applies to both read and write here.
            before = await asyncio.wait_for(
                asyncio.to_thread(read_text, path), timeout=self.timeout_sec
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"file_edit exceeded {self.timeout_sec}s reading {path}",
                retry_safe=True,
            )
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
            await asyncio.wait_for(
                asyncio.to_thread(write_text, path, after), timeout=self.timeout_sec
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"file_edit exceeded {self.timeout_sec}s writing {path}",
                retry_safe=False,  # partial write possible — caller should verify.
            )
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
