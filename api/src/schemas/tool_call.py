"""Pydantic schemas for tool_calls (Kanban #980 + #981).

`ToolCallRead` — wire shape for `GET /api/tasks/{task_id}/tool-calls`.
`ToolCallCreate` — internal POST payload from the langgraph specialist
node (#981). Not advertised in the public docs; the writer service
(`services.tool_call_writer.record_tool_call`) is the canonical
producer and the POST endpoint is a thin shim that delegates to it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Lead-row taxonomy (#2320). gap/blocked kinds are the improvement signal the
# future auditor mines; the rest narrate the run. Gated by Pydantic Literal
# only — NO DB CHECK (#980 posture: the audit log must never 23514).
LeadActivityKind = Literal[
    "spawn",
    "tool_result",
    "ac_verified",
    "commit",
    "status_change",
    "blocked",
    "tool_gap",
    "skill_gap",
    "note",
]


class ToolCallRead(BaseModel):
    """Wire shape for a tool_calls / activity-rail row.

    Mirrors the ORM. Engine rows fill the engine-only columns
    (tier/input_json/duration_ms/permission_decision); lead rows leave them
    NULL and fill kind/summary instead (#2320). `source` is always present.
    Serializing `None` for the nullable columns is intended behavior — the FE
    timeline branches on `source`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    invoked_at: datetime
    source: str
    kind: str | None
    summary: str | None
    tool_name: str
    # Engine-only columns — Optional in the Read shape (#2320). Engine rows are
    # never NULL here, so existing engine rows serialize identically.
    tier: str | None
    input_json: dict[str, Any] | None
    success: bool
    error_code: str | None
    error_msg: str | None
    output_summary: str | None
    duration_ms: int | None
    permission_decision: str | None


class ToolCallResult(BaseModel):
    """Subset of `ToolResult` (langgraph-side) carried in the POST body.

    The writer service reads `success`, `error_code`, `error_msg`, `output`,
    `duration_ms`. `retry_safe` is a langgraph-side hint (used by the LLM
    loop for retry behavior); it is NOT carried over the wire — the
    audit row doesn't persist it. The producer (langgraph/audit.py) filters
    it out before POSTing.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="True if the tool's operation succeeded.")
    error_code: str | None = Field(None, description="Machine-readable failure code.")
    error_msg: str | None = Field(None, description="Human-readable error message (truncated to 1KB).")
    output: str | None = Field(None, description="Tool output (truncated to 256 chars on persist).")
    duration_ms: int = Field(0, ge=0, description="Wall-clock duration in milliseconds.")


class ToolCallCreate(BaseModel):
    """Internal POST body for `/api/tasks/{task_id}/tool-calls` (Kanban #981).

    Producer: `langgraph/audit.py::record_tool_invocation`, invoked from
    inside the specialist tool-use loop. NOT a public client contract —
    no FE consumes this endpoint. Validation is intentionally lax on
    `tier` + `permission_decision` (free-form strings) to mirror the DB
    schema's no-CHECK policy on those columns; the source of truth is
    the langgraph container.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., min_length=1, description="Registered tool name.")
    tier: str = Field(
        ...,
        min_length=1,
        description="'read' / 'write' / 'network' / 'destructive' (free-form on wire).",
    )
    input_args: dict[str, Any] = Field(
        default_factory=dict,
        description="The tool's validated input args at invocation time.",
    )
    result: ToolCallResult = Field(
        ..., description="Serialised ToolResult from the tool's invoke()."
    )
    permission_decision: str = Field(
        ...,
        min_length=1,
        description="'auto_allow' / 'halt' / 'reject' (free-form on wire).",
    )


class LeadActivityCreate(BaseModel):
    """POST body for a Lead report-back checkpoint (#2320).

    Producer: the Lead via the `tn-report` skill. Shares the
    `POST /api/tasks/{task_id}/tool-calls` URL with `ToolCallCreate`; the
    router dispatches by the `source` discriminator (body `source:'lead'` →
    this shape). One row per call = one activity-rail checkpoint.

    Engine-only columns are NOT accepted here — they stay NULL for lead rows.
    """

    model_config = ConfigDict(extra="forbid")

    # Required discriminator — selects this shape in the dual-contract router.
    source: Literal["lead"]
    kind: LeadActivityKind = Field(
        ..., description="Checkpoint taxonomy — see LeadActivityKind."
    )
    summary: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Human-readable checkpoint evidence (sanitized + capped 2000 on persist, #2136).",
    )
    success: bool = Field(
        True,
        description="False marks a failure/blocker checkpoint (e.g. kind='blocked').",
    )
    tool_name: str | None = Field(
        None,
        description="Optional free label, e.g. the spawned agent name on kind='spawn'.",
    )
