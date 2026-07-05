"""Kanban #2565/#2721 — tier->channel policy for gate notifications (unit tests).

Tests `build_gate_notify_payload` — the PURE function that maps a gate's tier to
the Telegram delivery payload (AC3). No IO; the tier->shape mapping is the heart
of the policy (mode-a-autonomy-boundary.md §3 / async-hitl-gates.md §6):

  decision / hitl  -> HTML message + approve/reject buttons.
  commit (= push)  -> informed-approval: evidence IN the HTML card, THEN buttons.
  key / external   -> FYI only, HTML message, NO answerable buttons (Ring 4).

Kanban #2721: payload now carries _html (HTML-formatted message) instead of
title/body/url/task_id keys. Assertions updated accordingly.

`callback_data` on every button encodes {gate_id, option}; that is decoded by
the poller (covered in test_telegram_poller.py).
"""

from __future__ import annotations

import pytest

from src.services.notify_gate import build_gate_notify_payload
from src.services.notify_telegram import (
    TELEGRAM_CONTROL_KEY,
    TELEGRAM_HTML_KEY,
    decode_callback_data,
)


def _buttons(payload):
    return payload.get(TELEGRAM_CONTROL_KEY, {}).get("buttons", [])


def _html(payload) -> str:
    return payload.get(TELEGRAM_HTML_KEY, "")


# ---------------------------------------------------------------------------
# Shared AC guards — applied across all tiers
# ---------------------------------------------------------------------------


def _assert_no_raw_labels(payload: dict) -> None:
    """No raw key-label lines in the HTML string (AC1)."""
    html = _html(payload)
    for label in ("title:", "body:", "task_id:", "url:"):
        assert label not in html, f"raw label '{label}' found in HTML"


def _assert_no_url(payload: dict) -> None:
    """url key dropped entirely (AC2)."""
    assert "url" not in payload
    assert "/tasks/" not in _html(payload)


def _assert_bold_title(payload: dict, title: str) -> None:
    """Bold title present in HTML (AC3)."""
    import html as _h
    assert f"<b>{_h.escape(title, quote=False)}</b>" in _html(payload)


def _assert_status_on_own_line(payload: dict, status_prefix: str) -> None:
    """Status tag is the FIRST line (AC3 — separated from title)."""
    first_line = _html(payload).split("\n")[0]
    assert first_line.startswith(status_prefix)


# ---------------------------------------------------------------------------
# decision / hitl -> HTML message + simple buttons
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
    # Question present in HTML, not in a raw key.
    assert "Approve the plan?" in _html(payload)
    _assert_no_raw_labels(payload)
    _assert_no_url(payload)
    _assert_bold_title(payload, "Ship it?")
    _assert_status_on_own_line(payload, f"Gate · {tier}")


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


def test_simple_tier_empty_question_still_has_title_and_tier() -> None:
    # AC4: generic decision gate (empty question) must NOT reduce to
    # "Approve or reject this gate." — title + tier must be present.
    payload = build_gate_notify_payload(
        task_id=5,
        task_title="Deploy staging",
        gate_id=99,
        gate_tier="decision",
        question_payload=None,
    )
    html = _html(payload)
    assert "Deploy staging" in html
    assert "decision" in html
    # No bare fallback phrase.
    assert "Approve or reject this gate." not in html
    _assert_no_raw_labels(payload)
    _assert_no_url(payload)


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
    # NEGATIVE (the security boundary): NO control block -> adapter sends HTML,
    # the operator gets NO answerable approve button.
    assert TELEGRAM_CONTROL_KEY not in payload
    assert _buttons(payload) == []
    # POSITIVE: the message points the operator at the terminal.
    assert "TERMINAL" in _html(payload)
    _assert_no_raw_labels(payload)
    _assert_no_url(payload)
    _assert_bold_title(payload, "Provision the token")
    _assert_status_on_own_line(payload, f"Gate · {tier}")


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
    html = _html(payload)
    # POSITIVE: all three evidence facets are rendered into the card...
    assert "3 files +40 -12" in html
    assert "clean (no keywords)" in html
    assert "128 passed" in html
    # ...AND the approve/reject buttons are present (informed-approval, not FYI).
    btns = _buttons(payload)
    assert len(btns) == 2
    assert decode_callback_data(btns[0]["callback_data"]) == {"gate_id": 15, "option": "approve"}
    _assert_no_raw_labels(payload)
    _assert_no_url(payload)
    _assert_bold_title(payload, "Push to origin?")


def test_commit_tier_flags_missing_evidence_but_still_offers_buttons() -> None:
    # §3: "If evidence is absent, still show the ask but flag 'evidence missing'."
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Push?",
        gate_id=16,
        gate_tier="commit",
        question_payload={"question": "push?"},  # no evidence keys
    )
    assert "evidence missing" in _html(payload).lower()
    # NEGATIVE-of-the-FYI-path: buttons are STILL offered (commit is informed-
    # approval, not Ring-4 forbidden).
    assert len(_buttons(payload)) == 2
    _assert_no_raw_labels(payload)
    _assert_no_url(payload)


# ---------------------------------------------------------------------------
# HTML escaping in gate payload (injection guard)
# ---------------------------------------------------------------------------


def test_gate_payload_escapes_html_in_title() -> None:
    payload = build_gate_notify_payload(
        task_id=1,
        task_title="Fix <b>auth</b> & login",
        gate_id=7,
        gate_tier="decision",
        question_payload={"question": "OK?"},
    )
    html = _html(payload)
    assert "&lt;b&gt;" in html
    assert "&amp; login" in html
    assert "<script>" not in html
