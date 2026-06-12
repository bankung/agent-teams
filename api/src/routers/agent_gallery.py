"""HTTP routes for the agent gallery (Kanban #1017).

Mounted at ``/api/agents``. Platform-level resource (like ``/api/agents/validate``
in ``routers/agent_validation.py``) — NO ``X-Project-Id`` header. The agent
files belong to the agent-teams platform, not to any one bound project.

Endpoints (both GET-only):

  GET /api/agents
    Flat array of agent summaries, sorted by name. Built on the #1016 validator
    so an invalid file still appears (``valid=false`` + its diagnostics). Pure
    filesystem read — no DB touch.

  GET /api/agents/{name}
    Everything in the summary PLUS ``raw_frontmatter``, ``full_description``,
    and recent cross-project ``spawns`` (the ONE DB read in this feature).
    ``name`` is validated against ``AGENT_NAME_RE`` (404 on a bad shape — this
    blocks path-traversal-shaped input like ``..%2fetc`` before any lookup),
    then resolved by matching the SCANNED listing (client input is NEVER joined
    onto a filesystem path). Unknown name → 404.

The filesystem scan is synchronous; it is dispatched to the anyio thread pool so
the event loop is not blocked by directory I/O over the bind mount (same
discipline as ``routers/agent_validation.py`` / ``routers/task_outputs.py``).
"""

from __future__ import annotations

from pathlib import Path

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.schemas.agent_metadata import (
    AGENT_NAME_RE,
    AgentDetail,
    AgentSummary,
)
from src.services.agent_spawns import fetch_agent_spawns
from src.services.agent_validation import (
    default_agents_dir,
    get_agent_summary,
    list_agents,
)
from src.settings import get_settings

# Platform-level (no X-Project-Id) router. Mounted at /api/agents in main.py,
# alongside the #1016 validator router. The two share the /agents prefix; route
# paths do not collide (`/validate` vs `/` and `/{name}`).
router = APIRouter(prefix="/agents", tags=["agent-gallery"])


@router.get("", response_model=list[AgentSummary])
async def list_agents_endpoint() -> list[dict[str, object]]:
    """List every ``.claude/agents/*.md`` agent as a gallery summary row.

    Returns a flat array sorted by ``name``. Underscore-prefixed includes are
    skipped (same rule as the validator). Invalid files appear with
    ``valid=false`` and their diagnostics in ``validation_errors``.
    """
    repo_root = Path(get_settings().repo_root)
    agents_dir = default_agents_dir(repo_root)
    return await anyio.to_thread.run_sync(lambda: list_agents(agents_dir))


@router.get("/{name}", response_model=AgentDetail)
async def get_agent_detail_endpoint(
    name: str = PathParam(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Return one agent's full detail + recent cross-project spawn history.

    404 when ``name`` fails the agent-name regex (blocks traversal-shaped /
    malformed input before any filesystem or DB work) or when no agent file in
    the scanned directory carries that name. The spawn history is a single
    read-only query against ``tasks.subagent_models``.
    """
    # Regex gate FIRST — a name that cannot be a valid agent name cannot match
    # any scanned file, and rejecting it here keeps traversal-shaped input
    # (``..%2fetc``, ``foo/bar``) from ever reaching the lookup.
    if not AGENT_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail=f"Unknown agent {name!r}")

    repo_root = Path(get_settings().repo_root)
    agents_dir = default_agents_dir(repo_root)
    summary = await anyio.to_thread.run_sync(
        lambda: get_agent_summary(agents_dir, name)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent {name!r}")

    spawns = await fetch_agent_spawns(session, name)
    # `summary` already carries raw_frontmatter + full_description (computed in
    # the service); attach the spawn history and let AgentDetail serialize.
    return {**summary, "spawns": spawns}
