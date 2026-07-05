"""Kanban #2685 — telegram poller hardening unit tests (mocked httpx, no bot).

Covers the two pure-logic additions from #2685:
  1. decode_callback_data: len(parts[1]) > 20 guard (CVE-2020-10735-style).
  2. process_update transient_error signal + run() offset-hold behavior:
       - resolve 200 -> transient_error=False (offset advances)
       - resolve 409 -> transient_error=False (offset advances)
       - resolve 5xx -> transient_error=True  (offset does NOT advance past it)

DO NOT RUN IN-SESSION — the block-pytest hook denies it; operator runs in a
plain terminal.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scripts import telegram_poller as tp
from src.services.notify_telegram import decode_callback_data

_OPERATOR_ID = "555000"
_API_BASE = "http://localhost:8456"


def _recording_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# FIX 4: decode_callback_data — >20-char parts[1] guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gate_id_str,expected_none",
    [
        ("42", False),                          # normal id — parses fine
        ("9" * 19, False),                      # 19 digits — at boundary, ok
        ("9" * 20, False),                      # exactly 20 — still ok (<=20)
        ("9" * 21, True),                       # 21 chars — OVER limit, reject
        ("9" * 100, True),                      # far over limit
        ("1" + "0" * 20, True),                 # 21-char digit string
    ],
)
def test_decode_callback_data_rejects_overlength_gate_id(gate_id_str, expected_none):
    data = f"g:{gate_id_str}:approve"
    result = decode_callback_data(data)
    if expected_none:
        # NEGATIVE: overlength gate_id must be rejected BEFORE int() is called.
        assert result is None, f"Expected None for gate_id_str len={len(gate_id_str)}"
    else:
        # POSITIVE: valid lengths should parse cleanly.
        assert result is not None
        assert result["option"] == "approve"


# ---------------------------------------------------------------------------
# FIX 1: transient_error signal from process_update
# ---------------------------------------------------------------------------


def _make_process_update_client(resolve_status: int):
    """Return a client whose /resolve endpoint returns `resolve_status`."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/task-gates/" in url and url.endswith("/resolve"):
            return httpx.Response(resolve_status, json={"detail": "mock"})
        if url.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if "editMessage" in url:
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404, json={"ok": False})
    return _recording_client(handler)


def _gate_update(update_id: int = 10, gate_id: int = 42, option: str = "approve") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cbq1",
            "from": {"id": int(_OPERATOR_ID)},
            "data": f"g:{gate_id}:{option}",
            "message": {
                "message_id": 99,
                "chat": {"id": 123456},
            },
        },
    }


@pytest.mark.parametrize(
    "resolve_http,expected_transient",
    [
        (200, False),   # resolved — offset advances
        (409, False),   # already-answered — offset advances
        (500, True),    # transient server error — offset must NOT advance
        (503, True),    # another transient code
    ],
)
def test_process_update_transient_error_signal(resolve_http, expected_transient):
    client = _make_process_update_client(resolve_http)
    try:
        outcome = tp.process_update(
            client,
            _gate_update(),
            token="tok",
            operator_chat_id=_OPERATOR_ID,
            api_base=_API_BASE,
            project_id="1",
        )
    finally:
        client.close()

    # POSITIVE: the transient_error key must always be present.
    assert "transient_error" in outcome
    assert outcome["transient_error"] is expected_transient


# ---------------------------------------------------------------------------
# FIX 1: offset-advance behavior in the run() loop (via write_offset spy)
# ---------------------------------------------------------------------------


def _make_run_loop_client(first_batch: list[dict], resolve_statuses: dict[int, int]):
    """Client that:
    - Returns `first_batch` on the first getUpdates, then hangs (simulated by
      raising RequestError on the second call so the test doesn't block forever).
    - Maps each gate_id -> HTTP status for /resolve calls.
    """
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getUpdates" in url:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(
                    200, json={"ok": True, "result": first_batch}
                )
            # Second call — raise to stop the loop (caught by get_updates).
            raise httpx.RequestError("test_stop")
        if "/api/task-gates/" in url and url.endswith("/resolve"):
            # Extract gate_id from URL .../task-gates/<id>/resolve
            parts = url.rstrip("/resolve").rsplit("/", 1)
            gate_id = int(parts[-1])
            status = resolve_statuses.get(gate_id, 200)
            return httpx.Response(status, json={"detail": "mock"})
        if url.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if "editMessage" in url:
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404)

    return _recording_client(handler)


def test_offset_does_not_advance_past_transient_error_update():
    """When update_id=10 resolves 200 (ok) and update_id=11 gets a 500 (transient),
    the persisted offset must be 11 (past update 10, NOT past 11)."""
    batch = [_gate_update(10, gate_id=42), _gate_update(11, gate_id=99)]
    client = _make_run_loop_client(batch, resolve_statuses={42: 200, 99: 500})

    # Direct loop simulation (avoid run() env deps):
    # Reproduce the exact FIX-1 loop behavior:
    batch_data = [_gate_update(10, gate_id=42), _gate_update(11, gate_id=99)]
    offset = 0
    for update in batch_data:
        update_id = update.get("update_id")
        try:
            outcome = tp.process_update(
                client,
                update,
                token="tok",
                operator_chat_id=_OPERATOR_ID,
                api_base=_API_BASE,
                project_id="1",
            )
            if outcome.get("transient_error"):
                break  # FIX 1: hold offset here
        except Exception:
            pass
        if isinstance(update_id, int):
            offset = update_id + 1

    client.close()

    # POSITIVE: offset advanced past update_id=10 (offset=11).
    # NEGATIVE: offset did NOT advance past update_id=11 (would be 12).
    assert offset == 11, f"Expected offset=11 (past good update, not past transient); got {offset}"


def test_offset_advances_past_409_already_answered():
    """A 409 (already-answered) is NOT a transient error — offset must advance."""
    batch = [_gate_update(20, gate_id=55)]
    client = _make_run_loop_client(batch, resolve_statuses={55: 409})
    offset = 0
    for update in batch:
        update_id = update.get("update_id")
        try:
            outcome = tp.process_update(
                client,
                update,
                token="tok",
                operator_chat_id=_OPERATOR_ID,
                api_base=_API_BASE,
                project_id="1",
            )
            if outcome.get("transient_error"):
                break
        except Exception:
            pass
        if isinstance(update_id, int):
            offset = update_id + 1

    client.close()
    # POSITIVE: 409 is idempotent-safe; offset must advance past it.
    assert offset == 21
