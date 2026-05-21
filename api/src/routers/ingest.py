"""Email-to-task ingest webhook (Kanban #1327 M4a).

Single endpoint:

  POST /api/ingest/email

The operator does NOT run a mail server. The flow is:

  1. Customer email lands at the operator's existing mailbox (Gmail/Workspace/
     domain).
  2. Operator's mailbox forwards to a dedicated address — ``inbox@<domain>`` or
     ``inbox+<projectname>@<domain>`` (the +tag form is the per-project router).
  3. A forwarding service (Cloudflare Email Routing + Worker by default; or
     Mailgun Routes / SendGrid Inbound Parse) receives the message + POSTs a
     Mailgun-shape JSON body to this endpoint with the shared-secret header
     ``X-Email-Ingest-Secret``.
  4. (This module) Authenticates via ``hmac.compare_digest`` against the secret
     stored in the M3 vault under a fixed credential
     (project_id=``EMAIL_INGEST_SECRET_PROJECT_ID``, default=1 / agent-teams;
     name=``email_ingest_shared_secret``), parses the payload, routes to a
     target project, creates a task, persists attachments to disk.

See ``context/standards/integrations/email-ingest-setup.md`` once promoted
from ``_scratch/standards-draft-email-ingest-setup.md`` for the operator's
DNS / Worker / vault setup walkthrough.

Security:
  - 401 detail is always the operator-actionable hint when the secret is
    unconfigured; a static ``"invalid signature"`` on header mismatch (no
    oracle leak). Plaintext secrets are NEVER logged.
  - Attachment write target is anchored at ``project.working_path`` when set
    (lives outside the agent-teams repo for non-agent-teams projects) or
    ``<repo_root>/_runtime/email_attachments`` for fallback.
  - Filename sanitization strips ``/`` ``\\`` ``..`` and replaces unsafe chars
    with ``_`` before joining.

Out of scope (deferred):
  - Outbound email (reply-to-task → email customer)
  - Spam filtering (forwarder handles)
  - Signature stripping (downstream agent's job)
  - Calendar invite parsing
  - Newsletter / mailing-list mgmt
"""

from __future__ import annotations

import base64
import binascii
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import (
    RecordStatus,
    TaskKind,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.db import get_session
from src.models.credential import CredentialAccessLog, ProjectCredential
from src.models.task import Task
from src.schemas.email_ingest import EmailIngestRequest, EmailIngestResponse
from src.services import credentials_crypto
from src.services.email_ingest import (
    extract_body,
    resolve_attachment_path,
    resolve_target_project,
)
from src.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


# Fixed vault credential name — operator POSTs the secret once via
# /api/projects/{id}/credentials with name='email_ingest_shared_secret'.
EMAIL_INGEST_SECRET_NAME: Final[str] = "email_ingest_shared_secret"

# Default fallback project name when the inbound ``to`` carries no
# ``inbox+<tag>@`` form. Overridable via env EMAIL_INGEST_DEFAULT_PROJECT.
_DEFAULT_PROJECT_ENV: Final[str] = "EMAIL_INGEST_DEFAULT_PROJECT"
_DEFAULT_PROJECT_FALLBACK: Final[str] = "agent-teams"

# Project under which the shared secret credential lives. Default = 1
# (agent-teams). Overridable so a non-agent-teams operator can pin the
# secret to their primary project.
_SECRET_PROJECT_ID_ENV: Final[str] = "EMAIL_INGEST_SECRET_PROJECT_ID"
_SECRET_PROJECT_ID_FALLBACK: Final[int] = 1

# Default task_kind for ingested email tasks — 'human' by default since
# inbound customer email always needs operator triage before any agent
# touches it. Overridable via env.
_DEFAULT_TASK_KIND_ENV: Final[str] = "EMAIL_INGEST_DEFAULT_TASK_KIND"
_DEFAULT_TASK_KIND_FALLBACK: Final[str] = TaskKind.HUMAN

# Default priority — NORMAL (2) so the operator sees it on the board without
# the URGENT noise.
_DEFAULT_PRIORITY_ENV: Final[str] = "EMAIL_INGEST_DEFAULT_PRIORITY"
_DEFAULT_PRIORITY_FALLBACK: Final[int] = TaskPriority.NORMAL

# Per-attachment max size — 25 MB. Matches Gmail / Mailgun common limit.
ATTACHMENT_MAX_BYTES: Final[int] = 25 * 1024 * 1024

# Source-text-locked detail strings. Pinned by tests.
_DETAIL_BAD_SIGNATURE: Final[str] = "invalid signature"
_DETAIL_SECRET_NOT_CONFIGURED: Final[str] = (
    "email ingest secret not configured — operator must POST a credential "
    "via /api/projects/<id>/credentials with name='email_ingest_shared_secret' "
    "and kind='webhook_secret'"
)


def _env_str(name: str, fallback: str) -> str:
    """Read an env var as str with a fallback. Empty string treated as unset."""
    v = os.environ.get(name)
    return v if v else fallback


def _env_int(name: str, fallback: int) -> int:
    """Read an env var as int with a fallback. Invalid value → fallback + log."""
    raw = os.environ.get(name)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "email_ingest: env %s=%r is not a valid int; using fallback %d",
            name, raw, fallback,
        )
        return fallback


async def _load_secret(session: AsyncSession) -> tuple[ProjectCredential, str]:
    """Look up the shared-secret credential + decrypt it.

    Raises HTTPException(401) when the credential row is absent (or soft-
    deleted) — there is no audit row to write because there is no credential
    to log against. The error detail names the operator's recovery step.
    """
    project_id = _env_int(_SECRET_PROJECT_ID_ENV, _SECRET_PROJECT_ID_FALLBACK)
    stmt = (
        select(ProjectCredential)
        .where(ProjectCredential.project_id == project_id)
        .where(ProjectCredential.name == EMAIL_INGEST_SECRET_NAME)
        .where(ProjectCredential.status == RecordStatus.ACTIVE)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=401, detail=_DETAIL_SECRET_NOT_CONFIGURED,
        )
    secret = credentials_crypto.decrypt(cred.ciphertext)
    return cred, secret


async def _audit_use(
    session: AsyncSession, credential_id: int, *, ok: bool, reason: str | None
) -> None:
    """Append a CredentialAccessLog row recording the verification outcome.

    ``ok=True`` writes ``accessed_by='system:email_ingest'`` + bumps the
    counter on the credential. ``ok=False`` writes
    ``accessed_by='system:email_ingest (denied=<reason>)'`` and skips the
    counter bump — denial audit row stands on its own.
    """
    if ok:
        accessed_by = "system:email_ingest"
    else:
        accessed_by = f"system:email_ingest (denied={reason or 'unknown'})"
    session.add(
        CredentialAccessLog(
            credential_id=credential_id,
            accessed_by=accessed_by,
            action="use",
        )
    )
    if ok:
        cred = await session.get(ProjectCredential, credential_id)
        if cred is not None:
            cred.access_count = cred.access_count + 1
            cred.last_accessed_at = datetime.now(timezone.utc)
    await session.commit()


def _build_description(
    req: EmailIngestRequest,
    body: str,
    attachments_lines: list[str],
    skipped_lines: list[str],
) -> str:
    """Assemble the task description from the email metadata + body +
    attachment manifest. Keeps a stable header so a downstream agent can
    parse the metadata block.
    """
    ts_repr = req.timestamp.isoformat() if isinstance(req.timestamp, datetime) else "(none)"
    header = (
        f"From: {req.from_address}\n"
        f"To: {req.to}\n"
        f"Date: {ts_repr}\n"
        f"Message-Id: {req.message_id or '(none)'}\n"
        f"---\n"
        f"{body}"
    )
    if attachments_lines:
        header += (
            f"\n\n--- Attachments (n={len(attachments_lines)}) ---\n"
            + "\n".join(attachments_lines)
        )
    if skipped_lines:
        header += "\n\n--- Skipped attachments ---\n" + "\n".join(skipped_lines)
    return header


@router.post(
    "/email",
    response_model=EmailIngestResponse,
    status_code=200,
)
async def ingest_email(
    payload: EmailIngestRequest,
    x_email_ingest_secret: Annotated[
        str | None, Header(alias="X-Email-Ingest-Secret")
    ] = None,
    session: AsyncSession = Depends(get_session),
) -> EmailIngestResponse:
    """Authenticate + route + create-a-task for one inbound email.

    Returns 200 with ``{received, task_id, project_id, attachment_count}`` on
    success.

    Errors:
      - 401 (``"invalid signature"``) — header missing or mismatched. Audit
        row written with the denial reason.
      - 401 (``email ingest secret not configured ...``) — vault credential
        absent. No audit row (nothing to log against).
      - 404 (``"target project not found — ..."``) — both the
        ``inbox+<tag>@`` lookup AND the default-project lookup miss.
      - 413 — single attachment exceeds ``ATTACHMENT_MAX_BYTES`` (25 MB) AFTER
        decoding. The task is still created; only the oversized attachment is
        skipped (flagged in the description).  We choose 413 for a payload
        with NO acceptable attachments only when the inbound has exactly one
        attachment which is oversized AND the body is empty — see comment
        below. The current contract is: create the task, skip the oversized
        bits, return 200.
      - 422 — Pydantic body validation (missing ``to`` / ``subject`` / etc).
    """
    settings = get_settings()
    repo_root = settings.repo_root

    # ----- 1. Authenticate -------------------------------------------------
    cred, secret = await _load_secret(session)

    # hmac.compare_digest is constant-time on the byte representation.
    provided = (x_email_ingest_secret or "").encode("utf-8")
    expected = secret.encode("utf-8")
    if not hmac.compare_digest(provided, expected):
        await _audit_use(session, cred.id, ok=False, reason="signature_mismatch")
        raise HTTPException(status_code=401, detail=_DETAIL_BAD_SIGNATURE)
    await _audit_use(session, cred.id, ok=True, reason=None)

    # ----- 2. Resolve target project --------------------------------------
    default_name = _env_str(_DEFAULT_PROJECT_ENV, _DEFAULT_PROJECT_FALLBACK)
    project = await resolve_target_project(session, payload.to, default_name)

    # ----- 3. Build task fields (title + description + defaults) ----------
    title = payload.subject[:200] if payload.subject else "(no subject)"
    body = extract_body(payload)

    # Defaults are env-driven; fall back to module constants.
    raw_task_kind = _env_str(_DEFAULT_TASK_KIND_ENV, _DEFAULT_TASK_KIND_FALLBACK)
    # Defensive: a typo'd env value would otherwise CHECK-violate at INSERT
    # time. Coerce unknown → human (the safe default for inbound customer mail).
    if raw_task_kind not in TaskKind.ALL:
        logger.warning(
            "email_ingest: %s=%r is not a valid task_kind; using 'human'",
            _DEFAULT_TASK_KIND_ENV, raw_task_kind,
        )
        raw_task_kind = TaskKind.HUMAN
    task_priority = _env_int(_DEFAULT_PRIORITY_ENV, _DEFAULT_PRIORITY_FALLBACK)
    if task_priority not in TaskPriority.ALL:
        logger.warning(
            "email_ingest: %s=%d is not in TaskPriority.ALL; using NORMAL",
            _DEFAULT_PRIORITY_ENV, task_priority,
        )
        task_priority = TaskPriority.NORMAL

    # ----- 4. Insert the task (assign id via flush BEFORE attachments) ----
    task = Task(
        project_id=project.id,
        title=title,
        description=body,  # placeholder; finalized after attachments
        process_status=TaskStatus.TODO,
        priority=task_priority,
        task_kind=raw_task_kind,
        task_type=TaskType.FEATURE,
        # No assigned_role — operator triages later.
        # acceptance_criteria stays NULL — no structured AC for inbound mail.
    )
    session.add(task)
    await session.flush()  # populates task.id without committing yet
    task_id = task.id

    # ----- 5. Persist attachments to disk + assemble manifest --------------
    accepted_lines: list[str] = []
    skipped_lines: list[str] = []
    attachment_count = 0

    for att in payload.attachments:
        try:
            raw_bytes = base64.b64decode(att.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.warning(
                "email_ingest: attachment %r base64 decode failed: %s",
                att.filename, exc,
            )
            skipped_lines.append(
                f"- {att.filename}: base64 decode failed ({exc})"
            )
            continue

        # NEVER trust the payload's size_bytes — recompute from decoded len.
        actual_size = len(raw_bytes)
        if actual_size > ATTACHMENT_MAX_BYTES:
            logger.info(
                "email_ingest: attachment %r is %d bytes (>%d cap) — skipping",
                att.filename, actual_size, ATTACHMENT_MAX_BYTES,
            )
            skipped_lines.append(
                f"- Attachment skipped (>25MB): {att.filename} "
                f"({actual_size} bytes, {att.content_type})"
            )
            continue

        target = resolve_attachment_path(project, task_id, att.filename, repo_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(raw_bytes)
        except OSError as exc:
            logger.warning(
                "email_ingest: attachment %r write to %s failed: %s",
                att.filename, target, exc,
            )
            skipped_lines.append(
                f"- {att.filename}: disk write failed ({exc})"
            )
            continue

        accepted_lines.append(
            f"- {att.filename} ({att.content_type}, {actual_size} bytes) "
            f"-> {target} -- pending #1309 resource registration"
        )
        attachment_count += 1

    # ----- 6. Finalize description + commit --------------------------------
    task.description = _build_description(
        payload, body, accepted_lines, skipped_lines
    )
    await session.commit()
    await session.refresh(task)

    logger.info(
        "email_ingest: created task #%d in project #%d (%s) "
        "from=%r subject=%r attachments=%d (skipped=%d)",
        task.id, project.id, project.name,
        payload.from_address, title, attachment_count, len(skipped_lines),
    )

    return EmailIngestResponse(
        received=True,
        task_id=task.id,
        project_id=project.id,
        attachment_count=attachment_count,
    )
