"""Teams router (Kanban #1620).

Mounted at `/api/teams`. Exposes a single read-only endpoint:
  GET /api/teams — list every team + its dedicated role-folder roster.

No `X-Project-Id` header required — the team registry is global, not per-project
(same scoping as GET /api/templates/actions and GET /api/scaffold/{team}/files).

Powered directly by `src.constants.ProjectTeam.ALL` + `TEAM_ROSTERS` — the single
source of truth. The FE NewProjectModal (P2) fetches this to populate the team
<select> and render the per-team roster help line; bin/agent-teams-init.ps1 (P3)
can also read it. Deliberately NO `blurb`/prose field (round-5 review lock) —
the response is the machine-readable registry, not playbook prose.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.constants import TEAM_ROSTERS, ProjectTeam

router = APIRouter(prefix="/teams", tags=["teams"])


class TeamEntry(BaseModel):
    """One team + its dedicated role-folder roster (no prose)."""

    team: str
    roster: list[str]


@router.get("", response_model=list[TeamEntry])
async def list_teams() -> list[TeamEntry]:
    """Kanban #1620 — list every team and its dedicated agent roster.

    Returns one entry per `ProjectTeam.ALL` value, in registry order, each with
    the team's `TEAM_ROSTERS` roster (the dedicated agents that own a per-project
    role-state folder). Global — no `X-Project-Id` header required.
    """
    return [
        TeamEntry(team=team, roster=list(TEAM_ROSTERS[team]))
        for team in ProjectTeam.ALL
    ]
