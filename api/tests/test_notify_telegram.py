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
    CALLBACK_DATA_MAX_BYTES,
    TELEGRAM_API_BASE,
    TELEGRAM_CONTROL_KEY,
    TELEGRAM_ENV_TOKEN,
    decode_callback_data,
    encode_callback_data,
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


@pytest.mark.parametrize("status_code", [400, 500])
@pytest.mark.asyncio
async def test_send_telegram_non_200_returns_ok_false(monkeypatch, status_code) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=f"error body for {status_code}")

    client = _make_client(handler)
    try:
        result = await send_telegram(_VALID_TARGET, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is False
    assert f"http_{status_code}" in result["detail"]
    assert result["telegram_msg_id"] is None


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
# Pydantic-model target via .model_dump() accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_telegram_accepts_model_dump_target(monkeypatch) -> None:
    from src.schemas.notification import NotificationTarget

    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 42}}
        )

    client = _make_client(handler)
    try:
        # Callers with a Pydantic instance dump to dict first.
        target = NotificationTarget(
            kind="telegram", chat_id="789", priority=1, label="t"
        ).model_dump()
        result = await send_telegram(target, {"x": "y"}, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is True
    assert result["telegram_msg_id"] == 42


# ===========================================================================
# Kanban #2565 — inline buttons (callback_data + reply_markup)
# ===========================================================================


def test_encode_decode_callback_data_round_trips() -> None:
    enc = encode_callback_data(42, "approve")
    assert enc == "g:42:approve"
    assert decode_callback_data(enc) == {"gate_id": 42, "option": "approve"}


def test_encode_callback_data_within_64_bytes() -> None:
    # Even a long option id stays within Telegram's 64-byte cap (truncated).
    enc = encode_callback_data(999999, "x" * 200)
    assert len(enc.encode("utf-8")) <= CALLBACK_DATA_MAX_BYTES


def test_encode_callback_data_multibyte_boundary_decodes_cleanly() -> None:
    # Thai characters are 3 bytes each in UTF-8. A 200-char Thai option would be
    # 600 bytes; after truncation the encoded result must decode cleanly (no
    # UnicodeDecodeError) and round-trip through decode_callback_data correctly.
    thai_option = "ก" * 200  # 200 × 3 bytes = 600 bytes
    enc = encode_callback_data(1, thai_option)
    # Must stay within the 64-byte cap.
    assert len(enc.encode("utf-8")) <= CALLBACK_DATA_MAX_BYTES
    # Must decode cleanly — no UnicodeDecodeError / replacement characters.
    enc.encode("utf-8").decode("utf-8")  # raises if corrupt
    # Must round-trip through decode_callback_data without None.
    decoded = decode_callback_data(enc)
    assert decoded is not None
    assert decoded["gate_id"] == 1
    # The decoded option is a prefix of the original (no garbled tail).
    assert thai_option.startswith(decoded["option"])


def test_decode_callback_data_option_may_contain_colons() -> None:
    # Only the first two ':' delimit prefix + gate_id; the rest is the option.
    assert decode_callback_data("g:7:opt:with:colons") == {
        "gate_id": 7,
        "option": "opt:with:colons",
    }


@pytest.mark.parametrize(
    "bad",
    ["x:1:y", "g:notanint:y", "g:1", "garbage", "", "g::"],
)
def test_decode_callback_data_rejects_non_gate_payloads(bad) -> None:
    # POSITIVE control: a well-formed one decodes; these all return None so the
    # poller ignores foreign / malformed callbacks instead of raising.
    assert decode_callback_data("g:5:ok") == {"gate_id": 5, "option": "ok"}
    assert decode_callback_data(bad) is None


@pytest.mark.asyncio
async def test_send_telegram_attaches_inline_keyboard_when_buttons_present(
    monkeypatch,
) -> None:
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    payload = {
        "title": "Gate #42 [decision] — Ship it?",
        "body": "approve or reject",
        TELEGRAM_CONTROL_KEY: {
            "buttons": [
                {"text": "Approve", "callback_data": "g:42:approve"},
                {"text": "Reject", "callback_data": "g:42:reject"},
            ]
        },
    }
    client = _make_client(handler)
    try:
        result = await send_telegram(_VALID_TARGET, payload, client=client)
    finally:
        await client.aclose()

    assert result["ok"] is True
    body = captured["body"]
    # POSITIVE: reply_markup carries an inline keyboard, one button per row,
    # callback_data preserved.
    assert "reply_markup" in body
    kb = body["reply_markup"]["inline_keyboard"]
    assert kb == [
        [{"text": "Approve", "callback_data": "g:42:approve"}],
        [{"text": "Reject", "callback_data": "g:42:reject"}],
    ]
    # NEGATIVE: the control block never leaks into the visible text.
    assert TELEGRAM_CONTROL_KEY not in body["text"]
    assert "buttons" not in body["text"]


@pytest.mark.asyncio
async def test_send_telegram_plain_text_path_has_no_reply_markup(monkeypatch) -> None:
    # The plain-text path is UNCHANGED — a payload with no control block sends
    # NO reply_markup (regression guard for the existing #1224 contract).
    monkeypatch.setenv(TELEGRAM_ENV_TOKEN, "test-token-abc")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = _make_client(handler)
    try:
        result = await send_telegram(
            _VALID_TARGET, {"title": "digest", "summary": "3 open"}, client=client
        )
    finally:
        await client.aclose()

    assert result["ok"] is True
    assert "reply_markup" not in captured["body"]
    assert "title: digest" in captured["body"]["text"]
