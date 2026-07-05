"""HTTP routes for task outputs (Kanban #1305; extended by #2558).

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

  GET /api/projects/{project_id}/outputs   (Kanban #2558 — SECOND router below)
    Public — cross-task aggregate listing for a project (a later FE spawn's
    output-browser page consumes this). Does NOT introduce a second
    file-serving path — each row's `download_url` points back at the
    per-task route above.
    Headers:  X-Project-Id (required, MUST equal the path `project_id`)
    Query:    ?limit (default 50, 1..500) ?offset (default 0, >=0)
    Response: `{items: [...], total: <int>}` — `total` is the full
              collected-and-sorted count BEFORE the limit/offset slice.
              An empty store → `{items: [], total: 0}` (200, never an error).
    Sort: mtime DESC, tiebreak task_id DESC then filename ASC. Pagination is
    applied AFTER sorting the full collected list.
    Errors:
      400  X-Project-Id missing OR header != path project_id
      404  unknown / soft-deleted project

Why NOT FileResponse: FileResponse is a streaming response. Under the app's
BaseHTTPMiddleware (request_size_middleware), streaming responses deadlock when
sent over a real socket — the middleware awaits the full response body before
forwarding, but the StreamingResponse/FileResponse body is not buffered, causing
an indefinite hang that also wedges the event-loop and blocks /health.
Observed live 2026-06-12, Kanban #1305.

File content is read via anyio.open_file (non-blocking async I/O). The resolver
already caps files at 50 MB (MAX_FILE_BYTES), so the in-memory footprint is
bounded.

Service calls (list_task_outputs / resolve_output_file / list_project_outputs)
are synchronous; they are dispatched to the anyio thread pool so the event loop
is never blocked by filesystem I/O (observed 67 s P9 bind-mount scan in the dev
environment, 2026-06-12, #1305).

Output-folder resolution + the security guards (filename rejection, containment
via `Path.resolve()` + `is_relative_to`, no-symlink-escape) live in
`services/task_outputs.py`. See that module + the locked #1305 contract. The
#2558 aggregate walker reuses those SAME building blocks unchanged — it only
adds a task-id discovery layer on top (see that module for detail).
"""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.db import get_active_project_or_404, get_or_404, get_session
from src.models.project import Project
from src.models.task import Task
from src.schemas.task_outputs import ProjectOutputItem, ProjectOutputsResponse
from src.services.session_project import (
    assert_task_belongs_to_session,
    require_project_id_header,
)
from src.services.task_outputs import (
    is_safe_filename,
    list_project_outputs,
    list_task_outputs,
    resolve_output_file,
)
from src.settings import get_settings

router = APIRouter(prefix="/tasks", tags=["task-outputs"])

# Kanban #2558 — cross-task aggregate, project-scoped. A SECOND APIRouter in
# the same module (domain cohesion with the per-task routes above); registered
# separately in main.py alongside `router` (same pattern as
# routers/resources.py's router_project + router_resource split).
router_project = APIRouter(prefix="/projects", tags=["task-outputs"])

# Wire detail for the header-vs-path project_id mismatch (mirrors
# session_project._DETAIL_BODY_MISMATCH_TEMPLATE's shape, but for a PATH param
# rather than a request body — no existing helper covers that exact case).
_DETAIL_PROJECT_ID_MISMATCH_TEMPLATE = (
    "X-Project-Id header {header} does not match path project_id {path}"
)

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


# =============================================================================
# Kanban #2558 — cross-task aggregate listing (router_project)
# =============================================================================


@router_project.get("/{project_id}/outputs", response_model=ProjectOutputsResponse)
async def list_project_outputs_endpoint(
    project_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> ProjectOutputsResponse:
    """Cross-task output-file listing for a project, sorted + paginated.

    Gate order (mirrors `transactions.py`'s path-vs-header pattern): header
    required (400) -> header must equal path `project_id` (400) -> project
    must exist and be active (404). Then the SAME filesystem walk convention
    as the per-task listing (`services/task_outputs.list_project_outputs`,
    which reuses `_collect_entries`/`_scan_dir_direct_files` unchanged — no
    new storage layer, no new security posture).

    Sort: mtime DESC, tiebreak task_id DESC then filename ASC. Pagination is
    applied AFTER sorting the full collected list — cheap at this scale (a
    single project's task_outputs tree); see the service module for the
    per-task-id discovery cap (`MAX_DISCOVERED_TASK_IDS`).

    `task_title` is joined from a single project-scoped task query (id ->
    title), INCLUDING soft-deleted rows (files may outlive their task) —
    `None` when the discovered task_id has no DB row at all (never deleted vs
    never-existed; both surface as null, the filesystem is the source of
    truth for what to list). `download_url` points at the EXISTING per-task
    file route; this endpoint never serves file bytes itself.

    Empty store -> `{items: [], total: 0}` (200, never an error).
    """
    if session_project_id != project_id:
        raise HTTPException(
            status_code=400,
            detail=_DETAIL_PROJECT_ID_MISMATCH_TEMPLATE.format(
                header=session_project_id, path=project_id
            ),
        )
    project = await get_active_project_or_404(session, project_id)

    repo_root = Path(get_settings().repo_root)
    # Synchronous filesystem walk (multi-task-id scan) — dispatched to the
    # thread pool so the event loop is never blocked. Same rationale as the
    # per-task listing (67 s P9 bind-mount scan observed, 2026-06-12, #1305);
    # this walker touches MORE directories per call, so the risk is at least
    # as real here.
    raw_items = await anyio.to_thread.run_sync(
        lambda: list_project_outputs(project, repo_root)
    )

    # Title join: ONE query for every task_id referenced by a discovered file,
    # including soft-deleted rows (status filter intentionally omitted — a
    # file whose task was later deleted still needs a title, or an explicit
    # null when the id was never a real task row at all).
    task_ids = {int(item["task_id"]) for item in raw_items}
    titles_by_id: dict[int, str] = {}
    if task_ids:
        stmt = select(Task.id, Task.title).where(
            Task.project_id == project_id, Task.id.in_(task_ids)
        )
        rows = await session.execute(stmt)
        titles_by_id = dict(rows.all())

    def _sort_key(item: dict[str, object]) -> tuple[float, int, str]:
        # DESC on mtime + task_id needs NEGATION under an ascending sort key
        # (Python's sort has no per-field direction option without a Rich
        # comparable wrapper); filename stays ASC (not negated).
        return (
            -float(item["mtime"]),  # type: ignore[arg-type]
            -int(item["task_id"]),  # type: ignore[arg-type]
            str(item["filename"]),
        )

    raw_items.sort(key=_sort_key)
    total = len(raw_items)
    page = raw_items[offset : offset + limit]

    items = [
        ProjectOutputItem(
            filename=str(row["filename"]),
            task_id=int(row["task_id"]),
            task_title=titles_by_id.get(int(row["task_id"])),
            role=row["role"],  # type: ignore[arg-type]
            mtime=datetime.fromtimestamp(float(row["mtime"]), tz=timezone.utc),  # type: ignore[arg-type]
            size=int(row["size"]),
            mime=str(row["mime"]),
            kind=str(row["kind"]),
            download_url=(
                f"/api/tasks/{row['task_id']}/outputs/{row['filename']}?download=1"
            ),
        )
        for row in page
    ]
    return ProjectOutputsResponse(items=items, total=total)
