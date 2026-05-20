# FastAPI — filesystem path resolution against DB-sourced paths

**Scope:** any service that writes files using a path sourced from a DB column. Resolve via an absolute base anchored at the repo root, not via a bare `Path(db_value)` that depends on CWD.

## The bug

Bare `Path(db_value)` constructions are CWD-relative. Two specific failure modes:

1. **Windows-absolute path on a Linux container.** A `projects.working_path = "C:\\Users\\..."` value resolves to `Path("C:\\Users\\...")` which on Linux is a RELATIVE path — the container's CWD becomes the prefix. Worse, the `:` and `\` characters get substituted with Unicode PUA codepoints when written to a Linux filesystem, creating bizarre directory trees that the operator can't easily identify or `rm -rf` (PUA chars don't match `:` or `\` in shell globs).
2. **`Path("context/...")` when the DB value is null.** Resolves relative to the API container's CWD (`/repo/api/`) instead of the repo root (`/repo/`). Writes land at `/repo/api/context/...` — the wrong location, but no error fires.

Both cases manifested 3x in the 2026-05-20 session (Kanban #1285) before the fix landed.

## The pattern

Resolve via a helper that anchors at `settings.repo_root` (or equivalent absolute base) and guards the DB value before trusting it:

```python
# api/src/services/notification_router.py (Kanban #1285 canonical example)

def _resolve_fallback_base(project: Project, repo_root: str) -> Path:
    """Resolve the absolute fallback base directory for notification writes.

    Returns repo_root / 'context' / 'projects' / project.name when:
    - project.working_path is None, OR
    - project.working_path is not absolute on the current platform (e.g.
      Windows-absolute path on a Linux container), OR
    - resolved working_path doesn't exist as a directory.
    """
    if project.working_path:
        candidate = Path(project.working_path)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        logger.warning(
            "project.working_path %r is not a usable absolute path on this "
            "platform (is_absolute=%s, exists=%s); falling back to repo_root "
            "base %r",
            project.working_path,
            candidate.is_absolute(),
            candidate.exists(),
            repo_root,
        )
    return Path(repo_root) / "context" / "projects" / project.name
```

## Why the guards matter

- **`is_absolute()` check.** On Linux, `Path("C:\\...").is_absolute()` returns `False` because Linux doesn't recognize Windows drive letters. The check correctly rejects cross-platform pollution. On Windows the same path returns `True` — the check is correctly platform-aware.
- **`exists()` check.** Catches operator typos and stale `working_path` values (e.g. project moved on disk but DB row not updated). Falls back to repo_root rather than writing to a non-existent parent (which would otherwise create the deeply-nested PUA-char directory tree).
- **WARNING log with original value.** The operator needs to see the bad `working_path` to fix it. Silent fallback would hide the misconfiguration indefinitely.

## Anti-patterns

```python
# DON'T — bare Path() construction
base = Path(project.working_path or "context")
target = base / "notifications" / filename
# Both branches CWD-relative; both produce wrong-path writes.
```

```python
# DON'T — os.path.join with a relative second argument
base = os.path.join(project.working_path or ".", "notifications")
# "." is the container's CWD = /repo/api/, NOT the repo root.
```

## Generalizes to

- Local-file fallbacks for any service that may also write to an external target (notifications, exports, snapshots).
- Any service consuming a user-supplied path stored in the DB (project.working_path, project.working_repo, user-uploaded artifact paths).
- Cross-platform deployments where operators may run dev on Windows and prod on Linux.

## Cross-reference

- Canonical implementation: `api/src/services/notification_router.py::_resolve_fallback_base` (Kanban #1285, 2026-05-20).
- Settings field: `settings.repo_root` (default `/repo` inside the container; configurable via `REPO_ROOT` env var).
- Sibling concern: `scaffold_project_folder` (project-create side-effect) — uses the same repo_root anchoring per `api/src/services/project_scaffold.py`.
