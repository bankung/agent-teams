"""Kanban #2778 — poller-side command-forward unit tests (mocked httpx, no bot).

Covers the NEW plain-text branch added to `process_update` (D1: the poller
forwards raw text to `POST /api/telegram/command` and relays `reply_text`
back verbatim — no parse/authz/dispatch on the poller side). The EXISTING
callback-tap path (chat-id lock, callback_data decode, resolve mapping) is
covered by `test_telegram_poller.py` and is UNCHANGED by this addition —
these tests only exercise the plain-message branch.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from scripts import telegram_poller as tp

_OPERATOR_ID = "555000"
_FOREIGN_ID = "999111"
_API_BASE = "http://localhost:8456"


def _recording_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# happy path: text message -> forward -> relay
# ---------------------------------------------------------------------------


def test_plain_text_message_is_forwarded_and_relayed() -> None:
    captured: dict[str, Any] = {"forward": None, "send": None}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{_API_BASE}/api/telegram/command":
            captured["forward"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"reply_text": "Pending tasks:\n#1 Foo"})
        if url.endswith("/sendMessage"):
            captured["send"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
        return httpx.Response(404, json={"ok": False})

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 20,
            "message": {
                "message_id": 5,
                "from": {"id": int(_OPERATOR_ID)},
                "chat": {"id": int(_OPERATOR_ID)},
                "text": "/tasks",
            },
        }
        outcome = tp.process_update(
            client,
            update,
            token="tok",
            operator_chat_id=_OPERATOR_ID,
            api_base=_API_BASE,
            project_id="1",
        )
    finally:
        client.close()

    # POSITIVE: the endpoint got the RAW text + the update's own update_id,
    # keyed by the operator's chat id (as a string).
    assert outcome["action"] == "command_forwarded"
    assert outcome["command_status"] == "ok"
    fwd = captured["forward"]
    assert fwd == {"chat_id": _OPERATOR_ID, "update_id": 20, "text": "/tasks"}
    # POSITIVE: the endpoint's reply_text is relayed back VERBATIM.
    assert captured["send"] == {"chat_id": _OPERATOR_ID, "text": "Pending tasks:\n#1 Foo"}


# ---------------------------------------------------------------------------
# security boundary: foreign sender's text is IGNORED, nothing forwarded
# ---------------------------------------------------------------------------


def test_foreign_text_message_is_ignored_and_not_forwarded() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"reply_text": "should never be called"})

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 21,
            "message": {
                "message_id": 6,
                "from": {"id": int(_FOREIGN_ID)},
                "chat": {"id": int(_FOREIGN_ID)},
                "text": "/tasks",
            },
        }
        outcome = tp.process_update(
            client,
            update,
            token="tok",
            operator_chat_id=_OPERATOR_ID,
            api_base=_API_BASE,
            project_id="1",
        )
    finally:
        client.close()

    # NEGATIVE (security boundary, mirrors the callback-path test): the
    # chat-id lock fires BEFORE the message/callback branch — a foreign
    # sender's plain text must not reach the api at all.
    assert outcome["action"] == "ignored_foreign"
    assert calls == []


# ---------------------------------------------------------------------------
# non-text messages keep the pre-#2778 ignored_non_callback behavior
# ---------------------------------------------------------------------------


def test_non_text_message_is_ignored_non_callback_unchanged() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"reply_text": "should never be called"})

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 22,
            "message": {
                "message_id": 7,
                "from": {"id": int(_OPERATOR_ID)},
                "chat": {"id": int(_OPERATOR_ID)},
                "sticker": {"file_id": "abc"},
                # no "text" key — a sticker message, per Telegram's shape.
            },
        }
        outcome = tp.process_update(
            client,
            update,
            token="tok",
            operator_chat_id=_OPERATOR_ID,
            api_base=_API_BASE,
            project_id="1",
        )
    finally:
        client.close()

    # POSITIVE/regression: unchanged from pre-#2778 — no command forward for
    # a non-text message.
    assert outcome["action"] == "ignored_non_callback"
    assert calls == []


def test_empty_text_message_is_ignored_non_callback() -> None:
    """NEGATIVE boundary: whitespace-only text is also NOT forwarded (would
    dispatch as a deny-by-default 'unknown command' on the api side, which is
    a wasted round trip for what is almost certainly an accidental blank
    send)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"reply_text": "unexpected"})

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 23,
            "message": {
                "from": {"id": int(_OPERATOR_ID)},
                "chat": {"id": int(_OPERATOR_ID)},
                "text": "   ",
            },
        }
        outcome = tp.process_update(
            client,
            update,
            token="tok",
            operator_chat_id=_OPERATOR_ID,
            api_base=_API_BASE,
            project_id="1",
        )
    finally:
        client.close()

    assert outcome["action"] == "ignored_non_callback"
    assert calls == []


# ---------------------------------------------------------------------------
# forward failure still relays SOME reply (never silence) + never raises
# ---------------------------------------------------------------------------


def test_forward_http_error_still_relays_a_generic_reply() -> None:
    captured: dict[str, Any] = {"send": None}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{_API_BASE}/api/telegram/command":
            return httpx.Response(500, text="internal error")
        if url.endswith("/sendMessage"):
            captured["send"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 24,
            "message": {
                "from": {"id": int(_OPERATOR_ID)},
                "chat": {"id": int(_OPERATOR_ID)},
                "text": "/gates",
            },
        }
        outcome = tp.process_update(
            client,
            update,
            token="tok",
            operator_chat_id=_OPERATOR_ID,
            api_base=_API_BASE,
            project_id="1",
        )
    finally:
        client.close()

    # POSITIVE: a 500 from the endpoint does not raise, and the operator
    # still gets SOME reply (never total silence on failure).
    assert outcome["command_status"] == "error"
    assert outcome["transient_error"] is False, (
        "a lost command reply is retyped by the operator, not auto-retried "
        "(unlike a gate resolve — see the router-comment rationale)"
    )
    assert captured["send"] is not None
    assert "failed" in captured["send"]["text"].lower()


# ---------------------------------------------------------------------------
# direct unit on forward_command_to_api's status mapping
# ---------------------------------------------------------------------------


def test_forward_command_to_api_maps_success_and_failure() -> None:
    def make(status: int, body: dict | None = None, json_body: bool = True):
        def handler(request: httpx.Request) -> httpx.Response:
            if json_body:
                return httpx.Response(status, json=body or {})
            return httpx.Response(status, text="not json")
        return _recording_client(handler)

    # 200 with a reply_text -> status='ok', reply_text passed through.
    client = make(200, {"reply_text": "hi", "dispatched": True, "verb": "/tasks"})
    try:
        out = tp.forward_command_to_api(
            client, api_base=_API_BASE, chat_id=_OPERATOR_ID, update_id=1, text="/tasks"
        )
    finally:
        client.close()
    assert out == {"status": "ok", "reply_text": "hi"}

    # non-200 -> status='error' with a generic reply (never raises).
    client = make(500, {"detail": "boom"})
    try:
        out = tp.forward_command_to_api(
            client, api_base=_API_BASE, chat_id=_OPERATOR_ID, update_id=1, text="/tasks"
        )
    finally:
        client.close()
    assert out["status"] == "error"

    # 200 but bad JSON -> status='error', not a raise.
    client = make(200, json_body=False)
    try:
        out = tp.forward_command_to_api(
            client, api_base=_API_BASE, chat_id=_OPERATOR_ID, update_id=1, text="/tasks"
        )
    finally:
        client.close()
    assert out["status"] == "error"
