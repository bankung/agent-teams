"""HTTP routes for project registry CRUD.

Mounted at `/api/projects` from main.py. All endpoints async, async-SQLAlchemy.
After-create-side-effect: auto-scaffolds the on-disk context/projects/<name>/ folder.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_or_404, get_session
from src.models.project import Project
from src.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from src.services.project_scaffold import scaffold_project_folder
from src.settings import get_settings

router = APIRouter(prefix="/projects", tags=["projects"])


async def _clear_other_active(session: AsyncSession, keep_id: int | None) -> None:
    """Clear is_active on every row except keep_id (if given). Called inside an open transaction."""
    stmt = update(Project).values(is_active=False).where(Project.is_active.is_(True))
    if keep_id is not None:
        stmt = stmt.where(Project.id != keep_id)
    await session.execute(stmt)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Project]:
    result = await session.execute(
        select(Project).order_by(Project.id.asc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.get("/active", response_model=ProjectRead)
async def get_active_project(
    session: AsyncSession = Depends(get_session),
) -> Project:
    return await get_or_404(
        session, Project, detail="No active project", is_active=True
    )


@router.get("/by-name/{name}", response_model=ProjectRead)
async def get_project_by_name(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> Project:
    return await get_or_404(
        session, Project, detail=f"Project {name!r} not found", name=name
    )


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
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
    scaffold_project_folder(settings.repo_root, project.name)

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

    # Atomically flip active flag — clear other rows first (single transaction).
    if updates.get("is_active") is True:
        await _clear_other_active(session, keep_id=project.id)

    for field, value in updates.items():
        setattr(project, field, value)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc.orig)) from exc

    await session.refresh(project)
    return project
