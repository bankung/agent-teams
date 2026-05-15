"""git_status — read-only `git status --porcelain=v1 -b`.

Tier=read. Porcelain format because it's machine-parseable; the LLM can
extract modified/added/deleted/untracked counts trivially. The `-b` flag adds
the branch header so the LLM knows which branch it's on.
"""

from __future__ import annotations

import asyncio

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY
from ._run_git import run_git


@GLOBAL_REGISTRY.register
class GitStatusTool(Tool):
    name = "git_status"
    description = (
        "Show `git status --porcelain=v1 -b` against the working tree. "
        "Returns machine-readable status + branch header. No arguments."
    )
    tier = Tier.READ
    # No tool-specific fields — use ToolInput directly. An empty subclass
    # would only add a name; ToolInput itself satisfies the `input_schema`
    # contract on Tool (Pydantic v2 model with `extra='forbid'`).
    input_schema = ToolInput

    async def _run(
        self, input_obj: ToolInput, context: InvokeContext
    ) -> ToolResult:
        try:
            out = await run_git(
                ["status", "--porcelain=v1", "-b"],
                cwd=context.repo_root,
                timeout_sec=self.timeout_sec,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"git status exceeded {self.timeout_sec}s",
                retry_safe=True,
            )
        if out.returncode != 0:
            return ToolResult(
                success=False,
                error_code="git_error",
                error_msg=out.stderr.strip() or f"git status returned {out.returncode}",
                retry_safe=True,
            )
        return ToolResult(success=True, output=out.stdout, retry_safe=True)
