"""Credentials vault router — per-project, Fernet-encrypted (Kanban #1326 M3).

Mounted at `/api/projects/{project_id}/credentials` (mirrors the per-project
path convention from `pl.py`). Every endpoint requires the X-Project-Id header
to match the path's `{project_id}` — mismatch surfaces as 404 ("invisible" from
the bound session's view, parity with the P&L endpoint).

Endpoints:

  - POST   /api/projects/{project_id}/credentials              — create
  - GET    /api/projects/{project_id}/credentials              — list (active only by default)
  - PATCH  /api/projects/{project_id}/credentials/{name}       — update value/metadata
  - DELETE /api/projects/{project_id}/credentials/{name}       — soft-delete
  - POST   /api/projects/{project_id}/credentials/{name}/use   — HITL-gated plaintext retrieval

Security model:
  - PLAINTEXT VALUES ARE NEVER LOGGED.
  - PLAINTEXT VALUES NEVER APPEAR IN CredentialRead (response_model strips).
  - PLAINTEXT VALUES ONLY APPEAR IN CredentialUseResponse (single endpoint).
  - The /use endpoint is gated by the project's `approval_policies` JSONB.
    Matching rule with `auto_approve=true` → grant. Otherwise 403. The richer
    HITL approval flow is deferred (M3 returns 403 with an explicit hint).

Audit:
  - Every create / update / delete / use lands a row in `credential_access_log`.
  - Failed (denied) /use attempts also land a row with `accessed_by` carrying
    the denial reason — the audit trail covers both grants AND refusals.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.constants import RecordStatus
from src.db import get_or_404, get_session
from src.models.credential import CredentialAccessLog, ProjectCredential
from src.models.project import Project
from src.schemas.credential import (
    CredentialCreate,
    CredentialRead,
    CredentialUpdate,
    CredentialUseRequest,
    CredentialUseResponse,
)
from src.services.credentials_crypto import decrypt, encrypt
from src.services.session_project import require_project_id_header

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects-credentials"])


# Source-text-locked detail string — pinned by test_credentials_router.py.
_DETAIL_PROJECT_NOT_FOUND_TEMPLATE = "Project id={project_id} not found"
_DETAIL_CREDENTIAL_NOT_FOUND_TEMPLATE = "Credential {name!r} not found in project {project_id}"
_DETAIL_USE_DENIED = (
    "Credential use requires explicit approval. policy=not_matched. "
    "HITL approval flow not implemented in M3 (deferred); operator must add "
    "an approval_policies entry to project to grant use."
)


# ---------------------------------------------------------------------------
# X-Agent-Identity header sanitisation
# ---------------------------------------------------------------------------

_X_AGENT_IDENTITY_RE = re.compile(r"^[a-zA-Z0-9_:@/.\-]{1,100}$")


def _sanitize_agent_identity(raw: str | None) -> str:
    """Validate the X-Agent-Identity header value; coerce invalid input;
    prefix the stored audit string to mark it header-supplied.

    Returns 'header:<value>' for valid input, 'header:invalid_header' for
    missing / over-length / pattern-mismatch input. The prefix lets audit
    consumers tell header-supplied identity apart from system-derived identities
    (which use bare strings like 'operator:api' or 'system:webhook').

    None (absent header) returns the bare 'operator:api' fallback — the no-header
    path is a legitimate direct-operator call and carries no taint.
    """
    if raw is None:
        return "operator:api"
    if not _X_AGENT_IDENTITY_RE.match(raw):
        return "header:invalid_header"
    return f"header:{raw}"


def _assert_project_match(path_project_id: int, session_project_id: int) -> None:
    """Cross-project access (header != path) surfaces as 404, parity with /pl."""
    if path_project_id != session_project_id:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=path_project_id),
        )


async def _resolve_project(
    session: AsyncSession, project_id: int
) -> Project:
    """Fetch project or 404 with the standard detail string."""
    return await get_or_404(
        session,
        Project,
        detail=_DETAIL_PROJECT_NOT_FOUND_TEMPLATE.format(project_id=project_id),
        id=project_id,
    )


def _policy_grants_use(
    approval_policies: dict | list | None, credential_name: str
) -> bool:
    """Return True iff the project has an explicit auto_approve rule for this
    credential.

    Accepts both shapes the JSONB column might carry:
      - the new credentials-use convention: a list of {"action": "credential.use",
        "credential_name": "<name>", "auto_approve": true}
      - the existing approval_policies dict shape ({"rules": [...]}) — we only
        match items inside `rules` that follow the same credentials-use shape.

    Anything else → no match → deny.
    """
    if not approval_policies:
        return False

    # Tolerate both `[...]` and `{"rules": [...]}` element shapes.
    if isinstance(approval_policies, list):
        candidates = approval_policies
    elif isinstance(approval_policies, dict):
        candidates = approval_policies.get("rules") or []
        # Some projects might store it as a flat dict; bail gracefully.
        if not isinstance(candidates, list):
            return False
    else:
        return False

    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("action") == "credential.use"
            and entry.get("credential_name") == credential_name
            and entry.get("auto_approve") is True
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# POST /api/projects/{project_id}/credentials — create
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/credentials",
    response_model=CredentialRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_credential(
    project_id: int,
    payload: CredentialCreate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> ProjectCredential:
    """Encrypt + insert a new credential. Logs action='create' in access log.

    Errors:
      - 404 — path/header project_id mismatch OR project does not exist.
      - 409 — name already exists in this project (UNIQUE violation).
      - 422 — Pydantic validation (kind invalid, value too long, etc.).
    """
    _assert_project_match(project_id, session_project_id)
    await _resolve_project(session, project_id)

    # Encrypt plaintext outside the transaction so we don't hold a DB lock
    # across the (cheap but non-trivial) Fernet call.
    ciphertext = encrypt(payload.value)

    cred = ProjectCredential(
        project_id=project_id,
        name=payload.name,
        kind=payload.kind,
        ciphertext=ciphertext,
        meta=payload.meta,
        status=RecordStatus.ACTIVE,
    )
    session.add(cred)
    try:
        # Flush so we get cred.id before adding the audit row.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        orig_text = str(exc.orig)
        if "ux_project_credentials_project_name" in orig_text:
            raise HTTPException(
                status_code=409,
                detail=f"Credential {payload.name!r} already exists in project {project_id}",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="Credential write violates a database constraint",
        ) from exc

    audit = CredentialAccessLog(
        credential_id=cred.id,
        accessed_by="operator:api",
        action="create",
    )
    session.add(audit)
    await session.commit()
    await session.refresh(cred)
    return cred


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/credentials — list (active by default)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/credentials",
    response_model=list[CredentialRead],
)
async def list_credentials(
    project_id: int,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectCredential]:
    """List active credentials for this project, newest first.

    Soft-deleted (status=0) rows are NEVER returned via this endpoint —
    operators can pg_dump the table directly if they need historical visibility.
    """
    _assert_project_match(project_id, session_project_id)
    await _resolve_project(session, project_id)

    stmt = (
        select(ProjectCredential)
        .where(ProjectCredential.project_id == project_id)
        .where(ProjectCredential.status == RecordStatus.ACTIVE)
        .order_by(ProjectCredential.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PATCH /api/projects/{project_id}/credentials/{name} — update
# ---------------------------------------------------------------------------


@router.patch(
    "/{project_id}/credentials/{name}",
    response_model=CredentialRead,
)
async def update_credential(
    project_id: int,
    name: str,
    payload: CredentialUpdate,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> ProjectCredential:
    """Partial update: re-encrypt value if supplied, replace metadata if supplied.

    Bumps `updated_at`. Logs action='update' in access log.
    """
    _assert_project_match(project_id, session_project_id)

    cred = await _get_active_credential_or_404(session, project_id, name)

    updates = payload.model_dump(exclude_unset=True, by_alias=False)
    if "value" in updates and updates["value"] is not None:
        cred.ciphertext = encrypt(updates["value"])
    if "meta" in updates:
        cred.meta = updates["meta"]

    cred.updated_at = func.now()

    audit = CredentialAccessLog(
        credential_id=cred.id,
        accessed_by="operator:api",
        action="update",
    )
    session.add(audit)
    await session.commit()
    await session.refresh(cred)
    return cred


# ---------------------------------------------------------------------------
# DELETE /api/projects/{project_id}/credentials/{name} — soft-delete
# ---------------------------------------------------------------------------


@router.delete(
    "/{project_id}/credentials/{name}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_credential(
    project_id: int,
    name: str,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft-delete (status=0). Subsequent GET/list hides the row; subsequent
    /use returns 404.

    Idempotency: double-DELETE returns 404 because soft-deleted rows are
    treated as "not found" from the wire perspective — the same as a never-
    existed name. This matches the project's stated AC ("Subsequent GET
    hides; subsequent /use returns 404").
    """
    _assert_project_match(project_id, session_project_id)

    cred = await _get_active_credential_or_404(session, project_id, name)
    cred.status = RecordStatus.DELETED
    cred.updated_at = func.now()

    audit = CredentialAccessLog(
        credential_id=cred.id,
        accessed_by="operator:api",
        action="delete",
    )
    session.add(audit)
    await session.commit()

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /api/projects/{project_id}/credentials/{name}/use — gated plaintext fetch
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/credentials/{name}/use",
    response_model=CredentialUseResponse,
)
async def use_credential(
    project_id: int,
    name: str,
    payload: CredentialUseRequest,
    session_project_id: int = Depends(require_project_id_header),
    x_agent_identity: Annotated[str | None, Header(alias="X-Agent-Identity")] = None,
    session: AsyncSession = Depends(get_session),
) -> CredentialUseResponse:
    """HITL-gated plaintext retrieval — the only endpoint that returns plaintext.

    Gating:
      - If `projects.approval_policies` carries an entry with
        `action='credential.use'`, `credential_name='{name}'`, `auto_approve=true`
        → grant. Log action='use', increment access_count, set last_accessed_at.
      - Otherwise → 403 with explicit hint pointing operator at the policy
        column. Log action='use' with `accessed_by` carrying the denial reason
        (audit trail covers refusals too).

    The `X-Agent-Identity` header (optional) flows into accessed_by; default is
    'operator:api'. The future HITL flow will accept a one-shot approval token
    here as a sibling header.
    """
    _assert_project_match(project_id, session_project_id)

    project = await _resolve_project(session, project_id)
    cred = await _get_active_credential_or_404(session, project_id, name)

    identity = _sanitize_agent_identity(x_agent_identity)

    if not _policy_grants_use(project.approval_policies, name):
        # Audit the refusal — the trail covers denied attempts too.
        denial_audit = CredentialAccessLog(
            credential_id=cred.id,
            accessed_by=f"{identity} (denied=policy_unmatched)",
            task_id=payload.task_id,
            action="use",
        )
        session.add(denial_audit)
        await session.commit()
        raise HTTPException(status_code=403, detail=_DETAIL_USE_DENIED)

    # Granted — decrypt + stamp usage + write audit row.
    plaintext = decrypt(cred.ciphertext)
    cred.last_accessed_at = func.now()
    cred.access_count = cred.access_count + 1

    audit = CredentialAccessLog(
        credential_id=cred.id,
        accessed_by=identity,
        task_id=payload.task_id,
        action="use",
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)

    return CredentialUseResponse(
        value=plaintext,
        credential_id=cred.id,
        access_log_id=audit.id,
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _get_active_credential_or_404(
    session: AsyncSession, project_id: int, name: str
) -> ProjectCredential:
    """Fetch an active (status=1) credential by (project_id, name) or 404.

    Soft-deleted rows are treated as not found. The UNIQUE index spans both
    states so we filter explicitly on `status=ACTIVE`.
    """
    stmt = (
        select(ProjectCredential)
        .where(ProjectCredential.project_id == project_id)
        .where(ProjectCredential.name == name)
        .where(ProjectCredential.status == RecordStatus.ACTIVE)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_CREDENTIAL_NOT_FOUND_TEMPLATE.format(
                name=name, project_id=project_id
            ),
        )
    return cred
