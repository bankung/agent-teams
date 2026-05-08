"""Filesystem scaffold for a newly-created project.

Called from POST /api/projects after the row is committed. Creates:

    <repo_root>/context/projects/<name>/
        shared/
            decisions.md       (copied from templates)
            api-contracts.md   (copied from templates)
            db-schema.md       (copied from templates)
        <role>/.gitkeep       (per-lead roster)

Per-lead roster:
    dev   -> dev-frontend, dev-backend, dev-devops, dev-tester, dev-reviewer
    novel -> novel-writer, novel-editor

Per-lead shared templates are NOT yet implemented — every project gets the dev
template trio regardless of lead. Follow-up: ship novel-specific shared templates
(outline.md, continuity.md, etc.). See current-state.md handoffs.

Idempotent — if the folder or any file already exists it is left alone.
On failure logs and returns False (caller continues — the DB row is the
source of truth; missing folders can be repaired manually).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.constants import ProjectLead

logger = logging.getLogger(__name__)

# Roster per lead — must stay in lockstep with .claude/leads/<lead>.md and the
# ProjectLead.ALL tuple in src/constants.py.
LEAD_ROSTERS: dict[str, tuple[str, ...]] = {
    ProjectLead.DEV: (
        "dev-frontend",
        "dev-backend",
        "dev-devops",
        "dev-tester",
        "dev-reviewer",
    ),
    ProjectLead.NOVEL: (
        "novel-writer",
        "novel-editor",
    ),
}

_SHARED_TEMPLATES = ("decisions.md", "api-contracts.md", "db-schema.md")


def _templates_dir() -> Path:
    """Resolve the bundled templates directory inside the api package."""
    # services/project_scaffold.py -> services/ -> src/ -> src/templates/project_shared/
    return Path(__file__).resolve().parent.parent / "templates" / "project_shared"


def _resolve_role_folders(lead: str) -> tuple[str, ...]:
    """Pick the role-folder roster for a given lead. Falls back to dev roster
    if the lead is not in LEAD_ROSTERS — should never happen because the DB
    CHECK rejects unknown leads, but defensive in case the map drifts.
    """
    return LEAD_ROSTERS.get(lead, LEAD_ROSTERS[ProjectLead.DEV])


def scaffold_project_folder(
    repo_root: Path, project_name: str, lead: str = ProjectLead.DEV
) -> bool:
    """Create the on-disk folder structure for a project. Idempotent.

    `lead` selects the role-folder roster (see LEAD_ROSTERS). Defaults to 'dev'
    for backward compat with any caller that hasn't been updated yet, but the
    POST /api/projects handler always passes the explicit lead from the request.

    Returns True on success (or if everything already existed), False if
    something failed mid-way. Never raises.
    """
    try:
        base = Path(repo_root) / "context" / "projects" / project_name
        base.mkdir(parents=True, exist_ok=True)

        # shared/ + template files (dev templates regardless of lead — see module docstring)
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

        # role folders + .gitkeep — roster depends on lead
        for role in _resolve_role_folders(lead):
            role_dir = base / role
            role_dir.mkdir(exist_ok=True)
            keep = role_dir / ".gitkeep"
            if not keep.exists():
                keep.touch()

        return True
    except Exception:  # pragma: no cover — defensive: row commit must not roll back
        logger.exception("scaffold_project_folder failed for project=%r", project_name)
        return False
