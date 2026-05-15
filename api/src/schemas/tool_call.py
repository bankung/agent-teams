"""Pydantic schemas for tool_calls (Kanban #980).

Audit-row contract — read-only on the wire. Clients cannot POST/PATCH/
DELETE; the only public surface is `GET /api/tasks/{task_id}/tool-calls`.
The writer (`services.tool_call_writer.record_tool_call`) is the sole
producer; it is invoked from inside the langgraph tool-use loop (#981
wires it). NO `ToolCallCreate` / `ToolCallUpdate` exists by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
