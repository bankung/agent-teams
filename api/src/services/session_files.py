"""Filesystem skeleton for session markdown content (CTX-1 minimal slice).

Hybrid storage layout (Kanban #716 scope-lock):

    <repo_root>/_sessions/<session_id>/
        session.md                    Compacted History + Recent Activity
        archive/                      compact_001.md, compact_002.md, ...
        cards/                        <task_id>.md per-run heartbeat logs

CTX-1 ONLY creates the directory tree + skeleton `session.md` + an empty
per-task card file when a run is created with `task_id`. The rich
read/write/heartbeat API lives in CTX-2 (`services/session_store.py`,
not yet shipped).

`_sessions/` is gitignored; never committed. Production migration to a
named Docker volume is deferred (see decisions.md 2026-05-10 entry).

All paths derive from `settings.repo_root` (NEVER hardcoded `/repo`); the
test suite's `repo_root` fixture points at a tmpdir.
"""

from __future__ import annotations

from pathlib import Path

# Skeleton text for a brand-new session.md. CTX-2 owns append/read; this slice
# only stamps the section headers so the file is a valid scaffold.
_SESSION_MD_SKELETON = (
    "## Compacted History\n"
    "_(empty — no compacts yet)_\n"
    "\n"
    "## Recent Activity\n"
    "_(empty — session just started)_\n"
)

_CARD_MD_SKELETON_TEMPLATE = (
    "# Card heartbeat — task {task_id}\n"
    "\n"
    "_(empty — first run for this task; CTX-2 will append heartbeat lines)_\n"
)


def _session_dir(session_id: int, repo_root: Path) -> Path:
    """Return `<repo_root>/_sessions/<session_id>/`. Pure path math — does
    NOT touch the filesystem. Used by tests as a probe target."""
    return Path(repo_root) / "_sessions" / str(session_id)


def create_session_skeleton(session_id: int, repo_root: Path) -> Path:
    """Create `<repo_root>/_sessions/<session_id>/{session.md, archive/, cards/}`.

    Idempotent — re-call is a no-op (existing files are NOT overwritten;
    existing dirs are NOT removed). Returns the session directory `Path`.
    """
    session_dir = _session_dir(session_id, repo_root)
    session_dir.mkdir(parents=True, exist_ok=True)

    archive_dir = session_dir / "archive"
    archive_dir.mkdir(exist_ok=True)

    cards_dir = session_dir / "cards"
    cards_dir.mkdir(exist_ok=True)

    session_md = session_dir / "session.md"
    if not session_md.exists():
        session_md.write_text(_SESSION_MD_SKELETON, encoding="utf-8")

    return session_dir


def create_card_log_skeleton(
    session_id: int, task_id: int, repo_root: Path
) -> Path:
    """Create `<session_dir>/cards/<task_id>.md` if not exists. Idempotent.

    Returns the card file `Path`. The caller is responsible for ensuring the
    session skeleton exists first (the router calls `create_session_skeleton`
    on session creation; this function adds the per-task card on run create).
    """
    session_dir = _session_dir(session_id, repo_root)
    cards_dir = session_dir / "cards"
    # Defensive — the parent session skeleton MAY have been removed manually.
    # `parents=True` lets us land cleanly if cards/ is missing.
    cards_dir.mkdir(parents=True, exist_ok=True)

    card_path = cards_dir / f"{task_id}.md"
    if not card_path.exists():
        card_path.write_text(
            _CARD_MD_SKELETON_TEMPLATE.format(task_id=task_id), encoding="utf-8"
        )
    return card_path
