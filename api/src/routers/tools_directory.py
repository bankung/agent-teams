"""Tool directory endpoint — agent-runtime discovery (Kanban #1854).

Extends the #1799 tool-governance work (P0: Lead-mediated grant gate) to P1:
an agent can call this endpoint at RUNTIME to discover which Mode-A HTTP tools
it is allowed to use on this project, plus each tool's purpose string.

Why a dedicated endpoint rather than embedding this in the email router:
- The email router owns gmail/outlook TOOL ACTIONS (trash, auth, usage).
  The tool directory is GOVERNANCE METADATA — a different concept.
- Future tools (calendar, drive, …) registered in TOOL_REGISTRY would appear
  here automatically with zero changes to the email router.
- Keeps the email router focused on its own blast-radius concerns.

Consistency with #1799:
- Same `X-Project-Id` header dependency (`require_project_id_header`).
- Same optional `X-Agent-Role` header (`optional_agent_role_header`).
- Same DB read pattern as `_enforce_tool_grant_or_403`: reads `config.tool_grants`
  from the project row.
- DERIVED from TOOL_REGISTRY + config.tool_grants — never hand-maintained.

Endpoint surface:
  GET /api/tools/directory
    Headers: X-Project-Id (required), X-Agent-Role (optional)
    Query:   capability=<free text> (optional — triggers AC2 suggestion logic)
    Returns: ToolDirectoryResponse

Grant semantics (mirrors _evaluate in services/tool_grants):
  - tool_grants absent -> all registered tools are allowed (unrestricted).
  - role absent or not in tool_grants -> all registered tools are allowed.
  - role IS a key -> only tools in its list are included in `allowed_tools`.

AC2 (missing-tool suggestion):
  - If ?capability=<text> is provided and no tool in the allowed set mentions
    the capability keyword (case-insensitive match against tool name + purpose),
    the response includes a `suggestion` block with a no_tool_for flag so the
    agent can surface "no tool for X; consider building one" instead of silently
    failing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import get_session
from src.models.project import Project
from src.services.session_project import (
    optional_agent_role_header,
    require_project_id_header,
)
from src.services.tool_registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools-directory"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ToolDirectoryEntry(BaseModel):
    """One allowed tool entry in the directory response."""

    name: str
    tier: str
    version: str
    purpose: str


class MissingToolSuggestion(BaseModel):
    """AC2: returned when ?capability=<text> finds no matching tool.

    The agent surfaces this to the operator rather than silently failing:
    "no tool for X; consider building one".
    """

    no_tool_for: str
    hint: str = "No registered Mode-A tool matches this capability. Consider building one or ask the Lead to add it to the registry."


class ToolDirectoryResponse(BaseModel):
    """Response body for GET /api/tools/directory."""

    project_id: int
    role: str | None
    # Derived from TOOL_REGISTRY filtered by config.tool_grants — never
    # hand-maintained. See module docstring for grant semantics.
    allowed_tools: list[ToolDirectoryEntry]
    suggestion: MissingToolSuggestion | None = None


# ---------------------------------------------------------------------------
# Grant resolution (pure — no audit write; discovery is read-only)
# ---------------------------------------------------------------------------


def _allowed_tool_names(
    config: dict[str, Any] | None, role: str | None
) -> set[str]:
    """Return the set of TOOL_REGISTRY keys allowed for this (config, role) pair.

    Mirrors the _evaluate logic in services/tool_grants exactly — no audit write
    (discovery is read-only; auditing belongs on mutating calls).

    Returns ALL registered tool names when unrestricted (absent role, unlisted
    role, absent/malformed tool_grants). Returns only the explicitly-granted
    subset when the role is listed in tool_grants.
    """
    all_tools = set(TOOL_REGISTRY.keys())

    if not config or not isinstance(config, dict):
        return all_tools

    grants = config.get("tool_grants")
    if not isinstance(grants, dict):
        return all_tools

    if role is None or role not in grants:
        return all_tools

    allowed = grants.get(role)
    if not isinstance(allowed, list):
        # Malformed role value (not a list) — mirrors _evaluate's over-block:
        # once explicitly listed, nothing is allowed.
        return set()

    # Only tools also present in TOOL_REGISTRY (no phantom entries from config).
    return {t for t in allowed if t in TOOL_REGISTRY}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/directory", response_model=ToolDirectoryResponse)
async def get_tool_directory(
    capability: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Optional capability keyword. If provided and no allowed tool "
            "matches, the response includes a suggestion block (AC2)."
        ),
    ),
    session_project_id: int = Depends(require_project_id_header),
    agent_role: str | None = Depends(optional_agent_role_header),
    session: AsyncSession = Depends(get_session),
) -> ToolDirectoryResponse:
    """Return the Mode-A HTTP tools allowed for this project + role at runtime.

    DERIVED from `TOOL_REGISTRY` + `config.tool_grants` — never hand-maintained.
    An agent calls this to discover its allowed tools and each tool's purpose
    before deciding which to use.

    AC1 (Kanban #1854): agent can discover allowed tools + purpose at RUNTIME.
    AC2 (Kanban #1854): if ?capability=<text> finds no matching tool, the
      response includes a `suggestion` block so the agent can surface
      "no tool for X; consider building one" instead of silently failing.
    """
    # Read project config — same query as _enforce_tool_grant_or_403.
    # Missing project treated as None config (unrestricted) — same semantics.
    config = (
        await session.execute(
            select(Project.config)
            .where(Project.id == session_project_id)
            .where(Project.status == RecordStatus.ACTIVE)
        )
    ).scalar_one_or_none()

    allowed_names = _allowed_tool_names(config, agent_role)

    allowed_tools = [
        ToolDirectoryEntry(
            name=name,
            tier=entry["tier"],
            version=entry["version"],
            purpose=entry["purpose"],
        )
        for name, entry in TOOL_REGISTRY.items()
        if name in allowed_names
    ]

    # AC2 — missing-tool suggestion.
    suggestion: MissingToolSuggestion | None = None
    if capability is not None:
        cap_lower = capability.lower()
        match = any(
            cap_lower in tool.name.lower() or cap_lower in tool.purpose.lower()
            for tool in allowed_tools
        )
        if not match:
            suggestion = MissingToolSuggestion(no_tool_for=capability)

    return ToolDirectoryResponse(
        project_id=session_project_id,
        role=agent_role,
        allowed_tools=allowed_tools,
        suggestion=suggestion,
    )
