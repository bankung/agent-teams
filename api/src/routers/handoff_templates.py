"""Handoff templates CRUD router (Kanban #1004 AC4).

Mounted at `/api/handoff-templates`. Operators create reusable handoff
templates here; when a task carrying `handoff_template_id` flips to DONE,
the router spawn hook (`services/handoff_spawn.py`) builds a child task
from the named template.

Project scoping:
  - Template `project_id` IS NULL → global (cross-project) template.
  - Template `project_id` IS NOT NULL → scoped to that project; only that
    project's `X-Project-Id` header may CRUD it.

Listing semantics (GET):
  - With `X-Project-Id` header → return GLOBAL + that-project's templates.
  - Without header → return GLOBAL templates only.
  - `?project_id=N` query param overrides the header (operator power-user
    surface for cross-project discovery).
  - `?include_deleted=true` opts in to soft-deleted rows.

Soft-delete: parity with tasks / projects — `DELETE /api/handoff-templates/{id}`
flips `status=0`. The partial unique index `ux_handoff_templates_name_project`
is gated on `status=1` so re-creating a name after soft-delete is legal.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.handoff_template import HandoffTemplate
from src.schemas.handoff_template import (
    HandoffTemplateCreate,
    HandoffTemplateRead,
    HandoffTemplateUpdate,
)

router = APIRouter(prefix="/handoff-templates", tags=["handoff-templates"])

logger = logging.getLogger(__name__)


@router.get("", response_model=list[HandoffTemplateRead])
async def list_handoff_templates(
    project_id: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Filter to global + this-project templates. When provided this "
            "overrides the X-Project-Id header (power-user surface)."
        ),
    ),
    x_project_id: int | None = Header(default=None, alias="X-Project-Id"),
    include_deleted: bool = Query(
        default=False,
        description="If true, include soft-deleted (status=0) rows. Debug-only.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[HandoffTemplate]:
    """List handoff templates.

    Returns global templates (project_id IS NULL) plus the per-project ones
    scoped to the effective project (header / query param). With no project
    context, returns only globals.

    Ordering: `id ASC` for stable pagination.
    """
    effective_pid = project_id if project_id is not None else x_project_id

    stmt = select(HandoffTemplate)
    if not include_deleted:
        stmt = stmt.where(HandoffTemplate.status == RecordStatus.ACTIVE)
    if effective_pid is not None:
        stmt = stmt.where(
            or_(
                HandoffTemplate.project_id.is_(None),
                HandoffTemplate.project_id == effective_pid,
            )
        )
    else:
        stmt = stmt.where(HandoffTemplate.project_id.is_(None))
    stmt = stmt.order_by(HandoffTemplate.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{template_id}", response_model=HandoffTemplateRead)
async def get_handoff_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),
) -> HandoffTemplate:
    """Detail endpoint — returns the row regardless of soft-delete status
    (parity with the soft-delete detail convention; caller already has the id).
    """
    return await get_or_404(
        session,
        HandoffTemplate,
        detail=f"HandoffTemplate id={template_id} not found",
        id=template_id,
    )


@router.post(
    "",
    response_model=HandoffTemplateRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_handoff_template(
    payload: HandoffTemplateCreate,
    x_project_id: int | None = Header(default=None, alias="X-Project-Id"),
    session: AsyncSession = Depends(get_session),
) -> HandoffTemplate:
    """Create a handoff template.

    Project scope resolution:
      - If `payload.project_id` is set → that wins (operator power-user surface).
      - Else if `X-Project-Id` header is set → scope to that project.
      - Else → create as a GLOBAL template (project_id NULL).

    To CREATE a project-scoped template, the operator MUST supply either
    the body field or the header. To CREATE a global, omit both.

    Errors:
    - 409 — unique-name violation (per-project namespace).
    - 400 — FK violation (project_id does not exist).
    - 422 — Pydantic validation (handled by FastAPI default).
    """
    resolved_project_id = (
        payload.project_id if payload.project_id is not None else x_project_id
    )

    template = HandoffTemplate(
        name=payload.name,
        description=payload.description,
        title_pattern=payload.title_pattern,
        task_kind=payload.task_kind,
        task_type=payload.task_type,
        default_priority=payload.default_priority,
        default_assigned_role=payload.default_assigned_role,
        ac_outline=list(payload.ac_outline),
        carry_context_to_comment=payload.carry_context_to_comment,
        project_id=resolved_project_id,
    )
    session.add(template)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "ux_handoff_templates_name_project" in orig_text:
            scope = (
                f"project_id={resolved_project_id}"
                if resolved_project_id is not None
                else "global"
            )
            detail = (
                f"HandoffTemplate name={payload.name!r} already exists in {scope}"
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        if "handoff_templates_project_id_fkey" in orig_text:
            raise HTTPException(
                status_code=400,
                detail=f"project_id {resolved_project_id} does not exist",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="HandoffTemplate creation violates a database constraint",
        ) from exc

    await session.refresh(template)
    return template


@router.patch("/{template_id}", response_model=HandoffTemplateRead)
async def update_handoff_template(
    template_id: int,
    payload: HandoffTemplateUpdate,
    session: AsyncSession = Depends(get_session),
) -> HandoffTemplate:
    """Partial update.

    Excludes `project_id` from the surface — re-scoping a template between
    projects is intentionally NOT supported (consumers would be surprised).
    Soft-delete is via DELETE, not PATCH `status=0`.

    Errors:
    - 404 — template id not found.
    - 409 — name conflict on rename.
    """
    template = await get_or_404(
        session,
        HandoffTemplate,
        detail=f"HandoffTemplate id={template_id} not found",
        id=template_id,
    )

    updates = payload.model_dump(exclude_unset=True)

    # No-op skip + updated_at bump pattern (mirrors tasks / projects PATCH).
    changed = False
    for field, value in updates.items():
        if getattr(template, field) != value:
            setattr(template, field, value)
            changed = True
    if changed:
        template.updated_at = func.now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "ux_handoff_templates_name_project" in orig_text:
            raise HTTPException(
                status_code=409,
                detail="HandoffTemplate name conflicts with an existing row",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="HandoffTemplate update violates a database constraint",
        ) from exc

    await session.refresh(template)
    return template


@router.delete(
    "/{template_id}", status_code=http_status.HTTP_204_NO_CONTENT
)
async def delete_handoff_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete — flips `status=0`. Idempotent: subsequent DELETEs return 204
    without bumping `updated_at` (mirrors tasks / projects DELETE).
    """
    template = await get_or_404(
        session,
        HandoffTemplate,
        detail=f"HandoffTemplate id={template_id} not found",
        id=template_id,
    )

    if template.status == RecordStatus.ACTIVE:
        template.status = RecordStatus.DELETED
        template.updated_at = func.now()
        await session.commit()

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
