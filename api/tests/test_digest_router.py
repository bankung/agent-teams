"""Kanban #1217 / #1218 — POST /api/digest/fire contract smoke tests.

Uses the ASGI test client (no real network / no real SMTP or ntfy).
GmailSmtpSender is patched via smtplib.SMTP; ntfy send_push is patched via
the httpx_client kwarg (Kanban #1218 addition).

Covers (Kanban #1217 — email):
1. 200 response shape — ok, detail, flag_count, recipient, subject.
2. DIGEST_EMAIL_ENABLED=false → ok=False, detail='digest_email_disabled'.
3. DIGEST_EMAIL_ENABLED=true + mock SMTP → ok=True, subject contains date.

Covers (Kanban #1218 — push channel):
4. Push happy-path: both enabled → ok=True, push_ok=True.
5. Push disabled (PUSH_ENABLED unset) → ok=True, push_ok=False, push_detail='push_disabled'.
6. Both disabled → ok=False, push_ok=False.
7. Push fails, email succeeds → ok=True, push_ok=False.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    # FastAPI response_model=DigestFireResponse enforces types; check presence only.
    assert "flag_count" in body
    assert "subject" in body
    assert "recipient" in body
    # Push fields present even when both channels off (Kanban #1218).
    assert "push_ok" in body
    assert "push_detail" in body


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


# ---------------------------------------------------------------------------
# Kanban #1218 — push channel tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_push_happy_path_both_channels_fire(
    client, monkeypatch, smtp_success_mock, ntfy_success_mock, smtp_env, ntfy_env
) -> None:
    """Push happy-path: PUSH_ENABLED=true → both email + push fire → ok=True, push_ok=True."""
    with (
        patch("smtplib.SMTP", return_value=smtp_success_mock),
        patch(
            "src.services.notify_ntfy.httpx.Client",
            return_value=ntfy_success_mock,
        ),
    ):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, f"email should succeed; got detail={body['detail']!r}"
    assert body["push_ok"] is True, f"push should succeed; got push_detail={body['push_detail']!r}"
    assert body["push_detail"] == "sent"


@pytest.mark.asyncio
async def test_digest_fire_push_disabled_email_fires(
    client, monkeypatch, smtp_success_mock, smtp_env
) -> None:
    """Push disabled (PUSH_ENABLED unset) → email fires, push skipped → push_ok=False, push_detail='push_disabled'."""
    monkeypatch.delenv("PUSH_ENABLED", raising=False)

    with patch("smtplib.SMTP", return_value=smtp_success_mock):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, f"email should succeed; got detail={body['detail']!r}"
    assert body["push_ok"] is False
    assert body["push_detail"] == "push_disabled"


@pytest.mark.asyncio
async def test_digest_fire_both_channels_disabled(client, monkeypatch) -> None:
    """Both disabled → ok=False, push_ok=False."""
    monkeypatch.delenv("DIGEST_EMAIL_ENABLED", raising=False)
    monkeypatch.delenv("PUSH_ENABLED", raising=False)

    resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["push_ok"] is False
    assert body["push_detail"] == "push_disabled"


@pytest.mark.asyncio
async def test_digest_fire_push_fails_email_succeeds(
    client, monkeypatch, smtp_success_mock, smtp_env, ntfy_env
) -> None:
    """Push raises / returns error; email still succeeds → ok=True, push_ok=False."""
    failing_ntfy_client = MagicMock()
    failing_ntfy_client.post = MagicMock(side_effect=Exception("ntfy_connect_error"))

    with (
        patch("smtplib.SMTP", return_value=smtp_success_mock),
        patch(
            "src.services.notify_ntfy.httpx.Client",
            return_value=failing_ntfy_client,
        ),
    ):
        resp = await client.post("/api/digest/fire")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, f"email should still succeed; got detail={body['detail']!r}"
    assert body["push_ok"] is False
    # push_detail should reflect the exception type, not 'push_disabled'
    assert body["push_detail"] != "push_disabled"
    assert "unexpected_error" in body["push_detail"]
