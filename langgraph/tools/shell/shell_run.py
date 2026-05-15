"""shell_run — run a narrow allowlist of shell commands with a hard denylist.

Per the locked decisions on #949 (Q5 → A):
- DENYLIST is HARDCODED here for security. Per-project config can NEVER lift it.
- ALLOWLIST is the initial command-prefix set; future per-project config (#979)
  may broaden it. For #977 we use the hardcoded initial set so tests are
  reproducible without DB state.

Tier=destructive. Even with the allowlist, arbitrary shell COULD do damage,
so the permission gate (#979) treats this as the highest-risk tier — any
shell_run call halts for review by default. The allowlist's job is to make
"approve" a defensible choice; the denylist's job is to make "approve a denied
command" impossible without source-level changes.

Detection passes:
1. Tokenize cmd via shlex (POSIX mode).
2. Reject if cmd contains shell-control chars (`|`, `;`, `&&`, `||`, `$(`,
   backticks, `>` `<` redirections, `&` background) — those compositions
   could mask denylist commands inside subshells. If detection is uncertain,
   halt (matches the locked decision).
3. First token (and 'cmd1 cmd2' multi-token prefix) MUST match an allowlist
   entry. Allowlist match is by EXACT-TOKEN prefix — `pytest` matches
   `pytest --version` but not `pytestxyz`.
4. First token must NOT appear in the denylist. (Step 2 already rejects any
   composite cmd so denylist enforcement only needs to check token #1.)

Note: this layer enforces detection-time policy. Subprocess timeout is
enforced via asyncio.wait_for. The global output-cap (100KB) is applied here
loosely — full cap discipline + fs-boundary is #981.
"""

from __future__ import annotations

import asyncio
import shlex

from pydantic import Field

from ..base import InvokeContext, Tier, Tool, ToolInput, ToolResult
from ..registry import GLOBAL_REGISTRY


# Locked decision (Kanban #949 Q5 → A): immutable, source-level only.
# `mv` and `cp` are on the denylist because the LLM treating them as harmless
# is a real footgun ("move that file out of the way..." → side effects).
DENYLIST: tuple[str, ...] = (
    "rm",
    "sudo",
    "kill",
    "dd",
    "mkfs",
    "chmod",
    "chown",
    "mv",
    "cp",
)

# Initial allowlist — command-prefix match. Multi-token entries are matched
# greedily: `python -m pytest` matches `python -m pytest tests/` but NOT
# `python -m pytestxyz`.
ALLOWLIST: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("pnpm", "test"),
    ("npm", "test"),
    ("tsc",),
    ("npm", "run", "build"),
    ("pnpm", "run", "build"),
    ("python", "-m", "pytest"),
    ("docker", "compose", "exec"),
    ("git", "status"),
    ("git", "diff"),
)

# Shell-control chars / sequences. Presence of ANY of these → halt.
# `>` and `<` redirections are caught here because they can be used to write
# to arbitrary paths bypassing fs-boundary checks. `&` background is also
# caught because a backgrounded process escapes the timeout.
_SHELL_CONTROL_CHARS = ("|", ";", "&", "$", "`", ">", "<")
_SHELL_CONTROL_SEQUENCES = ("&&", "||", "$(", "`")


# Output size cap — design doc §6 says 100KB. Applied here so stdout doesn't
# blow up the audit log. #981 will move this into a centralized config.
_OUTPUT_CAP_BYTES = 100_000


def _contains_shell_control(cmd: str) -> bool:
    """True if the raw cmd string contains any shell-control char/sequence."""
    for seq in _SHELL_CONTROL_SEQUENCES:
        if seq in cmd:
            return True
    for ch in _SHELL_CONTROL_CHARS:
        if ch in cmd:
            return True
    return False


def _matches_allowlist(tokens: list[str]) -> bool:
    """True iff `tokens` starts with one of the ALLOWLIST entries."""
    for prefix in ALLOWLIST:
        if len(tokens) < len(prefix):
            continue
        if tuple(tokens[: len(prefix)]) == prefix:
            return True
    return False


def _truncate_output(text: str, cap: int = _OUTPUT_CAP_BYTES) -> tuple[str, bool]:
    """Truncate `text` to `cap` bytes (UTF-8). Returns (text, was_truncated).

    Truncation is conservative — we encode → slice → decode with errors='replace'
    so we never emit a partial UTF-8 sequence to the LLM.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text, False
    sliced = encoded[:cap].decode("utf-8", errors="replace")
    return sliced + f"\n... [output truncated; total {len(encoded)} bytes]", True


class ShellRunInput(ToolInput):
    cmd: str = Field(
        ...,
        description=(
            "Shell command to run. Must start with an ALLOWLISTED prefix "
            "(pytest, pnpm test, npm test, tsc, npm/pnpm run build, "
            "python -m pytest, docker compose exec, git status, git diff). "
            "Must NOT contain shell-control chars (| ; & $ ` > < && || $(...). "
            "Must NOT start with a denylisted command "
            "(rm/sudo/kill/dd/mkfs/chmod/chown/mv/cp)."
        ),
    )
    timeout_s: int = Field(
        30,
        ge=1,
        le=300,
        description="Hard subprocess timeout in seconds (1..300; default 30).",
    )


@GLOBAL_REGISTRY.register
class ShellRunTool(Tool):
    name = "shell_run"
    description = (
        "Run a shell command from a narrow allowlist (pytest, npm test, tsc, "
        "git status, git diff, docker compose exec, python -m pytest, "
        "npm/pnpm run build). Hard denylist: rm/sudo/kill/dd/mkfs/chmod/chown/"
        "mv/cp. Composite commands (pipes, redirections, &&, ||, subshells) "
        "are refused outright. Returns stdout in output, stderr in error_msg "
        "on non-zero exit, with a {timeout_s}s subprocess timeout."
    )
    tier = Tier.DESTRUCTIVE
    input_schema = ShellRunInput

    async def _run(
        self, input_obj: ToolInput, context: InvokeContext
    ) -> ToolResult:
        assert isinstance(input_obj, ShellRunInput)
        cmd = input_obj.cmd.strip()
        if not cmd:
            return ToolResult(
                success=False,
                error_code="invalid_input",
                error_msg="cmd is empty",
                retry_safe=True,
            )

        # 1. Shell-control char check. Refuse before tokenization so a cmd like
        #    `pytest && rm -rf /` is rejected even though token #1 is `pytest`.
        if _contains_shell_control(cmd):
            return ToolResult(
                success=False,
                error_code="shell_control_forbidden",
                error_msg=(
                    "Composite shell commands are not allowed. Remove pipe, "
                    "redirection, subshell, background, and chain operators "
                    "(| ; & $ ` > < && || $(...). Run one command per call."
                ),
                retry_safe=True,
            )

        # 2. Tokenize.
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError as exc:
            return ToolResult(
                success=False,
                error_code="invalid_input",
                error_msg=f"Failed to tokenize cmd: {exc}",
                retry_safe=True,
            )
        if not tokens:
            return ToolResult(
                success=False,
                error_code="invalid_input",
                error_msg="cmd parsed to zero tokens",
                retry_safe=True,
            )

        # 3. Denylist on the first token.
        if tokens[0] in DENYLIST:
            return ToolResult(
                success=False,
                error_code="blocked_command",
                error_msg=(
                    f"Command {tokens[0]!r} is on the hard denylist "
                    f"(immutable: {sorted(DENYLIST)}). Refusing to execute."
                ),
                retry_safe=False,
            )

        # 4. Allowlist match.
        if not _matches_allowlist(tokens):
            return ToolResult(
                success=False,
                error_code="command_not_allowed",
                error_msg=(
                    f"Command prefix not in allowlist. First token(s): "
                    f"{tokens[: min(3, len(tokens))]!r}. "
                    f"Allowed prefixes: {[' '.join(p) for p in ALLOWLIST]}."
                ),
                retry_safe=True,
            )

        # 5. Execute. Capture stdout+stderr separately so non-zero exits can
        #    surface stderr in error_msg.
        try:
            proc = await asyncio.create_subprocess_exec(
                *tokens,
                cwd=context.working_path or context.repo_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return ToolResult(
                success=False,
                error_code="executable_not_found",
                error_msg=f"Executable {tokens[0]!r} not found: {exc}",
                retry_safe=True,
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=input_obj.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                success=False,
                error_code="timeout",
                error_msg=f"shell_run exceeded {input_obj.timeout_s}s",
                retry_safe=True,
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        stdout, _ = _truncate_output(stdout)

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error_code="nonzero_exit",
                error_msg=(stderr.strip() or f"exit={proc.returncode}"),
                output=stdout,
                retry_safe=True,
            )

        return ToolResult(success=True, output=stdout, retry_safe=True)
