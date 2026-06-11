"""Project resources API — upload / attach / list / preview / delete (Kanban #1309).

Built on the LIVE `project_resources` table (#1302, migration 0059). Two mount
shapes:
  * project-scoped (router_project, prefix /api/projects/{project_id}/resources):
      - POST   create (multipart file | JSON link), operator-gated, 201
      - GET    list active resources, filters ?task_id ?kind, paginated, ungated
  * resource-scoped (router_resource, prefix /api/resources):
      - GET    /{id}          detail, ungated
      - GET    /{id}/preview  preview from tags (no full-file re-read), ungated
      - DELETE /{id}          soft-delete + move file to .trash, operator-gated

Operator gate mirrors task_templates: `require_operator_proof` -> 403 when the
gate is ACTIVE and no valid X-Operator-Token (fail-OPEN/dormant until
OPERATOR_ACTION_KEY is set). GET endpoints are ungated.

The on-disk file path is stashed in `tags["stored_path"]` (the table has no
dedicated path column — #1302 routes all derived metadata through the JSONB
`tags` object). DELETE reads it to move the file to `.trash`.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio.to_thread

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.constants import RecordStatus, ResourceKind
from src.db import get_active_project_or_404, get_or_404, get_session
from src.models.project_resource import ProjectResource
from src.models.task import Task
from src.schemas.project_resource import (
    ResourcePreview,
    ResourceRead,
)
from src.services.operator_auth import OperatorDecision, require_operator_proof
from src.services.resource_storage import (
    MAX_UPLOAD_BYTES,
    UploadTooLargeError,
    move_to_trash,
    resolve_storage_base,
    sanitize_filename,
    stream_to_disk,
)
from src.services.resource_verify import (
    guess_content_type,
    verify_and_tag_file,
    verify_and_tag_link,
)
from src.middleware.rate_limit import limiter
from src.settings import get_settings

logger = logging.getLogger(__name__)


router_project = APIRouter(
    prefix="/projects/{project_id}/resources", tags=["resources"]
)
router_resource = APIRouter(prefix="/resources", tags=["resources"])

# Source-text-locked 403 detail (parity with task_templates gate).
_DETAIL_OPERATOR_PROOF_REQUIRED = (
    "operator_proof_required: creating/deleting a project resource is "
    "operator-only"
)

def _require_operator(operator_proof: OperatorDecision) -> None:
    """Raise 403 unless the request is operator-backed (no-op when gate inactive)."""
    if operator_proof is not OperatorDecision.OPERATOR:
        raise HTTPException(
            status_code=403, detail=_DETAIL_OPERATOR_PROOF_REQUIRED
        )


async def _validate_task_same_project(
    session: AsyncSession, task_id: int | None, project_id: int
) -> None:
    """Mirror tasks.py milestone_id posture: the optional task_id must EXIST
    (not soft-deleted) AND belong to the same project, else 422.
    """
    if task_id is None:
        return
    task = await session.get(Task, task_id)
    if task is None or task.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=422,
            detail=f"task_id {task_id} does not exist or is deleted",
        )
    if task.project_id != project_id:
        raise HTTPException(
            status_code=422,
            detail=f"task_id {task_id} belongs to a different project",
        )


# HEAD probe deferred — SSRF guard needed first (#1309 follow-up).
# _probe_link_head removed; link resources are created with head_status=None,
# title=None until a safe allow-listed probe is implemented.

# ---------------------------------------------------------------------------
# POST — create (multipart file OR json link)
# ---------------------------------------------------------------------------


@router_project.post(
    "", response_model=ResourceRead, status_code=http_status.HTTP_201_CREATED
)
@limiter.limit("20/minute")  # #1309 fix #1d — per-IP cap; mirrors projects POST pattern
async def create_resource(
    project_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> ProjectResource:
    """Create a project resource (operator-gated).

    Dispatch by request content-type:
      * multipart/form-data -> FILE upload (file=..., kind='file', task_id?, label?)
      * application/json     -> LINK attach ({kind:'link', url, task_id?, label?})

    IMPORT-SAFETY (#1309): the multipart form is parsed inside the handler via
    `await request.form()` rather than declaring `UploadFile`/`Form` route
    params. Declaring them triggers FastAPI's `ensure_multipart_is_installed()`
    at IMPORT time — which would crash the WHOLE app on a deployment that hasn't
    yet rebuilt with `python-multipart`. Parsing inside the body keeps the module
    importable; only an actual multipart REQUEST fails (clean 500) pre-rebuild.

    Pipeline: validate project (404) + optional same-project task_id (422) ->
    store/probe -> verify-and-tag -> INSERT -> 201 ResourceRead. 413 when the
    streamed upload exceeds the 520 MB cap (no row created).
    """
    _require_operator(operator_proof)
    # Validate project exists + active (404); capture the row to avoid re-fetching.
    project = await get_active_project_or_404(session, project_id)

    content_type = (request.headers.get("content-type") or "").lower()
    is_multipart = content_type.startswith("multipart/form-data")

    if is_multipart:
        return await _create_file_resource(session, project_id, project, request)
    return await _create_link_resource(session, project_id, request)


async def _create_file_resource(
    session: AsyncSession,
    project_id: int,
    project: Any,
    request: Request,
) -> ProjectResource:
    """FILE path: parse the multipart form, stream to confined storage,
    verify-and-tag, INSERT.

    `request.form()` requires python-multipart at RUNTIME (not import time). On a
    pre-rebuild deployment a multipart POST raises here — caught + surfaced as a
    503 so the operator sees a clear "rebuild needed" signal, never a stack trace.

    Early Content-Length guard: if Content-Length already exceeds the 520 MB cap,
    reject immediately before Starlette spools the body into a temp file (DoS
    prevention, #1309 fix #1b). Defense-in-depth: stream_to_disk also enforces
    the cap during chunked write for missing/unreliable Content-Length.
    """
    cl_raw = request.headers.get("content-length")
    if cl_raw is not None:
        try:
            cl_val = int(cl_raw)
        except ValueError:
            cl_val = 0
        if cl_val > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload "
                    "cap; nothing was saved"
                ),
            )

    try:
        form = await request.form()
    except (AssertionError, RuntimeError) as exc:
        # python-multipart not installed -> Starlette raises at request time.
        raise HTTPException(
            status_code=503,
            detail=(
                "multipart upload unavailable: the api container needs a rebuild "
                "to install python-multipart (Kanban #1309)"
            ),
        ) from exc

    upload = form.get("file")
    kind = form.get("kind")
    task_id_raw = form.get("task_id")
    label = form.get("label")

    # `upload` is a Starlette UploadFile when a file part is present.
    filename = getattr(upload, "filename", None)
    if upload is None or not filename:
        raise HTTPException(
            status_code=422,
            detail="multipart upload requires a 'file' part with a filename",
        )
    if kind not in (None, ResourceKind.FILE):
        raise HTTPException(
            status_code=422,
            detail=f"multipart upload implies kind='file'; got kind={kind!r}",
        )
    task_id: int | None = None
    if task_id_raw not in (None, ""):
        try:
            task_id = int(task_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422, detail="task_id must be an integer"
            )
    label = label if isinstance(label, str) and label != "" else None

    await _validate_task_same_project(session, task_id, project_id)

    # Resolve storage base using the already-fetched project row (#1309 fix #5:
    # drop the redundant second session.get(Project) that created a race window).
    settings = get_settings()
    storage_base = resolve_storage_base(
        project.working_path if project else None,
        project_id,
        settings.repo_root,
    )

    safe_name = sanitize_filename(filename)
    upload_content_type = getattr(upload, "content_type", None)

    # We need the resource id to name the file; reserve a row first (flush) so
    # the PK is assigned, then stream, then fill metadata + commit. On a 413 we
    # roll back (no row persists).
    resource = ProjectResource(
        project_id=project_id,
        task_id=task_id,
        kind=ResourceKind.FILE,
        filename=safe_name,
        label=label,
        tags={},
    )
    session.add(resource)
    await session.flush()  # assigns resource.id without committing

    async def _chunks():
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            yield chunk

    try:
        stored = await stream_to_disk(
            _chunks(), storage_base, resource.id, safe_name
        )
    except UploadTooLargeError:
        await session.rollback()
        raise HTTPException(
            status_code=413,
            detail=(
                f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload "
                "cap; nothing was saved"
            ),
        )
    finally:
        await upload.close()

    # Read back the stored bytes for verify-and-tag. Files within the cap are
    # safe to read fully for parsing (CSV/JSON). For the degrade formats the
    # parser only sniffs, but we still pass the bytes (small relative to cap).
    # Offload to a thread so the blocking read doesn't starve the event loop.
    # B5: wrap read+verify in try/except so an unexpected exception here still
    # cleans up the on-disk file (DB will roll back the unflushed row; file
    # would otherwise be orphaned). Mirrors the IntegrityError cleanup below.
    try:
        data = await anyio.to_thread.run_sync(stored.path.read_bytes)
        resolved_ct = guess_content_type(safe_name, upload_content_type)
        tags = verify_and_tag_file(
            data, safe_name, upload_content_type, stored.size_bytes
        )
    except Exception:
        try:
            move_to_trash(storage_base, str(stored.path))
        except Exception as trash_exc:
            logger.warning("resources: orphan cleanup failed: %s", trash_exc)
        raise
    tags["stored_path"] = str(stored.path)

    resource.content_type = resolved_ct
    resource.size_bytes = stored.size_bytes
    resource.tags = tags

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Stored file is now orphaned relative to a missing row — move to trash.
        move_to_trash(storage_base, str(stored.path))
        raise HTTPException(
            status_code=400,
            detail="resource creation violates a database constraint",
        ) from exc

    await session.refresh(resource)
    return resource


async def _create_link_resource(
    session: AsyncSession, project_id: int, request: Request
) -> ProjectResource:
    """LINK path: validate URL syntax, best-effort HEAD probe, verify-and-tag,
    INSERT. Body shape: {kind:'link', url, task_id?, label?}.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=422, detail="link create requires a JSON body"
        )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422, detail="link create body must be a JSON object"
        )

    kind = body.get("kind")
    # B4: single guard — the first check (not in LINK|FILE) made the second
    # (kind != LINK) reachable only when kind=='file', producing a confusing
    # second message. Collapse to one clear 422. (#1309 fix #9 superseded).
    if kind != ResourceKind.LINK:
        raise HTTPException(
            status_code=422,
            detail=(
                f"JSON body must include kind='link'; "
                f"got kind={kind!r}. For file uploads use multipart/form-data."
            ),
        )
    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(
            status_code=422, detail="kind='link' requires a non-empty url"
        )
    task_id = body.get("task_id")
    if task_id is not None and not isinstance(task_id, int):
        raise HTTPException(status_code=422, detail="task_id must be an integer")
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        raise HTTPException(status_code=422, detail="label must be a string")
    if len(url) > 2_000:
        raise HTTPException(status_code=422, detail="url must be <= 2000 chars")

    await _validate_task_same_project(session, task_id, project_id)

    # URL-syntax validate (422 on malformed) — no outbound network call here.
    # HEAD probe deferred — SSRF guard needed first (#1309 follow-up).
    try:
        link_tags = verify_and_tag_link(url, head_status=None, title=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    resource = ProjectResource(
        project_id=project_id,
        task_id=task_id,
        kind=ResourceKind.LINK,
        url=url,
        label=label,
        tags=link_tags,
    )
    session.add(resource)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=400,
            detail="resource creation violates a database constraint",
        ) from exc

    await session.refresh(resource)
    return resource


# ---------------------------------------------------------------------------
# GET (list) — project-scoped
# ---------------------------------------------------------------------------


@router_project.get("", response_model=list[ResourceRead])
async def list_resources(
    project_id: int,
    task_id: int | None = Query(default=None, ge=1),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectResource]:
    """List ACTIVE resources for a project, newest first (created_at DESC).

    Optional ?task_id pins to one task; ?kind filters file/link (422 on a bad
    kind). Paginated via ?limit&offset. Ungated (read-only).
    """
    # B3: validate project existence — return 404 for unknown project_id instead
    # of silently returning [] + 200 (mirrors tasks/milestones list behaviour).
    await get_active_project_or_404(session, project_id)

    if kind is not None and kind not in ResourceKind.ALL:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown kind {kind!r}; valid: {sorted(ResourceKind.ALL)}",
        )

    stmt = select(ProjectResource).where(
        ProjectResource.project_id == project_id,
        ProjectResource.status == RecordStatus.ACTIVE,
    )
    if task_id is not None:
        stmt = stmt.where(ProjectResource.task_id == task_id)
    if kind is not None:
        stmt = stmt.where(ProjectResource.kind == kind)
    stmt = (
        stmt.order_by(ProjectResource.created_at.desc(), ProjectResource.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GET (detail) + GET (preview) — resource-scoped
# ---------------------------------------------------------------------------


@router_resource.get("/{resource_id}", response_model=ResourceRead)
async def get_resource(
    resource_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProjectResource:
    """Full resource row incl. tags metadata. 404 if missing or soft-deleted. Ungated."""
    resource = await get_or_404(
        session,
        ProjectResource,
        detail=f"Resource id={resource_id} not found",
        id=resource_id,
    )
    # #1309 fix #3: soft-deleted rows are invisible to GET detail (same as list).
    if resource.status != RecordStatus.ACTIVE:
        raise HTTPException(
            status_code=404, detail=f"Resource id={resource_id} not found"
        )
    return resource


@router_resource.get("/{resource_id}/preview", response_model=ResourcePreview)
async def get_resource_preview(
    resource_id: int,
    session: AsyncSession = Depends(get_session),
) -> ResourcePreview:
    """Preview from the stored `tags` metadata — does NOT re-read the file.

    404 if missing. Ungated. Pulls preview / schema / counts straight off the
    JSONB tags object captured at upload time.
    """
    resource = await get_or_404(
        session,
        ProjectResource,
        detail=f"Resource id={resource_id} not found",
        id=resource_id,
    )
    # #1309 fix #3: soft-deleted rows are invisible to preview (same as list).
    if resource.status != RecordStatus.ACTIVE:
        raise HTTPException(
            status_code=404, detail=f"Resource id={resource_id} not found"
        )
    tags = resource.tags or {}
    return ResourcePreview(
        id=resource.id,
        kind=resource.kind,
        filename=resource.filename,
        content_type=resource.content_type,
        format_detected=tags.get("format_detected"),
        row_count=tags.get("row_count"),
        col_count=tags.get("col_count"),
        schema_detected=tags.get("schema_detected"),
        preview=tags.get("preview"),
        parser_unavailable=bool(tags.get("parser_unavailable", False)),
    )


# ---------------------------------------------------------------------------
# DELETE — soft-delete + move file to trash (operator-gated)
# ---------------------------------------------------------------------------


@router_resource.delete(
    "/{resource_id}", status_code=http_status.HTTP_204_NO_CONTENT
)
async def delete_resource(
    resource_id: int,
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> Response:
    """Soft-delete (status=0) + MOVE the stored file to `.trash/`. Idempotent.

    Operator-gated. 204 No Content. Errors: 403 (gate active, no proof), 404
    (missing). Re-deleting an already-deleted resource is a no-op (still 204);
    the file move is also idempotent (no-op if already trashed / link kind).
    """
    _require_operator(operator_proof)
    resource = await get_or_404(
        session,
        ProjectResource,
        detail=f"Resource id={resource_id} not found",
        id=resource_id,
    )

    if resource.status == RecordStatus.ACTIVE:
        resource.status = RecordStatus.DELETED
        resource.updated_at = func.now()

        # Move the stored file to trash (file resources only).
        stored_path = (resource.tags or {}).get("stored_path")
        if stored_path:
            from src.models.project import Project  # local import — avoids circular at top level
            settings = get_settings()
            project = await session.get(Project, resource.project_id)
            storage_base = resolve_storage_base(
                project.working_path if project else None,
                resource.project_id,
                settings.repo_root,
            )
            try:
                move_to_trash(storage_base, stored_path)
            except Exception as exc:  # noqa: BLE001 — intentional soft-delete guard (#1309)
                logger.warning(
                    "resources: trash move failed for id=%s path=%s: %s",
                    resource_id, stored_path, exc,
                )

        await session.commit()

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
