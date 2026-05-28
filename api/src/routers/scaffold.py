"""HTTP route for serving the orchestration scaffold manifest + file bytes
(Kanban #795, MVP-D/3).

Background — the original zero-config bet (#789) was that POST /api/projects
would auto-scaffold the agent-teams orchestration harness (CLAUDE.md, `.claude/`,
context/standards/, context/teams/<team>/) into the project's `working_path`
directly from the API container. That works for paths the container can see
(via the `/repo` bind mount), but breaks for Windows host paths like
`C:\\Users\\…\\Writing` that the container's filesystem cannot reach.

The pivot (decisions.md #794): the server publishes the manifest + raw file
bytes over HTTP; a host-side CLI (MVP-E, #796) fetches and writes the files
itself. The container only needs to see its OWN repo (`/repo`), which it
already does — no extra mounts required.

The single endpoint reuses the same manifest resolver
(`_resolve_manifest`) and glob expander (`_expand_glob`) as the on-disk
scaffolder (#792), so adding a file to the harness is still a one-place edit
in `services/zero_config_scaffold.py`.

settings.json runs through the shared `substitute_settings_json` helper so the
CLI receives the same filtered bytes the on-disk path would write — no client-
side filtering required.

Missing source files are silently skipped (consistent with the idempotent-add
philosophy of #792 — the CLI proceeds with whatever the server can serve).
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.constants import TEAM_ROSTERS
from src.services.zero_config_scaffold import (
    _expand_glob,
    _resolve_manifest,
    substitute_settings_json,
)
from src.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scaffold", tags=["scaffold"])


class ScaffoldFile(BaseModel):
    rel_path: str
    content_b64: str


class ScaffoldManifestResponse(BaseModel):
    team: str
    project_name: str
    project_id: int
    files: list[ScaffoldFile]
    # Kanban #1620 — per-team dedicated role-folder roster (from TEAM_ROSTERS).
    # The host-side bin/agent-teams-init.ps1 (P3) creates
    # `context/projects/<name>/<role>/` folders from this list instead of
    # carrying its own $TeamRosters hashtable.
    role_folders: list[str]


_SETTINGS_REL = ".claude/settings.json"


def _read_and_encode(
    repo_root: Path, rel_path: str, project_name: str, project_id: int
) -> ScaffoldFile | None:
    """Read one source file → optionally substitute → base64-encode.

    Returns None when the source is missing (silently skipped — consistent
    with the idempotent-add philosophy: the CLI proceeds with what the server
    has). Other I/O errors are logged + skipped too, since one missing file
    must not break the whole manifest fetch.
    """
    src = repo_root / rel_path
    try:
        if not src.is_file():
            return None
        content = src.read_bytes()
    except OSError as e:
        logger.warning(
            "scaffold endpoint: skipping %s — read failed: %s", rel_path, e
        )
        return None

    if rel_path == _SETTINGS_REL:
        content = substitute_settings_json(
            content, project_name=project_name, project_id=project_id
        )

    return ScaffoldFile(
        rel_path=rel_path,
        content_b64=base64.b64encode(content).decode("ascii"),
    )


@router.get("/{team}/files", response_model=ScaffoldManifestResponse)
def get_scaffold_manifest(
    team: str,
    project_name: str = Query(
        ...,
        min_length=1,
        description="Target project name. Reserved for future per-project token "
        "substitution in settings.json — currently echoed back in the response.",
    ),
    project_id: int = Query(
        ...,
        ge=1,
        description="Target project id. Reserved for future per-project token "
        "substitution — currently echoed back in the response.",
    ),
) -> ScaffoldManifestResponse:
    """Return the agent-teams orchestration harness manifest as base64-encoded
    file bytes for the given team, plus the team's dedicated role-folder roster.

    Unknown team → 422 (Kanban #1620). The manifest is convention-derived from
    `TEAM_ROSTERS`; a team with no roster entry has no harness to serve, so we
    reject loud rather than fall back to a wrong-team manifest (the pre-#1620
    behavior was a silent dev fallback).
    """
    if team not in TEAM_ROSTERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown team {team!r}; valid: {sorted(TEAM_ROSTERS)}",
        )
    repo_root = Path(get_settings().repo_root)
    files, globs = _resolve_manifest(team, repo_root)

    out: list[ScaffoldFile] = []

    # Bare files first — order matches the manifest tuple, which is stable.
    for rel in files:
        sf = _read_and_encode(repo_root, rel, project_name, project_id)
        if sf is not None:
            out.append(sf)

    # Globs second. _expand_glob already returns sorted POSIX-style rel paths
    # under each pattern's base directory; duplicates across globs are rare but
    # harmless (the CLI's idempotent-add handles repeats).
    for pattern in globs:
        for rel in _expand_glob(repo_root, pattern):
            sf = _read_and_encode(repo_root, rel, project_name, project_id)
            if sf is not None:
                out.append(sf)

    return ScaffoldManifestResponse(
        team=team,
        project_name=project_name,
        project_id=project_id,
        files=out,
        role_folders=list(TEAM_ROSTERS[team]),
    )
