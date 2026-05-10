"""Filesystem session.md writer/reader (CTX-2, Kanban #717).

Pure-Python helpers — no FastAPI / no DB. Routers call these to
append Recent Activity entries, write per-card heartbeat logs, and
read the prompt-ready markdown for LLM injection.

Layout (CTX-1 owns skeleton creation; we read/write into it):

    <repo_root>/_sessions/<session_id>/
        session.md                    Compacted History + Recent Activity
        archive/                      compact_001.md, compact_002.md, ...
        cards/                        <task_id>.md per-run heartbeat logs
        .lock                         advisory file lock (filelock)

Section markers in `session.md` are byte-equal exact strings:

    ## Compacted History
    ## Recent Activity

`get_section_text` / `replace_section` find them by exact match. Recent
Activity entries are appended as `### <ISO-Z> — task #<N> — <role>:<kind>`
sub-headings followed by the body.

File-locking discipline: every WRITE under `_sessions/<sid>/` (session.md
append/replace AND card log append) holds the per-session `.lock` for the
critical section. Single-process FastAPI is V1 — multi-process gunicorn
deferred (advisory lock semantics under POSIX/Windows differ; revisit when
we add a worker pool). CTX-2 is intentionally synchronous on disk: typical
append is < 1ms; CTX-3 will surface heavier reads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from filelock import FileLock

# Section heading byte-equal markers. Public — CTX-4 will import these
# rather than re-typing the literals.
SECTION_COMPACTED_HISTORY = "## Compacted History"
SECTION_RECENT_ACTIVITY = "## Recent Activity"
_VALID_SECTIONS = (SECTION_COMPACTED_HISTORY, SECTION_RECENT_ACTIVITY)

_SectionLiteral = Literal["## Compacted History", "## Recent Activity"]

# Skeleton text — re-exported from the CTX-1 helper indirectly. We keep the
# skeleton definition single-sourced in this module (the CTX-1 helper now
# delegates to us; see `session_files.py` for the back-compat shim).
_SESSION_MD_SKELETON = (
    f"{SECTION_COMPACTED_HISTORY}\n"
    "_(empty — no compacts yet)_\n"
    "\n"
    f"{SECTION_RECENT_ACTIVITY}\n"
    "_(empty — session just started)_\n"
)

_CARD_MD_SKELETON_TEMPLATE = (
    "# Card heartbeat — task {task_id}\n"
    "\n"
    "_(empty — first run for this task; heartbeat lines will append below)_\n"
)


# =============================================================================
# Path helpers
# =============================================================================


def _session_dir(session_id: int, repo_root: Path) -> Path:
    return Path(repo_root) / "_sessions" / str(session_id)


def _session_md(session_id: int, repo_root: Path) -> Path:
    return _session_dir(session_id, repo_root) / "session.md"


def _card_md(session_id: int, task_id: int, repo_root: Path) -> Path:
    return _session_dir(session_id, repo_root) / "cards" / f"{task_id}.md"


def _lock_for(session_id: int, repo_root: Path) -> FileLock:
    """Per-session advisory lock. Lock file lives inside the session dir."""
    return FileLock(str(_session_dir(session_id, repo_root) / ".lock"))


# =============================================================================
# Skeleton creation
# =============================================================================


def create_session_files(session_id: int, repo_root: Path) -> Path:
    """Create `<repo_root>/_sessions/<session_id>/{session.md, archive/, cards/}`.

    Idempotent — safe to call repeatedly. Returns the session directory.
    """
    sdir = _session_dir(session_id, repo_root)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "archive").mkdir(exist_ok=True)
    (sdir / "cards").mkdir(exist_ok=True)

    sess_md = sdir / "session.md"
    if not sess_md.exists():
        sess_md.write_text(_SESSION_MD_SKELETON, encoding="utf-8")
    return sdir


def create_card_log_skeleton(
    session_id: int, task_id: int, repo_root: Path
) -> Path:
    """Create `<session_dir>/cards/<task_id>.md` skeleton if missing. Idempotent."""
    sdir = _session_dir(session_id, repo_root)
    cards = sdir / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    card_path = cards / f"{task_id}.md"
    if not card_path.exists():
        card_path.write_text(
            _CARD_MD_SKELETON_TEMPLATE.format(task_id=task_id), encoding="utf-8"
        )
    return card_path


# =============================================================================
# Section read / replace
# =============================================================================


def _split_sections(text: str) -> dict[str, tuple[int, int]]:
    """Locate each section marker in the file and return body slice indices.

    For each known section, returns (body_start, body_end) — character offsets
    into `text`. body_start is the position right after the marker line's
    newline; body_end is the position of the next section marker (or EOF).
    Sections not present in the text are absent from the dict.
    """
    spans: dict[str, tuple[int, int]] = {}
    # Find all marker positions first, sorted by offset.
    found: list[tuple[int, str]] = []
    for marker in _VALID_SECTIONS:
        pos = text.find(marker)
        if pos == -1:
            continue
        # Marker must start on a fresh line (offset 0 or preceded by \n).
        if pos != 0 and text[pos - 1] != "\n":
            continue
        found.append((pos, marker))
    found.sort()

    for i, (pos, marker) in enumerate(found):
        # Body starts after the marker line's trailing newline.
        nl = text.find("\n", pos)
        body_start = nl + 1 if nl != -1 else len(text)
        # Body ends where the next marker begins, or EOF.
        body_end = found[i + 1][0] if i + 1 < len(found) else len(text)
        spans[marker] = (body_start, body_end)
    return spans


def get_section_text(
    session_id: int, section: _SectionLiteral, repo_root: Path
) -> str:
    """Return the body of one section (between marker headings).

    Trailing whitespace is preserved. Missing section → empty string. Raises
    `FileNotFoundError` if `session.md` does not exist.
    """
    if section not in _VALID_SECTIONS:
        raise ValueError(f"unknown section {section!r}")
    # Acquire to avoid observing torn writes from concurrent appenders (V1: serialized reads behind writes).
    with _lock_for(session_id, repo_root):
        text = _session_md(session_id, repo_root).read_text(encoding="utf-8")
    spans = _split_sections(text)
    if section not in spans:
        return ""
    start, end = spans[section]
    return text[start:end]


def replace_section(
    session_id: int,
    section: _SectionLiteral,
    new_content: str,
    repo_root: Path,
) -> None:
    """Replace a section's body text. Other sections preserved verbatim.

    `new_content` should NOT include the section heading itself (we keep
    the heading line; only the body between markers is rewritten). A
    trailing newline is enforced so the next marker stays on a fresh line.
    """
    if section not in _VALID_SECTIONS:
        raise ValueError(f"unknown section {section!r}")
    if not new_content.endswith("\n"):
        new_content = new_content + "\n"

    sess_md = _session_md(session_id, repo_root)
    with _lock_for(session_id, repo_root):
        text = sess_md.read_text(encoding="utf-8")
        spans = _split_sections(text)
        if section not in spans:
            # Append a fresh section to EOF if missing — defensive.
            text = (
                text.rstrip("\n")
                + "\n\n"
                + section
                + "\n"
                + new_content
            )
        else:
            start, end = spans[section]
            text = text[:start] + new_content + text[end:]
        sess_md.write_text(text, encoding="utf-8")


# =============================================================================
# Recent Activity append
# =============================================================================


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp like `2026-05-10T12:34:56Z` (second precision)."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_activity_block(
    *,
    summary: str,
    task_id: int | None = None,
    role: str | None = None,
    kind: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Build a Recent Activity entry. Used by `append_recent_activity`.

    Header format: `### <ISO-Z> — task #<N> — <role>:<kind>`. Missing fields
    are elided cleanly (e.g., no task → `### <ISO-Z> — <role>:<kind>`).
    Body is the summary text, terminated with a blank line for readability.
    """
    ts = timestamp or _utc_now_iso()
    parts = [ts]
    if task_id is not None:
        parts.append(f"task #{task_id}")
    role_kind = ":".join(p for p in (role, kind) if p)
    if role_kind:
        parts.append(role_kind)
    header = "### " + " — ".join(parts)
    body = summary.rstrip()
    return f"{header}\n{body}\n\n"


def append_recent_activity(
    session_id: int,
    *,
    summary: str,
    task_id: int | None = None,
    role: str | None = None,
    kind: str | None = None,
    repo_root: Path,
) -> str:
    """Append an entry to `## Recent Activity`. Returns the appended block.

    Lock-protected: serializes concurrent appends within one process. The
    initial skeleton's `_(empty — session just started)_` placeholder is
    detected and replaced on first append (otherwise it would persist
    forever as a misleading prefix).
    """
    block = format_activity_block(
        summary=summary, task_id=task_id, role=role, kind=kind
    )
    sess_md = _session_md(session_id, repo_root)
    with _lock_for(session_id, repo_root):
        text = sess_md.read_text(encoding="utf-8")
        spans = _split_sections(text)
        if SECTION_RECENT_ACTIVITY not in spans:
            # Defensive — append a fresh section if the marker drifted.
            text = (
                text.rstrip("\n")
                + "\n\n"
                + SECTION_RECENT_ACTIVITY
                + "\n"
                + block
            )
            sess_md.write_text(text, encoding="utf-8")
            return block

        start, end = spans[SECTION_RECENT_ACTIVITY]
        body = text[start:end]
        # Drop the placeholder line on first real append.
        stripped = body.strip()
        if stripped == "_(empty — session just started)_":
            new_body = block
        else:
            # Ensure single blank line separator before the new block.
            new_body = body.rstrip("\n") + "\n\n" + block
        text = text[:start] + new_body + text[end:]
        sess_md.write_text(text, encoding="utf-8")
    return block


# =============================================================================
# Card heartbeat
# =============================================================================


def write_card_log(
    session_id: int,
    task_id: int,
    content: str,
    *,
    mode: Literal["append", "replace"] = "append",
    repo_root: Path,
) -> Path:
    """Write a heartbeat block to `cards/<task_id>.md`.

    `mode='append'` (default) appends a timestamped block; the card file is
    auto-created with skeleton if missing. `mode='replace'` overwrites the
    full file with `content` verbatim (snapshot mode for end-of-run dumps).
    """
    if mode not in ("append", "replace"):
        raise ValueError(f"unknown mode {mode!r}")
    card_path = _card_md(session_id, task_id, repo_root)
    card_path.parent.mkdir(parents=True, exist_ok=True)

    with _lock_for(session_id, repo_root):
        if mode == "replace":
            card_path.write_text(content, encoding="utf-8")
            return card_path

        # Append mode — ensure skeleton exists, then add a timestamped block.
        if not card_path.exists():
            card_path.write_text(
                _CARD_MD_SKELETON_TEMPLATE.format(task_id=task_id),
                encoding="utf-8",
            )
        existing = card_path.read_text(encoding="utf-8")
        ts = _utc_now_iso()
        block = f"### {ts}\n{content.rstrip()}\n\n"
        card_path.write_text(existing.rstrip("\n") + "\n\n" + block, encoding="utf-8")
    return card_path


# =============================================================================
# Prompt-ready read
# =============================================================================


def read_session_for_prompt(
    session_id: int,
    repo_root: Path,
    *,
    include_card_id: int | None = None,
) -> tuple[str, int]:
    """Return (markdown, char_count) ready for LLM prompt injection.

    Layout:

        # Session context

        ## Compacted History
        <body>

        ## Recent Activity
        <body>

        ## Current card detail (if include_card_id given)
        <card file body verbatim>

    Token counting is deferred to CTX-3 — char count is returned as a cheap
    proxy for the caller to surface to the operator.
    """
    sess_md = _session_md(session_id, repo_root)
    # Acquire to avoid observing torn writes from concurrent appenders (V1: serialized reads behind writes).
    with _lock_for(session_id, repo_root):
        text = sess_md.read_text(encoding="utf-8")
        card_text: str | None = None
        if include_card_id is not None:
            card_path = _card_md(session_id, include_card_id, repo_root)
            if card_path.exists():
                card_text = card_path.read_text(encoding="utf-8").rstrip("\n")

    parts = ["# Session context\n", text.rstrip("\n") + "\n"]

    if card_text is not None:
        parts.append(
            f"\n## Current card detail (task #{include_card_id})\n{card_text}\n"
        )

    out = "\n".join(parts)
    return out, len(out)
