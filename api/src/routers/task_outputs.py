"""HTTP routes for task outputs (Kanban #1305).

Mounted at `/api/tasks/{task_id}/outputs`. Sub-resource of tasks — mirrors the
`tool_calls.py` gate order, X-Project-Id scoping, and soft-delete (410)
semantics.

Endpoints:

  GET /api/tasks/{task_id}/outputs
    Public — the FE output-panel UI consumes this.
    Headers:  X-Project-Id (required — sub-resource of /api/tasks/*)
    Response: [ {filename, mime, size, kind}, ... ]  sorted by filename.
              An empty / missing output folder → `[]` (NOT an error).
    Errors:
      400  X-Project-Id missing OR task belongs to a different project
      404  task not found
      410  task soft-deleted (status=0) — sub-resource Gone with the parent

  GET /api/tasks/{task_id}/outputs/{filename}
    Serves one output file. Same gate order as the listing, THEN:
      * `filename` validated (no `/`, `\\`, `..`, null byte) → 404 on a bad name
        (we do not echo the rejected path).
      * file must be in the listing (resolved via the same convention) → 404.
    Default: `Content-Disposition: inline` with a guessed mimetype.
    `?download=1` → `Content-Disposition: attachment; filename="..."`.
    Always sets `X-Content-Type-Options: nosniff`.

Why NOT FileResponse: FileResponse is a streaming response. Under the app's
BaseHTTPMiddleware (request_size_middleware), streaming responses deadlock when
sent over a real socket — the middleware awaits the full response body before
forwarding, but the StreamingResponse/FileResponse body is not buffered, causing
an indefinite hang that also wedges the event-loop and blocks /health.
Observed live 2026-06-12, Kanban #1305.

File content is read via anyio.open_file (non-blocking async I/O). The resolver
already caps files at 50 MB (MAX_FILE_BYTES), so the in-memory footprint is
bounded.

Service calls (list_task_outputs / resolve_output_file) are synchronous; they are
dispatched to the anyio thread pool so the event loop is never blocked by
filesystem I/O (observed 67 s P9 bind-mount scan in the dev environment,
2026-06-12, #1305).

Output-folder resolution + the security guards (filename rejection, containment
via `Path.resolve()` + `is_relative_to`, no-symlink-escape) live in
`services/task_outputs.py`. See that module + the locked #1305 contract.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.project import Project
from src.models.task import Task
from src.services.session_project import (
    assert_task_belongs_to_session,
    require_project_id_header,
)
from src.services.task_outputs import (
    is_safe_filename,
    list_task_outputs,
    resolve_output_file,
)
from src.settings import get_settings

router = APIRouter(prefix="/tasks", tags=["task-outputs"])

# Force attachment for active content types — inline text/html executes on the
# API origin (nosniff does NOT prevent declared-html execution); the FE previews
# these via fetch+sandboxed iframe, never via this inline path. (#1305 security review)
FORCE_ATTACHMENT_SUFFIXES = {".html", ".htm", ".svg", ".xml"}


async def _gate_task_and_project(
    task_id: int,
    session_project_id: int,
    session: AsyncSession,
) -> tuple[Task, Project]:
    """Run the shared gate chain and return (task, project).

    Gate order mirrors tool_calls.py exactly:
      1. require_project_id_header (400 on missing) — applied via Depends.
      2. get_or_404(Task) (404 on unknown task).
      3. assert_task_belongs_to_session (400 on cross-project header).
      4. RecordStatus.DELETED → 410 (outputs Gone with the parent).
    Then loads the owning project row (needed for working_path/team resolution).
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    # Fires AFTER get_or_404 so 404 still wins on a missing id.
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)
    if task.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=410,
            detail=f"Task id={task_id} is deleted; outputs are gone with the parent",
        )
    project = await get_or_404(
        session,
        Project,
        detail=f"Project for task id={task_id} not found (data integrity error)",
        id=task.project_id,
    )
    return task, project


@router.get("/{task_id}/outputs")
async def list_outputs(
    task_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """List a task's output files — `[{filename, mime, size, kind}]`, sorted.

    Empty / no folder → `[]` (not an error). See module docstring for the full
    contract; resolution convention lives in `services/task_outputs.py`.
    """
    _task, project = await _gate_task_and_project(
        task_id, session_project_id, session
    )
    repo_root = Path(get_settings().repo_root)
    # list_task_outputs is synchronous (filesystem scan); run in thread pool so
    # the event loop is not blocked. Observed 67 s P9 bind-mount scan in dev
    # (2026-06-12, #1305).
    return await anyio.to_thread.run_sync(
        lambda: list_task_outputs(project, task_id, repo_root)
    )


@router.get("/{task_id}/outputs/{filename}")
async def get_output_file(
    task_id: int,
    filename: str,
    download: bool = Query(default=False),
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Serve one output file inline (default) or as an attachment (`?download=1`).

    Same gate order as the listing; then `filename` is validated and the file
    must be present in the resolved listing. Always sets
    `X-Content-Type-Options: nosniff`.

    NOTE: FileResponse is intentionally NOT used here. The app's
    BaseHTTPMiddleware (request_size_middleware) deadlocks with any streaming
    response over a real socket — observed live 2026-06-12, Kanban #1305. The
    resolver caps files at MAX_FILE_BYTES (50 MB), so reading into memory is
    safe. See module docstring for the full rationale.
    """
    _task, project = await _gate_task_and_project(
        task_id, session_project_id, session
    )

    # The filename path param is the ONLY client-controlled path component.
    # Reject path traversal, separators, null bytes, double-quotes, and CR/LF
    # BEFORE touching the filesystem; 404 without echoing the rejected path.
    if not is_safe_filename(filename):
        raise HTTPException(status_code=404, detail="Output file not found")

    repo_root = Path(get_settings().repo_root)
    # resolve_output_file is synchronous (filesystem scan + stat); run in thread
    # pool so the event loop is not blocked.
    path = await anyio.to_thread.run_sync(
        lambda: resolve_output_file(project, task_id, filename, repo_root)
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Output file not found")

    # Read via anyio.open_file (fully async, non-blocking). Files are capped at
    # MAX_FILE_BYTES (50 MB) by the resolver listing, so in-memory read is safe.
    async with await anyio.open_file(path, "rb") as f:
        data = await f.read()

    media_type, _ = mimetypes.guess_type(filename)
    suffix = Path(filename).suffix.lower()
    # Active content suffixes are always forced to attachment regardless of
    # ?download — inline text/html executes on the API origin (nosniff does NOT
    # prevent declared-html execution). The FE previews these via
    # fetch()+blob/srcDoc, never via this inline path, so the UI is unaffected.
    if suffix in FORCE_ATTACHMENT_SUFFIXES:
        disposition = "attachment"
    else:
        disposition = "attachment" if download else "inline"
    return Response(
        content=data,
        media_type=media_type or "application/octet-stream",
        headers={
            "X-Content-Type-Options": "nosniff",
            # Quote the filename; is_safe_filename has already rejected anything
            # with a quote / separator / control char, so this is a single safe
            # token.
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        },
    )
