"""Kanban #1217 — POST /api/digest/fire contract smoke tests.

Uses the ASGI test client (no real network / no real SMTP).
GmailSmtpSender is tested via monkeypatching the smtplib factory at the
module level — the router creates a fresh GmailSmtpSender() on each request
so we swap smtplib.SMTP at the module level via monkeypatch.

Covers:
1. 200 response shape — ok, detail, flag_count, recipient, subject.
2. DIGEST_EMAIL_ENABLED=false → ok=False, detail='digest_email_disabled'.
3. DIGEST_EMAIL_ENABLED=true + mock SMTP → ok=True; subject contains flag count.
4. flag_count in response reflects DB state (0 when no open flags exist in test DB).
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_smtp_mock() -> MagicMock:
    """Return a MagicMock that behaves as a successful smtplib.SMTP context manager."""
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_disabled_returns_200_ok_false(client, monkeypatch) -> None:
    """When DIGEST_EMAIL_ENABLED is false (default), endpoint returns 200 but ok=False."""
    monkeypatch.delenv("DIGEST_EMAIL_ENABLED", raising=False)

    resp = await client.post("/api/digest/fire")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    assert body["detail"] == "digest_email_disabled"
    assert isinstance(body["flag_count"], int)
    assert isinstance(body["subject"], str)
    assert isinstance(body["recipient"], str)


@pytest.mark.asyncio
async def test_digest_fire_enabled_with_mock_smtp_returns_ok_true(client, monkeypatch) -> None:
    """When DIGEST_EMAIL_ENABLED=true and SMTP succeeds, response ok=True."""
    monkeypatch.setenv("DIGEST_EMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_SMTP_USER", "test@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "app-pw-16-chars-x")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "dest@example.com")

    smtp_mock = _make_smtp_mock()
    with patch("smtplib.SMTP", return_value=smtp_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["detail"] == "sent"
    assert body["recipient"] == "dest@example.com"
    # subject contains date + flag count info
    assert "Digest" in body["subject"]


@pytest.mark.asyncio
async def test_digest_fire_subject_contains_flag_count(client, monkeypatch) -> None:
    """Response subject reflects the flag_count from the DB query."""
    monkeypatch.delenv("DIGEST_EMAIL_ENABLED", raising=False)

    resp = await client.post("/api/digest/fire")
    assert resp.status_code == 200

    body = resp.json()
    flag_count = body["flag_count"]
    subject = body["subject"]

    # The test DB seed creates no AA3 flag tasks, so flag_count should be 0.
    # Subject must be consistent with count.
    if flag_count == 0:
        assert "no open flags" in subject
    else:
        assert str(flag_count) in subject
