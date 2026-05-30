"""git_diff — read-only `git diff [-- <paths>]` against the working tree.

Tier=read. Per design doc §3.3. Returns the raw diff in `output`. Large diffs
are NOT truncated here — output cap enforcement is #981's job; for now the
caller (specialist node) is expected to reason about size.
"""

from __future__ import annotations

from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY
from ._run_git import run_git


class GitDiffInput(ToolInput):
    paths: list[str] | None = Field(
        None,
        description=(
            "Optional list of paths to diff. None = diff every modified file. "
            "Paths are passed verbatim to `git diff -- <paths>` so they're "
            "resolved relative to the git repo root."
        ),
    )


@GLOBAL_REGISTRY.register
class GitDiffTool(Tool):
    name = "git_diff"
    description = (
        "Show `git diff` against the working tree. Returns the raw unified "
        "diff output. Pass `paths` to scope the diff; omit to diff every "
        "modified file."
    )
    tier = Tier.READ
    input_schema = GitDiffInput

    async def _run(
        self, input_obj: GitDiffInput, context: InvokeContext
    ) -> ToolResult:
        args = ["diff"]
        if input_obj.paths is not None:
            args.append("--")
            args.extend(input_obj.paths)
        out = await run_git(args, cwd=context.repo_root, timeout_sec=self.timeout_sec)
        if out.timed_out:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"git diff exceeded {self.timeout_sec}s",
                retry_safe=True,
            )
        if out.returncode != 0:
            return ToolResult(
                success=False,
                error_code="git_error",
                error_msg=out.stderr.strip() or f"git diff returned {out.returncode}",
                retry_safe=True,
            )
        return ToolResult(success=True, output=out.stdout, retry_safe=True)
