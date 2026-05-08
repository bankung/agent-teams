"""Filesystem scaffold for a newly-created project.

Called from POST /api/projects after the row is committed. Creates:

    <repo_root>/context/projects/<name>/
        shared/
            decisions.md       (copied from templates)
            api-contracts.md   (copied from templates)
            db-schema.md       (copied from templates)
        <role>/.gitkeep       (per-team roster)

Per-team roster:
    dev   -> dev-frontend, dev-backend, dev-devops, dev-tester, dev-reviewer
    novel -> novel-writer, novel-editor

Per-team shared templates are NOT yet implemented — every project gets the dev
template trio regardless of team. Follow-up: ship novel-specific shared templates
(outline.md, continuity.md, etc.). See current-state.md handoffs.

Idempotent — if the folder or any file already exists it is left alone.
On failure logs and returns False (caller continues — the DB row is the
source of truth; missing folders can be repaired manually).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.constants import ProjectTeam

logger = logging.getLogger(__name__)

# Roster per team — must stay in lockstep with .claude/teams/<team>.md and the
# ProjectTeam.ALL tuple in src/constants.py.
TEAM_ROSTERS: dict[str, tuple[str, ...]] = {
    ProjectTeam.DEV: (
        "dev-frontend",
        "dev-backend",
        "dev-devops",
        "dev-tester",
        "dev-reviewer",
    ),
    ProjectTeam.NOVEL: (
        "novel-writer",
        "novel-editor",
    ),
}

_SHARED_TEMPLATES = ("decisions.md", "api-contracts.md", "db-schema.md")


def _templates_dir() -> Path:
    """Resolve the bundled templates directory inside the api package."""
    # services/project_scaffold.py -> services/ -> src/ -> src/templates/project_shared/
    return Path(__file__).resolve().parent.parent / "templates" / "project_shared"


def _resolve_role_folders(team: str) -> tuple[str, ...]:
    """Pick the role-folder roster for a given team. Falls back to dev roster
    if the team is not in TEAM_ROSTERS — should never happen because the DB
    CHECK rejects unknown teams, but defensive in case the map drifts.
    """
    if team not in TEAM_ROSTERS:
        logger.warning(
            "scaffold: unknown team %r — falling back to dev roster", team
        )
    return TEAM_ROSTERS.get(team, TEAM_ROSTERS[ProjectTeam.DEV])


def scaffold_project_folder(
    repo_root: Path, project_name: str, team: str = ProjectTeam.DEV
) -> bool:
    """Create the on-disk folder structure for a project. Idempotent.

    `team` selects the role-folder roster (see TEAM_ROSTERS). Defaults to 'dev'
    for backward compat with any caller that hasn't been updated yet, but the
    POST /api/projects handler always passes the explicit team from the request.

    Returns True on success (or if everything already existed), False if
    something failed mid-way. Never raises.
    """
    try:
        # Defense-in-depth: schema enforces charset at the boundary, but anything
        # bypassing Pydantic (e.g., a future internal caller) must still get caught.
        # Reject path separators, NUL, and parent-dir tokens.
        forbidden = {"/", "\\", "..", "\x00"}
        if any(token in project_name for token in forbidden):
            logger.warning(
                "scaffold: rejected suspicious project_name=%r (path-traversal guard)",
                project_name,
            )
            return False

        base = Path(repo_root) / "context" / "projects" / project_name
        projects_root = (Path(repo_root) / "context" / "projects").resolve()
        if not base.resolve().is_relative_to(projects_root):
            logger.warning(
                "scaffold: rejected project_name=%r — resolves outside %s",
                project_name,
                projects_root,
            )
            return False
        base.mkdir(parents=True, exist_ok=True)

        # shared/ + template files (dev templates regardless of team — see module docstring)
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

        # role folders + .gitkeep — roster depends on team
        for role in _resolve_role_folders(team):
            role_dir = base / role
            role_dir.mkdir(exist_ok=True)
            keep = role_dir / ".gitkeep"
            if not keep.exists():
                keep.touch()

        return True
    except Exception:  # pragma: no cover — defensive: row commit must not roll back
        logger.exception("scaffold_project_folder failed for project=%r", project_name)
        return False
