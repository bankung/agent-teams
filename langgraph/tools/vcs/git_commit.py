"""git_commit — stage explicit paths and commit with the given message.

Tier=write. NEVER runs `git push`, `--amend`, or `--force-*`. Per the locked
design (§3.5 + the user/Lead decision): we hard-refuse if the message or
paths look like push/force/amend/branch-ref ops.

The contract is intentionally narrower than `git commit`:
- `paths` is REQUIRED (no implicit `git add -A`).
- `message` is REQUIRED.
- We invoke `git add <paths>` then `git commit -m <message> -- <paths>` so
  only the listed paths are committed — never accidentally stages other
  dirty files in the worktree.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY
from ._run_git import run_git


# Substrings whose presence in the message or paths is an automatic refusal.
# Substring (not whole-word) is intentional — the LLM occasionally hides flags
# inside a sentence ("please --amend this for me") and we'd rather over-refuse
# than ever execute --amend / --force / push.
_FORBIDDEN_TOKENS = ("--force", "--push", "--amend", "--no-verify")

# Path looks like a git ref (branch / tag / remote / HEAD pointer) — refuse.
# Matches: HEAD, HEAD~1, ORIG_HEAD, refs/heads/main, origin/main, master, etc.
_REF_LIKE_RE = re.compile(
    r"^(HEAD|ORIG_HEAD|FETCH_HEAD|MERGE_HEAD)(\^|~|$)|^refs/|^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
)


def _looks_like_ref(path: str) -> bool:
    """Heuristic — refs are short, slash-using, non-file-extension paths."""
    if not path:
        return False
    if _REF_LIKE_RE.match(path):
        return True
    return False


class GitCommitInput(ToolInput):
    message: str = Field(..., description="Commit message. Multi-line accepted.")
    paths: list[str] = Field(
        ...,
        description=(
            "Explicit list of paths to stage + commit. At least one. The tool "
            "stages ONLY these paths — other dirty files in the worktree are "
            "left untouched. Branch/ref-like values are refused."
        ),
        min_length=1,
    )


@GLOBAL_REGISTRY.register
class GitCommitTool(Tool):
    name = "git_commit"
    description = (
        "Stage the given paths and run `git commit -m <message> -- <paths>`. "
        "Refuses if the message contains --force / --push / --amend / "
        "--no-verify, or if any path looks like a branch/ref name. "
        "NEVER runs git push — pushes are human-only."
    )
    tier = Tier.WRITE
    input_schema = GitCommitInput

    async def _run(
        self, input_obj: ToolInput, context: InvokeContext
    ) -> ToolResult:
        assert isinstance(input_obj, GitCommitInput)

        msg_lower = input_obj.message.lower()
        for tok in _FORBIDDEN_TOKENS:
            if tok in msg_lower:
                return ToolResult(
                    success=False,
                    error_code="forbidden_flag",
                    error_msg=(
                        f"Refusing commit: message contains forbidden token {tok!r}. "
                        "git_commit is for plain `git commit -m` only; "
                        "push/amend/force operations are human-only."
                    ),
                    retry_safe=False,
                )

        for p in input_obj.paths:
            if _looks_like_ref(p):
                return ToolResult(
                    success=False,
                    error_code="path_looks_like_ref",
                    error_msg=(
                        f"Refusing commit: path {p!r} looks like a git ref "
                        "(branch/tag/HEAD pointer). git_commit only takes "
                        "explicit file paths."
                    ),
                    retry_safe=False,
                )

        # Stage. Use `git add --` to be sure paths aren't parsed as flags.
        try:
            add_out = await run_git(
                ["add", "--", *input_obj.paths],
                cwd=context.repo_root,
                timeout_sec=self.timeout_sec,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"git add exceeded {self.timeout_sec}s",
                retry_safe=False,
            )
        if add_out.returncode != 0:
            return ToolResult(
                success=False,
                error_code="git_error",
                error_msg=add_out.stderr.strip()
                or f"git add returned {add_out.returncode}",
                retry_safe=False,
            )

        # Commit. `--only --` restricts to the listed paths so any incidentally
        # staged file outside `paths` isn't pulled in.
        try:
            commit_out = await run_git(
                ["commit", "--only", "-m", input_obj.message, "--", *input_obj.paths],
                cwd=context.repo_root,
                timeout_sec=self.timeout_sec,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"git commit exceeded {self.timeout_sec}s",
                retry_safe=False,
            )
        if commit_out.returncode != 0:
            err = commit_out.stderr.strip() or commit_out.stdout.strip()
            return ToolResult(
                success=False,
                error_code="git_error",
                error_msg=err or f"git commit returned {commit_out.returncode}",
                retry_safe=False,
            )

        return ToolResult(
            success=True,
            output=commit_out.stdout,
            retry_safe=False,  # git_commit is not idempotent
        )
