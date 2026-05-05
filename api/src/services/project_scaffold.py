"""Filesystem scaffold for a newly-created project.

Called from POST /api/projects after the row is committed. Creates:

    <repo_root>/context/projects/<name>/
        shared/
            decisions.md       (copied from templates)
            api-contracts.md   (copied from templates)
            db-schema.md       (copied from templates)
        frontend/.gitkeep
        backend/.gitkeep
        devops/.gitkeep
        qa/.gitkeep
        reviewer/.gitkeep

Idempotent — if the folder or any file already exists it is left alone.
On failure logs and returns False (caller continues — the DB row is the
source of truth; missing folders can be repaired manually).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_ROLE_FOLDERS = ("frontend", "backend", "devops", "qa", "reviewer")
_SHARED_TEMPLATES = ("decisions.md", "api-contracts.md", "db-schema.md")


def _templates_dir() -> Path:
    """Resolve the bundled templates directory inside the api package."""
    # services/project_scaffold.py -> services/ -> src/ -> src/templates/project_shared/
    return Path(__file__).resolve().parent.parent / "templates" / "project_shared"


def scaffold_project_folder(repo_root: Path, project_name: str) -> bool:
    """Create the on-disk folder structure for a project. Idempotent.

    Returns True on success (or if everything already existed), False if
    something failed mid-way. Never raises.
    """
    try:
        base = Path(repo_root) / "context" / "projects" / project_name
        base.mkdir(parents=True, exist_ok=True)

        # shared/ + template files
        shared = base / "shared"
        shared.mkdir(exist_ok=True)
        templates = _templates_dir()
        for tpl_name in _SHARED_TEMPLATES:
            dest = shared / tpl_name
            if dest.exists():
                continue
            src = templates / tpl_name
            if not src.exists():
                logger.warning(
                    "scaffold: template %s missing at %s — skipping", tpl_name, src
                )
                continue
            shutil.copyfile(src, dest)

        # role folders + .gitkeep
        for role in _ROLE_FOLDERS:
            role_dir = base / role
            role_dir.mkdir(exist_ok=True)
            keep = role_dir / ".gitkeep"
            if not keep.exists():
                keep.touch()

        return True
    except Exception:  # pragma: no cover — defensive: row commit must not roll back
        logger.exception("scaffold_project_folder failed for project=%r", project_name)
        return False
