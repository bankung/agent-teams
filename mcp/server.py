"""Minimal stdio MCP server for the agent-teams Kanban backend (Kanban #806, AC2 spike).

THIN SHIM ONLY. Each of the three tools is an httpx call against the existing
FastAPI at ``API_BASE`` (default http://localhost:8456). No direct DB/ORM access,
no business logic, no new endpoints -- the routers remain the single chokepoint for
validation, AC discipline, and the operator-proof/HALT gates (mcp-adapter-design.md
sections 1, 3, 6).

Exposes EXACTLY three tools, the curated read+create subset; nothing in the
always-HALT / destructive / operator-gated tier is reachable here:
  1. list_projects()                         -> GET  /api/projects
  2. list_tasks(project, status?)            -> GET  /api/tasks  (X-Project-Id header)
  3. create_task(project, title, ...)        -> POST /api/tasks  (project_id in BOTH
                                                 the JSON body AND the X-Project-Id
                                                 header -- the documented 422 footgun)

Transport: stdio (``mcp.run()``). Run it directly (uvx / python) and point a stdio
MCP client at it. See README.md for the client-config snippet.
"""

from __future__ import annotations

import os

import httpx
from fastmcp import FastMCP

# Base URL of the existing FastAPI. Override with API_BASE for container/hosted runs
# (e.g. http://host.docker.internal:8456 from inside a sibling container).
API_BASE = os.environ.get("API_BASE", "http://localhost:8456").rstrip("/")

# A single short timeout: this is a localhost shim, not a long-poll surface.
_TIMEOUT = httpx.Timeout(30.0)

mcp = FastMCP("agent-teams-kanban")


class _ApiError(RuntimeError):
    """Surfaced to the MCP client as a tool error carrying the FastAPI detail."""


def _raise_for_status(resp: httpx.Response, context: str) -> None:
    """Map a FastAPI 4xx/5xx into a tool error that surfaces the ``detail`` string.

    Keeps the router's own validation message (e.g. the 422 you get when project_id
    is missing from the body) visible to the MCP client instead of swallowing it.
    """
    if resp.is_success:
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:  # noqa: BLE001 - response body may not be JSON
        detail = resp.text
    raise _ApiError(f"{context}: HTTP {resp.status_code} -- {detail}")


def _resolve_project_id(client: httpx.Client, project: int | str) -> int:
    """Resolve ``project`` (an int id, an all-digit string, or a name) to an int id.

    Name -> id goes through GET /api/projects/by-name/{name} (the bootstrap step),
    mirroring the server-side X-Project-Id resolution. Unknown names raise (no silent
    cross-project access).
    """
    if isinstance(project, int):
        return project
    project = project.strip()
    if project.isdigit():
        return int(project)
    resp = client.get(f"{API_BASE}/api/projects/by-name/{project}")
    _raise_for_status(resp, f"resolve project {project!r}")
    return int(resp.json()["id"])


@mcp.tool()
def list_projects() -> list[dict]:
    """List all projects in the agent-teams backend (id, name, team, ...).

    Read-only. Backs onto GET /api/projects -- use it for discovery and to find the
    ``id`` (or confirm the name) you then pass to list_tasks / create_task.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(f"{API_BASE}/api/projects")
        _raise_for_status(resp, "list_projects")
        return resp.json()


@mcp.tool()
def list_tasks(project: int | str, status: int | None = None) -> list[dict]:
    """List tasks for one project (windowed), optionally filtered by status.

    Read-only. Backs onto GET /api/tasks with the X-Project-Id header.

    Args:
        project: project id (int or all-digit string) or exact project name.
        status: optional process_status filter (1=todo .. 5=done); omit for all.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        pid = _resolve_project_id(client, project)
        params: dict[str, int] = {}
        if status is not None:
            params["process_status"] = status
        resp = client.get(
            f"{API_BASE}/api/tasks",
            params=params,
            headers={"X-Project-Id": str(pid)},
        )
        _raise_for_status(resp, "list_tasks")
        return resp.json()


@mcp.tool()
def create_task(
    project: int | str,
    title: str,
    description: str | None = None,
    acceptance_criteria: list[dict] | None = None,
) -> dict:
    """Create a Kanban task on one project. Returns the created task row.

    Backs onto POST /api/tasks. ``project_id`` is sent in BOTH the JSON body AND the
    X-Project-Id header -- the header alone 422s (the documented agent-teams footgun).
    All router-level validation (and the AC schema) still applies.

    Args:
        project: project id (int or all-digit string) or exact project name.
        title: task title (required).
        description: optional task description.
        acceptance_criteria: optional list of {text, status, ...} AC items.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        pid = _resolve_project_id(client, project)
        body: dict = {"project_id": pid, "title": title}
        if description is not None:
            body["description"] = description
        if acceptance_criteria is not None:
            body["acceptance_criteria"] = acceptance_criteria
        resp = client.post(
            f"{API_BASE}/api/tasks",
            json=body,
            headers={"X-Project-Id": str(pid)},
        )
        _raise_for_status(resp, "create_task")
        return resp.json()


if __name__ == "__main__":
    mcp.run()
