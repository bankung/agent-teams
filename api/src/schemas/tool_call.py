"""Pydantic schemas for tool_calls (Kanban #980 + #981).

`ToolCallRead` — wire shape for `GET /api/tasks/{task_id}/tool-calls`.
`ToolCallCreate` — internal POST payload from the langgraph specialist
node (#981). Not advertised in the public docs; the writer service
(`services.tool_call_writer.record_tool_call`) is the canonical
producer and the POST endpoint is a thin shim that delegates to it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolCallRead(BaseModel):
    """Wire shape for a tool_calls row.

    Mirrors the ORM 1:1. `input_json` is the raw JSONB payload (the
    tool's validated input args at the moment of invocation). All fields
    are server-written; serializing `None` for the three nullable
    columns is the intended behavior.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    invoked_at: datetime
    tool_name: str
    tier: str
    input_json: dict[str, Any]
    success: bool
    error_code: str | None
    error_msg: str | None
    output_summary: str | None
    duration_ms: int
    permission_decision: str


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
