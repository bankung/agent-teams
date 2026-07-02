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
import re
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


# =============================================================================
# Cross-task aggregate listing (Kanban #2558) — GET /api/projects/{id}/outputs
# =============================================================================
#
# Reuses every building block above. The one new piece is TASK-ID DISCOVERY:
# the per-task functions above take a task_id as input and resolve its output
# dir; the aggregate walker instead has to find every task_id that HAS outputs
# on disk, per the same three-branch convention documented in the module
# docstring. Once a task_id is discovered, `_collect_entries` (already
# security-reviewed: containment, no-symlink-escape, is_safe_filename skip) is
# called for it unchanged — the walker only ever ADDS a discovery layer on top,
# it never re-implements the trust boundary.

# Task-id discovery is capped independently of MAX_OUTPUT_FILES — a project
# could have thousands of tasks; this bounds the number of directories probed
# before the per-task 50-file cap even applies. shortcut: flat cap, fine at
# current scale (a few hundred tasks with outputs); upgrade: paginate the
# discovery walk itself if a project ever approaches this ceiling.
MAX_DISCOVERED_TASK_IDS = 2000


# `str.isdigit()` accepts Unicode digit categories (fullwidth '１２３', superscript
# '²', Devanagari '१२३', ...) that `int()` either can't parse (raises ValueError
# -> unhandled 500 for the WHOLE aggregate request) or silently parses into an
# ASCII-equivalent integer (collides with a real task's numeric id). A task_id
# directory/file name must be plain ASCII digits — `str.isascii()` (which
# `_ASCII_DIGITS_RE` implicitly enforces via `[0-9]`) closes both failure modes.
_ASCII_DIGITS_RE = re.compile(r"^[0-9]+$")


def _discover_task_ids_with_outputs(project: Project, repo_root: Path) -> set[int]:
    """Find every task_id that has an outputs location on disk (existence only).

    Mirrors the three `_collect_entries` branches for WHERE outputs live, but
    only lists directory/file names — it does not open or stat file content.
    Capped at `MAX_DISCOVERED_TASK_IDS`; a project exceeding the cap logs a
    warning (mirrors the MAX_OUTPUT_FILES truncation-warning pattern) and only
    the numerically LARGEST ids up to the cap are scanned further — task ids
    are assigned by DB auto-increment, so the largest ids skew newest, keeping
    the kept pool consistent with the endpoint's own mtime-DESC/task_id-DESC
    sort contract (truncating to the smallest ids would silently drop the
    newest tasks' outputs before the sort ever sees them).
    """
    task_ids: set[int] = set()

    working = _usable_working_path(project)
    if working is not None:
        if project.team == _DATA_ANALYTICS_TEAM:
            outputs_root = working / "analysis" / "outputs"
        else:
            outputs_root = working / "outputs"
        try:
            entries = list(outputs_root.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            entries = []
        for entry in entries:
            if entry.is_dir() and _ASCII_DIGITS_RE.match(entry.name):
                task_ids.add(int(entry.name))
    else:
        # working_path null → role-state folders under the repo-root project dir.
        project_dir = repo_root / "context" / "projects" / project.name
        try:
            role_dirs = list(d for d in project_dir.iterdir() if d.is_dir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            role_dirs = []
        for role_dir in role_dirs:
            try:
                role_entries = list(role_dir.iterdir())
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue
            for entry in role_entries:
                # (a) a `<task_id>/` subdirectory.
                if entry.is_dir() and _ASCII_DIGITS_RE.match(entry.name):
                    task_ids.add(int(entry.name))
                    continue
                # (b) a direct `task-<task_id>-*` file.
                if entry.is_file() and entry.name.startswith("task-"):
                    rest = entry.name[len("task-") :]
                    digits = rest.split("-", 1)[0]
                    if _ASCII_DIGITS_RE.match(digits):
                        task_ids.add(int(digits))

    if len(task_ids) > MAX_DISCOVERED_TASK_IDS:
        logger.warning(
            "task_outputs: project=%r has %d discovered task ids with outputs "
            "exceeding MAX_DISCOVERED_TASK_IDS=%d; truncating",
            project.name,
            len(task_ids),
            MAX_DISCOVERED_TASK_IDS,
        )
        # Keep the LARGEST ids (descending sort) — see docstring above.
        task_ids = set(sorted(task_ids, reverse=True)[:MAX_DISCOVERED_TASK_IDS])

    return task_ids


def _role_for_entry(project: Project, repo_root: Path, path: Path) -> str | None:
    """Derive the owning role-dir name for a file, or None when not applicable.

    Only the null-working_path convention (branch 3) has a role subdivision —
    `<repo_root>/context/projects/<name>/<role>/...`. The working_path branches
    (1/2) store outputs flat under `<working>/outputs/<id>/` with no per-role
    folder, so `role` is structurally None there. Derived by reading `.parts`
    off the ALREADY-RESOLVED entry path returned by `_collect_entries` — this
    does not re-touch the filesystem or re-derive containment, it only reads
    path segments that `_scan_dir_direct_files` has already validated.
    """
    if _usable_working_path(project) is not None:
        return None
    project_dir = repo_root / "context" / "projects" / project.name
    try:
        rel_parts = path.relative_to(project_dir).parts
    except ValueError:
        return None
    return rel_parts[0] if rel_parts else None


def list_project_outputs(
    project: Project, repo_root: Path
) -> list[dict[str, object]]:
    """List every output file across every task in `project` (contract #2558).

    Flat, unsorted (caller sorts/paginates) list of
    `{filename, task_id, role, mtime (float, epoch seconds), size, mime, kind}`.
    `mtime` is left as a raw epoch float here — the router converts to
    ISO-8601 UTC and joins task titles (DB access does not belong in this
    filesystem-only service module, matching the existing split with
    `list_task_outputs`). `role` is the owning role-dir name (null-working_path
    convention only) or `None` (working_path conventions have no role
    subdivision — see `_role_for_entry`).

    Per-task file caps (`MAX_OUTPUT_FILES`) and per-file size caps
    (`MAX_FILE_BYTES`) apply exactly as in the single-task listing, because
    this reuses `_collect_entries`/`_scan_dir_direct_files` unchanged — same
    security posture (containment, symlink-escape rejection, unsafe-name skip).
    """
    import mimetypes

    result: list[dict[str, object]] = []
    for task_id in _discover_task_ids_with_outputs(project, repo_root):
        entries = _collect_entries(project, task_id, repo_root)
        sorted_names = sorted(entries)
        if len(sorted_names) > MAX_OUTPUT_FILES:
            logger.warning(
                "task_outputs: project=%r task_id=%d has %d entries exceeding "
                "MAX_OUTPUT_FILES=%d; truncating to %d (aggregate listing)",
                project.name,
                task_id,
                len(sorted_names),
                MAX_OUTPUT_FILES,
                MAX_OUTPUT_FILES,
            )
            sorted_names = sorted_names[:MAX_OUTPUT_FILES]
        for filename in sorted_names:
            path = entries[filename]
            try:
                st = path.stat()
            except OSError:
                continue
            mime, _ = mimetypes.guess_type(filename)
            result.append(
                {
                    "filename": filename,
                    "task_id": task_id,
                    "role": _role_for_entry(project, repo_root, path),
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "mime": mime or "application/octet-stream",
                    "kind": kind_for_filename(filename),
                }
            )
    return result
