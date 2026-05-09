"""Pydantic schemas for the `projects` table.

`ProjectCreate` flattens the user-friendly nested DTO ({paths, stack, standards})
into the flat columns the ORM uses (paths_web, stack_api, etc.). The `standards`
mapping is stored under `config.standards` (JSONB) so we don't need a column
per team.

Soft-delete `status` (0/1) is intentionally NOT exposed in any public schema —
clients call `DELETE /api/projects/{id}` to soft-delete; the flag is implementation
detail. (Decision 2026-05-07.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.constants import ProjectTeam

TeamCode = Literal["dev", "novel"]


class _Paths(BaseModel):
    web: str
    api: str
    db: str


class _Stack(BaseModel):
    web: str | None = None
    api: str | None = None
    db: str | None = None


class _Standards(BaseModel):
    web: list[str] = Field(default_factory=list)
    api: list[str] = Field(default_factory=list)
    db: list[str] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    """Request body for POST /api/projects.

    Accepts the nested shape used by the Kanban UI's "Create Project" form.
    Server merges `standards` into `config['standards']` before insert.

    `team` is required — picks the subagent roster (dev=frontend/backend/devops/
    tester/reviewer; novel=writer/editor). Unknown values reject with 422.
    """

    name: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str | None = None
    paths: _Paths
    stack: _Stack = Field(default_factory=_Stack)
    config: dict[str, Any] = Field(default_factory=dict)
    standards: _Standards | None = None
    is_active: bool = False
    team: TeamCode


class ProjectUpdate(BaseModel):
    """Request body for PATCH /api/projects/{id} — all fields optional.

    `team` may be changed post-creation; the scaffold side-effect does NOT
    re-run on update (existing role folders are kept; the user manages folder
    drift manually). Soft-delete `status` is NOT accepted here — use
    DELETE /api/projects/{id} to soft-delete (silently ignored if sent).
    """

    # Text-lock the silent-ignore behavior so a future Pydantic default change
    # can't flip it. `status` and any other unknown key drop on the floor.
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str | None = None

    paths_web: str | None = None
    paths_api: str | None = None
    paths_db: str | None = None

    stack_web: str | None = None
    stack_api: str | None = None
    stack_db: str | None = None

    config: dict[str, Any] | None = None
    is_active: bool | None = None
    team: TeamCode | None = None


class ProjectRead(BaseModel):
    """Full project row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    paths_web: str
    paths_api: str
    paths_db: str
    stack_web: str | None
    stack_api: str | None
    stack_db: str | None
    config: dict[str, Any]
    is_active: bool
    team: str
    created_at: datetime
    updated_at: datetime
    # Step 2 (Kanban #481/#483) — per-project consent gate for Mode B
    # (auto_headless tasks). NULL = not consented. First grant via
    # POST /api/projects/{id}/grant-consent stamps it; idempotent re-grant.
    auto_run_consent_at: datetime | None


class ProjectGrantConsent(BaseModel):
    """Request body for POST /api/projects/{id}/grant-consent.

    Typed-acknowledgment endpoint — the user must type the project name
    verbatim. `extra="forbid"` is deliberate (NOT the default `extra="ignore"`):
    a deliberate-action UX should fail loud if the client smuggles extra fields.
    """

    model_config = ConfigDict(extra="forbid")

    confirm_name: str = Field(..., min_length=1, max_length=255)


# Sanity: the Literal stays in lockstep with src.constants.ProjectTeam.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
if set(TeamCode.__args__) != set(ProjectTeam.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"TeamCode Literal {TeamCode.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"ProjectTeam.ALL {ProjectTeam.ALL!r}"
    )
