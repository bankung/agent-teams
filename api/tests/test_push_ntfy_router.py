"""Kanban #1192 — POST /api/push/fire contract smoke tests.

Uses the ASGI test client (no real network / no real ntfy call).
send_push is patched at the module level.

Covers:
1. 200 response shape — ok, detail, recipient_topic, message.
2. PUSH_ENABLED not set → ok=False, detail='push_disabled'.
3. PUSH_ENABLED=true + mock ntfy success → ok=True, detail='sent', topic in response.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.notify_ntfy import SendResult


@pytest.mark.asyncio
async def test_push_fire_disabled_returns_200_ok_false(client, monkeypatch) -> None:
    """When PUSH_ENABLED is not set (default), endpoint returns 200 but ok=False."""
    monkeypatch.delenv("PUSH_ENABLED", raising=False)

    resp = await client.post("/api/push/fire")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is False
    assert body["detail"] == "push_disabled"
    # FastAPI response_model=PushFireResponse enforces types; check presence.
    assert "recipient_topic" in body
    assert "message" in body


@pytest.mark.asyncio
async def test_push_fire_enabled_with_mock_ntfy_returns_ok_true(
    client, monkeypatch
) -> None:
    """When PUSH_ENABLED=true and ntfy succeeds, response ok=True."""
    monkeypatch.setenv("PUSH_ENABLED", "true")
    monkeypatch.setenv("NTFY_TOPIC", "bankung-agt-7x4q2k9w")
    monkeypatch.setenv("NTFY_BASE_URL", "https://ntfy.sh")
    monkeypatch.delenv("NTFY_ACCESS_TOKEN", raising=False)

    with patch(
        "src.routers.push_ntfy.send_push",
        return_value=SendResult(ok=True, detail="sent"),
    ):
        resp = await client.post(
            "/api/push/fire",
            json={"message": "smoke test message", "title": "smoke"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["detail"] == "sent"
    assert body["recipient_topic"] == "bankung-agt-7x4q2k9w"
    assert body["message"] == "smoke test message"


@pytest.mark.asyncio
async def test_push_fire_default_body_when_no_json(client, monkeypatch) -> None:
    """POST /api/push/fire with no body uses default message and still returns 200."""
    monkeypatch.delenv("PUSH_ENABLED", raising=False)

    resp = await client.post("/api/push/fire")
    assert resp.status_code == 200

    body = resp.json()
    # Default message from PushFireRequest
    assert body["message"] == "agent-teams push smoke"
