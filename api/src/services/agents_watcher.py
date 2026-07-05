"""Agents-dir change watcher (Kanban #1019).

Polls `.claude/agents/*.md` every ~4s for a filename/mtime/size signature
change and, on a change, broadcasts `{"table": "agents", "op": "changed",
"ts": ...}` over the existing SSE broker (`row_changed_listener.broker`) so
connected clients can show a "restart your Claude Code session" banner.

Polling, not inotify/watchdog: this repo runs on Windows host + Docker Desktop
bind mounts, where filesystem-event notification (inotify inside the
container) is unreliable across the bind-mount boundary. There is no
`watchdog` dependency in this project and this feature does not warrant
adding one — a cheap directory stat scan every few seconds is simpler and
sufficient (Karpathy ladder: already-installed primitives only).

Wiring: main.lifespan calls `start_agents_watcher()` alongside
`start_listener()` on enter, and `stop_agents_watcher()` alongside
`stop_listener()` on exit. Honors the SAME `APP_SSE_DISABLE` env as the
broker (pytest default) so no background task spins up during the test
suite unless a test explicitly wants it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.services import row_changed_listener
from src.services.agent_validation import default_agents_dir
from src.settings import get_settings

logger = logging.getLogger(__name__)

# Default poll interval — overridable via APP_AGENTS_WATCH_SECONDS (tests use
# a short interval; production is fine at the 4s default from the task brief).
_DEFAULT_POLL_SECONDS = 4.0

_task: asyncio.Task | None = None


def _poll_seconds() -> float:
    """Read the poll interval from env at CALL time (mirrors is_disabled()'s
    call-time-read pattern so tests can monkeypatch the env per-test)."""
    raw = os.environ.get("APP_AGENTS_WATCH_SECONDS", str(_DEFAULT_POLL_SECONDS))
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_POLL_SECONDS


def compute_dir_signature(agents_dir: Path) -> frozenset[tuple[str, int, int]]:
    """Return a stable signature of the agents dir's `*.md` files.

    One tuple per real agent file (same skip rule as the validator/gallery:
    `*.md`, not underscore-prefixed): `(filename, st_mtime_ns, st_size)`. A
    frozenset so member order never affects equality — only membership
    (add/remove/touch a file) does. Returns an empty frozenset when the
    directory is missing or unreadable (mirrors `validate_agents_dir`'s
    "missing dir = zero files, not an error" posture); the caller's try/except
    is the actual crash guard for transient I/O mid-scan.
    """
    entries: set[tuple[str, int, int]] = set()
    for path in agents_dir.iterdir():
        if path.suffix != ".md" or path.name.startswith("_"):
            continue
        st = path.stat()
        entries.add((path.name, st.st_mtime_ns, st.st_size))
    return frozenset(entries)


async def _watch_loop(agents_dir: Path) -> None:
    """The polling loop body — runs until cancelled.

    First scan establishes the baseline WITHOUT broadcasting (no spurious
    startup banner on every api restart). Every subsequent scan compares
    against the last-known signature; a difference triggers one broadcast and
    updates the baseline. Any exception during a scan (missing dir mid-tick,
    transient IO) is caught, logged, and the tick is skipped — the loop itself
    must never crash, since a raised exception here would silently kill the
    background task with the broker never noticing.
    """
    baseline: frozenset[tuple[str, int, int]] | None = None
    while True:
        try:
            current = compute_dir_signature(agents_dir)
        except OSError:
            logger.warning(
                "agents_watcher: scan failed for %r (transient IO?) — skipping tick",
                str(agents_dir),
            )
        else:
            if baseline is None:
                baseline = current
                logger.info(
                    "agents_watcher: baseline established — %d agent file(s) in %r",
                    len(current),
                    str(agents_dir),
                )
            elif current != baseline:
                baseline = current
                row_changed_listener.broker.broadcast(
                    {
                        "table": "agents",
                        "op": "changed",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                )
                logger.info("agents_watcher: change detected — broadcast sent")

        await asyncio.sleep(_poll_seconds())


async def start_agents_watcher() -> None:
    """Called from FastAPI lifespan on enter. No-op when disabled or already
    running (idempotent, mirrors `start_listener`'s posture)."""
    global _task
    if row_changed_listener.is_disabled():
        logger.info("agents_watcher disabled via APP_SSE_DISABLE")
        return
    if _task is not None and not _task.done():
        return
    agents_dir = default_agents_dir(Path(get_settings().repo_root))
    _task = asyncio.create_task(_watch_loop(agents_dir), name="agents_watcher")


async def stop_agents_watcher() -> None:
    """Called from FastAPI lifespan on exit. Always safe — cancels the task
    and awaits its (suppressed) CancelledError so shutdown doesn't leak a
    pending task."""
    global _task
    task = _task
    _task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
