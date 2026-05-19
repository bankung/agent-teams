"""Kanban #1224 — Telegram bot adapter unit tests.

Mocked httpx (httpx.MockTransport) — no real network. Verifies:
- Request URL + body shape (bot/<token>/sendMessage + chat_id + text).
- 200 OK with parsed message_id → ok=True + telegram_msg_id set.
- 4xx/5xx → ok=False with detail containing the status code.
- Telegram API "ok=false" envelope → ok=False with description.
- Missing TELEGRAM_BOT_TOKEN env → ok=False with detail
  'missing_env_TELEGRAM_BOT_TOKEN' (NOT a raised exception; the router
  needs to fall through cleanly on misconfiguration).
- httpx.RequestError → ok=False with detail 'request_error: <ExcName>'.

Live curl smoke deferred to operator (needs real TELEGRAM_BOT_TOKEN +
chat_id pair).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.services.notify_telegram import (
    TELEGRAM_API_BASE,
    TELEGRAM_ENV_TOKEN,
    send_telegram,
)


_VALID_TARGET = {
    "kind": "telegram",
    "chat_id": "123456",
    "priority": 1,
    "label": "operator-default",
}


def _make_client(handler) -> httpx.AsyncClient:
    """Build an AsyncClient wired to a MockTransport returning whatever
    `handler(request) -> httpx.Response` returns. Tests pass this client
    into send_telegram so the production code path is exercised without
    real network."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ---------------------------------------------------------------------------
# happy path: 200 OK with message_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_happy_path_returns_ok_with_msg_id(monkeypatch) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 99, "chat": {"id": 123456}}},
        )

    client = _make_client(handler)
    try:
        result = await send_telegram(
            _VALID_TARGET,
            {"title": "digest", "summary": "3 open"},
            client=client,
        )
    finally:
        await client.aclose()

    assert result["ok"] is True
    assert result["detail"] == "sent"
    assert result["telegram_msg_id"] == 99
    # Verify request URL shape: <base>/bot<token>/sendMessage
    assert captured["url"] == f"{TELEGRAM_API_BASE}/bottest-token-abc/sendMessage"
    # Verify body shape: chat_id + text (serialized payload)
    assert captured["body"]["chat_id"] == "123456"
    assert "title: digest" in captured["body"]["text"]
    assert "summary: 3 open" in captured["body"]["text"]


# ---------------------------------------------------------------------------
# 4xx / 5xx HTTP errors → ok=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_400_returns_ok_false_with_status_detail(monkeypatch) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"ok": false, "description": "Bad Request: chat not found"}')

    client = _make_client(handler)
    try:
        result = await send_telegram(_VALID_TARGET, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is False
    assert "http_400" in result["detail"]
    assert result["telegram_msg_id"] is None


@pytest.mark.asyncio
async def test_send_telegram_500_returns_ok_false(monkeypatch) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = _make_client(handler)
    try:
        result = await send_telegram(_VALID_TARGET, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is False
    assert "http_500" in result["detail"]


# ---------------------------------------------------------------------------
# Telegram API-level "ok=false" envelope → ok=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_api_ok_false_returns_ok_false(monkeypatch) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": False, "description": "Forbidden: bot was blocked by the user"},
        )

    client = _make_client(handler)
    try:
        result = await send_telegram(_VALID_TARGET, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is False
    assert "blocked" in result["detail"]
    assert result["telegram_msg_id"] is None


# ---------------------------------------------------------------------------
# Missing env token → ok=False (no exception raised)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_missing_env_token_returns_ok_false(monkeypatch) -> None:
    monkeypatch.delenv(TELEGRAM_ENV_TOKEN, raising=False)

    # No client needed — the function should short-circuit before any HTTP call.
    result = await send_telegram(_VALID_TARGET, {"x": "y"})

    assert result["ok"] is False
    assert result["detail"] == f"missing_env_{TELEGRAM_ENV_TOKEN}"
    assert result["telegram_msg_id"] is None


# ---------------------------------------------------------------------------
# httpx.RequestError (timeout / network) → ok=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_request_error_returns_ok_false(monkeypatch) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    client = _make_client(handler)
    try:
        result = await send_telegram(_VALID_TARGET, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is False
    assert "request_error" in result["detail"]
    assert "ConnectTimeout" in result["detail"]


# ---------------------------------------------------------------------------
# Missing chat_id on target → ok=False (no env / no HTTP call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_missing_chat_id_returns_ok_false(monkeypatch) -> None:
    # Even with env set, missing chat_id should short-circuit.
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")
    target_no_chat = {"kind": "telegram", "priority": 1, "label": "x"}

    result = await send_telegram(target_no_chat, {"x": "y"})

    assert result["ok"] is False
    assert result["detail"] == "missing_chat_id"


# ---------------------------------------------------------------------------
# Pydantic-model target (not dict) accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_accepts_pydantic_target(monkeypatch) -> None:
    from src.schemas.notification import NotificationTarget

    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 42}}
        )

    client = _make_client(handler)
    try:
        # Pass a Pydantic instance — accessor uses getattr() fallback.
        target = NotificationTarget(
            kind="telegram", chat_id="789", priority=1, label="t"
        )
        result = await send_telegram(target, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is True
    assert result["telegram_msg_id"] == 42
