"""Shared git subprocess helper.

Centralizes the `asyncio.create_subprocess_exec(...)` boilerplate so each
git_* tool stays focused on the args it constructs. Encoding is forced to
UTF-8 + errors='replace' — git can spit raw bytes when filenames contain
non-UTF-8 chars and we'd rather see U+FFFD than crash.

Timeout handling is also centralized here (Phase 1 minimization). When
`asyncio.wait_for` raises `asyncio.TimeoutError`, the process is killed and
`GitOutput.timed_out` is set to True. Each calling tool checks this flag and
builds its own `ToolResult` with the appropriate message — the per-caller
ToolResult shape is preserved byte-for-byte; only the repetitive
`except asyncio.TimeoutError:` blocks are removed from the callers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class GitOutput:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = field(default=False)


async def run_git(
    args: list[str],
    cwd: str,
    timeout_sec: int = 30,
) -> GitOutput:
    """Invoke `git <args>` in `cwd`, capture stdout+stderr, with a hard timeout.

    Returns a GitOutput dataclass. When the timeout fires, the process is
    killed and `GitOutput.timed_out` is set to True — callers check this
    flag and build their own ToolResult (so the per-caller error message and
    retry_safe value remain unchanged). Caller decides whether a non-zero
    returncode is a ToolResult error or a legitimate git state.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return GitOutput(returncode=-1, stdout="", stderr="", timed_out=True)
    return GitOutput(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )
