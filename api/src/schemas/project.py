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

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.constants import ProjectTeam

TeamCode = Literal["dev", "novel"]

# Kanban #777: per-project agent-model overrides. Values are constrained to the
# three Claude tiers we route across via AgentModelLiteral (Pydantic enforces at
# the request boundary). Keys are role names, allowlisted by `_AGENT_OVERRIDE_KEY`
# below — same shape as project.name. Forward-compat with #774/#775/#779/#780
# role names which all fit.
AgentModelLiteral = Literal["haiku", "sonnet", "opus"]

# Kanban #777 WARN-4: agent_overrides keys are role names — restrict to the
# same shape as project.name (alphanumeric + underscore + hyphen, 1-64 chars).
# Prevents row bloat / audit-log noise / hypothetical FE prototype-pollution
# vectors. Forward-compat with `#774/#775/#779/#780` role names which all fit.
_AGENT_OVERRIDE_KEY = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Kanban #778 BLOCKER-1: SourceEntry.url scheme allowlist — closes the
# javascript:// XSS bypass class (canonical AngularJS-sanitizer-bypass payload
# `javascript://%0aalert(1)//`). Substring `"://" in s` was the prior gate; it
# admits every code-execution scheme. The allowlist accepts only the schemes
# this app actually navigates to: http/https (external links), ref (internal
# refs consumed by dev-researcher), file (local docs on the user's machine).
# Case-insensitive match; the stored value is NOT lowercased (we don't mutate
# user input beyond .strip()).
_ALLOWED_URL_SCHEMES = ("http", "https", "ref", "file")
_SCHEME_RE = re.compile(rf"^({'|'.join(_ALLOWED_URL_SCHEMES)})://", re.IGNORECASE)


class SourceEntry(BaseModel):
    """Kanban #778 — element of `projects.sources` JSONB list.

    Shape validated at the API boundary; the DB has CHECK `jsonb_array_length <= 20`
    as defense-in-depth, no DB CHECK on element shape (same precedent as
    `tasks.acceptance_criteria` / `agent_overrides`).

    `extra="forbid"` keeps the wire contract tight — unknown keys (e.g. a typo'd
    `lable` for `label`) fail 422 instead of silently persisting. Same posture as
    `ProjectGrantConsent` / `AcceptanceCriterion`.

    `url` allows: a scheme-allowlisted URL (`http`/`https`/`ref`/`file`, case-
    insensitive; see `_ALLOWED_URL_SCHEMES` below) OR an absolute path (Unix
    `/...` or Windows `X:\\...` / `X:/...`). Pure-blank / control-char-only
    strings are rejected via `min_length=1` + the strip-then-check below.

    The scheme allowlist is the XSS-bypass gate (Kanban #778 BLOCKER-1, 2026-05-13):
    a permissive `"://" in s` substring check admits `javascript://%0aalert(1)//`
    (canonical AngularJS-sanitizer-bypass payload) — the FE then renders such
    an entry as a click-navigable `<a href={url}>` and the browser executes JS
    in-origin. The allowlist closes the class — `javascript:`, `data:`, `vbscript:`,
    `gopher:`, and any other non-allowlisted scheme are rejected at the boundary.
    Mirror the allowlist on the FE renderer too (see `web/components/SourcesBadge.tsx`).

    See proposed standard `context/standards/web/url-validation.md`.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1, max_length=2000)
    label: str | None = Field(None, min_length=1, max_length=200)
    kind: Literal["doc", "spec", "repo", "dashboard", "other"] | None = None

    @field_validator("url")
    @classmethod
    def _url_shape(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("url must not be blank")
        ok = (
            bool(_SCHEME_RE.match(s))  # allowlisted scheme://
            or s.startswith("/")  # Unix absolute
            or (len(s) >= 3 and s[0].isalpha() and s[1:3] in (":\\", ":/"))  # Windows X:\ or X:/
        )
        if not ok:
            raise ValueError(
                "url must be http/https/ref/file scheme, or an absolute path"
            )
        return s


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

    # Kanban #777: project-root + repo override + agent-model routing overrides.
    # All optional — DB defaults working_path/repo to NULL and agent_overrides
    # to '{}'::jsonb. min_length=1 guards against accidental empty strings on
    # the two free-form text fields; agent_overrides values are constrained by
    # AgentModelLiteral.
    working_path: str | None = Field(default=None, min_length=1)
    working_repo: str | None = Field(default=None, min_length=1)
    agent_overrides: dict[str, AgentModelLiteral] | None = Field(default=None)

    # Kanban #778: per-project curated source list. None = use DB default `[]`.
    # `max_length=20` is the Pydantic boundary check; DB CHECK
    # `ck_projects_sources_length` is defense-in-depth — Pydantic 422 fires first.
    sources: list[SourceEntry] | None = Field(default=None, max_length=20)

    @field_validator("agent_overrides")
    @classmethod
    def _validate_agent_override_keys(cls, v):
        if v is None:
            return v
        for key in v:
            if not _AGENT_OVERRIDE_KEY.fullmatch(key):
                raise ValueError(
                    f"agent_overrides key {key!r} must match {_AGENT_OVERRIDE_KEY.pattern}"
                )
        return v


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

    # Kanban #777 — same three fields as ProjectCreate. Per existing project
    # convention (description, stack_*, etc.), explicit `null` in PATCH is
    # treated as "clear the field"; key-absent means "leave unchanged". No
    # _reject_explicit_null validator — parity with neighbors, no audit-trail
    # concern this slice. agent_overrides replace-semantics (not deep-merge):
    # the value sent is the new value, full-stop.
    working_path: str | None = Field(default=None, min_length=1)
    working_repo: str | None = Field(default=None, min_length=1)
    agent_overrides: dict[str, AgentModelLiteral] | None = Field(default=None)

    # Kanban #778: sources PATCH semantics mirror `agent_overrides`:
    # - key-absent → leave existing value unchanged (exclude_unset=True)
    # - explicit `null` → router normalizes to `[]` BEFORE the UPDATE so the
    #   response (and subsequent GET) returns `[]`, never `None` — keeps the
    #   "always a list at the response boundary" wire contract intact across
    #   PATCH. The DB column IS nullable (unlike agent_overrides) but the app
    #   layer treats NULL identically to `[]`, so normalizing is purely about
    #   the response shape.
    # - explicit array (incl. `[]`) → REPLACES the prior list (no merge).
    sources: list[SourceEntry] | None = Field(default=None, max_length=20)

    @field_validator("agent_overrides")
    @classmethod
    def _validate_agent_override_keys(cls, v):
        if v is None:
            return v
        for key in v:
            if not _AGENT_OVERRIDE_KEY.fullmatch(key):
                raise ValueError(
                    f"agent_overrides key {key!r} must match {_AGENT_OVERRIDE_KEY.pattern}"
                )
        return v


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

    # Kanban #777 — required-attribute reads. DB invariants:
    #   working_path / working_repo  → nullable TEXT  (None when unset)
    #   agent_overrides              → JSONB, always a dict at the response
    #                                  boundary — both INSERT (via DB
    #                                  server_default '{}'::jsonb) and PATCH-to-
    #                                  null (via router transform, WARN-1
    #                                  Option A) guarantee this. Reads stay
    #                                  tolerant on VALUE (dict[str, Any], not
    #                                  strict Literal) for legacy-backfill
    #                                  resilience.
    working_path: str | None
    working_repo: str | None
    agent_overrides: dict[str, Any]

    # Kanban #778: per-project curated source list. ALWAYS a list at the wire
    # boundary — DB column is nullable but `_coerce_sources_none_to_empty` below
    # coerces NULL → `[]` (parity with agent_overrides "always-a-dict"
    # precedent). Element TYPE is `dict[str, Any]` (always-list, value-tolerant)
    # rather than the stricter `SourceEntry` so legacy / hand-edited rows that
    # violate the element shape don't 500 a read endpoint. Writes still go
    # through the strict `SourceEntry` validator on POST/PATCH.
    sources: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources_none_to_empty(cls, v):
        # SQL NULL surfaces as Python None when read via `from_attributes`. The
        # router PATCH path normalizes explicit-null to `[]`, and INSERT inherits
        # server_default `'[]'::jsonb` when omitted — so a None here is the
        # edge case of a pre-existing row that pre-dates the migration (PG 16
        # metadata-only ADD COLUMN with DEFAULT means NEW reads see `[]` even
        # for old rows, so practically this is paranoia-tier). Costs nothing.
        if v is None:
            return []
        return v


class ProjectStatsRunModeBreakdown(BaseModel):
    """Per-project run_mode counts (Kanban #769).

    All three keys always present (zero when unobserved) so the FE can render
    the badge grid without coalescing. Mirrors TaskRunMode.ALL.
    """

    manual: int = 0
    auto_pickup: int = 0
    auto_headless: int = 0


class ProjectStatsEntry(BaseModel):
    """Single project's stats row in the batched stats response (Kanban #769).

    Powers the cross-project dashboard. One entry per `status=1` project, in
    `projects.created_at ASC` order (deterministic, matches GET /api/projects).

    `counts`: keys are string-form ints `"1".."5"` (mirrors `TaskStatus.ALL`).
    All five keys always present even when count is 0 — FE renders the lane
    grid without coalescing.

    `last_activity_at`: `MAX(tasks.updated_at)` across the project's active
    (`status=1`) tasks; `None` when the project has no active tasks.

    Soft-deleted tasks (`status=0`) excluded from BOTH `counts` /
    `run_mode_breakdown` AND `last_activity_at`. Soft-deleted projects
    (`projects.status=0`) excluded from the list.
    """

    id: int
    name: str
    team: str
    run_mode_breakdown: ProjectStatsRunModeBreakdown
    counts: dict[str, int]
    last_activity_at: datetime | None


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
