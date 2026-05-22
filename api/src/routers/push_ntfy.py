"""ntfy push-notification fire endpoint (Kanban #1192).

Mounted at `/api/push` from main.py (same prefix as push.py Web Push
subscription endpoints — FastAPI merges routers cleanly).

POST /api/push/fire:
  - Reads NTFY_BASE_URL / NTFY_TOPIC / NTFY_ACCESS_TOKEN / PUSH_ENABLED from env.
  - Sends via send_push (reads creds from env at call time).
  - Returns 200 + delivery status JSON regardless of ntfy outcome
    (ok=False is a soft failure — the endpoint doesn't 500 on send failure).

Rate-limited at 6/minute matching /api/digest/fire (Kanban #1124).
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from src.middleware.rate_limit import limiter
from src.services.notify_ntfy import NTFY_ENV_TOPIC, send_push

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push-ntfy"])


class PushFireRequest(BaseModel):
    """Optional body for POST /api/push/fire.

    Both fields are optional — defaults match the digest/fire convention
    (useful smoke values when called with an empty body).
    """

    model_config = ConfigDict(extra="forbid")

    message: str = "agent-teams push smoke"
    title: str = "agent-teams"


class PushFireResponse(BaseModel):
    """Response from POST /api/push/fire.

    ok=True when ntfy accepted the message; False when disabled or failed.
    detail mirrors SendResult.detail. recipient_topic and message are
    informational for the caller / cron log.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail: str
    recipient_topic: str
    message: str


@router.post("/fire", response_model=PushFireResponse)
@limiter.limit("6/minute")
async def fire_push(
    request: Request,  # required by slowapi key_func — not used in handler body
    body: PushFireRequest | None = None,
) -> PushFireResponse:
    """Send a push notification via ntfy.

    Returns 200 in all cases — ntfy failure is a soft failure surfaced in
    the response body (ok=False, detail describes the failure mode).

    Body is optional (JSON); defaults: message='agent-teams push smoke',
    title='agent-teams'. Mirrors /api/digest/fire's zero-dependency shape
    (no DB session needed — just env + HTTP).
    """
    if body is None:
        body = PushFireRequest()

    recipient_topic = os.environ.get(NTFY_ENV_TOPIC, "").strip() or "<unset>"

    result = await asyncio.to_thread(
        send_push,
        body.message,
        title=body.title,
    )

    if not result.ok:
        logger.warning(
            "push fire: send failed detail=%r topic=%s message=%r",
            result.detail, recipient_topic, body.message,
        )

    return PushFireResponse(
        ok=result.ok,
        detail=result.detail,
        recipient_topic=recipient_topic,
        message=body.message,
    )
