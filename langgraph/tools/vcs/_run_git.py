"""Shared git subprocess helper.

Centralizes the `asyncio.create_subprocess_exec(...)` boilerplate so each
git_* tool stays focused on the args it constructs. Encoding is forced to
UTF-8 + errors='replace' — git can spit raw bytes when filenames contain
non-UTF-8 chars and we'd rather see � than crash.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class GitOutput:
    returncode: int
    stdout: str
    stderr: str


async def run_git(
    args: list[str],
    cwd: str,
    timeout_sec: int = 30,
) -> GitOutput:
    """Invoke `git <args>` in `cwd`, capture stdout+stderr, with a hard timeout.

    Returns a GitOutput dataclass. Caller decides whether non-zero return is a
    `ToolResult` error or a legitimate state (e.g. `git status` non-zero on a
    detached HEAD is rare and should propagate to error_code='git_error').
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
        raise
    return GitOutput(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )
