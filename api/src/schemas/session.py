"""Pydantic schemas for sessions / session_runs / session_compacts (CTX-1).

Three Literal types (status / run-status / compact-trigger), each guarded by
an import-time lockstep check against an inline canonical tuple — mirrors
the TaskRunModeLiteral / TaskKindLiteral guards in `schemas/task.py` and the
TeamCode guard in `schemas/project.py`.

CTX-1 deliberately keeps Update schemas narrow:
- SessionUpdate: only token_budget_per_run / process_label / status.
  Setting `status='closed'` is terminal (router rejects re-opening).
- SessionRunUpdate: status / finished_at / token totals / budget_warning /
  total_cost_usd. CTX-1 accepts client-supplied `total_cost_usd` with NO
  validation; CTX-3 will replace this with a server-authoritative computation.

`session_root_path` and `card_log_path` are SERVER-COMPUTED (router-side after
INSERT) — they are not on the Create schemas; clients cannot set them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.constants import (
    SessionCompactTrigger,
    SessionRunStatus,
    SessionStatus,
)

# Wire-level Literals. Stay in lockstep with `src.constants.<class>.ALL` via
# the import-time guards at the bottom of this module — same pattern as
# TaskRunModeLiteral / TaskKindLiteral in `schemas/task.py`.
SessionStatusLiteral = Literal["active", "compacting", "closed"]
SessionRunStatusLiteral = Literal["running", "done", "error", "timeout"]
SessionCompactTriggerLiteral = Literal["size", "manual", "run_count"]


# =============================================================================
# Session
# =============================================================================


class SessionCreate(BaseModel):
    """POST /api/sessions request body.

    Server computes `session_root_path` post-INSERT (`_sessions/<id>/`); it is
    NOT accepted from the client. The four ceilings use DB defaults when
    omitted (compacted_history=13000, recent_activity=15000,
    card_detail=6000, output_budget=4000); explicit overrides are accepted.

    `extra='forbid'` (Kanban #721, 2026-05-11) — smuggled fields (e.g.
    `{"status":"weird"}` or server-managed `closed_at`) fail loud with 422.
    Mirrors the `ConsentGrant` deliberate-action-UX pattern.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    process_label: str | None = Field(default=None, max_length=64)
    token_budget_per_run: int | None = Field(default=None, ge=1)
    compacted_history_ceiling_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    recent_activity_ceiling_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    card_detail_ceiling_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    output_budget_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class SessionUpdate(BaseModel):
    """PATCH /api/sessions/{id} — narrow update surface for CTX-1.

    Setting `status='closed'` is terminal — router 400s any subsequent PATCH
    on a closed row. The four ceilings are mutable mid-session (operator may
    bump on a misbehaving long-context run).
    """

    model_config = ConfigDict(extra="ignore")

    process_label: str | None = Field(default=None, max_length=64)
    token_budget_per_run: int | None = Field(default=None, ge=1)
    status: SessionStatusLiteral | None = None
    compacted_history_ceiling_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    recent_activity_ceiling_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    card_detail_ceiling_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    output_budget_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class SessionRead(BaseModel):
    """Full session row + computed counts for runs/compacts."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    process_label: str | None
    status: SessionStatusLiteral
    token_budget_per_run: int | None
    compacted_history_ceiling_tokens: int
    recent_activity_ceiling_tokens: int
    card_detail_ceiling_tokens: int
    output_budget_tokens: int
    session_root_path: str
    started_at: datetime
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Computed by the router via separate count queries (NOT a column_property
    # — keeps the ORM model simple; counts are only needed on detail GET, not
    # on every list row). On list endpoints these default to 0; on detail
    # endpoint the router fills them in.
    runs_count: int = 0
    compacts_count: int = 0


# =============================================================================
# SessionRun
# =============================================================================


class SessionRunCreate(BaseModel):
    """POST /api/sessions/{id}/runs request body.

    `session_id` is taken from the URL — clients do NOT supply it here.
    `card_log_path` is SERVER-COMPUTED post-INSERT when `task_id` is given.
    """

    task_id: int | None = Field(default=None, ge=1)
    status: SessionRunStatusLiteral = "running"


class SessionRunUpdate(BaseModel):
    """PATCH /api/session_runs/{id} request body.

    CTX-3 (Kanban #718): `total_cost_usd` is server-authoritative. The field
    stays on the schema for forward-compat (clients sending it get no 422),
    but the router DROPS it from updates and recomputes from token totals +
    `(provider, model)` via `services.cost_tracker.compute_cost`.

    `provider` / `model` (also CTX-3) are accepted for cost lookup but NOT
    persisted to the row — we don't model per-run provider/model on
    `session_runs` in V1. They are pure inputs to `compute_cost`.
    """

    model_config = ConfigDict(extra="ignore")

    status: SessionRunStatusLiteral | None = None
    finished_at: datetime | None = None
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    total_context_chars: int | None = Field(default=None, ge=0)
    total_cost_usd: Decimal | None = None
    budget_warning: bool | None = None
    # CTX-3: cost lookup inputs. Kanban #2135: NOW PERSISTED to session_runs.
    # provider max=32 (e.g. 'google', 'anthropic'); model max=128 to fit local
    # model names like 'gemma4:e4b-it-qat'.
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    # G2 (#1689): cache token inputs for accurate prompt-cache cost.
    # Names match compute_cost's params exactly. Anthropic API fields:
    # usage.cache_read_input_tokens + usage.cache_creation_input_tokens.
    # Not persisted; forwarded to compute_cost alongside input/output_tokens.
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)


class SessionRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    task_id: int | None
    status: SessionRunStatusLiteral
    started_at: datetime
    finished_at: datetime | None
    # Kanban #2135: provider/model persisted for cost rollup.
    provider: str | None
    model: str | None
    total_input_tokens: int
    total_output_tokens: int
    # G2 (#1689): persisted cache token columns (Anthropic prompt-cache fields).
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    total_context_chars: int
    total_cost_usd: Decimal
    budget_warning: bool
    card_log_path: str | None
    created_at: datetime
    updated_at: datetime


# =============================================================================
# SessionCompact
# =============================================================================


class SessionCompactCreate(BaseModel):
    """INTERNAL — full audit-row insert payload used by the compact runner.

    Not bound to any HTTP route. The public POST endpoint accepts only
    `SessionCompactRequest` (trigger_kind); every other field is computed
    server-side by `services.compact_runner.run_compact`.
    """

    trigger_kind: SessionCompactTriggerLiteral
    archive_path: str = Field(min_length=1)
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    compact_model: str = Field(min_length=1, max_length=64)
    compact_cost_usd: Decimal = Decimal("0")


class SessionCompactRequest(BaseModel):
    """POST /api/sessions/{id}/compact request body (CTX-4, Kanban #719).

    `trigger_kind` defaults to 'manual' for direct human callers; size /
    run_count are passed by server-side automation. Every other audit
    field is computed by the runner — never accepted from the client.
    `extra='ignore'` is intentionally ABSENT here pending #721's project-wide
    locked decision; we keep Pydantic's default ('extra=ignore' at v2 default
    behavior is `ignore`, so silently drops unknown keys without raising).
    """

    trigger_kind: SessionCompactTriggerLiteral = "manual"


class SessionCompactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    trigger_kind: SessionCompactTriggerLiteral
    archive_path: str
    before_tokens: int
    after_tokens: int
    compact_model: str
    compact_cost_usd: Decimal
    compacted_at: datetime


# =============================================================================
# CTX-2 — Recent Activity append + card heartbeat (Kanban #717)
# =============================================================================


class SessionActivityCreate(BaseModel):
    """POST /api/sessions/{id}/activity request body.

    `summary` is the only required field; `task_id` / `role` / `kind` enrich
    the entry header. `task_id` (when given) must belong to the same project
    as the session — router 400s on mismatch (mirror of run cross-project).

    `extra='forbid'` (Kanban #721, 2026-05-11) — tightened from the CTX-2
    `extra='ignore'` for parity with `ConsentGrant`. Smuggled fields fail
    loud with 422.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int | None = Field(default=None, ge=1)
    summary: str = Field(min_length=1, max_length=4000)
    role: str | None = Field(default=None, max_length=64)
    kind: str | None = Field(default=None, max_length=64)


class SessionActivityRead(BaseModel):
    """Response shape for POST /api/sessions/{id}/activity.

    CTX-3 (Kanban #718) added 3 ADDITIVE advisory fields — caller
    (Lead / master agent) reads them to decide whether to trigger a
    CTX-4 compact. Defaults (None) preserve the #717 contract for
    callers that don't care about token budgets.
    """

    appended_block: str
    section_preview: str
    section_chars: int
    # CTX-3: token-budget advisories.
    compact_recommended: bool | None = None
    current_recent_tokens: int | None = None
    recent_ceiling_tokens: int | None = None


class SessionPromptRead(BaseModel):
    """Response shape for GET /api/sessions/{id}/prompt."""

    markdown: str
    char_count: int


class SessionRunHeartbeat(BaseModel):
    """POST /api/session_runs/{id}/heartbeat request body.

    `mode='append'` (default) writes a timestamped block to the card log;
    `mode='replace'` overwrites the file verbatim (end-of-run snapshot).

    `extra='forbid'` (Kanban #721, 2026-05-11) — tightened from the CTX-2
    `extra='ignore'` for parity with `ConsentGrant`. Smuggled fields fail
    loud with 422.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20000)
    mode: Literal["append", "replace"] = "append"


class SessionRunHeartbeatRead(BaseModel):
    """Response shape for POST /api/session_runs/{id}/heartbeat.

    `total_bytes` is the total size of the card log file after this write
    (i.e. `card_path.stat().st_size`), NOT the number of bytes appended
    during this single heartbeat call.
    """

    card_log_path: str
    total_bytes: int


# =============================================================================
# Lockstep guards — drift between Literal args and the constants ALL tuples
# raises RuntimeError at import time. Mirrors the TaskRunModeLiteral /
# TaskKindLiteral guards in `schemas/task.py`.
# =============================================================================


if set(SessionStatusLiteral.__args__) != set(SessionStatus.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"SessionStatusLiteral {SessionStatusLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"SessionStatus.ALL {SessionStatus.ALL!r}"
    )

if set(SessionRunStatusLiteral.__args__) != set(SessionRunStatus.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"SessionRunStatusLiteral {SessionRunStatusLiteral.__args__!r} drifted "  # type: ignore[attr-defined]
        f"from SessionRunStatus.ALL {SessionRunStatus.ALL!r}"
    )

if set(SessionCompactTriggerLiteral.__args__) != set(SessionCompactTrigger.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"SessionCompactTriggerLiteral {SessionCompactTriggerLiteral.__args__!r} "  # type: ignore[attr-defined]
        f"drifted from SessionCompactTrigger.ALL {SessionCompactTrigger.ALL!r}"
    )
