"""HTTP routes for project registry CRUD.

Mounted at `/api/projects` from main.py. All endpoints async, async-SQLAlchemy.
After-create-side-effect: auto-scaffolds the on-disk context/projects/<name>/ folder.

Soft-delete: list endpoints default-filter `WHERE status=1`; opt-in `?include_deleted=true`
returns soft-deleted rows too. DELETE flips `status=0` (and clears `is_active` if true).
Detail endpoints return rows regardless of status (per standards/postgresql/soft-delete.md).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.project import Project
from src.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from src.services.project_scaffold import scaffold_project_folder
from src.settings import get_settings

router = APIRouter(prefix="/projects", tags=["projects"])


async def _clear_other_active(session: AsyncSession, keep_id: int | None) -> None:
    """Clear is_active on every row except keep_id (if given). Called inside an open transaction."""
    # We deliberately don't filter status=1 here — the partial unique index already
    # excludes status=0 rows, so a soft-deleted is_active=true row is harmless on
    # the index but still worth clearing if it leaks from out-of-band edits.
    stmt = (
        update(Project)
        .values(is_active=False)
        .where(Project.is_active.is_(True))
        .execution_options(synchronize_session=False)
    )
    if keep_id is not None:
        stmt = stmt.where(Project.id != keep_id)
    await session.execute(stmt)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[Project]:
    stmt = select(Project)
    if not include_deleted:
        stmt = stmt.where(Project.status == RecordStatus.ACTIVE)
    stmt = stmt.order_by(Project.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/active", response_model=ProjectRead)
async def get_active_project(
    session: AsyncSession = Depends(get_session),
) -> Project:
    # The partial unique index `ux_projects_active_one` already gates is_active=true
    # on status=1, so we don't need to filter status here — but pass it explicitly
    # for clarity and as a safety net if the index is ever loosened.
    return await get_or_404(
        session,
        Project,
        detail="No active project",
        is_active=True,
        status=RecordStatus.ACTIVE,
    )


@router.get("/by-name/{name}", response_model=ProjectRead)
async def get_project_by_name(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> Project:
    # By-name lookup is used by Lead bootstrap and external integrations — they
    # only ever care about active projects. Soft-deleted projects are invisible
    # by name (the partial unique index allows a new project to claim the name).
    return await get_or_404(
        session,
        Project,
        detail=f"Project {name!r} not found",
        name=name,
        status=RecordStatus.ACTIVE,
    )


@router.post("", response_model=ProjectRead, status_code=http_status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> Project:
    config = dict(payload.config or {})
    if payload.standards is not None:
        config["standards"] = payload.standards.model_dump()
    data = {
        "name": payload.name,
        "description": payload.description,
        "paths_web": payload.paths.web,
        "paths_api": payload.paths.api,
        "paths_db": payload.paths.db,
        "stack_web": payload.stack.web,
        "stack_api": payload.stack.api,
        "stack_db": payload.stack.db,
        "config": config,
        "is_active": payload.is_active,
        "team": payload.team,
    }

    # If caller wants this project to be the active one, clear others first —
    # avoids tripping the partial unique index in the same transaction.
    if data["is_active"]:
        await _clear_other_active(session, keep_id=None)

    project = Project(**data)
    session.add(project)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Project name {payload.name!r} already exists",
        ) from exc
    await session.refresh(project)

    # Side-effect: scaffold context/projects/<name>/ — failure is non-fatal.
    settings = get_settings()
    scaffold_project_folder(settings.repo_root, project.name, team=project.team)

    return project


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = await get_or_404(
        session, Project, detail=f"Project id={project_id} not found", id=project_id
    )

    updates = payload.model_dump(exclude_unset=True)

    # M10: cannot reactivate a soft-deleted project via PATCH — restore is a
    # separate (not-yet-built) admin path. Other fields ARE editable on a
    # soft-deleted row (admin edit / metadata correction).
    if updates.get("is_active") is True and project.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=400,
            detail="Cannot activate a soft-deleted project — restore first",
        )

    # Atomically flip active flag — clear other rows first (single transaction).
    if updates.get("is_active") is True:
        await _clear_other_active(session, keep_id=project.id)

    # Skip writes where the new value equals the existing one — keeps PATCHes
    # that touch only some fields from bumping `updated_at` (and from writing
    # redundant rows once an audit table lands). SQL clause elements bypass the
    # equality check — comparing a ClauseElement with `!=` returns a SQL
    # BinaryExpression (not a bool), so the isinstance guard exists to keep the
    # no-op detector from crashing on dynamic SQL values. Mirrors tasks.py.
    changed = False
    for field, value in updates.items():
        if isinstance(value, ClauseElement) or getattr(project, field) != value:
            setattr(project, field, value)
            changed = True

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    if changed:
        project.updated_at = func.now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Mirror create_project — surface a stable detail instead of leaking PG internals.
        orig_text = str(exc.orig)
        if "ux_projects_name_active" in orig_text:
            detail = f"Project name {updates['name']!r} already exists"
        else:
            detail = "Project update conflicts with an existing row"
        raise HTTPException(status_code=409, detail=detail) from exc

    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a project: flip status=0; if it was the active project, also
    clear is_active so a new project can take that slot. Returns 204 No Content.
    Idempotent — deleting an already-deleted project is a no-op (still 204).
    """
    project = await get_or_404(
        session, Project, detail=f"Project id={project_id} not found", id=project_id
    )

    # Idempotent: if already soft-deleted, skip the no-op UPDATE so we don't
    # write a redundant audit row. The is_active clear is also unnecessary —
    # an already-deleted row should not be active, but be defensive and skip.
    if project.status == RecordStatus.DELETED:
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    project.status = RecordStatus.DELETED
    if project.is_active:
        # Same transaction so the partial unique index never sees a half-state.
        project.is_active = False

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    project.updated_at = func.now()

    await session.commit()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
