"""Inbound webhook ingest endpoints — email (#1327 M4a) + generic (#1328 M4b).

Endpoints:

  POST /api/ingest/email                          (M4a — Mailgun-shape email)
  POST /api/ingest/webhook/{project_id}/{tag}     (M4b — generic JSON webhook)

The M4b generic webhook complements M4a: external sources (Calendly, GitHub
Issues, contact forms, Typeform, etc.) POST arbitrary JSON to the per-project
endpoint with an ``X-Webhook-Secret`` header. Auth is the same M3-vault
shared-secret pattern; the credential name follows ``webhook_<tag>`` so each
external source gets its own rotatable secret. The body is run through a
named template (Mustache-flat) → a Task is created in the path-named project.
Tags with no registered template land in a default-fallback template that
dumps the full payload into the description, so operators always get a
usable task even pre-configuration.

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

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path
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
from src.models.project import Project
from src.schemas.email_ingest import EmailIngestRequest, EmailIngestResponse
from src.services import credentials_crypto
from src.services.email_ingest import (
    extract_body,
    resolve_attachment_path,
    resolve_target_project,
)
from src.services.webhook_rate_limit import (
    RateLimitError,
    check_and_consume as rate_limit_check_and_consume,
)
from src.services.webhook_templates import (
    DEFAULT_FALLBACK_TEMPLATE,
    MissingTemplateField,
    WebhookTemplate,
    get_template as get_webhook_template,
    pretty_dump_for_fallback,
    substitute as substitute_template,
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
    # Guard: a malformed vault entry (non-UTF-8 surrogate chars, etc.) or a
    # mis-encoded header value can cause .encode("utf-8") / .decode("utf-8")
    # to raise Unicode*Error.  Treat any encoding failure as a signature
    # mismatch — returning 401 rather than leaking a 500.  Reason string
    # "secret_encoding_error" is intentionally more specific than
    # "signature_mismatch" so the audit log can distinguish a corrupt vault
    # entry from a genuine bad-secret attempt without leaking anything to the
    # HTTP caller (both map to the same static "invalid signature" detail).
    try:
        provided = (x_email_ingest_secret or "").encode("utf-8")
        expected = secret.encode("utf-8")
        sig_ok = hmac.compare_digest(provided, expected)
    except (UnicodeEncodeError, UnicodeDecodeError):
        await _audit_use(session, cred.id, ok=False, reason="secret_encoding_error")
        raise HTTPException(status_code=401, detail=_DETAIL_BAD_SIGNATURE)
    if not sig_ok:
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


# ===========================================================================
# Kanban #1328 (M4b) — generic webhook-to-task ingest
# ===========================================================================
#
# POST /api/ingest/webhook/{project_id}/{tag}
#
# Auth:  X-Webhook-Secret header compared (constant-time) against the M3-vault
#        credential named ``webhook_<tag>`` scoped to the path's project_id.
# Body:  Arbitrary JSON. Passed verbatim to template substitution.
# Resp:  200 + {received, task_id, project_id, template_used, tag}
# Errors: 401 (no credential / bad secret), 404 (project not found),
#         422 (template references a missing field in the payload),
#         429 (per-(project, tag) rate cap hit).
#
# The template registry is in-code (``services/webhook_templates.py``) for v1;
# X.5 will swap it for a DB-backed loader without changing this router.


# Per-source webhook credential names follow this prefix — e.g. tag='calendly'
# means the operator stored the shared secret as `webhook_calendly`.
WEBHOOK_CREDENTIAL_NAME_PREFIX: Final[str] = "webhook_"

# Source-text-locked detail strings. Pinned by tests.
_DETAIL_WEBHOOK_SECRET_NOT_CONFIGURED_TEMPLATE: Final[str] = (
    "webhook secret not configured for this project — store via "
    "POST /api/projects/{project_id}/credentials with name='webhook_{tag}' "
    "and kind='webhook_secret'"
)
_DETAIL_WEBHOOK_BAD_SIGNATURE: Final[str] = "invalid signature"
_DETAIL_WEBHOOK_MISSING_FIELD_TEMPLATE: Final[str] = (
    "missing required template field: {field_path}"
)
_DETAIL_WEBHOOK_PROJECT_NOT_FOUND_TEMPLATE: Final[str] = (
    "Project id={project_id} not found"
)


async def _load_webhook_secret(
    session: AsyncSession, project_id: int, tag: str
) -> tuple[ProjectCredential, str]:
    """Look up the per-source webhook secret + decrypt.

    Returns the (credential_row, plaintext_secret) tuple. Raises 401 with the
    operator-actionable hint when the credential row is absent (or soft-deleted).
    No audit row is written here — there's nothing to log against (no credential
    id).
    """
    name = f"{WEBHOOK_CREDENTIAL_NAME_PREFIX}{tag}"
    stmt = (
        select(ProjectCredential)
        .where(ProjectCredential.project_id == project_id)
        .where(ProjectCredential.name == name)
        .where(ProjectCredential.status == RecordStatus.ACTIVE)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=401,
            detail=_DETAIL_WEBHOOK_SECRET_NOT_CONFIGURED_TEMPLATE.format(
                project_id=project_id, tag=tag,
            ),
        )
    secret = credentials_crypto.decrypt(cred.ciphertext)
    return cred, secret


async def _audit_webhook_use(
    session: AsyncSession, credential_id: int, *, ok: bool, reason: str | None
) -> None:
    """Append a CredentialAccessLog row for a webhook verification outcome.

    ``ok=True``  → ``accessed_by='system:webhook_ingest'`` + bumps counter.
    ``ok=False`` → ``accessed_by='system:webhook_ingest (denied=<reason>)'``
                  + no counter bump (denial audit row stands on its own).
    """
    if ok:
        accessed_by = "system:webhook_ingest"
    else:
        accessed_by = f"system:webhook_ingest (denied={reason or 'unknown'})"
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


async def _resolve_webhook_project(
    session: AsyncSession, project_id: int
) -> Project:
    """Fetch the project row by id or 404 with a stable detail string.

    Soft-deleted projects (status=0) are treated as not found — webhook
    deliveries to a killed project must NOT silently land in a hidden row.
    """
    stmt = (
        select(Project)
        .where(Project.id == project_id)
        .where(Project.status == RecordStatus.ACTIVE)
    )
    project = (await session.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=_DETAIL_WEBHOOK_PROJECT_NOT_FOUND_TEMPLATE.format(
                project_id=project_id,
            ),
        )
    return project


def _render_template(
    template: WebhookTemplate, tag: str, payload: dict
) -> tuple[str, str]:
    """Run the template's title + description strings through substitute().

    For the default-fallback template, inject ``__tag`` + ``__pretty_payload``
    into the substitution context BEFORE running substitute() so the fallback
    placeholders resolve. Returns (title, description). Raises
    ``MissingTemplateField`` (with the offending path) on any unresolvable
    placeholder — the caller maps that to a 422.
    """
    ctx = dict(payload)
    # Reserve the underscore-prefixed names for fallback hooks. The real
    # webhook payloads from Calendly/GitHub/etc never use ``__tag``/
    # ``__pretty_payload`` as a top-level key, so this is collision-free.
    ctx["__tag"] = tag
    ctx["__pretty_payload"] = pretty_dump_for_fallback(payload)
    title = substitute_template(template.title_template, ctx)
    description = substitute_template(template.description_template, ctx)
    return title, description


@router.post(
    "/webhook/{project_id}/{tag}",
    status_code=200,
)
async def ingest_webhook(
    project_id: Annotated[int, Path(ge=1)],
    tag: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")],
    payload: Annotated[dict, Body()],
    x_webhook_secret: Annotated[
        str | None, Header(alias="X-Webhook-Secret")
    ] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Authenticate + template-substitute + create-a-task for one inbound webhook.

    Returns 200 with ``{received, task_id, project_id, template_used, tag}`` on
    success.

    Errors:
      - 401 (``"invalid signature"``) — header missing or mismatched. Audit
        row written with the denial reason.
      - 401 (``webhook secret not configured for this project ...``) — vault
        credential absent. No audit row (nothing to log against).
      - 404 — ``project_id`` does not exist (or has been soft-deleted).
      - 422 — A template placeholder references a missing field in the body.
        Detail names the exact dot-path so operator can fix the mapping.
      - 429 — Per-(project_id, tag) rate cap exceeded (60/min hard, v1).
    """
    # ----- 1. Resolve project (404 if missing) ----------------------------
    # 404-first ordering is intentional: a missing-credential 401 against a
    # nonexistent project would leak project existence via the 401-vs-404
    # distinction. We resolve the project before EITHER credential lookup
    # or rate-limit consumption so unknown-project requests never advance
    # past this check.
    project = await _resolve_webhook_project(session, project_id)

    # ----- 2. Rate-limit (per (project_id, tag), 60/min hard) -------------
    # The rate limit runs BEFORE the auth check by design: a flood of
    # unauthenticated requests would still consume credential-decrypt work
    # per request if we authed first. Eating the rate-limit early bounds the
    # work the endpoint does for any attacker. The tradeoff: a legitimate
    # client whose secret is correct can still be rate-limited (visible).
    try:
        rate_limit_check_and_consume(project_id, tag)
    except RateLimitError as exc:
        logger.warning(
            "webhook rate limit exceeded project_id=%s tag=%s: %s",
            project_id, tag, exc,
        )
        raise HTTPException(status_code=429, detail="rate_limit_exceeded") from exc

    # ----- 3. Authenticate via M3-vault webhook_<tag> secret --------------
    cred, secret = await _load_webhook_secret(session, project_id, tag)
    # Guard: encoding failure on a malformed vault entry or mis-encoded header
    # must not surface as a 500.  See the matching comment in ingest_email for
    # the rationale; same audit-reason semantics apply here.
    try:
        provided = (x_webhook_secret or "").encode("utf-8")
        expected = secret.encode("utf-8")
        sig_ok = hmac.compare_digest(provided, expected)
    except (UnicodeEncodeError, UnicodeDecodeError):
        await _audit_webhook_use(
            session, cred.id, ok=False, reason="secret_encoding_error",
        )
        raise HTTPException(
            status_code=401, detail=_DETAIL_WEBHOOK_BAD_SIGNATURE,
        )
    if not sig_ok:
        await _audit_webhook_use(
            session, cred.id, ok=False, reason="signature_mismatch",
        )
        raise HTTPException(
            status_code=401, detail=_DETAIL_WEBHOOK_BAD_SIGNATURE,
        )
    await _audit_webhook_use(session, cred.id, ok=True, reason=None)

    # ----- 4. Look up template (or default-fallback) ----------------------
    template = get_webhook_template(tag)
    if template is None:
        template = DEFAULT_FALLBACK_TEMPLATE
        template_used = "default-fallback"
    else:
        template_used = tag

    # ----- 5. Substitute placeholders -------------------------------------
    try:
        title, description = _render_template(template, tag, payload)
    except MissingTemplateField as exc:
        # 422 with the specific dot-path — operator-debuggable.
        raise HTTPException(
            status_code=422,
            detail=_DETAIL_WEBHOOK_MISSING_FIELD_TEMPLATE.format(
                field_path=exc.field_path,
            ),
        ) from exc

    # Title cap matches the email-ingest convention (200 chars). The Task
    # column is TEXT (uncapped) — the cap is for UI legibility.
    title = title[:200] if title else f"(webhook {tag})"

    # ----- 6. Coerce template-declared task fields (defensive) ------------
    # The in-code registry is the source of truth but we still defend against
    # a future X.5 DB-backed row carrying an unknown task_kind/task_type /
    # out-of-range priority. The DB CHECK would refuse the INSERT anyway;
    # coercing here gives the operator a usable task with a clear log line
    # instead of a 500.
    task_kind = template.task_kind
    if task_kind not in TaskKind.ALL:
        logger.warning(
            "webhook_ingest: template %r has invalid task_kind=%r — coercing to 'human'",
            template_used, task_kind,
        )
        task_kind = TaskKind.HUMAN

    task_type = template.task_type
    if task_type not in TaskType.ALL:
        logger.warning(
            "webhook_ingest: template %r has invalid task_type=%r — coercing to 'feature'",
            template_used, task_type,
        )
        task_type = TaskType.FEATURE

    task_priority = template.priority
    if task_priority not in TaskPriority.ALL:
        logger.warning(
            "webhook_ingest: template %r has invalid priority=%r — coercing to NORMAL",
            template_used, task_priority,
        )
        task_priority = TaskPriority.NORMAL

    # ----- 7. Create the task --------------------------------------------
    task = Task(
        project_id=project.id,
        title=title,
        description=description,
        process_status=TaskStatus.TODO,
        priority=task_priority,
        task_kind=task_kind,
        task_type=task_type,
        # No assigned_role; operator triages.
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    logger.info(
        "webhook_ingest: created task #%d in project #%d (%s) "
        "tag=%r template=%r",
        task.id, project.id, project.name, tag, template_used,
    )

    return {
        "received": True,
        "task_id": task.id,
        "project_id": project.id,
        "template_used": template_used,
        "tag": tag,
    }
