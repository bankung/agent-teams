"""HTTP route for the agent-frontmatter validator (Kanban #1016).

Mounted at ``/api/agents``. Platform-level resource (like ``/api/projects`` /
``/api/pnl``) — NO ``X-Project-Id`` header. The agent files are a property of the
agent-teams platform itself, not of any one bound project.

Endpoint:

  GET /api/agents/validate
    Scan EVERY ``.claude/agents/*.md`` file and report diagnostics. No path /
    body parameters — there is no client-supplied path (the POST-body variant
    is dropped by design; it would be an arbitrary-path read primitive). A POST
    to this URL therefore 405s (no POST handler registered).

    Response (200):
      {
        "files_scanned": int,
        "diagnostics": [ {file, line, field, message, severity}, ... ],
        "error_count": int,
        "warning_count": int
      }
    ``file`` is the basename only — no absolute paths on the wire.

The filesystem scan is synchronous; it is dispatched to the anyio thread pool so
the event loop is not blocked by directory I/O over the bind mount (same
discipline as ``routers/task_outputs.py``).
"""

from __future__ import annotations

from pathlib import Path

import anyio
import anyio.to_thread
from fastapi import APIRouter

from src.schemas.agent_metadata import AgentValidationResponse
from src.services.agent_validation import default_agents_dir, validate_agents_dir
from src.settings import get_settings

# Platform-level (no X-Project-Id) router. Mounted at /api/agents in main.py.
router = APIRouter(prefix="/agents", tags=["agent-validation"])


@router.get("/validate", response_model=AgentValidationResponse)
async def validate_agents() -> dict[str, object]:
    """Validate every ``.claude/agents/*.md`` frontmatter block.

    Returns ``{files_scanned, diagnostics, error_count, warning_count}``. See
    the module docstring + ``services/agent_validation.py`` for the contract.
    """
    repo_root = Path(get_settings().repo_root)
    agents_dir = default_agents_dir(repo_root)
    # validate_agents_dir is synchronous (filesystem scan + YAML parse); run in
    # the thread pool so the event loop is never blocked by directory I/O.
    return await anyio.to_thread.run_sync(lambda: validate_agents_dir(agents_dir))
