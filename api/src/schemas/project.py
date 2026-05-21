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
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.constants import ProjectTeam, TaskRole
from src.models.projects_audit import PROJECT_AUDIT_ACTIONS
from src.schemas.notification import NotificationTarget

TeamCode = Literal["dev", "novel", "general", "content", "seo", "data-analytics", "sem"]

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


# Kanban #7 Section A (AC#1) — per-project enabled_roles validator (JSONB
# subkey, not a column). Semantic contract:
#   - key absent       → "all roles allowed" (no restriction; default behavior)
#   - value `[]`       → "no role enabled" (explicit empty roster)
#   - value list[int]  → allowlist of TaskRole codes (each in 1..20)
# Booleans are rejected — Python treats `bool` as a subclass of `int`, so a
# naked `isinstance(v, int)` check admits `True`/`False`. The explicit
# `type(item) is bool` guard rejects them before the range check.
def _validate_enabled_roles_in_config(config: dict[str, Any]) -> dict[str, Any]:
    if "enabled_roles" not in config:
        return config
    value = config["enabled_roles"]
    if not isinstance(value, list):
        raise ValueError(
            "config.enabled_roles must be a list of int role codes "
            f"(got {type(value).__name__})"
        )
    for idx, item in enumerate(value):
        if type(item) is bool or not isinstance(item, int):
            raise ValueError(
                f"config.enabled_roles[{idx}] must be an int role code "
                f"(got {type(item).__name__}: {item!r})"
            )
        if not (TaskRole.RANGE_MIN <= item <= TaskRole.RANGE_MAX):
            raise ValueError(
                f"config.enabled_roles[{idx}]={item} is out of range "
                f"({TaskRole.RANGE_MIN}..{TaskRole.RANGE_MAX})"
            )
    return config


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


# Kanban #979 — specialist-tool permission tiers. Mirrors
# `langgraph/tools/base.py::Tier` value strings. The two sources of truth are
# kept in lockstep by `test_permission_gate.py::test_tier_literal_matches_enum`
# (langgraph side) — a drift fires immediately on the next test run.
ToolTier = Literal["read", "write", "network", "destructive"]


class ToolsConfig(BaseModel):
    """Kanban #979 — per-project specialist-tool permission gate config.

    Stored in `projects.tools_config` JSONB. Read by
    `langgraph/tools/permission_gate.check_permission()` BEFORE invoking any
    registered tool. The locked default ships "permissive read, halt on
    everything else" plus `tools_enabled=false` as a master kill switch — see
    migration `0027_projects_tools_config` for the full rationale.

    Field semantics (locked design #949 — see
    `_scratch/standards-proposal-permission-tiers.md`):

    - `tools_enabled` — master kill switch. False → gate returns `reject` for
      EVERY tool regardless of tier (including reads). Only the user (FE
      config UI, gated by #943) can flip true.
    - `auto_allow_tiers` — tiers whose tool calls auto-execute without human
      review. The ship default ships `["read"]` only.
    - `halt_tiers` — tiers whose tool calls halt the agent for human review
      via the standard halt_reason mechanism. Default
      `["write", "network", "destructive"]`.
    - `http_hosts` — forward-compat host allowlist for the HTTP tool family
      (shipped by #978; consumed by #981 sandbox). The gate (this slice)
      does NOT check this — only `tools_enabled` + tier. Empty list = no
      hosts allowed once the HTTP tool consults it.

    Invariants enforced at the API boundary (422 on violation):
    - `auto_allow_tiers` and `halt_tiers` MUST be disjoint. The same tier
      cannot live in both lists — the gate's lookup order would still pick
      auto_allow first, but the config would be semantically incoherent.
      Tiers absent from BOTH lists fall through to `reject` at the gate
      (defensive default — over-block beats under-block on misconfiguration).
    - Unknown tier strings fail 422 via the `ToolTier` Literal.
    - `http_hosts` entries are free-form strings here (no scheme/wildcard
      validation in this slice — the HTTP tool's own validator handles host
      shape when #981 wires it).

    `extra="forbid"` keeps the wire contract tight: a typo'd `tool_enabled`
    fails 422 instead of silently persisting under a garbage key.
    """

    model_config = ConfigDict(extra="forbid")

    tools_enabled: bool = False
    auto_allow_tiers: list[ToolTier] = Field(default_factory=lambda: ["read"])
    halt_tiers: list[ToolTier] = Field(
        default_factory=lambda: ["write", "network", "destructive"]
    )
    http_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _tiers_must_be_disjoint(self) -> ToolsConfig:
        overlap = set(self.auto_allow_tiers) & set(self.halt_tiers)
        if overlap:
            raise ValueError(
                "auto_allow_tiers and halt_tiers must be disjoint; "
                f"overlap: {sorted(overlap)!r}"
            )
        return self


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

    # max_length=64 fires BEFORE the regex check in Pydantic v2 field
    # validation order, so a 65-char name produces "String should have at most
    # 64 characters" rather than the opaque "String should match pattern" that
    # the regex alone would emit. The regex still enforces the character-class
    # constraint; max_length makes the length constraint independently legible
    # in the 422 error body. (Kanban #1300 smoke artifact, parent #1293)
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
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

    # Kanban #951: per-project budget caps (USD). All three None = unlimited
    # (mirrors the DB NULL semantics). `ge=0` is the Pydantic 422 boundary;
    # DB CHECK `ck_projects_budget_caps_nonneg` is defense-in-depth.
    # decimal_places=2 mirrors the NUMERIC(10,2) column shape; Pydantic
    # quantizes incoming values to 2 places on validation.
    budget_daily_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    budget_monthly_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    budget_total_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)

    # Kanban #979: per-project specialist-tool permission gate config. None
    # (the default) → router OMITS the column from INSERT so the DB
    # server_default fires (locked Q2 Option B default — see migration 0027).
    # An explicit dict here REPLACES the default; the disjoint-tiers
    # validator on `ToolsConfig` fires before the row reaches the DB.
    tools_config: ToolsConfig | None = Field(default=None)

    # Kanban #1224 (2026-05-19): per-project default push-notification targets.
    # None = no default (router falls back to local-file write per AC4).
    # Element shape validated by NotificationTarget at the API boundary.
    # max_length=20 caps the array size at the boundary (defense-in-depth
    # against payload bloat — operator-configured surface, low cardinality
    # expected). See `src/services/notification_router.py` for resolution
    # priority (task override > project default > local-file fallback).
    notification_targets: list[NotificationTarget] | None = Field(
        default=None, max_length=20
    )

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

    # Kanban #7 Section A (AC#1) — validate config.enabled_roles JSONB subkey.
    @field_validator("config")
    @classmethod
    def _validate_config_enabled_roles(cls, v):
        if not v:
            return v
        return _validate_enabled_roles_in_config(v)


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

    # max_length=64 mirrors ProjectCreate.name — same length-first error
    # message discipline (Kanban #1300 smoke artifact, parent #1293). The regex
    # still covers the character-class constraint.
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
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

    # Kanban #951: per-project budget caps. PATCH semantics — key-absent
    # leaves the column unchanged (exclude_unset); explicit `null` CLEARS to
    # unlimited; explicit Decimal sets the cap. `ge=0` rejects negative caps
    # at 422; the DB CHECK is defense-in-depth.
    budget_daily_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    budget_monthly_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    budget_total_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)

    # Kanban #979: per-project specialist-tool permission gate config. PATCH
    # semantics — key-absent leaves the column unchanged (exclude_unset);
    # explicit dict REPLACES the prior value (no deep merge — same as
    # `agent_overrides`); explicit `null` would CLEAR the column to NULL,
    # which the gate treats as "kill switch on" (reject all). The router
    # path applies the same null-handling pattern as agent_overrides /
    # sources for forward-compat — see update_project() in routers/projects.py.
    tools_config: ToolsConfig | None = Field(default=None)

    # Kanban #989: per-project HITL timeout (hours). PATCH semantics —
    # key-absent leaves unchanged (exclude_unset); explicit `null` CLEARS to
    # NULL (= unlimited / indefinite pause, pre-#989 default); explicit int
    # sets the threshold. `ge=1` rejects zero / negative at 422; the DB
    # CHECK `ck_projects_hitl_timeout_positive` is defense-in-depth.
    hitl_timeout_hours: int | None = Field(default=None, ge=1)

    # Kanban #960 (2026-05-17): per-project Health monitor tuning. PATCH
    # semantics — key-absent leaves unchanged (exclude_unset); explicit dict
    # REPLACES the prior value (no deep merge); explicit `null` clears to
    # NULL (= use env defaults). Operator-facing surface for over-rides like
    # `{"enabled": false}` to silence a noisy project. Element shape is
    # value-tolerant here (free-form dict) since detector knobs evolve
    # together; the service layer validates required keys + types when it
    # merges with defaults.
    health_thresholds: dict[str, Any] | None = Field(default=None)

    # Kanban #957 (2026-05-17): per-project HITL approval policies. PATCH
    # semantics — key-absent leaves unchanged (exclude_unset); explicit dict
    # REPLACES the prior value (no deep merge — same as `agent_overrides` /
    # `tools_config`); explicit `null` CLEARS to NULL (= no policies = every
    # HITL prompt requires operator attention). Element shape is value-tolerant
    # here (free-form dict) so the API doesn't 422 on a forward-compat shape
    # the operator wants to stage before the evaluator learns it; the
    # `approval_evaluator` service treats malformed shapes as
    # REQUIRE_ATTENTION + logs a warning. Sample shape (validated by the
    # service layer, not by Pydantic):
    #
    #     {
    #       "rules": [
    #         {
    #           "name": "auto-approve small llm spend",
    #           "match": {"text_contains": "spend", "amount_usd_lt": 5.0},
    #           "action": "auto_approve",
    #           "default_answer": "accept"
    #         },
    #         {
    #           "name": "auto-deny git push to main",
    #           "match": {"text_contains_all": ["git push", "main"]},
    #           "action": "auto_deny"
    #         }
    #       ]
    #     }
    approval_policies: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Per-project HITL approval rules. CANONICAL SHAPE: "
            '{"rules": [...]} where each rule has "name", "match" '
            '(predicates), "action" in {auto_approve, auto_deny}, and '
            'optional "default_answer". First match wins; no match falls '
            "back to REQUIRE_ATTENTION. See `services/approval_evaluator.py`. "
            "IMPORTANT: bare-list form ([...]) is NOT accepted here — sending "
            "a JSON array returns 422. Use the dict-with-rules canonical shape. "
            "The credentials /use gate (credentials.py::_policy_grants_use) "
            "also only matches rules inside the 'rules' key."
        ),
    )

    # Kanban #953 (2026-05-17): per-project financial-separation columns.
    # All four PATCH-able. Semantics mirror description / halt_reason:
    # key-absent → leave unchanged; explicit null → CLEAR to NULL (legacy
    # / unset); explicit value → write. `fiscal_year_start` validated 1..12
    # at the boundary (DB CHECK is defense-in-depth); `currency_default` is
    # 3-letter ISO 4217 (uppercased server-side).
    tax_jurisdiction: str | None = Field(default=None, min_length=1, max_length=64)
    legal_entity: str | None = Field(default=None, min_length=1, max_length=200)
    fiscal_year_start: int | None = Field(default=None, ge=1, le=12)
    currency_default: str | None = Field(default=None, min_length=3, max_length=3)

    # Kanban #1224 (2026-05-19): PATCH-able per-project default targets.
    # Semantics — key-absent leaves unchanged (exclude_unset); explicit dict
    # REPLACES the prior value (no deep merge — same as agent_overrides /
    # tools_config); explicit `null` CLEARS to NULL (= no default; router
    # falls back to local-file). Element shape validated by NotificationTarget.
    notification_targets: list[NotificationTarget] | None = Field(
        default=None, max_length=20
    )

    # Kanban #1011 (2026-05-20): per-project HITL aging nudge threshold.
    # PATCH semantics — key-absent leaves unchanged (exclude_unset); explicit
    # `null` CLEARS to NULL (= disabled); explicit int sets the threshold.
    # `ge=0` rejects negative values at 422; DB CHECK
    # `ck_projects_hitl_nudge_threshold_nonneg` is defense-in-depth.
    # Value of 0 is accepted (app layer treats 0 identical to NULL = disabled).
    # Sibling of `hitl_timeout_hours` — same NULL-as-disabled convention.
    hitl_nudge_threshold_hours: int | None = Field(default=None, ge=0)

    @field_validator("currency_default")
    @classmethod
    def _normalize_currency_default(cls, v: str | None) -> str | None:
        if v is None:
            return v
        import re as _re
        s = v.strip().upper()
        if not _re.fullmatch(r"^[A-Z]{3}$", s):
            raise ValueError(
                f"currency_default must be a 3-letter ISO 4217 code (got {v!r})"
            )
        return s

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

    # Kanban #7 Section A (AC#1) — validate config.enabled_roles JSONB subkey
    # on PATCH (mirrors ProjectCreate). Key-absent → leave unchanged
    # (exclude_unset); explicit dict → validate enabled_roles if present.
    @field_validator("config")
    @classmethod
    def _validate_config_enabled_roles(cls, v):
        if v is None or not v:
            return v
        return _validate_enabled_roles_in_config(v)


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

    # Kanban #951: per-project budget caps surfaced on every project read.
    # All three nullable — NULL = unlimited (no enforcement). FE renders
    # progress bar only when the corresponding cap is non-null; the
    # ProjectStatsCostUsage `total_cost_usd` aggregate (#871) provides the
    # numerator.
    budget_daily_usd: Decimal | None = None
    budget_monthly_usd: Decimal | None = None
    budget_total_usd: Decimal | None = None

    # Kanban #979: specialist-tool permission gate config. NULL semantics on
    # the wire = "no config yet / kill switch on" — but in practice every
    # row carries the locked default (migration 0027 backfills existing
    # rows; the DB column-level server_default fills new INSERTs). The
    # type is `dict[str, Any] | None` rather than the strict `ToolsConfig`
    # so legacy / hand-edited rows that drift from the element shape don't
    # 500 a read endpoint (parity with `sources` "value-tolerant on read,
    # strict on write" precedent). Writes still go through the strict
    # `ToolsConfig` validator on POST/PATCH.
    tools_config: dict[str, Any] | None = None

    # Kanban #989: per-project HITL timeout (hours). NULL = no timeout
    # (indefinite pause — pre-#989 default behavior, preserved for every
    # existing project). When non-null, the on-demand gate inside
    # GET /api/tasks/next-autorun stamps `halt_reason='hitl_timeout'` on
    # any BLOCKED HITL task waiting longer than this threshold.
    hitl_timeout_hours: int | None = None

    # Kanban #960 (2026-05-17): per-project Health monitor tuning. NULL = use
    # env defaults. `enabled=false` short-circuits the sweep for this project.
    # Value-tolerant on read (dict[str, Any]) for legacy / hand-edited resilience.
    health_thresholds: dict[str, Any] | None = None

    # Kanban #957 (2026-05-17): per-project HITL approval policies. NULL =
    # no policies (every HITL prompt requires operator attention). Value-
    # tolerant on read (dict[str, Any]) for legacy / hand-edited resilience
    # — mirrors `tools_config` / `health_thresholds` precedent. Writes still
    # land via the PATCH path; the worker's evaluator validates shape on
    # consumption and falls back to REQUIRE_ATTENTION on malformed values.
    approval_policies: dict[str, Any] | None = None

    # Kanban #953 (2026-05-17): per-project financial-separation columns.
    # All four NULLABLE on the wire — legacy rows pre-migration carry NULL;
    # new INSERTs land DB DEFAULT for fiscal_year_start (1) + currency_default
    # ('USD'). Free-form for tax_jurisdiction + legal_entity (operator-facing).
    tax_jurisdiction: str | None = None
    legal_entity: str | None = None
    fiscal_year_start: int | None = None
    currency_default: str | None = None

    # Kanban #1209 (2026-05-19): AA1 hard kill switch — hot pause state.
    # `is_killed` always present (NOT NULL DEFAULT false on the column).
    # `killed_at` / `killed_reason` carry historical signal AFTER revive too —
    # revive only flips `is_killed=false` and intentionally preserves the
    # two history columns (D4). FE reads these to show "last killed YYYY-MM-DD"
    # even on revived projects.
    is_killed: bool = False
    killed_at: datetime | None = None
    killed_reason: str | None = None

    # Kanban #1211 (2026-05-19): AA3 soft-pause governance — soft pause state.
    # `is_paused` + `paused_at` + `paused_reason` mirror the kill triad above
    # (D4 history-preservation pattern). `audit_enabled` is the per-project
    # opt-out for governance audits — defaults true; operators set false to
    # suppress audit-template creation/firing for this project (column added
    # in Phase 1 to avoid a follow-up migration when AC#2 lands; consumed
    # only by AC#2 work for now).
    is_paused: bool = False
    paused_at: datetime | None = None
    paused_reason: str | None = None
    audit_enabled: bool = True

    # Kanban #1224 (2026-05-19) — push-notification routing targets. NULL =
    # no default configured (router falls back to local-file fallback per
    # AC4). Value-tolerant on read (list[dict[str, Any]]) for legacy /
    # hand-edited resilience — mirrors `sources` / `tools_config` precedent.
    # Writes still go through the strict `NotificationTarget` validator on
    # POST/PATCH.
    notification_targets: list[dict[str, Any]] | None = None

    # Kanban #1011 (2026-05-20) — per-project HITL aging nudge threshold.
    # NULL = nudges disabled. 0 = nudges disabled (same semantics as NULL).
    # Non-zero positive int = threshold in hours before a HITL task gets
    # nudged. Migration 0047 backfills existing rows to 24 (server_default).
    # Sibling of `hitl_timeout_hours`.
    hitl_nudge_threshold_hours: int | None = None

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


class ProjectStatsCostUsage(BaseModel):
    """Per-project cost/token aggregates rolled up from `session_runs` (Kanban #871).

    Sums every `session_runs` row whose `session.project_id` matches this project.
    No soft-delete filter — `sessions` / `session_runs` carry NO `status` column
    (per db-schema.md: "NO audit trigger on `sessions`, `session_runs`, or
    `session_compacts`"). Joined via `session_runs.session_id → sessions.id →
    sessions.project_id` (2 hops; do NOT route via `session_runs.task_id` which
    is nullable on `ON DELETE SET NULL`).

    All six keys ALWAYS emitted (zero-filled) even when the project has no
    session_runs — mirrors the `counts` / `run_mode_breakdown` "no-coalescing"
    contract so the FE renders the dashboard widget without `||0` defaults.

    `total_cost_usd` is serialized as a JSON string by Pydantic v2 default
    (e.g. `"1.2345"`), mirroring `SessionRunRead.total_cost_usd` exactly.
    """

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_context_chars: int = 0
    total_cost_usd: Decimal = Decimal("0")
    # Count of session_runs WHERE budget_warning = true (per-row flag, summed).
    budget_warning_count: int = 0
    # Total session_runs for the project — convenient "no usage yet" check on FE
    # without scanning every numeric field for zero.
    session_run_count: int = 0


class ProjectStatsEntry(BaseModel):
    """Single project's stats row in the batched stats response (Kanban #769).

    Powers the cross-project dashboard. One entry per `status=1` project, in
    `projects.created_at ASC` order (deterministic, matches GET /api/projects).

    `counts`: keys are string-form ints `"1".."6"` (mirrors `TaskStatus.ALL`).
    All six keys always present even when count is 0 — FE renders the lane
    grid without coalescing. `"6"` is the CANCELLED bucket (Kanban #854).

    `last_activity_at`: `MAX(tasks.updated_at)` across the project's active
    (`status=1`) tasks, EXCLUDING cancelled (process_status=6) rows;
    `None` when the project has no qualifying active tasks. Kanban #854
    Option A: cancelled work is dead-end and a cancellation flip's
    `updated_at` bump MUST NOT poke through as "last activity".

    `cost_usage` (Kanban #871): per-project cost/token aggregates from
    `session_runs`. Always emitted (zero-filled when the project has no
    session_runs).

    Soft-deleted tasks (`status=0`) excluded from BOTH `counts` /
    `run_mode_breakdown` AND `last_activity_at`. Cancelled tasks
    (`process_status=6`, Kanban #854) excluded ONLY from
    `last_activity_at` — they DO appear in `counts["6"]` and
    `run_mode_breakdown`. Soft-deleted projects (`projects.status=0`)
    excluded from the list.
    """

    id: int
    name: str
    team: str
    run_mode_breakdown: ProjectStatsRunModeBreakdown
    counts: dict[str, int]
    last_activity_at: datetime | None
    cost_usage: ProjectStatsCostUsage


class ProjectGrantConsent(BaseModel):
    """Request body for POST /api/projects/{id}/grant-consent.

    Typed-acknowledgment endpoint — the user must type the project name
    verbatim. `extra="forbid"` is deliberate (NOT the default `extra="ignore"`):
    a deliberate-action UX should fail loud if the client smuggles extra fields.
    """

    model_config = ConfigDict(extra="forbid")

    confirm_name: str = Field(..., min_length=1, max_length=255)


# ---------------------------------------------------------------------------
# Kanban #1209 (2026-05-19) — AA1 hard kill switch request / response schemas
# ---------------------------------------------------------------------------

ProjectAuditAction = Literal[
    "kill", "revive", "pause", "unpause", "pause_override"
]


class KillProjectRequest(BaseModel):
    """Request body for POST /api/projects/{id}/kill.

    `reason` is REQUIRED with a minimum length of 10 chars — kill is an
    operator-deliberate action and the audit row should capture WHY. The FE
    enforces the same minimum (D5: "reason text >=10 chars" + type-project-name
    confirmation). `extra="forbid"` mirrors `ProjectGrantConsent` — a
    deliberate-action endpoint should fail loud on smuggled keys.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description=(
            "Operator-supplied rationale for the kill. >=10 chars required; "
            "captured into projects_audit.reason for future project-auditor read."
        ),
    )


class ReviveProjectRequest(BaseModel):
    """Request body for POST /api/projects/{id}/revive.

    No body fields required — revive is a single-button action. The schema
    exists so the router signature carries a real Pydantic model (FastAPI
    auto-generates OpenAPI with an empty object schema) and so a future
    revive-time field (e.g. `recompute_recurrence: bool`) can land without
    breaking the wire contract.
    """

    model_config = ConfigDict(extra="forbid")


class _KillReviveBase(BaseModel):
    """Shared response shape for kill + revive endpoints (Kanban #1209).

    `action` discriminates which side fired; `drain_summary` carries the
    counts the service captured at action time. `audit_id` lets the FE deep-
    link to the audit row in any future audit-log view.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    project_id: int
    action: ProjectAuditAction
    is_killed: bool
    killed_at: datetime | None
    killed_reason: str | None
    drain_summary: dict[str, Any]
    audit_id: int


class KillProjectResponse(_KillReviveBase):
    """Response body for POST /api/projects/{id}/kill."""


class ReviveProjectResponse(_KillReviveBase):
    """Response body for POST /api/projects/{id}/revive."""


# ---------------------------------------------------------------------------
# Kanban #1211 (2026-05-19) — AA3 soft-pause request / response schemas
# ---------------------------------------------------------------------------


class PauseProjectRequest(BaseModel):
    """Request body for POST /api/projects/{id}/pause.

    Mirrors KillProjectRequest's reason-required pattern — soft-pause is also
    an operator-deliberate action and the audit row must capture WHY. Same
    `extra='forbid'` posture as the kill body.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description=(
            "Operator/system rationale for the pause. >=10 chars required; "
            "captured into projects_audit.reason for AA4/AA5 review."
        ),
    )


class UnpauseProjectRequest(BaseModel):
    """Request body for POST /api/projects/{id}/unpause.

    No body fields required. Schema exists so the router signature carries
    a real Pydantic model (FastAPI auto-generates OpenAPI with an empty
    object schema) and so a future unpause-time field can land without
    breaking the wire contract.
    """

    model_config = ConfigDict(extra="forbid")


class PauseUnpauseResponse(BaseModel):
    """Shared response shape for pause + unpause endpoints (Kanban #1211).

    Mirror of `_KillReviveBase` (deliberately a separate type — pause carries
    `is_paused` + `paused_*` rather than the kill triad; combining into one
    base would force null fields in both directions).
    """

    model_config = ConfigDict(extra="forbid")

    success: bool
    project_id: int
    action: ProjectAuditAction  # 'pause' or 'unpause'
    is_paused: bool
    paused_at: datetime | None
    paused_reason: str | None
    drain_summary: dict[str, Any]
    audit_id: int


# Vocabulary for resolve-flag actions (D4). The Pydantic Literal stays in
# lockstep with services/pause_switch.RESOLVE_FLAG_ACTIONS via the sanity
# check at the bottom of this module.
ResolveFlagAction = Literal[
    "continue", "adjust_continue", "keep_paused", "terminate"
]


class ResolveFlagRequest(BaseModel):
    """Request body for POST /api/tasks/{flag_id}/resolve-flag (Kanban #1211).

    `action` discriminates the four operator responses:
    - continue        → flag DONE + project unpaused (no further input needed).
    - adjust_continue → adjustments REQUIRED; only allowlisted keys are
                        applied (see services/pause_switch.ADJUST_CONTINUE_ALLOWED_KEYS).
    - keep_paused     → flag DONE; project stays paused for next audit cycle.
    - terminate       → delegates to AA1 kill_project with auto-formatted reason.

    `extra='forbid'` matches the kill/grant-consent deliberate-action posture.
    """

    model_config = ConfigDict(extra="forbid")

    action: ResolveFlagAction
    adjustments: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Required for action='adjust_continue'. Allowlisted keys: "
            "budget_daily_usd, budget_monthly_usd, budget_total_usd, "
            "health_thresholds, approval_policies, hitl_timeout_hours, "
            "audit_enabled. Other keys are silently dropped."
        ),
    )


class ResolveFlagResponse(BaseModel):
    """Response body for POST /api/tasks/{flag_id}/resolve-flag.

    Shape varies by branch — `is_paused` / `is_killed` / `kill_audit_id` /
    `adjustments_applied` / `stale` are all branch-conditional. Using
    `extra='allow'` to keep the wire contract forward-compatible without
    forcing every consumer to handle each combination's nulls.
    """

    model_config = ConfigDict(extra="allow")

    flag_id: int
    project_id: int
    action: ResolveFlagAction
    flag_completed_at: datetime | None = None
    # Branch-specific (Pydantic surfaces None when absent):
    is_paused: bool | None = None
    is_killed: bool | None = None
    audit_id: int | None = None
    kill_audit_id: int | None = None
    adjustments_applied: dict[str, Any] | None = None
    drain_summary: dict[str, Any] | None = None
    stale: bool | None = None


class ProjectsAuditEntry(BaseModel):
    """Single projects_audit row as exposed via any future GET list endpoint.

    Wire shape is value-tolerant on `drain_summary` (dict[str, Any]) —
    legacy / hand-edited rows should not 500 a read endpoint. Writes still
    land through the service layer with concrete dict payloads.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    actor: str
    action: ProjectAuditAction
    reason: str | None
    drain_summary: dict[str, Any]
    created_at: datetime


# Sanity: the Literal stays in lockstep with src.constants.ProjectTeam.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
if set(TeamCode.__args__) != set(ProjectTeam.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"TeamCode Literal {TeamCode.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"ProjectTeam.ALL {ProjectTeam.ALL!r}"
    )

# Sanity (Kanban #1209 + #1211): ProjectAuditAction Literal stays in lockstep
# with models.projects_audit.PROJECT_AUDIT_ACTIONS (which mirrors the DB CHECK
# in migration 0039 (kill/revive) + 0040 (pause/unpause/pause_override)).
if set(ProjectAuditAction.__args__) != set(PROJECT_AUDIT_ACTIONS):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"ProjectAuditAction Literal {ProjectAuditAction.__args__!r} "  # type: ignore[attr-defined]
        f"drifted from PROJECT_AUDIT_ACTIONS {PROJECT_AUDIT_ACTIONS!r}"
    )

# Sanity (Kanban #1211): ResolveFlagAction Literal stays in lockstep with
# services.pause_switch.RESOLVE_FLAG_ACTIONS. Imported here at module-bottom
# to avoid a circular import — pause_switch.py imports nothing from this
# module, so the late import is safe.
from src.services.pause_switch import RESOLVE_FLAG_ACTIONS  # noqa: E402

if set(ResolveFlagAction.__args__) != set(RESOLVE_FLAG_ACTIONS):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"ResolveFlagAction Literal {ResolveFlagAction.__args__!r} "  # type: ignore[attr-defined]
        f"drifted from RESOLVE_FLAG_ACTIONS {RESOLVE_FLAG_ACTIONS!r}"
    )
