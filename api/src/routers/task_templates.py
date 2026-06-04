"""Task-template CRUD router (Kanban #1303).

Mounted at `/api/task-templates`. A task template is a per-TEAM reusable
Kanban-task starting point — a name/icon + a mustache-style ({{placeholder}})
description + an AC template array + default task metadata. It is a GLOBAL config
table keyed by `team`; it is NOT project-scoped, so these routes take NO
`X-Project-Id` header (parity with the global teams / templates / dashboard
routers).

TEAM / DEFAULT-ENUM VALIDATION (#1620 doctrine): `team`, `default_task_type`,
and `default_task_kind` are validated APP-SIDE against the single-source
constants (`ProjectTeam.ALL` / `TaskType.ALL` / `TaskKind.ALL`) — NO DB CHECK.
A precise 422 lists the valid values (mirror of `routers/projects.py`).

OPERATOR GATE (#1857): POST + PATCH + DELETE are wired through
`require_operator_proof`. The gate is fail-OPEN (dormant) until
`OPERATOR_ACTION_KEY` is set in the api container's env, so these routes land
without breaking the running app — exactly like the #1859 email-tier gate and
the #1275 tasks PATCH gate. GET endpoints are ungated (read-only).

SOFT-DELETE: the list endpoint default-filters `WHERE status=1` (active);
`?include_disabled=true` returns disabled rows too. DELETE flips `status=0`
(soft-delete, mirror of the resources / milestones pattern). The detail endpoint
returns the row regardless of status.

`updated_at` is set EXPLICITLY by the PATCH handler (no DB trigger) — NULL until
the first edit (#1303 spec).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.constants import (
    ProjectTeam,
    RecordStatus,
    TaskKind,
    TaskType,
)
from src.db import get_or_404, get_session
from src.models.task_template import TaskTemplate
from src.schemas.task_template import (
    TaskTemplateCreate,
    TaskTemplateRead,
    TaskTemplateUpdate,
)
from src.services.operator_auth import OperatorDecision, require_operator_proof

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/task-templates", tags=["task-templates"])

# Source-text-locked detail string for the operator-proof 403 (parity with the
# tasks PATCH gate _DETAIL_OPERATOR_PROOF_REQUIRED). Pinned by the smoke test.
_DETAIL_OPERATOR_PROOF_REQUIRED = (
    "operator_proof_required: creating/editing/deleting a task template is "
    "operator-only"
)


def _require_operator(operator_proof: OperatorDecision) -> None:
    """Raise 403 when the request is not backed by a valid operator proof.

    No-op when the gate is INACTIVE (`require_operator_proof` returns OPERATOR
    for any request while OPERATOR_ACTION_KEY is unset), so write routes stay
    functional on the live deployment until the operator activates the gate.
    """
    if operator_proof is not OperatorDecision.OPERATOR:
        raise HTTPException(
            status_code=403, detail=_DETAIL_OPERATOR_PROOF_REQUIRED
        )


def _validate_enums(
    *,
    team: str | None,
    default_task_type: str | None,
    default_task_kind: str | None,
) -> None:
    """Validate the app-side enum fields against the single-source constants.

    Each is checked only when present (None = "not supplied on this PATCH" /
    "use the schema default already applied"). Precise 422 listing valid values,
    mirror of routers/projects.py::create_project. NO DB CHECK backs these.
    """
    if team is not None and team not in ProjectTeam.ALL:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown team {team!r}; valid: {sorted(ProjectTeam.ALL)}",
        )
    if default_task_type is not None and default_task_type not in TaskType.ALL:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown default_task_type {default_task_type!r}; "
                f"valid: {sorted(TaskType.ALL)}"
            ),
        )
    if default_task_kind is not None and default_task_kind not in TaskKind.ALL:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown default_task_kind {default_task_kind!r}; "
                f"valid: {sorted(TaskKind.ALL)}"
            ),
        )


@router.get("", response_model=list[TaskTemplateRead])
async def list_task_templates(
    team: str | None = Query(
        default=None,
        description=(
            "Filter to one team's templates (validated against the team "
            "registry; 422 on unknown). When omitted, returns templates across "
            "all teams."
        ),
    ),
    include_disabled: bool = Query(
        default=False,
        description="If true, include disabled (status=0) templates too.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[TaskTemplate]:
    """List task templates (active-only by default).

    Default returns only `status=1` (active) rows; `?include_disabled=true`
    includes disabled rows. The optional `?team=` filter is validated against the
    team registry (422 on unknown — fail fast rather than silently returning
    `[]`). Ordering: `team ASC, name ASC, id ASC` for a stable picker list.
    """
    if team is not None and team not in ProjectTeam.ALL:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown team {team!r}; valid: {sorted(ProjectTeam.ALL)}",
        )

    stmt = select(TaskTemplate)
    if team is not None:
        stmt = stmt.where(TaskTemplate.team == team)
    if not include_disabled:
        stmt = stmt.where(TaskTemplate.status == RecordStatus.ACTIVE)
    stmt = (
        stmt.order_by(
            TaskTemplate.team.asc(),
            TaskTemplate.name.asc(),
            TaskTemplate.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{template_id}", response_model=TaskTemplateRead)
async def get_task_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),
) -> TaskTemplate:
    """Full template (incl. raw description + AC template). 404 if missing.

    Returns the row regardless of soft-delete status (the caller has the id).
    """
    return await get_or_404(
        session,
        TaskTemplate,
        detail=f"Task template id={template_id} not found",
        id=template_id,
    )


@router.post(
    "", response_model=TaskTemplateRead, status_code=http_status.HTTP_201_CREATED
)
async def create_task_template(
    payload: TaskTemplateCreate,
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> TaskTemplate:
    """Create a task template (operator-gated).

    Errors:
    - 403 — operator-proof gate active and no valid X-Operator-Token.
    - 422 — unknown team / default_task_type / default_task_kind, or Pydantic
            validation (empty name/description, malformed AC items).
    """
    _require_operator(operator_proof)
    _validate_enums(
        team=payload.team,
        default_task_type=payload.default_task_type,
        default_task_kind=payload.default_task_kind,
    )

    template = TaskTemplate(
        team=payload.team,
        name=payload.name,
        icon=payload.icon,
        description_template=payload.description_template,
        acceptance_criteria_template=payload.acceptance_criteria_template,
        default_task_type=payload.default_task_type,
        default_priority=payload.default_priority,
        default_task_kind=payload.default_task_kind,
        placeholders=payload.placeholders,
    )
    session.add(template)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Task template creation violates a database constraint",
        ) from exc

    await session.refresh(template)
    return template


@router.patch("/{template_id}", response_model=TaskTemplateRead)
async def update_task_template(
    template_id: int,
    payload: TaskTemplateUpdate,
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> TaskTemplate:
    """Partial update (operator-gated). Toggle `status`, edit text, re-assign team.

    Sets `updated_at` explicitly (no DB trigger). Errors:
    - 403 — operator-proof gate active and no valid X-Operator-Token.
    - 404 — template not found.
    - 422 — unknown team / default_task_type / default_task_kind, or Pydantic.
    """
    _require_operator(operator_proof)
    template = await get_or_404(
        session,
        TaskTemplate,
        detail=f"Task template id={template_id} not found",
        id=template_id,
    )

    updates = payload.model_dump(exclude_unset=True)

    # Re-validate the enum fields only when present in this PATCH.
    _validate_enums(
        team=updates.get("team"),
        default_task_type=updates.get("default_task_type"),
        default_task_kind=updates.get("default_task_kind"),
    )

    # No-op skip + explicit updated_at bump (mirrors milestones / projects PATCH).
    # No ClauseElement values are ever assigned here, so a plain `!=` is safe.
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
        raise HTTPException(
            status_code=400,
            detail="Task template update violates a database constraint",
        ) from exc

    await session.refresh(template)
    return template


@router.delete("/{template_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_task_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),
    operator_proof: OperatorDecision = Depends(require_operator_proof),
) -> Response:
    """Soft-delete a task template: flip `status=0` (operator-gated).

    Idempotent: deleting an already-disabled template is a no-op (still 204).
    Returns 204 No Content. Errors: 403 (gate active, no proof), 404 (missing).
    """
    _require_operator(operator_proof)
    template = await get_or_404(
        session,
        TaskTemplate,
        detail=f"Task template id={template_id} not found",
        id=template_id,
    )

    if template.status == RecordStatus.ACTIVE:
        template.status = RecordStatus.DELETED
        template.updated_at = func.now()
        await session.commit()

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
