"""Kanban #1217 — send_email unit tests.

Mocked smtplib (via test-seam factory arg) — no real SMTP.

Covers:
- Happy path: SMTP transaction succeeds → ok=True, detail='sent'.
- DIGEST_EMAIL_ENABLED not 'true' → ok=False, detail='digest_email_disabled'.
- Missing GMAIL_SMTP_USER → ok=False, detail='missing_env_GMAIL_SMTP_USER'.
- Missing GMAIL_SMTP_APP_PASSWORD → ok=False, detail='missing_env_GMAIL_SMTP_APP_PASSWORD'.
- SMTPAuthenticationError → ok=False, detail='smtp_auth_error'.
- OSError (network) → ok=False, detail='network_error: ConnectionRefusedError'.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock

import pytest

from src.services.notify_email import (
    EMAIL_ENV_APP_PASSWORD,
    EMAIL_ENV_ENABLED,
    EMAIL_ENV_USER,
    send_email,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _call_send(factory, **send_kwargs):
    """Call send_email with smtplib_factory=factory. Extra kwargs override defaults."""
    defaults = dict(
        to="dest@example.com",
        subject="Test subject",
        text_body="Plain text body",
        html_body="<p>HTML body</p>",
    )
    defaults.update(send_kwargs)
    return send_email(**defaults, smtplib_factory=factory)


def _make_factory(exc: Exception | None = None):
    """Return (factory, smtp_mock). If exc is provided, __enter__ raises it."""
    smtp_mock = MagicMock()
    smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
    smtp_mock.__exit__ = MagicMock(return_value=False)
    if exc is not None:
        smtp_mock.__enter__.side_effect = exc
    factory = MagicMock(return_value=smtp_mock)
    return factory, smtp_mock


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_send_happy_path(monkeypatch) -> None:
    monkeypatch.setenv(EMAIL_ENV_ENABLED, "true")
    monkeypatch.setenv(EMAIL_ENV_USER, "test@gmail.com")
    monkeypatch.setenv(EMAIL_ENV_APP_PASSWORD, "app-pw-16-chars-x")

    factory, smtp_mock = _make_factory()
    result = _call_send(factory)

    assert result.ok is True
    assert result.detail == "sent"
    assert result.error is None
    smtp_mock.login.assert_called_once_with("test@gmail.com", "app-pw-16-chars-x")
    smtp_mock.sendmail.assert_called_once()
    to_arg = smtp_mock.sendmail.call_args[0][1]
    assert "dest@example.com" in to_arg


# ---------------------------------------------------------------------------
# DIGEST_EMAIL_ENABLED gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_val,use_delenv", [("false", False), (None, True)])
def test_send_disabled_returns_ok_false(monkeypatch, env_val, use_delenv) -> None:
    """When DIGEST_EMAIL_ENABLED is not 'true', no SMTP call, ok=False."""
    if use_delenv:
        monkeypatch.delenv(EMAIL_ENV_ENABLED, raising=False)
    else:
        monkeypatch.setenv(EMAIL_ENV_ENABLED, env_val)
    monkeypatch.setenv(EMAIL_ENV_USER, "test@gmail.com")
    monkeypatch.setenv(EMAIL_ENV_APP_PASSWORD, "app-pw-16-chars-x")

    factory, smtp_mock = _make_factory()
    result = _call_send(factory)

    assert result.ok is False
    assert result.detail == "digest_email_disabled"
    smtp_mock.login.assert_not_called()


# ---------------------------------------------------------------------------
# Missing env vars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_to_delete,expected_detail", [
    (EMAIL_ENV_USER, f"missing_env_{EMAIL_ENV_USER}"),
    (EMAIL_ENV_APP_PASSWORD, f"missing_env_{EMAIL_ENV_APP_PASSWORD}"),
])
def test_send_missing_env_returns_ok_false(monkeypatch, var_to_delete, expected_detail) -> None:
    monkeypatch.setenv(EMAIL_ENV_ENABLED, "true")
    monkeypatch.setenv(EMAIL_ENV_USER, "test@gmail.com")
    monkeypatch.setenv(EMAIL_ENV_APP_PASSWORD, "app-pw-16-chars-x")
    monkeypatch.delenv(var_to_delete, raising=False)

    factory, _ = _make_factory()
    result = _call_send(factory)

    assert result.ok is False
    assert result.detail == expected_detail


# ---------------------------------------------------------------------------
# SMTP authentication failure
# ---------------------------------------------------------------------------


def test_send_auth_error_returns_ok_false(monkeypatch) -> None:
    monkeypatch.setenv(EMAIL_ENV_ENABLED, "true")
    monkeypatch.setenv(EMAIL_ENV_USER, "test@gmail.com")
    monkeypatch.setenv(EMAIL_ENV_APP_PASSWORD, "wrong-password-xxxxx")

    # login() raises, not __enter__
    smtp_mock = MagicMock()
    smtp_mock.__enter__ = MagicMock(return_value=smtp_mock)
    smtp_mock.__exit__ = MagicMock(return_value=False)
    smtp_mock.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials")
    factory = MagicMock(return_value=smtp_mock)
    result = _call_send(factory)

    assert result.ok is False
    assert result.detail == "smtp_auth_error"
    assert result.error is not None


# ---------------------------------------------------------------------------
# Network / OSError
# ---------------------------------------------------------------------------


def test_send_network_error_returns_ok_false(monkeypatch) -> None:
    monkeypatch.setenv(EMAIL_ENV_ENABLED, "true")
    monkeypatch.setenv(EMAIL_ENV_USER, "test@gmail.com")
    monkeypatch.setenv(EMAIL_ENV_APP_PASSWORD, "app-pw-16-chars-x")

    factory, _ = _make_factory(exc=ConnectionRefusedError("refused"))
    result = _call_send(factory)

    assert result.ok is False
    assert "network_error" in result.detail
    assert "ConnectionRefusedError" in result.detail
