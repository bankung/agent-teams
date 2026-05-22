"""HTTP router for daily-digest fire endpoint (Kanban #1217).

Mounted at `/api/digest` from main.py.

Cross-project endpoint — takes NO `X-Project-Id` header (parity with
`/api/audit/daily-rollup` and `/api/dashboard` precedent). The digest
covers all active projects, so a project-scoped header would be wrong.

POST /api/digest/fire:
  - Fetches all open AA3 audit flags across active projects.
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
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.middleware.rate_limit import limiter
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/digest", tags=["digest"])


class DigestFireResponse(BaseModel):
    """Response from POST /api/digest/fire.

    ok=True when SMTP accepted the message; False when disabled or failed.
    detail mirrors email SendResult.detail.
    push_ok=True when ntfy accepted the push; push_detail mirrors push SendResult.detail.
    flag_count, recipient, subject are informational for the caller / cron log.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str
    flag_count: int
    recipient: str
    subject: str
    push_ok: bool = False
    push_detail: str = "push_disabled"


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

    # Build web base URL from env — defaults to localhost (dev) so links are
    # always absolute even when the env is unconfigured.
    base_url = os.environ.get("WEB_BASE_URL", "http://localhost:5431").rstrip("/")

    payload = {
        "date": str(today),
        "flags": flags,
        "base_url": base_url,
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

    result = await asyncio.to_thread(send_email, recipient, subject, text_body, html_body)

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
    )
