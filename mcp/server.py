"""Minimal stdio MCP server for the agent-teams Kanban backend (Kanban #806/#2518).

THIN SHIM ONLY. Each tool is an httpx call against the existing FastAPI at
``API_BASE`` (default http://localhost:8456). No direct DB/ORM access, no business
logic, no new endpoints -- the routers remain the single chokepoint for validation,
AC discipline, and the operator-proof/HALT gates (mcp-adapter-design.md sections 1,
3, 6).

Exposes EXACTLY six tools, the curated read+create+update subset; nothing in the
always-HALT / destructive / operator-gated tier is reachable here:
  1. list_projects()                         -> GET   /api/projects
  2. list_tasks(project, status?)            -> GET   /api/tasks   (X-Project-Id header)
  3. create_task(project, title, ...)        -> POST  /api/tasks   (project_id in BOTH
                                                 the JSON body AND the X-Project-Id
                                                 header -- the documented 422 footgun)
  4. get_task(project, task_id)              -> GET   /api/tasks/{id}
  5. update_task(project, task_id, fields)   -> PATCH /api/tasks/{id}  (rejects any
                                                 attempt to set process_status=5 --
                                                 use complete_task for that)
  6. complete_task(project, task_id, ...)    -> client-side AC-verify-then-flip:
                                                 refuses (no API write at all) when
                                                 any effective acceptance_criteria item
                                                 is still pending/failed, else one
                                                 combined PATCH (AC + process_status=5
                                                 + status_change_reason). This is a
                                                 friendlier client-side pre-check, NOT
                                                 the only guard -- the router's own
                                                 #2765 resolved-final gate still applies
                                                 server-side and is the real backstop.

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


def _get_task(client: httpx.Client, pid: int, task_id: int) -> dict:
    """Shared fetch used by both the get_task tool and complete_task's pre-read."""
    resp = client.get(
        f"{API_BASE}/api/tasks/{task_id}",
        headers={"X-Project-Id": str(pid)},
    )
    _raise_for_status(resp, f"get_task({task_id})")
    return resp.json()


@mcp.tool()
def get_task(project: int | str, task_id: int) -> dict:
    """Fetch one task by id, including its acceptance_criteria array.

    Read-only. Backs onto GET /api/tasks/{id} with the X-Project-Id header.

    Args:
        project: project id (int or all-digit string) or exact project name.
        task_id: the task's numeric id.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        pid = _resolve_project_id(client, project)
        return _get_task(client, pid, task_id)


@mcp.tool()
def update_task(project: int | str, task_id: int, fields: dict) -> dict:
    """Update fields on a task. Returns the updated task row.

    Backs onto PATCH /api/tasks/{id}. ``fields`` is sent as the PATCH body verbatim
    (status/priority/description/acceptance_criteria/etc edits) -- all router-level
    validation still applies, and any 4xx is surfaced with the router's own `detail`
    string.

    REFUSES client-side to set process_status=5 (DONE) through this tool -- that
    transition must go through complete_task, which encodes the AC-verify-then-flip
    contract and cannot be bypassed via a plain field update on this surface.

    Args:
        project: project id (int or all-digit string) or exact project name.
        task_id: the task's numeric id.
        fields: dict of fields to PATCH (e.g. {"priority": 2, "description": "..."}).
    """
    if fields.get("process_status") == 5:
        raise _ApiError(
            "update_task: refusing to set process_status=5 (DONE) directly -- "
            "use complete_task(project, task_id, acceptance_criteria=...) instead, "
            "which verifies every acceptance criterion before flipping."
        )
    with httpx.Client(timeout=_TIMEOUT) as client:
        pid = _resolve_project_id(client, project)
        resp = client.patch(
            f"{API_BASE}/api/tasks/{task_id}",
            json=fields,
            headers={"X-Project-Id": str(pid)},
        )
        _raise_for_status(resp, f"update_task({task_id})")
        return resp.json()


@mcp.tool()
def complete_task(
    project: int | str,
    task_id: int,
    acceptance_criteria: list[dict] | None = None,
    status_change_reason: str | None = None,
) -> dict:
    """Verify acceptance criteria, then flip a task to process_status=5 (DONE).

    Encodes the AC-verify-then-flip contract (mcp-adapter-design.md section 7):

      1. Fetches the task's current row (same path as get_task).
      2. The EFFECTIVE acceptance_criteria array is ``acceptance_criteria`` if you
         pass it (the full updated array, with your verdicts), else the task's
         currently-stored array.
      3. If ANY effective item has status "pending" or "failed", this tool makes
         NO API write at all and raises a tool error naming the offending item(s)
         -- fix the verdicts (or the underlying work) and retry.
      4. Otherwise sends ONE combined PATCH: acceptance_criteria (only when you
         supplied one) + process_status=5 + status_change_reason (T2/#2541
         single-round-trip convention).
      5. A null/empty AC array is allowed to flip (nothing to resolve) -- the
         platform rule; this is noted in the response.

    This is a friendlier CLIENT-SIDE pre-check, not the only guard: the router's
    own #2765 resolved-final gate re-validates server-side on the PATCH and will
    422 independently if this check were ever bypassed.

    Args:
        project: project id (int or all-digit string) or exact project name.
        task_id: the task's numeric id.
        acceptance_criteria: optional full replacement AC array (list of
            {text, status, verified_by, verified_at, notes} dicts) carrying your
            verdicts. Omit to judge against the task's currently-stored array.
        status_change_reason: optional free-text reason recorded with the flip.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        pid = _resolve_project_id(client, project)
        task = _get_task(client, pid, task_id)

        effective_ac = (
            acceptance_criteria if acceptance_criteria is not None else task.get("acceptance_criteria")
        )

        note = ""
        if not effective_ac:
            note = " (no acceptance_criteria on this task -- nothing to resolve)"
        else:
            offenders = [
                item.get("text", "<no text>")
                for item in effective_ac
                if isinstance(item, dict) and item.get("status") in ("pending", "failed")
            ]
            if offenders:
                raise _ApiError(
                    f"complete_task({task_id}): refusing to flip to DONE -- "
                    f"{len(offenders)} acceptance criterion/criteria not resolved: "
                    f"{'; '.join(offenders)}. Every criterion must be 'passed' or "
                    "'na' first. No API write was made."
                )

        body: dict = {"process_status": 5}
        if acceptance_criteria is not None:
            body["acceptance_criteria"] = acceptance_criteria
        if status_change_reason is not None:
            body["status_change_reason"] = status_change_reason

        resp = client.patch(
            f"{API_BASE}/api/tasks/{task_id}",
            json=body,
            headers={"X-Project-Id": str(pid)},
        )
        _raise_for_status(resp, f"complete_task({task_id})")
        result = resp.json()
        if note:
            result = {**result, "_complete_task_note": note.strip(" ()")}
        return result


if __name__ == "__main__":
    mcp.run()
