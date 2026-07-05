"""Kanban #1217 — POST /api/digest/fire contract smoke tests.

Uses the ASGI test client (no real network / no real SMTP).
GmailSmtpSender is patched via smtplib.SMTP.

Covers (Kanban #1217 — email):
1. 200 response shape — ok, detail, flag_count, recipient, subject.
2. DIGEST_EMAIL_ENABLED=false → ok=False, detail='digest_email_disabled'.
3. DIGEST_EMAIL_ENABLED=true + mock SMTP → ok=True, subject contains date.
4. push_ok always False, push_detail always 'push_disabled'.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_digest_fire_disabled_returns_200_ok_false(client, monkeypatch) -> None:
    """When DIGEST_EMAIL_ENABLED is false (default), endpoint returns 200 but ok=False."""
    monkeypatch.delenv("DIGEST_EMAIL_ENABLED", raising=False)

    resp = await client.post("/api/digest/fire")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    assert body["detail"] == "digest_email_disabled"
    assert "flag_count" in body
    assert "subject" in body
    assert "recipient" in body
    # Push fields present; always disabled (push channel not configured).
    assert body["push_ok"] is False
    assert body["push_detail"] == "push_disabled"


@pytest.mark.asyncio
async def test_digest_fire_enabled_with_mock_smtp_returns_ok_true(
    client, monkeypatch, smtp_success_mock
) -> None:
    """When DIGEST_EMAIL_ENABLED=true and SMTP succeeds, response ok=True."""
    monkeypatch.setenv("DIGEST_EMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_SMTP_USER", "test@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "app-pw-16-chars-x")
    monkeypatch.setenv("DIGEST_EMAIL_RECIPIENT", "dest@example.com")

    with patch("smtplib.SMTP", return_value=smtp_success_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["detail"] == "sent"
    assert body["recipient"] == "dest@example.com"
    assert "Digest" in body["subject"]


@pytest.mark.asyncio
async def test_digest_fire_push_always_disabled(client, monkeypatch) -> None:
    """push_ok is always False and push_detail always 'push_disabled'."""
    monkeypatch.delenv("DIGEST_EMAIL_ENABLED", raising=False)

    resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["push_ok"] is False
    assert body["push_detail"] == "push_disabled"
