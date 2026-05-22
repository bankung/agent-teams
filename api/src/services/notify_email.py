"""Gmail SMTP email adapter for the daily-digest channel (Kanban #1217).

`send_email` reads credentials from environment variables at call time and
never raises — every failure lands in `SendResult`.
DIGEST_EMAIL_ENABLED=false (or unset) skips the actual SMTP send and returns
ok=False with detail='digest_email_disabled'.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Module-level env-var name constants — exported for tests + monkeypatch parity
# with notify_telegram / notify_web_push.
EMAIL_ENV_ENABLED = "DIGEST_EMAIL_ENABLED"
EMAIL_ENV_HOST = "GMAIL_SMTP_HOST"
EMAIL_ENV_PORT = "GMAIL_SMTP_PORT"
EMAIL_ENV_USER = "GMAIL_SMTP_USER"
EMAIL_ENV_APP_PASSWORD = "GMAIL_SMTP_APP_PASSWORD"
EMAIL_ENV_FROM = "GMAIL_SMTP_FROM"
EMAIL_ENV_RECIPIENT = "DIGEST_EMAIL_RECIPIENT"

# Defaults
GMAIL_SMTP_DEFAULT_HOST = "smtp.gmail.com"
GMAIL_SMTP_DEFAULT_PORT = 587  # STARTTLS
GMAIL_SMTP_DEFAULT_TIMEOUT = 30  # seconds — prevents infinite hang on unresponsive relay


@dataclass
class SendResult:
    """Return value from send_email().

    ok=True means the SMTP server accepted the message (250 OK). ok=False
    means delivery was not attempted or failed; `error` carries a short
    human-readable descriptor (same posture as notify_telegram's `detail`).
    """

    ok: bool
    detail: str
    error: str | None = field(default=None)


def send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    *,
    smtplib_factory=None,
) -> SendResult:
    """Send via Gmail SMTP. Never raises — all failures land in SendResult.

    Reads configuration from environment variables at call time (not cached)
    to mirror the notify_telegram / notify_web_push pattern.

    Gate: if DIGEST_EMAIL_ENABLED is not 'true' (case-insensitive), returns
    ok=False with detail='digest_email_disabled' immediately — no SMTP call.

    smtplib_factory: test seam — when provided, called as
        smtplib_factory(host, port) -> SMTP-like context manager.
    If None (production), `smtplib.SMTP` is used.
    """
    enabled = os.environ.get(EMAIL_ENV_ENABLED, "false").strip().lower()
    if enabled != "true":
        return SendResult(
            ok=False,
            detail="digest_email_disabled",
            error=f"{EMAIL_ENV_ENABLED} is not 'true'",
        )

    host = os.environ.get(EMAIL_ENV_HOST, GMAIL_SMTP_DEFAULT_HOST).strip()
    port_raw = os.environ.get(EMAIL_ENV_PORT, str(GMAIL_SMTP_DEFAULT_PORT)).strip()
    user = os.environ.get(EMAIL_ENV_USER, "").strip()
    app_password = os.environ.get(EMAIL_ENV_APP_PASSWORD, "").strip()
    from_addr = os.environ.get(EMAIL_ENV_FROM, user).strip() or user

    # Env gate — fail fast with a clear diagnostic before attempting SMTP.
    for env_name, env_val in (
        (EMAIL_ENV_USER, user),
        (EMAIL_ENV_APP_PASSWORD, app_password),
    ):
        if not env_val:
            return SendResult(
                ok=False,
                detail=f"missing_env_{env_name}",
                error=f"Environment variable {env_name} is not set",
            )

    try:
        port = int(port_raw)
    except (ValueError, TypeError):
        return SendResult(
            ok=False,
            detail=f"invalid_port: {port_raw!r}",
            error=f"{EMAIL_ENV_PORT}={port_raw!r} is not an integer",
        )

    # Build MIME/multipart message with text + HTML alternatives.
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    smtp_factory = smtplib_factory or smtplib.SMTP

    try:
        with smtp_factory(host, port, timeout=GMAIL_SMTP_DEFAULT_TIMEOUT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(user, app_password)
            smtp.sendmail(from_addr, [to], msg.as_string())
        logger.info(
            "notify_email: sent to=%s subject=%r host=%s port=%d",
            to, subject, host, port,
        )
        return SendResult(ok=True, detail="sent")
    except smtplib.SMTPAuthenticationError as exc:
        logger.warning(
            "notify_email: auth_error to=%s type=%s code=%s",
            to, type(exc).__name__, exc.smtp_code,
        )
        return SendResult(
            ok=False,
            detail="smtp_auth_error",
            error=f"SMTPAuthenticationError({exc.smtp_code})",
        )
    except smtplib.SMTPException as exc:
        logger.warning("notify_email: smtp_error to=%s err=%r", to, exc)
        return SendResult(
            ok=False,
            detail=f"smtp_error: {type(exc).__name__}",
            error=repr(exc),
        )
    except OSError as exc:
        # Covers ConnectionRefusedError, TimeoutError, socket errors, etc.
        logger.warning("notify_email: network_error to=%s err=%r", to, exc)
        return SendResult(
            ok=False,
            detail=f"network_error: {type(exc).__name__}",
            error=repr(exc),
        )
    except Exception as exc:  # noqa: BLE001 — docstring contract: MUST NOT raise
        logger.warning("notify_email: unexpected_error to=%s err=%r", to, exc)
        return SendResult(
            ok=False,
            detail=f"unexpected_error: {type(exc).__name__}",
            error=repr(exc),
        )
