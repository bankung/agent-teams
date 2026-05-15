"""file_write — create a NEW file with the given content.

Matches the design doc §3.2. Refuses if the path already exists — the LLM
should use `file_edit` to modify existing files. This split keeps the
semantics crisp: write = creation, edit = mutation.

Dry-run returns the would-be size + path without touching disk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY
from ._common import write_text


class FileWriteInput(ToolInput):
    path: str = Field(..., description="Absolute or repo-relative path. Must NOT exist.")
    content: str = Field(..., description="Full content to write (UTF-8).")
    dry_run: bool = Field(
        False,
        description=(
            "When True, return the would-be size + path without writing."
        ),
    )


@GLOBAL_REGISTRY.register
class FileWriteTool(Tool):
    name = "file_write"
    description = (
        "Create a new file with the given UTF-8 content. The path must NOT "
        "already exist — use file_edit to modify existing files. Parent "
        "directory must exist (this tool does NOT mkdir -p). Set dry_run=True "
        "to preview without writing."
    )
    tier = Tier.WRITE
    input_schema = FileWriteInput

    async def _run(
        self, input_obj: FileWriteInput, context: InvokeContext
    ) -> ToolResult:
        path = Path(input_obj.path)
        if path.exists():
            return ToolResult(
                success=False,
                error_code="already_exists",
                error_msg=(
                    f"Refusing to overwrite existing path: {path}. "
                    "Use file_edit to modify existing files."
                ),
                retry_safe=True,
            )
        parent = path.parent
        if not parent.exists():
            return ToolResult(
                success=False,
                error_code="parent_missing",
                error_msg=(
                    f"Parent directory does not exist: {parent}. "
                    "Create the directory first (this tool does not mkdir -p)."
                ),
                retry_safe=True,
            )

        size = len(input_obj.content.encode("utf-8"))

        if input_obj.dry_run:
            return ToolResult(
                success=True,
                output=(
                    f"Dry-run: file_write({path})\n"
                    f"Would write {size} bytes."
                ),
                retry_safe=True,
            )

        try:
            await asyncio.to_thread(write_text, path, input_obj.content)
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="io_error",
                error_msg=f"Failed to write {path}: {exc}",
                retry_safe=False,
            )

        return ToolResult(
            success=True,
            output=f"Wrote {size} bytes to {path}.",
            retry_safe=True,
        )
