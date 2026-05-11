"""HTTP routes for project registry CRUD.

Mounted at `/api/projects` from main.py. All endpoints async, async-SQLAlchemy.
After-create-side-effect: auto-scaffolds the on-disk context/projects/<name>/ folder.

Soft-delete: list endpoints default-filter `WHERE status=1`; opt-in `?include_deleted=true`
returns soft-deleted rows too. DELETE flips `status=0` (and clears `is_active` if true).
Detail endpoints return rows regardless of status (per standards/postgresql/soft-delete.md).

Session-scoped active (Kanban #694, Phase 2): the legacy "single active project"
invariant is gone. `is_active` is a free boolean — multiple rows may carry
`is_active=true` simultaneously. PATCH /api/projects/{id} no longer atomically
clears other rows; GET /api/projects/active returns 410 Gone (use
/api/projects/by-name/{name} or /api/projects?status=1 instead).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import ClauseElement

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.project import Project
from src.schemas.project import (
    ProjectCreate,
    ProjectGrantConsent,
    ProjectRead,
    ProjectUpdate,
)
from src.services.project_scaffold import scaffold_project_folder
from src.settings import get_settings

router = APIRouter(prefix="/projects", tags=["projects"])


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


@router.get(
    "/active",
    responses={
        410: {
            "description": (
                "Endpoint deprecated. Use /api/projects/by-name/{name} or "
                "/api/projects?status=1 instead."
            )
        },
    },
)
async def get_active_project() -> Response:
    """Deprecated by Kanban #694 (Phase 2 of session-scoped active project shift).

    The legacy "single active project" invariant is gone — each Claude Code
    session binds to a project by name at bootstrap, and multiple rows may
    carry `is_active=true` simultaneously. Returns 410 Gone with a stable
    detail string pointing callers at the replacement endpoints.

    Detail string source-text-locked per the #122 pattern by
    `test_get_active_project_410_detail_pinned_in_router_source` —
    keep in sync.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Endpoint deprecated. Use /api/projects/by-name/{name} or "
            "/api/projects?status=1 instead."
        ),
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


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project_by_id(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> Project:
    # By-id lookup parity with /by-name/{name} — FE V3 project switcher + external
    # integrations only ever want active rows. Soft-deleted projects 404 by id
    # too; restore is a future admin path. Detail string matches grant-consent
    # / PATCH / DELETE byte-for-byte (source-text-locked, Kanban #691).
    return await get_or_404(
        session,
        Project,
        detail=f"Project id={project_id} not found",
        id=project_id,
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
        # Kanban #777: pass-through for the two text fields (None is fine — DB column
        # is nullable). For agent_overrides, OMIT the key when None so the ORM's
        # Python-side `default=dict` fires (DB server_default '{}'::jsonb is the safety
        # net). Without this branch, Project(agent_overrides=None) would explicitly
        # INSERT NULL, bypassing both defaults.
        "working_path": payload.working_path,
        "working_repo": payload.working_repo,
    }
    if payload.agent_overrides is not None:
        data["agent_overrides"] = payload.agent_overrides

    # Kanban #694, Phase 2: `is_active` is a free boolean — no atomic-clear of
    # other rows. The legacy `_clear_other_active(keep_id=None)` here was
    # load-bearing on the dropped `ux_projects_active_one` invariant.
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

    # Kanban #777 WARN-1: PATCH explicit-null on agent_overrides means "clear to
    # empty dict", NOT "write SQL NULL". The server_default '{}'::jsonb fires only
    # on INSERT, so without this transform a null-PATCH would land JSONB scalar
    # 'null' in the column (Pydantic surfaces it as None on read). Locked by
    # test_patch_project_agent_overrides_null_clears_to_empty_dict.
    if "agent_overrides" in updates and updates["agent_overrides"] is None:
        updates["agent_overrides"] = {}

    # M10: cannot reactivate a soft-deleted project via PATCH — restore is a
    # separate (not-yet-built) admin path. Other fields ARE editable on a
    # soft-deleted row (admin edit / metadata correction).
    if updates.get("is_active") is True and project.status == RecordStatus.DELETED:
        raise HTTPException(
            status_code=400,
            detail="Cannot activate a soft-deleted project — restore first",
        )

    # Kanban #694, Phase 2: setting `is_active=true` no longer clears other
    # rows' is_active. Multiple rows may legitimately be active simultaneously
    # under session-scoped binding. The atomic-clear was load-bearing on the
    # dropped `ux_projects_active_one` invariant.

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


@router.post("/{project_id}/grant-consent", response_model=ProjectRead)
async def grant_project_consent(
    project_id: int,
    payload: ProjectGrantConsent,
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Grant per-project consent for Mode B (auto_headless) tasks (Kanban #481/#483).

    Typed-acknowledgment UX — the user must type the project name verbatim
    (case-sensitive). Idempotent: re-granting on an already-consented project
    returns 200 + the existing row WITHOUT re-stamping `auto_run_consent_at`
    or `updated_at`. The first consent is the legally-significant timestamp.

    404 on missing/soft-deleted project (active-only — `status=1`).
    400 on `confirm_name` mismatch — detail string pinned by source-text-lock
    test in test_routes_smoke.py.
    """
    project = await get_or_404(
        session,
        Project,
        detail=f"Project id={project_id} not found",
        id=project_id,
        status=RecordStatus.ACTIVE,
    )

    # Case-sensitive exact match — the friction is the point. Stable detail
    # string per #122 source-text-lock pattern.
    if payload.confirm_name != project.name:
        raise HTTPException(
            status_code=400,
            detail="confirm_name must match project name exactly",
        )

    # Idempotent re-grant: return the existing row untouched.
    if project.auto_run_consent_at is not None:
        return project

    # First grant — stamp consent + force updated_at refresh (server_default
    # only fires on INSERT; mirror the PATCH /api/projects/{id} pattern).
    project.auto_run_consent_at = func.now()
    project.updated_at = func.now()
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete a project: flip status=0 and defensively clear is_active.

    A soft-deleted row should not advertise itself as active in any list/by-name
    query. Returns 204 No Content. Idempotent — deleting an already-deleted
    project is a no-op (still 204).
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
        # Defensive cleanup: same transaction keeps the row consistent for any
        # concurrent reader (no window where status=0 AND is_active=true).
        project.is_active = False

    # Force `updated_at` to refresh — server_default only fires on INSERT.
    project.updated_at = func.now()

    await session.commit()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
