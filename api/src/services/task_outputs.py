"""Task-output folder resolution + safe file serving (Kanban #1305).

This service backs two GET routes (see `routers/task_outputs.py`):

  * `GET /api/tasks/{id}/outputs`          → `list_task_outputs`
  * `GET /api/tasks/{id}/outputs/{name}`   → `resolve_output_file`

Both serve filesystem content over HTTP, so path-resolution discipline is
load-bearing. The convention (from the #1305 spec) for *where* a task's outputs
live:

  1. `working_path` set AND team == 'data-analytics'
        → `<working_path>/analysis/outputs/<task_id>/`
  2. `working_path` set (any other team)
        → `<working_path>/outputs/<task_id>/`
  3. `working_path` null (agent-teams itself + legacy projects)
        → scan the project's role-state folders
          `<repo_root>/context/projects/<name>/<role>/` for:
            (a) DIRECT files matching the glob `task-<task_id>-*`, AND
            (b) a subdirectory named `<task_id>/` — its DIRECT files.
          Matches are aggregated across role folders.

Resolution rules (all three branches):
  * Only DIRECT files are listed (no recursion below the stated subdir level).
  * Dot-files are skipped.
  * Files larger than `MAX_FILE_BYTES` (50 MB) are skipped from the listing.
  * The listing is capped at `MAX_OUTPUT_FILES` (50 entries). If the on-disk
    directory exceeds the cap a warning is logged (project/task id included) and
    only the first 50 sorted entries are returned.
  * The `working_path` value is guarded exactly like
    `notification_router._resolve_fallback_base` — Windows-absolute paths on a
    Linux container (`C:\\...`) are NOT absolute and would resolve CWD-relative,
    so a non-absolute / non-existent `working_path` falls back to the role-folder
    scan (branch 3) rather than trusting a bogus path. See
    `context/standards/fastapi/filesystem-path-resolution.md`.

Security (this serves files over HTTP):
  * The `filename` path param is the ONLY client-controlled path component.
    `is_safe_filename` rejects `/`, `\\`, `..`, double-quotes (Content-
    Disposition quoted-string break), `;` and `'` (Content-Disposition param
    injection), and any control character including CR/LF/NUL (HTTP header
    injection) BEFORE any filesystem touch — the router calls it first and
    404s on a bad name.
  * `_scan_dir_direct_files` also skips on-disk entries whose names fail
    `is_safe_filename` (defense-in-depth: agent-written names are not trusted).
  * `resolve_output_file` NEVER joins client input onto a root directly. It
    re-runs the listing and matches by basename, then `Path.resolve()`-es and
    asserts containment within the resolved root (`is_relative_to`). Defense in
    depth: even if a future caller bypassed `is_safe_filename`, a symlink that
    escapes the root resolves to an out-of-root realpath and is rejected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from src.models.project import Project

logger = logging.getLogger(__name__)

# Files larger than this are skipped from the listing (and not served via the
# listing-backed lookup). 50 MB per the #1305 spec.
MAX_FILE_BYTES = 50 * 1024 * 1024

# Listing cap: at most this many files per task are returned. If exceeded, a
# warning is logged (with project/task id) and only the first 50 sorted entries
# are returned. (#1305 security review — prevents unbounded listing response).
MAX_OUTPUT_FILES = 50

# Extension → kind mapping (lower-cased extension WITHOUT the dot). Anything not
# in this map falls through to "text" (the #1305 contract default).
_KIND_BY_EXT: dict[str, str] = {
    "png": "chart",
    "svg": "chart",
    "html": "chart",
    "md": "doc",
    "csv": "export",
    "json": "export",
    "txt": "text",
    "log": "text",
}

_DATA_ANALYTICS_TEAM = "data-analytics"


def kind_for_filename(filename: str) -> str:
    """Map a filename to its output `kind` by extension (contract §1)."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return _KIND_BY_EXT.get(ext, "text")


def is_safe_filename(filename: str) -> bool:
    """Reject path-traversal / separator / null-byte / header-injection filenames.

    The `filename` path param is the only client-controlled path component. A
    safe filename is a single path segment: no directory separators (`/` or
    `\\`), no `..` traversal token, no null byte, no double-quote (breaks the
    Content-Disposition quoted-string), no CR/LF/control chars (HTTP header
    injection), no semicolon or single-quote (Content-Disposition param
    injection — e.g. `report.csv; filename=pwned.exe`).
    Empty / dot names are also rejected. Called by the router BEFORE any
    filesystem access; a False return maps to 404 (we never echo the rejected
    path back).
    """
    if not filename or filename in (".", ".."):
        return False
    if "/" in filename or "\\" in filename:
        return False
    if ".." in filename:
        return False
    # Reject double-quote (breaks the Content-Disposition quoted-string),
    # semicolon and single-quote (Content-Disposition param injection), and
    # any control character (covers NUL, CR, LF, form-feed, vertical-tab, …).
    if '"' in filename or ";" in filename or "'" in filename:
        return False
    if any(ord(c) < 0x20 for c in filename):
        return False
    # DEL (0x7F) is excluded from RFC 7230 qdtext; reject it explicitly.
    if any(ord(c) == 0x7F for c in filename):
        return False
    return True


def _usable_working_path(project: Project) -> Path | None:
    """Return the project's working_path as a usable absolute dir, else None.

    Mirrors `notification_router._resolve_fallback_base`'s guard: a Windows
    path on a Linux container is NOT absolute and a stale value may not exist.
    Either failure → None (caller falls back to the role-folder scan).
    """
    if not project.working_path:
        return None
    candidate = Path(project.working_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    logger.warning(
        "task_outputs: project.working_path %r is not a usable absolute path on "
        "this platform (is_absolute=%s, exists=%s); falling back to role-folder "
        "scan under repo_root",
        project.working_path,
        candidate.is_absolute(),
        candidate.exists(),
    )
    return None


def _scan_dir_direct_files(
    directory: Path,
    root: Path,
    name_predicate: "Callable[[str], bool] | None" = None,
) -> list[Path]:
    """Return DIRECT non-dot files in `directory` that are <= MAX_FILE_BYTES.

    `root` is the containment boundary the file must resolve under (defense in
    depth against a symlink that escapes the listed directory). Non-files,
    dot-files, oversized files, and symlinks that escape `root` are skipped.

    `name_predicate` — when provided, is evaluated on the dirent name BEFORE any
    `is_file()` / `resolve()` / `stat()` syscall.  Entries that fail it are
    skipped at zero cost, making it safe to call on large directories where only
    a small name-prefix subset is relevant (e.g. branch-3 role-folder scan).
    """
    out: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return out
    for entry in entries:
        if entry.name.startswith("."):
            continue
        # Skip on-disk files whose names contain injection characters (double-
        # quote, CR, LF) — is_safe_filename covers all rejection criteria; an
        # unsafe name would 404 on serve and pollute the listing / FE markup.
        if not is_safe_filename(entry.name):
            continue
        # Apply the name filter BEFORE any stat/is_file/resolve call so that
        # non-matching entries cost zero extra RPCs over the bind mount.
        if name_predicate is not None and not name_predicate(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
            # Containment: the realpath of the entry must stay under `root`.
            resolved = entry.resolve()
            if not _is_within(resolved, root):
                continue
            if resolved.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(entry)
    return out


def _is_within(child: Path, parent: Path) -> bool:
    """True if resolved `child` is `parent` or below it (containment check)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _collect_entries(
    project: Project, task_id: int, repo_root: Path
) -> dict[str, Path]:
    """Resolve the task's output entries as `{filename: absolute_path}`.

    First-write-wins on duplicate basenames across role folders (sorted-stable:
    role folders are visited in sorted name order, the file glob before the
    `<task_id>/` subdir within each). The returned paths are the ON-DISK entry
    paths (not yet `.resolve()`-ed) — `_scan_dir_direct_files` has already
    confirmed each resolves within its own root.
    """
    entries: dict[str, Path] = {}

    working = _usable_working_path(project)
    if working is not None:
        if project.team == _DATA_ANALYTICS_TEAM:
            outputs_dir = working / "analysis" / "outputs" / str(task_id)
        else:
            outputs_dir = working / "outputs" / str(task_id)
        for f in _scan_dir_direct_files(outputs_dir, outputs_dir):
            entries.setdefault(f.name, f)
        return entries

    # working_path null → scan role-state folders under the repo-root project dir.
    project_dir = repo_root / "context" / "projects" / project.name
    try:
        role_dirs = sorted(
            (d for d in project_dir.iterdir() if d.is_dir()),
            key=lambda p: p.name,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return entries

    task_prefix = f"task-{task_id}-"
    for role_dir in role_dirs:
        # (a) DIRECT files matching `task-<task_id>-*` in the role folder.
        # name_predicate filters by name BEFORE any is_file/resolve/stat call so
        # the ~1 000+ non-matching files in large role dirs (e.g. notifications/)
        # cost zero extra stat RPCs over the Docker 9P bind mount.
        for f in _scan_dir_direct_files(
            role_dir, role_dir, name_predicate=lambda n: n.startswith(task_prefix)
        ):
            entries.setdefault(f.name, f)
        # (b) a `<task_id>/` subdir — its DIRECT files (all relevant, no filter).
        sub = role_dir / str(task_id)
        if sub.is_dir():
            for f in _scan_dir_direct_files(sub, sub):
                entries.setdefault(f.name, f)

    return entries


def list_task_outputs(
    project: Project, task_id: int, repo_root: Path
) -> list[dict[str, object]]:
    """List a task's output files (contract §1).

    Returns a flat list of `{filename, mime, size, kind}` sorted by filename,
    capped at `MAX_OUTPUT_FILES` (50) entries. If the directory exceeds the cap,
    a warning is logged with the project name and task id, and only the first 50
    sorted entries are returned.
    An empty / missing output folder is NOT an error — returns `[]`.
    """
    import mimetypes

    entries = _collect_entries(project, task_id, repo_root)
    sorted_names = sorted(entries)
    if len(sorted_names) > MAX_OUTPUT_FILES:
        logger.warning(
            "task_outputs: project=%r task_id=%d has %d entries exceeding "
            "MAX_OUTPUT_FILES=%d; truncating to %d",
            project.name,
            task_id,
            len(sorted_names),
            MAX_OUTPUT_FILES,
            MAX_OUTPUT_FILES,
        )
        sorted_names = sorted_names[:MAX_OUTPUT_FILES]
    result: list[dict[str, object]] = []
    for filename in sorted_names:
        path = entries[filename]
        try:
            size = path.stat().st_size
        except OSError:
            continue
        mime, _ = mimetypes.guess_type(filename)
        result.append(
            {
                "filename": filename,
                "mime": mime or "application/octet-stream",
                "size": size,
                "kind": kind_for_filename(filename),
            }
        )
    return result


def resolve_output_file(
    project: Project, task_id: int, filename: str, repo_root: Path
) -> Path | None:
    """Resolve a single output file to its on-disk path, or None if not listed.

    The router has already validated `filename` via `is_safe_filename`. This
    re-runs the listing logic and matches by basename — client input is NEVER
    joined onto a root directly. Returns the absolute on-disk path (already
    confirmed within its root by the scan) or None when the file is not in the
    listing (→ 404).
    """
    entries = _collect_entries(project, task_id, repo_root)
    return entries.get(filename)
