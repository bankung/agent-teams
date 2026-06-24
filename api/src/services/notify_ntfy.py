"""ntfy push-notification adapter (Kanban #1192).

`send_push` reads credentials from environment variables at call time and
never raises — every failure lands in `SendResult`.
PUSH_ENABLED=false (or unset) skips the actual HTTP send and returns
ok=False with detail='push_disabled'.

Mirrors the shape of notify_email.py (module-level function + SendResult
dataclass + env-var reads + 30s timeout + soft-fail).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Module-level env-var name constants — exported for tests + monkeypatch parity
# with notify_email / notify_telegram.
NTFY_ENV_BASE_URL = "NTFY_BASE_URL"
NTFY_ENV_TOPIC = "NTFY_TOPIC"
NTFY_ENV_ACCESS_TOKEN = "NTFY_ACCESS_TOKEN"
NTFY_ENV_ENABLED = "PUSH_ENABLED"

# Defaults
NTFY_DEFAULT_BASE_URL = "https://ntfy.sh"
NTFY_DEFAULT_TIMEOUT = 30  # seconds — prevents infinite hang on unresponsive relay


@dataclass
class SendResult:
    """Return value from send_push().

    ok=True means the ntfy server accepted the message (HTTP 2xx). ok=False
    means delivery was not attempted or failed; `error` carries a short
    human-readable descriptor (same posture as notify_email's SendResult).
    """

    ok: bool
    detail: str
    error: str | None = field(default=None)


def send_push(
    message: str,
    *,
    title: str | None = None,
    priority: int = 3,
    click_url: str | None = None,
    tags: str | None = None,
    httpx_client: httpx.Client | None = None,
) -> SendResult:
    """POST a push notification via ntfy. Never raises — all failures land in SendResult.

    Reads configuration from environment variables at call time (not cached)
    to mirror the notify_telegram / notify_email pattern.

    Gate: if PUSH_ENABLED is not 'true' (case-insensitive), returns
    ok=False with detail='push_disabled' immediately — no HTTP call.

    Args:
        message:     Notification body text (required).
        title:       Optional X-Title header.
        priority:    ntfy priority 1-5 (default 3 = default). X-Priority header.
        click_url:   Optional URL opened when the notification is clicked. X-Click header.
        tags:        Optional comma-separated emoji tag names. X-Tags header.
        httpx_client: Test seam — when provided, this sync client is used for
                      the POST. If None (production), a short-lived httpx.Client
                      is created internally.

    Returns:
        SendResult(ok, detail, error)

    Auth: if NTFY_ACCESS_TOKEN.strip() is non-empty (after stripping trailing
    whitespace and inline shell comments), an Authorization: Bearer header is
    added.  The trailing-comment caveat applies: `.env` values like
    `NTFY_ACCESS_TOKEN=abc  # only if auth` — strip() handles trailing spaces
    but NOT inline `#` comments.  The convention in this project is to strip
    only leading/trailing whitespace; the operator is responsible for keeping
    the .env value comment-free.  The spawn brief documents this explicitly.
    """
    enabled = os.environ.get(NTFY_ENV_ENABLED, "false").strip().lower()
    if enabled != "true":
        return SendResult(
            ok=False,
            detail="push_disabled",
            error=f"{NTFY_ENV_ENABLED} is not 'true'",
        )

    base_url = os.environ.get(NTFY_ENV_BASE_URL, NTFY_DEFAULT_BASE_URL).strip().rstrip("/")
    topic = os.environ.get(NTFY_ENV_TOPIC, "").strip()
    # Strip leading/trailing whitespace only — NOT inline comments (operator
    # must ensure the .env value is clean; see docstring for caveat).
    access_token = os.environ.get(NTFY_ENV_ACCESS_TOKEN, "").strip()

    if not topic:
        return SendResult(
            ok=False,
            detail=f"missing_env_{NTFY_ENV_TOPIC}",
            error=f"Environment variable {NTFY_ENV_TOPIC} is not set",
        )

    # Defensive: .env files often have trailing inline comments
    # (e.g. "NTFY_ACCESS_TOKEN=  # only if auth configured").
    # A value that starts with '#' after strip() is a comment artifact — treat as empty.
    if access_token.startswith("#"):
        access_token = ""

    url = f"{base_url}/{topic}"

    headers: dict[str, str] = {}
    if title:
        headers["X-Title"] = title
    headers["X-Priority"] = str(priority)
    if click_url:
        headers["X-Click"] = click_url
    if tags:
        headers["X-Tags"] = tags
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    own_client = httpx_client is None
    client = httpx_client or httpx.Client(timeout=NTFY_DEFAULT_TIMEOUT)
    try:
        try:
            resp = client.post(url, content=message.encode("utf-8"), headers=headers)
        except httpx.RequestError as exc:
            logger.warning("notify_ntfy: request_error topic=%s err_type=%s", topic, type(exc).__name__)
            return SendResult(
                ok=False,
                detail=f"request_error: {type(exc).__name__}",
                error=type(exc).__name__,
            )
        if not (200 <= resp.status_code < 300):
            snippet = (resp.text or "")[:200]
            logger.warning(
                "notify_ntfy: non_2xx topic=%s status=%d body=%s",
                topic, resp.status_code, snippet,
            )
            return SendResult(
                ok=False,
                detail=f"http_{resp.status_code}",
                error=snippet or None,
            )
        logger.info("notify_ntfy: sent topic=%s priority=%d", topic, priority)
        return SendResult(ok=True, detail="sent")
    except Exception as exc:  # noqa: BLE001 — docstring contract: MUST NOT raise
        logger.warning("notify_ntfy: unexpected_error topic=%s err_type=%s", topic, type(exc).__name__)
        return SendResult(
            ok=False,
            detail=f"unexpected_error: {type(exc).__name__}",
            error=type(exc).__name__,
        )
    finally:
        if own_client:
            client.close()
