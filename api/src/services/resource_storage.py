"""Resource file-storage confinement + streaming (Kanban #1309).

Security-critical. Resolves the per-project storage root, sanitizes the uploaded
filename, CONFINES every write under that root (path-traversal made impossible
via a `Path.relative_to` guard — same idea as
`services/skill_stub_detector._write_stub`), streams the upload to disk in chunks
while enforcing the 520 MB cap DURING the stream, and on soft-delete MOVES the
stored object to a `.trash/` sibling (never hard-deletes).

Storage layout
--------------
  <storage_root>/data/raw/<resource_id>-<sanitized_filename>
  <storage_base>/.trash/<resource_id>-<sanitized_filename>     (on delete)

`<storage_root>` = `project.working_path` when set, else a documented fallback
under the repo: `<repo_root>/_data/projects/<project_id>/`. The `.trash` dir
hangs off the SAME base so a deleted file stays inside the project's storage
subtree.

NEVER trusts the client filename. Sanitization strips path separators, `..`,
NUL bytes, and leading dots, collapses to a safe basename, and falls back to a
generic name when nothing safe remains.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Hard upload cap (#1309): 520 MB. Enforced DURING streaming via a running byte
# count so we never buffer a giant file in memory or fully write it before
# rejecting.
MAX_UPLOAD_BYTES: int = 520 * 1024 * 1024

# Chunk size for streaming the UploadFile to disk.
CHUNK_SIZE: int = 1024 * 1024  # 1 MiB

# Sanitization: keep alnum, dot, dash, underscore, space; everything else -> "_".
_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._\- ]")
# Collapse runs of dots (defeats "..." style) and strip leading dots.
_MULTI_DOT_RE = re.compile(r"\.{2,}")
_FALLBACK_NAME = "upload.bin"
_MAX_BASENAME_LEN = 200


class UploadTooLargeError(Exception):
    """Raised when the streamed upload exceeds MAX_UPLOAD_BYTES (-> HTTP 413)."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"upload exceeds the {limit}-byte cap")


@dataclass
class StoredFile:
    """Result of a successful streamed store."""

    path: Path
    size_bytes: int
    sanitized_filename: str


def sanitize_filename(raw: str | None) -> str:
    """Reduce an arbitrary client filename to a safe BASENAME.

    Defenses: take only the basename (drops any directory component on both
    `/` and `\\`), drop NUL bytes, replace unsafe chars, collapse `..` runs,
    strip leading dots (no hidden/`..` names), cap length. Falls back to a
    generic name when nothing safe survives. NEVER returns a value containing a
    path separator, `..`, or a leading dot.
    """
    if not raw:
        return _FALLBACK_NAME

    # Drop NUL + normalize both separator styles to take the last component.
    cleaned = raw.replace("\x00", "")
    cleaned = cleaned.replace("\\", "/")
    base = cleaned.rsplit("/", 1)[-1]

    # Replace unsafe chars, collapse multi-dot runs, strip surrounding junk.
    base = _SAFE_CHARS_RE.sub("_", base)
    base = _MULTI_DOT_RE.sub(".", base)
    base = base.strip().strip(".").strip()

    if not base or base in (".", ".."):
        return _FALLBACK_NAME

    if len(base) > _MAX_BASENAME_LEN:
        # Preserve the extension when truncating.
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 16:
            keep = _MAX_BASENAME_LEN - len(ext) - 1
            base = stem[:keep] + "." + ext
        else:
            base = base[:_MAX_BASENAME_LEN]

    return base or _FALLBACK_NAME


def resolve_storage_base(working_path: str | None, project_id: int, repo_root: Path) -> Path:
    """Per-project storage BASE dir.

    `working_path` set  -> Path(working_path).
    `working_path` null -> documented fallback `<repo_root>/_data/projects/<id>/`.

    Returns the base (NOT yet the data/raw subdir). Does not create anything.
    """
    if working_path and working_path.strip():
        return Path(working_path)
    return Path(repo_root) / "_data" / "projects" / str(project_id)


def _raw_dir(storage_base: Path) -> Path:
    return storage_base / "data" / "raw"


def _trash_dir(storage_base: Path) -> Path:
    return storage_base / ".trash"


def _confine(target: Path, root: Path) -> Path:
    """Assert `target` resolves INSIDE `root`; return the resolved path.

    Path-traversal guard mirroring skill_stub_detector._write_stub: resolve both
    and require `target.relative_to(root)` to succeed. Raises ValueError on
    escape — the caller must treat this as a hard 400/500, never silently write.
    """
    resolved = target.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"resource_storage: path {resolved!r} escapes storage root "
            f"{root_resolved!r}; refusing to write"
        ) from exc
    return resolved


def build_target_path(
    storage_base: Path, resource_id: int, sanitized_filename: str
) -> Path:
    """Compute + confine the on-disk target `<base>/data/raw/<id>-<name>`.

    Confinement is checked against `storage_base` (the project subtree), so even
    a sanitized name that somehow re-introduced traversal would be rejected.
    """
    raw = _raw_dir(storage_base)
    target = raw / f"{resource_id}-{sanitized_filename}"
    return _confine(target, storage_base)


async def stream_to_disk(
    chunks: AsyncIterator[bytes],
    storage_base: Path,
    resource_id: int,
    sanitized_filename: str,
) -> StoredFile:
    """Stream an async chunk iterator to the confined target, enforcing the cap.

    Enforces MAX_UPLOAD_BYTES via a running byte count: on the chunk that would
    cross the cap we ABORT — close + delete the partial file + raise
    UploadTooLargeError (the router maps to 413 and does NOT create a row).
    Returns StoredFile on success.
    """
    target = build_target_path(storage_base, resource_id, sanitized_filename)
    target.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    try:
        with target.open("wb") as fh:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    # Stop immediately; clean up the partial file below.
                    raise UploadTooLargeError(MAX_UPLOAD_BYTES)
                fh.write(chunk)
    except UploadTooLargeError:
        _safe_unlink(target)
        raise
    except Exception:
        # Any IO failure mid-stream -> remove the partial so we never leave a
        # half-written orphan, then re-raise for the router to 500.
        _safe_unlink(target)
        raise

    return StoredFile(path=target, size_bytes=total, sanitized_filename=sanitized_filename)


_TRASH_RENAME_CAP = 1000  # max collision-rename attempts before giving up


def move_to_trash(storage_base: Path, stored_path: str | None) -> bool:
    """Soft-delete: MOVE the stored file into `<base>/.trash/`. Idempotent.

    Returns True when a move happened, False when there was nothing to move
    (already gone / never stored / link resource). Never raises on a missing
    source (idempotent re-delete). The destination is confined to the trash dir.

    Security (#1309 fix #6):
      (a) Collision-rename loop is capped at _TRASH_RENAME_CAP to prevent an
          unbounded loop on the event loop.
      (b) The SOURCE path is confined to storage_base before any FS access —
          rejects a tampered stored_path pointing outside the storage root.
    """
    if not stored_path:
        return False

    # (b) Confine the source path — reject tampered stored_path.
    src_unconfined = Path(stored_path)
    try:
        src = _confine(src_unconfined, storage_base)
    except ValueError:
        logger.warning(
            "resource_storage: stored_path %r escapes storage root %r; refusing move",
            stored_path, storage_base,
        )
        raise

    if not src.exists():
        return False

    trash = _trash_dir(storage_base)
    trash.mkdir(parents=True, exist_ok=True)
    dest = _confine(trash / src.name, storage_base)

    # Avoid clobbering a same-named earlier trashed file.
    # (a) Cap the loop so it can never spin forever on the event loop.
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        dest = None
        for n in range(1, _TRASH_RENAME_CAP + 1):
            candidate = _confine(trash / f"{stem}.{n}{suffix}", storage_base)
            if not candidate.exists():
                dest = candidate
                break
        if dest is None:
            raise OSError(
                f"resource_storage: could not find a free trash name for {src.name!r} "
                f"after {_TRASH_RENAME_CAP} attempts"
            )

    shutil.move(str(src), str(dest))
    logger.info("resource_storage: moved %s -> %s (soft-delete)", src, dest)
    return True


def _safe_unlink(path: Path) -> None:
    """Best-effort delete of a partial/orphan file; swallow errors."""
    try:
        if path.exists():
            os.unlink(path)
    except OSError as exc:  # pragma: no cover - cleanup best-effort
        logger.warning("resource_storage: failed to unlink partial %s: %s", path, exc)
