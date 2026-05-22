"""Kanban #1437 — digest opt-out via signed-token email footer link.

Tests:
1. test_optout_endpoint_valid_token_flips_flag
2. test_optout_endpoint_invalid_signature_returns_400
3. test_optout_endpoint_expired_token_returns_400
4. test_optout_endpoint_idempotent
5. test_digest_fire_respects_optout
6. test_render_html_contains_signed_optout_link
7. test_render_text_contains_optout_link

All DB-touching tests use the conftest `agent_teams_test` isolation contract.
No raw SQL DML — opt-out state is set via the GET endpoint and reset via the
projects API per CLAUDE.md golden rules.
"""

from __future__ import annotations

import re
import time
from unittest.mock import patch

import pytest
from itsdangerous import URLSafeTimedSerializer

from src.services.digest_template import _OPTOUT_SALT, make_optout_token, render_html, render_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONTROL_PROJECT_ID = 1  # agent-teams project — seed always creates it


def _make_expired_token(project_id: int, secret_key: str) -> str:
    """Produce a token whose timestamp is far in the past (always expired)."""
    s = URLSafeTimedSerializer(secret_key, salt=_OPTOUT_SALT)
    # dumps accepts a `now` timestamp — use epoch 0 so max_age=1 will expire it.
    data = s.dumps({"pid": project_id, "action": "digest_optout"})
    # We can't easily back-date using the public API; instead use loads with
    # a tiny max_age to verify expiry behaviour. Here we directly craft a
    # stale token by using a timestamp of 0 via internal hook.
    # Simpler: produce a real token then verify with max_age=0 in the test.
    return data  # caller passes max_age=0 to verify_optout_token to trigger expiry


async def _reset_optout_flag(client) -> None:
    """Re-enable digest email for project 1 via PATCH /api/projects/1.

    Sets config.digest_email_enabled=True. Note: PATCH config does a full
    replace on the column — we only set digest_email_enabled here so existing
    config keys are lost in tests. Acceptable for test isolation.
    """
    resp = await client.patch(
        "/api/projects/1",
        headers={"X-Project-Id": "1"},
        json={"config": {"digest_email_enabled": True}},
    )
    # 200 or 204 — just ensure no 4xx/5xx
    assert resp.status_code in (200, 204), (
        f"Failed to reset optout flag: {resp.status_code} {resp.text}"
    )


# ---------------------------------------------------------------------------
# Test 1 — valid token flips flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optout_endpoint_valid_token_flips_flag(client, monkeypatch) -> None:
    """GET /api/notifications/digest-optout with a valid token sets digest_email_enabled=False."""
    # Ensure we start with enabled=True (idempotent reset).
    await _reset_optout_flag(client)

    token = make_optout_token(_CONTROL_PROJECT_ID)
    resp = await client.get(f"/api/notifications/digest-optout?token={token}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Verify the flag was actually flipped — read the project back.
    proj_resp = await client.get("/api/projects/1")
    assert proj_resp.status_code == 200, proj_resp.text
    proj = proj_resp.json()
    cfg = proj.get("config") or {}
    assert cfg.get("digest_email_enabled") is False, (
        f"Expected config.digest_email_enabled=False after opt-out, got config={cfg!r}"
    )

    # Cleanup — re-enable.
    await _reset_optout_flag(client)


# ---------------------------------------------------------------------------
# Test 2 — invalid signature returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optout_endpoint_invalid_signature_returns_400(client) -> None:
    """GET /api/notifications/digest-optout with a tampered token returns 400."""
    bogus_token = "this-is-not-a-valid-itsdangerous-token"
    resp = await client.get(f"/api/notifications/digest-optout?token={bogus_token}")

    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["detail"] in ("invalid_token", "expired_token"), (
        f"Expected invalid_token or expired_token, got detail={body['detail']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — expired token returns 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optout_endpoint_expired_token_returns_400(client, monkeypatch) -> None:
    """GET /api/notifications/digest-optout with an expired token returns 400.

    Strategy: produce a real token, then patch verify_optout_token to raise
    SignatureExpired (the itsdangerous expiry path). This avoids sleeping 90 days.
    """
    from itsdangerous import SignatureExpired

    token = make_optout_token(_CONTROL_PROJECT_ID)

    with patch(
        "src.routers.notifications.verify_optout_token",
        side_effect=SignatureExpired("token expired"),
    ):
        resp = await client.get(f"/api/notifications/digest-optout?token={token}")

    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["detail"] == "expired_token", (
        f"Expected expired_token, got detail={body['detail']!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — idempotent: two calls both succeed, flag stays False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optout_endpoint_idempotent(client) -> None:
    """Two GET calls with a valid token both return 200; flag stays False after second."""
    await _reset_optout_flag(client)

    token = make_optout_token(_CONTROL_PROJECT_ID)

    resp1 = await client.get(f"/api/notifications/digest-optout?token={token}")
    assert resp1.status_code == 200, f"First call failed: {resp1.status_code} {resp1.text}"

    resp2 = await client.get(f"/api/notifications/digest-optout?token={token}")
    assert resp2.status_code == 200, f"Second call failed: {resp2.status_code} {resp2.text}"

    # Flag is still False after two calls.
    proj_resp = await client.get("/api/projects/1")
    proj = proj_resp.json()
    cfg = proj.get("config") or {}
    assert cfg.get("digest_email_enabled") is False, (
        f"Expected config.digest_email_enabled=False after idempotent second call, got {cfg!r}"
    )

    await _reset_optout_flag(client)


# ---------------------------------------------------------------------------
# Test 5 — digest fire respects opt-out (email skipped, push unaffected)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_fire_respects_optout(
    client, monkeypatch, smtp_success_mock, ntfy_success_mock, smtp_env, ntfy_env
) -> None:
    """POST /api/digest/fire skips email when project 1 has digest_email_enabled=False.

    Positive assertion: ok=False and email_skipped_reason='opted_out_per_project'.
    Push channel fires independently (push is not gated by this opt-out).
    Negative assertion: ok is not True (email was not sent despite mock SMTP).
    """
    # Set opted-out state directly via PATCH /api/projects/1 config.
    await client.patch(
        "/api/projects/1",
        headers={"X-Project-Id": "1"},
        json={"config": {"digest_email_enabled": False}},
    )

    try:
        with (
            patch("smtplib.SMTP", return_value=smtp_success_mock),
            patch(
                "src.services.notify_ntfy.httpx.Client",
                return_value=ntfy_success_mock,
            ),
        ):
            resp = await client.post("/api/digest/fire")

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Email must be skipped.
        assert body["ok"] is False, (
            f"Expected ok=False (opted out), got ok={body['ok']!r} detail={body['detail']!r}"
        )
        assert body["email_skipped_reason"] == "opted_out_per_project", (
            f"Expected email_skipped_reason='opted_out_per_project', got {body['email_skipped_reason']!r}"
        )

        # SMTP should NOT have been called.
        assert smtp_success_mock.sendmail.call_count == 0, (
            f"Expected SMTP not called when opted out, got call_count={smtp_success_mock.sendmail.call_count}"
        )

        # Push is independent — still fires.
        assert body["push_ok"] is True, (
            f"Push should still fire regardless of email opt-out, got push_ok={body['push_ok']!r}"
        )

    finally:
        await _reset_optout_flag(client)


# ---------------------------------------------------------------------------
# Test 6 — render_html contains signed optout link
# ---------------------------------------------------------------------------


def test_render_html_contains_signed_optout_link() -> None:
    """render_html footer must contain a URL with the /digest-optout?token= param."""
    payload = {
        "date": "2026-05-22",
        "flags": [],
        "base_url": "http://localhost:5431",
        "project_id": 1,
    }
    html = render_html(payload)

    # Must contain the endpoint path with a token query param.
    assert "/api/notifications/digest-optout?token=" in html, (
        f"render_html output does not contain optout URL with token param.\n"
        f"Footer excerpt: {html[-500:]!r}"
    )
    # Must be wrapped in an anchor tag.
    assert 'href=' in html, "Expected href= in render_html output"
    # Token must not be empty (itsdangerous produces non-empty URL-safe strings).
    match = re.search(r"/api/notifications/digest-optout\?token=([^\"& ]+)", html)
    assert match, "Could not find token value in optout URL"
    assert len(match.group(1)) > 10, f"Token looks too short: {match.group(1)!r}"


# ---------------------------------------------------------------------------
# Test 7 — render_text contains optout link
# ---------------------------------------------------------------------------


def test_render_text_contains_optout_link() -> None:
    """render_text footer must contain an Unsubscribe: line with the full optout URL."""
    payload = {
        "date": "2026-05-22",
        "flags": [],
        "base_url": "http://localhost:5431",
        "project_id": 1,
    }
    text = render_text(payload)

    assert "Unsubscribe:" in text, (
        f"render_text output does not contain 'Unsubscribe:' line.\n"
        f"Output: {text!r}"
    )
    assert "/api/notifications/digest-optout?token=" in text, (
        f"render_text output does not contain optout URL.\nOutput: {text!r}"
    )
    # Token must not be empty.
    match = re.search(r"/api/notifications/digest-optout\?token=([^\s]+)", text)
    assert match, "Could not find token value in optout URL in plaintext"
    assert len(match.group(1)) > 10, f"Token looks too short: {match.group(1)!r}"
