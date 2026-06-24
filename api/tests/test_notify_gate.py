"""Kanban #2565 — tier->channel policy for gate notifications (unit tests).

Tests `build_gate_notify_payload` — the PURE function that maps a gate's tier to
the Telegram delivery payload (AC3). No IO; the tier->shape mapping is the heart
of the policy (mode-a-autonomy-boundary.md §3 / async-hitl-gates.md §6):

  decision / hitl  -> simple approve/reject buttons.
  commit (= push)  -> informed-approval: evidence (diff-stat + pre-push scan +
                      test result) IN the card, THEN buttons.
  key / external   -> FYI only, NO answerable buttons (Ring 4, terminal-only).

`callback_data` on every button encodes {gate_id, option}; that is decoded by
the poller (covered in test_telegram_poller.py).
"""

from __future__ import annotations

import pytest

from src.services.notify_gate import build_gate_notify_payload
from src.services.notify_telegram import (
    TELEGRAM_CONTROL_KEY,
    decode_callback_data,
)


def _buttons(payload):
    return payload.get(TELEGRAM_CONTROL_KEY, {}).get("buttons", [])


# ---------------------------------------------------------------------------
# decision / hitl -> simple buttons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["decision", "hitl"])
def test_simple_tiers_get_approve_reject_buttons(tier) -> None:
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Ship it?",
        gate_id=42,
        gate_tier=tier,
        question_payload={"question": "Approve the plan?"},
    )
    btns = _buttons(payload)
    # POSITIVE: two default buttons whose callback_data encodes THIS gate_id.
    assert len(btns) == 2
    decoded = [decode_callback_data(b["callback_data"]) for b in btns]
    assert decoded == [
        {"gate_id": 42, "option": "approve"},
        {"gate_id": 42, "option": "reject"},
    ]
    assert "Approve the plan?" in payload["body"]


def test_simple_tier_uses_explicit_options_when_present() -> None:
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Pick one",
        gate_id=7,
        gate_tier="decision",
        question_payload={
            "question": "Which?",
            "options": [{"id": "a", "label": "Option A"}, {"id": "b", "label": "Option B"}],
        },
    )
    btns = _buttons(payload)
    assert [b["text"] for b in btns] == ["Option A", "Option B"]
    assert [decode_callback_data(b["callback_data"])["option"] for b in btns] == ["a", "b"]


# ---------------------------------------------------------------------------
# key / external -> FYI only, NEVER buttons (Ring 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["key", "external"])
def test_forbidden_tiers_have_no_buttons(tier) -> None:
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Provision the token",
        gate_id=9,
        gate_tier=tier,
        question_payload={"question": "set the key"},
    )
    # NEGATIVE (the security boundary): NO control block -> adapter sends plain
    # text, the operator gets NO answerable approve button.
    assert TELEGRAM_CONTROL_KEY not in payload
    assert _buttons(payload) == []
    # POSITIVE: the message points the operator at the terminal.
    assert "TERMINAL" in payload["body"]


# ---------------------------------------------------------------------------
# commit (push) -> informed-approval: evidence THEN buttons
# ---------------------------------------------------------------------------


def test_commit_tier_renders_evidence_then_buttons() -> None:
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Push to origin?",
        gate_id=15,
        gate_tier="commit",
        question_payload={
            "question": "OK to push?",
            "diff_stat": "3 files +40 -12",
            "pre_push_scan": "clean (no keywords)",
            "test_result": "128 passed",
        },
    )
    body = payload["body"]
    # POSITIVE: all three evidence facets are rendered into the card...
    assert "3 files +40 -12" in body
    assert "clean (no keywords)" in body
    assert "128 passed" in body
    # ...AND the approve/reject buttons are present (informed-approval, not FYI).
    btns = _buttons(payload)
    assert len(btns) == 2
    assert decode_callback_data(btns[0]["callback_data"]) == {"gate_id": 15, "option": "approve"}


def test_commit_tier_flags_missing_evidence_but_still_offers_buttons() -> None:
    # §3: "If evidence is absent, still show the ask but flag 'evidence missing'."
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Push?",
        gate_id=16,
        gate_tier="commit",
        question_payload={"question": "push?"},  # no evidence keys
    )
    assert "evidence missing" in payload["body"].lower()
    # NEGATIVE-of-the-FYI-path: buttons are STILL offered (commit is informed-
    # approval, not Ring-4 forbidden).
    assert len(_buttons(payload)) == 2
