"""Kanban #1217 — POST /api/digest/fire network-failure path tests.

Area 1 of the followup test suite: verifies that SMTP transport failures
(SMTPException, ConnectionError, socket.timeout) are absorbed as soft failures
and returned as 200 ok=False — the endpoint never 500s on send failure.

The router creates `GmailSmtpSender()` without a smtplib_factory override, so
we patch `smtplib.SMTP` at the module level (the same seam used by
test_digest_router.py and test_notify_email.py).

Anti-pattern notes (per test_surface_pollution feedback):
- No production code modified.
- No *_for_tests helpers added.
- `_make_smtp_mock` helper lives here (not in production src/).
"""

from __future__ import annotations

import smtplib
import socket
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_smtp_raising(exc: BaseException) -> MagicMock:
    """Return a smtplib.SMTP mock whose __enter__ raises `exc`.

    The GmailSmtpSender does `with smtp_factory(host, port) as smtp:` so
    raising in __enter__ simulates a connection-level failure before any
    SMTP command is issued.
    """
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(side_effect=exc)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


def _make_smtp_login_raising(exc: BaseException) -> MagicMock:
    """Return a smtplib.SMTP mock that enters but raises on smtp.login().

    Simulates a failure after the STARTTLS handshake but before credentials
    are accepted.
    """
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    smtp.login.side_effect = exc
    return smtp


def _enable_smtp_env(monkeypatch) -> None:
    """Set the minimum env vars so the SMTP enabled-gate passes."""
    monkeypatch.setenv("DIGEST_EMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_SMTP_USER", "test@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "app-pw-16-chars-x")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "dest@example.com")


# ---------------------------------------------------------------------------
# Area 1a — SMTPException (base class, covers DATA / EHLO / RCPTTO errors)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_smtpexception_returns_200_ok_false(
    client, monkeypatch
) -> None:
    """SMTPException during the SMTP transaction → 200, ok=False, meaningful detail."""
    _enable_smtp_env(monkeypatch)

    smtp_mock = _make_smtp_raising(smtplib.SMTPException("simulated DATA error"))
    with patch("smtplib.SMTP", return_value=smtp_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    # GmailSmtpSender maps SMTPException → "smtp_error: <ClassName>"
    assert "smtp_error" in body["detail"]
    assert "SMTPException" in body["detail"]
    # Shape fields still present
    assert isinstance(body["flag_count"], int)
    assert isinstance(body["subject"], str)
    assert body["recipient"] == "dest@example.com"


# ---------------------------------------------------------------------------
# Area 1b — ConnectionError (OSError subclass, connection-refused scenario)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_connection_error_returns_200_ok_false(
    client, monkeypatch
) -> None:
    """ConnectionError (network layer) → 200, ok=False, 'network_error' in detail."""
    _enable_smtp_env(monkeypatch)

    smtp_mock = _make_smtp_raising(ConnectionError("connection refused"))
    with patch("smtplib.SMTP", return_value=smtp_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    # GmailSmtpSender maps OSError (including ConnectionError) → "network_error: <ClassName>"
    assert "network_error" in body["detail"]
    assert "ConnectionError" in body["detail"]


# ---------------------------------------------------------------------------
# Area 1c — socket.timeout (OSError subclass, common in CI / slow relays)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_socket_timeout_returns_200_ok_false(
    client, monkeypatch
) -> None:
    """socket.timeout → 200, ok=False, 'network_error' in detail.

    socket.timeout is a subclass of OSError; GmailSmtpSender's except-OSError
    clause captures it. This test locks that the catch-all is broad enough.
    """
    _enable_smtp_env(monkeypatch)

    smtp_mock = _make_smtp_raising(socket.timeout("timed out"))
    with patch("smtplib.SMTP", return_value=smtp_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    assert "network_error" in body["detail"]


# ---------------------------------------------------------------------------
# Area 1d — SMTPConnectError (concrete subclass — relay not responding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_smtp_connect_error_returns_200_ok_false(
    client, monkeypatch
) -> None:
    """SMTPConnectError during connection → 200, ok=False.

    SMTPConnectError is a subclass of SMTPException (not OSError), so it
    hits the `except smtplib.SMTPException` branch — distinct from network_error.
    """
    _enable_smtp_env(monkeypatch)

    smtp_mock = _make_smtp_raising(
        smtplib.SMTPConnectError(421, b"Service temporarily unavailable")
    )
    with patch("smtplib.SMTP", return_value=smtp_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    assert "smtp_error" in body["detail"]
    # Distinguishes from the baseline auth-error path
    assert body["detail"] != "smtp_auth_error"


# ---------------------------------------------------------------------------
# Area 1e — NEGATIVE check: success path still returns ok=True
# (positive-path assertion required by anti-hackable-test discipline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_success_path_returns_ok_true(
    client, monkeypatch
) -> None:
    """Positive control: successful SMTP mock → ok=True.

    Required by anti-hackable-test discipline — failure-only tests are
    vacuous if the ok=False path is always taken regardless of the mock.
    This test ensures the same endpoint returns ok=True when SMTP succeeds,
    proving the failure tests are locking real behavior.
    """
    _enable_smtp_env(monkeypatch)

    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    with patch("smtplib.SMTP", return_value=smtp):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["detail"] == "sent"
