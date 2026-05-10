"""Filesystem skeleton for session markdown content (CTX-1 minimal slice).

Hybrid storage layout (Kanban #716 scope-lock):

    <repo_root>/_sessions/<session_id>/
        session.md                    Compacted History + Recent Activity
        archive/                      compact_001.md, compact_002.md, ...
        cards/                        <task_id>.md per-run heartbeat logs

The rich read/write/heartbeat API lives in CTX-2 (not yet shipped).
All paths derive from `settings.repo_root` (NEVER hardcoded `/repo`).
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


def create_session_skeleton(session_id: int, repo_root: Path) -> Path:
    """Create `<repo_root>/_sessions/<session_id>/{session.md, archive/, cards/}`. Idempotent."""
    session_dir = Path(repo_root) / "_sessions" / str(session_id)
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
    """Create `<session_dir>/cards/<task_id>.md` if not exists. Idempotent."""
    session_dir = Path(repo_root) / "_sessions" / str(session_id)
    cards_dir = session_dir / "cards"
    # Defensive — `parents=True` lets us land cleanly if cards/ is missing.
    cards_dir.mkdir(parents=True, exist_ok=True)

    card_path = cards_dir / f"{task_id}.md"
    if not card_path.exists():
        card_path.write_text(
            _CARD_MD_SKELETON_TEMPLATE.format(task_id=task_id), encoding="utf-8"
        )
    return card_path
