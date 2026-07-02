"""Pydantic schemas for the cross-task project-outputs aggregate (Kanban #2558).

Backs `GET /api/projects/{project_id}/outputs` (routers/task_outputs.py,
`router_project`). The per-task listing (`GET /api/tasks/{id}/outputs`) returns
a bare `list[dict]` with no schema (see routers/task_outputs.py) — this new
project-scoped route gets typed schemas since it is a NEW surface with its own
response envelope (`{items, total}`), not an extension of the locked #1305
per-task contract, which stays untouched.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectOutputItem(BaseModel):
    """One output file row in the cross-task aggregate listing.

    `download_url` points at the EXISTING per-task file-serving route
    (`GET /api/tasks/{task_id}/outputs/{filename}?download=1`) — this schema
    never introduces a second file-serving path. `task_title` is `None` when
    the owning task_id has no live DB row (files can outlive their task).
    """

    model_config = ConfigDict(extra="forbid")

    filename: str
    task_id: int
    task_title: str | None
    role: str | None
    mtime: datetime
    size: int
    mime: str
    kind: str
    download_url: str


class ProjectOutputsResponse(BaseModel):
    """Envelope for `GET /api/projects/{project_id}/outputs` — paginated.

    `total` is the FULL collected-and-sorted count BEFORE the limit/offset
    slice, so the FE can paginate.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ProjectOutputItem]
    total: int
