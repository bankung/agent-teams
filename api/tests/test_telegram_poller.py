"""Kanban #2565 — inbound Telegram poller unit tests (mocked httpx, no bot).

The poller is a DUMB loop (no AI). These tests prove its three load-bearing
behaviors WITHOUT a live bot, via httpx.MockTransport:

  1. callback_data parse -> {gate_id, option} (delegates to decode_callback_data).
  2. chat-id LOCK: a callback from a foreign from.id is IGNORED (the security
     boundary) — no resolve call is made.
  3. resolve-call mapping: an allowed tap POSTs the resolve endpoint with
     {answer: option, provenance: 'telegram', answered_by: <chat_id>} and acks
     the callback. A resolve 409 (already-answered) is handled gracefully.

Offset persistence is covered by the read/write round-trip test. The live
send/button-tap/poll smoke is the OPERATOR's step (needs a real token).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import telegram_poller as tp

_OPERATOR_ID = "555000"
_FOREIGN_ID = "999111"
_API_BASE = "http://localhost:8456"


def _recording_client(handler):
    """A sync httpx.Client wired to a MockTransport. `handler(request)` returns
    the httpx.Response and may append to a captured list for assertions."""
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# offset persistence round-trip
# ---------------------------------------------------------------------------


def test_offset_round_trip_resumes_without_loss() -> None:
    d = Path(tempfile.mkdtemp())
    op = d / "telegram_offset.txt"
    assert tp.read_offset(op) == 0  # absent -> 0
    tp.write_offset(op, 4242)
    assert tp.read_offset(op) == 4242  # a restart reads exactly this


def test_read_offset_tolerates_garbage() -> None:
    d = Path(tempfile.mkdtemp())
    op = d / "telegram_offset.txt"
    op.write_text("not-a-number", encoding="utf-8")
    assert tp.read_offset(op) == 0


# ---------------------------------------------------------------------------
# chat-id lock: foreign sender ignored, NO resolve call
# ---------------------------------------------------------------------------


def test_foreign_chat_id_is_ignored_and_makes_no_resolve_call() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 1,
            "callback_query": {
                "id": "cbq1",
                "from": {"id": int(_FOREIGN_ID)},
                "data": "g:42:approve",
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

    # NEGATIVE (security boundary): ignored, and NOTHING was called upstream —
    # not even an ack (we don't reveal the bot reacts to foreign senders).
    assert outcome["action"] == "ignored_foreign"
    assert calls == []


# ---------------------------------------------------------------------------
# allowed tap -> resolve call mapping + ack
# ---------------------------------------------------------------------------


def test_allowed_callback_resolves_gate_and_acks() -> None:
    captured: dict[str, Any] = {"resolve": None, "ack": None}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/task-gates/" in url and url.endswith("/resolve"):
            captured["resolve"] = {
                "url": url,
                "body": json.loads(request.content.decode()),
                "headers": dict(request.headers),
            }
            return httpx.Response(
                200,
                json={
                    "gate_id": 42,
                    "task_id": 7,
                    "process_status": 1,
                    "open_gate_count_remaining": 0,
                    "resume_context": {},
                    "resolved_at": "2026-06-24T00:00:00Z",
                },
            )
        if url.endswith("/answerCallbackQuery"):
            captured["ack"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(404, json={"ok": False})

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 10,
            "callback_query": {
                "id": "cbq42",
                "from": {"id": int(_OPERATOR_ID)},
                "data": "g:42:approve",
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

    # POSITIVE: a resolve call fired against THIS gate with the locked body.
    assert outcome["action"] == "resolved"
    assert outcome["result"]["status"] == "resolved"
    rc = captured["resolve"]
    assert rc is not None
    assert rc["url"] == f"{_API_BASE}/api/task-gates/42/resolve"
    assert rc["body"] == {
        "answer": "approve",
        "provenance": "telegram",
        "answered_by": _OPERATOR_ID,
    }
    assert rc["headers"]["x-project-id"] == "1"
    # And the tap was acked (spinner cleared).
    assert captured["ack"]["callback_query_id"] == "cbq42"


def test_resolve_409_already_answered_is_handled_gracefully() -> None:
    captured: dict[str, Any] = {"ack": None}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/resolve"):
            return httpx.Response(409, json={"detail": "Gate id=42 is not open"})
        if url.endswith("/answerCallbackQuery"):
            captured["ack"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(404)

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 11,
            "callback_query": {
                "id": "cbq-late",
                "from": {"id": int(_OPERATOR_ID)},
                "data": "g:42:approve",
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

    # 409 maps to 'already' (idempotent) and the operator gets an "already
    # handled" toast — no retry, no crash.
    assert outcome["result"]["status"] == "already"
    assert "already" in captured["ack"]["text"].lower()


def test_malformed_callback_data_is_ignored_with_ack() -> None:
    captured: dict[str, Any] = {"ack": None, "resolve_called": False}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/resolve"):
            captured["resolve_called"] = True
            return httpx.Response(200, json={})
        if url.endswith("/answerCallbackQuery"):
            captured["ack"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = _recording_client(handler)
    try:
        update = {
            "update_id": 12,
            "callback_query": {
                "id": "cbq-bad",
                "from": {"id": int(_OPERATOR_ID)},
                "data": "totally-not-a-gate-callback",
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

    # Bad callback_data -> ignored, NO resolve call (POSITIVE control: the
    # allowed-tap test proves a good one DOES resolve).
    assert outcome["action"] == "ignored_bad_callback_data"
    assert captured["resolve_called"] is False


def test_resolve_gate_via_api_maps_status_codes() -> None:
    # Direct unit on the resolve mapper: 200->resolved, 409->already, 500->error.
    def make(status, body=None):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body or {})
        return _recording_client(handler)

    for status, expected in [(200, "resolved"), (409, "already"), (500, "error")]:
        client = make(status)
        try:
            out = tp.resolve_gate_via_api(
                client,
                api_base=_API_BASE,
                project_id="1",
                gate_id=1,
                option="approve",
                answered_by=_OPERATOR_ID,
            )
        finally:
            client.close()
        assert out["status"] == expected


def test_get_updates_parses_result_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getUpdates" in str(request.url)
        return httpx.Response(
            200, json={"ok": True, "result": [{"update_id": 1}, {"update_id": 2}]}
        )

    client = _recording_client(handler)
    try:
        updates = tp.get_updates(client, token="tok", offset=0, timeout=1)
    finally:
        client.close()
    assert [u["update_id"] for u in updates] == [1, 2]
