"""HTTP router for daily-digest fire endpoint (Kanban #1217).

Mounted at `/api/digest` from main.py.

Cross-project endpoint — takes NO `X-Project-Id` header (parity with
`/api/audit/daily-rollup` and `/api/dashboard` precedent). The digest
covers all active projects, so a project-scoped header would be wrong.

POST /api/digest/fire:
  - Fetches all open GOV3 audit flags across active projects.
  - Renders subject + text + html via digest_template.
  - Sends via send_email (reads creds from env at call time).
  - Returns 200 + delivery status JSON regardless of SMTP outcome
    (ok=False is a soft failure — the endpoint doesn't 500 on send failure).

The cron infrastructure (Kanban #1283 / #1432) fires this endpoint at 18:00
BKK; the endpoint itself is stateless and idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timezone, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.middleware.rate_limit import limiter
from src.models.project import Project
from src.services.digest_template import (
    fetch_open_audit_flags,
    render_html,
    render_push_body,
    render_push_title,
    render_subject,
    render_text,
)
from src.services.notify_email import (
    EMAIL_ENV_RECIPIENT,
    EMAIL_ENV_USER,
    send_email,
)
from src.services.notify_ntfy import NTFY_ENV_ENABLED, NTFY_ENV_TOPIC, send_push
from src.services.skill_stub_detector import run_skill_stub_detector
from src.services.stale_doc_curator import run_stale_doc_curator

# Kanban #1437 — "control" project id whose notification_targets carries the
# digest_email_enabled flag. Single-tenant convention: always project id=1.
_CONTROL_PROJECT_ID = 1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/digest", tags=["digest"])


class DigestFireResponse(BaseModel):
    """Response from POST /api/digest/fire.

    ok=True when SMTP accepted the message; False when disabled or failed.
    detail mirrors email SendResult.detail.
    push_ok=True when ntfy accepted the push; push_detail mirrors push SendResult.detail.
    flag_count, recipient, subject are informational for the caller / cron log.
    email_skipped_reason: non-null when email was skipped by a gate other than
      DIGEST_EMAIL_ENABLED env (e.g. "opted_out_per_project" for Kanban #1437).
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str
    flag_count: int
    recipient: str
    subject: str
    push_ok: bool = False
    push_detail: str = "push_disabled"
    email_skipped_reason: str | None = None


@router.post("/fire", response_model=DigestFireResponse)
@limiter.limit("6/minute")
async def fire_digest(
    request: Request,  # required by slowapi key_func — not used in handler body
    session: AsyncSession = Depends(get_session),
) -> DigestFireResponse:
    """Pull open flags, render digest, send via Gmail SMTP.

    Returns 200 in all cases — SMTP failure is a soft failure surfaced in
    the response body (ok=False, detail describes the failure mode).
    """
    today = datetime.now(timezone.utc).date()

    # Fetch open flags across active projects.
    flags = await fetch_open_audit_flags(session)
    flag_count = len(flags)

    # Kanban #1223 — run skill/runbook stub detector (HITL-gated; writes to
    # _scratch/auditor/ only; soft-fail so a detector error never blocks the
    # digest send).
    try:
        stub_result = await run_skill_stub_detector(session)
        skill_stubs_payload = {
            "proposed_count": stub_result.proposed_count,
            "stub_dir": stub_result.stub_dir,
            "groups_found": stub_result.groups_found,
            "threshold_used": stub_result.threshold_used,
        }
        logger.info(
            "digest fire: skill_stub_detector proposed=%d groups=%d skipped_dedup=%d",
            stub_result.proposed_count,
            stub_result.groups_found,
            stub_result.skipped_dedup,
        )
    except Exception as _det_exc:  # noqa: BLE001
        logger.warning(
            "digest fire: skill_stub_detector failed (non-fatal): %s", _det_exc
        )
        skill_stubs_payload = {}

    # Kanban #1222 — run stale-doc curator (HITL-gated; writes to
    # _scratch/auditor/ only; soft-fail so a curator error never blocks the
    # digest send). Synchronous FS-only function; no DB session needed.
    try:
        stale_result = run_stale_doc_curator()
        stale_docs_payload = {
            "stale_count": stale_result.stale_count,
            "contradiction_count": stale_result.contradiction_count,
            "report_path": stale_result.report_path,
            "scanned_count": stale_result.scanned_count,
            "threshold_days": stale_result.threshold_days,
        }
        logger.info(
            "digest fire: stale_doc_curator stale=%d contradictions=%d scanned=%d",
            stale_result.stale_count,
            stale_result.contradiction_count,
            stale_result.scanned_count,
        )
    except Exception as _cur_exc:  # noqa: BLE001
        logger.warning(
            "digest fire: stale_doc_curator failed (non-fatal): %s", _cur_exc
        )
        stale_docs_payload = {}

    # Build web base URL from env — defaults to localhost (dev) so links are
    # always absolute even when the env is unconfigured.
    base_url = os.environ.get("WEB_BASE_URL", "http://localhost:5431").rstrip("/")

    payload = {
        "date": str(today),
        "flags": flags,
        "base_url": base_url,
        "project_id": _CONTROL_PROJECT_ID,
        "skill_stubs": skill_stubs_payload,  # Kanban #1223
        "stale_docs": stale_docs_payload,  # Kanban #1222
    }

    subject = render_subject(flag_count, today)
    text_body = render_text(payload)
    html_body = render_html(payload)

    # Resolve recipient: DIGEST_EMAIL_RECIPIENT → GMAIL_SMTP_USER → '<unset>'.
    recipient = (
        os.environ.get(EMAIL_ENV_RECIPIENT, "").strip()
        or os.environ.get(EMAIL_ENV_USER, "").strip()
        or "<unset>"
    )

    # Kanban #1437 — per-project opt-out gate. Fetch the control project's
    # config and skip email if digest_email_enabled is explicitly False.
    # Missing key or NULL config = treat as True (opt-in by default).
    # Stored in projects.config (free JSONB dict) rather than notification_targets
    # (a typed list of push delivery targets — incompatible shape).
    # This check is independent of DIGEST_EMAIL_ENABLED env (env is the ops
    # gate; config.digest_email_enabled is the user-facing opt-out).
    email_skipped_reason: str | None = None
    project_config_row = (
        await session.execute(
            select(Project.config).where(Project.id == _CONTROL_PROJECT_ID)
        )
    ).scalar_one_or_none()
    project_config: dict = project_config_row if isinstance(project_config_row, dict) else {}
    if project_config.get("digest_email_enabled") is False:
        email_skipped_reason = "opted_out_per_project"
        logger.info(
            "digest fire: email skipped — project %d opted out (config.digest_email_enabled=false)",
            _CONTROL_PROJECT_ID,
        )

    if email_skipped_reason is None:
        result = await asyncio.to_thread(send_email, recipient, subject, text_body, html_body)
    else:
        from src.services.notify_email import SendResult  # local import avoids circular
        result = SendResult(ok=False, detail=email_skipped_reason)

    if not result.ok:
        logger.warning(
            "digest fire: email send failed detail=%r recipient=%s flag_count=%d",
            result.detail, recipient, flag_count,
        )

    # --- Push channel (independent of email; soft-fail) ---------------------
    push_enabled = os.environ.get(NTFY_ENV_ENABLED, "false").strip().lower() == "true"
    push_topic = os.environ.get(NTFY_ENV_TOPIC, "").strip()

    push_ok: bool = False
    push_detail: str = "push_disabled"

    if push_enabled and push_topic:
        push_title = render_push_title(flag_count, today)
        push_message = render_push_body(flags)
        click_url = base_url.rstrip("/") + "/review"

        push_result = await asyncio.to_thread(
            send_push,
            push_message,
            title=push_title,
            click_url=click_url,
        )
        push_ok = push_result.ok
        push_detail = push_result.detail

        if not push_result.ok:
            logger.warning(
                "digest fire: push send failed detail=%r flag_count=%d",
                push_result.detail, flag_count,
            )
    # -----------------------------------------------------------------------

    return DigestFireResponse(
        ok=result.ok,
        detail=result.detail,
        flag_count=flag_count,
        recipient=recipient,
        subject=subject,
        push_ok=push_ok,
        push_detail=push_detail,
        email_skipped_reason=email_skipped_reason,
    )
