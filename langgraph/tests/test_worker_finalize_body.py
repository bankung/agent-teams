"""Unit tests for the worker's finalize PATCH body construction — Kanban #1096.

The worker's finalize step builds a PATCH body from `final_state` and sends
it to PATCH /api/tasks/{id}. The body shape is a hard contract with the API
validator (services/is_pending.py): `is_pending=True` requires
`process_status=2` (IN_PROGRESS). The previous worker.py finalize sent
`is_pending=True + process_status=4` on every non-HITL halt, which the API
rejected with 400 — stranding all auditor AUTO_RESOLVE retry tasks.

These tests pin down the body shape for each finalize category so a future
refactor can't silently regress:

  1. Generic halt (halt_reason='transient_error') — BLOCKED, no is_pending.
  2. Generic halt (halt_reason='auditor_giveup') — BLOCKED, no is_pending.
  3. HITL pause (interrupt with question_payload) — BLOCKED + question,
     no is_pending (this path was already correct; guards against regression).
  4. DONE (halt_reason=None, no interrupt) — DONE + completed_at, no halt.

The construction logic is pure (`_build_finalize_body(final_state, *,
completed_at)`) so we call it directly — no httpx, no AsyncClient, no I/O.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from worker import (
    STATUS_BLOCKED,
    STATUS_DONE,
    _build_finalize_body,
)


_FAKE_COMPLETED_AT = "2026-05-16T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Case 1: Generic halt — transient_error (the #1096 trigger case)
# ---------------------------------------------------------------------------


def test_finalize_body_generic_halt_transient_error() -> None:
    """halt_reason='transient_error' (auditor AUTO_RESOLVE source) → BLOCKED
    body without is_pending=True. The previous bug shipped is_pending=True
    which the API validator rejected with 400."""
    final_state: dict[str, Any] = {
        "halt_reason": "transient_error",
        "final_result": "specialist halted: tool timeout",
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "transient_error"
    # Hard contract: is_pending must NOT be True (omitted entirely is preferred).
    assert body.get("is_pending", False) is not True
    # No question payload on a non-HITL halt.
    assert "question_payload" not in body
    assert "interaction_kind" not in body
    # Status change reason carried final_result.
    assert "tool timeout" in body["status_change_reason"]
    # No completed_at on a non-DONE body.
    assert "completed_at" not in body


# ---------------------------------------------------------------------------
# Case 2: Generic halt — auditor_giveup (retry cap hit)
# ---------------------------------------------------------------------------


def test_finalize_body_generic_halt_auditor_giveup() -> None:
    """halt_reason='auditor_giveup' (cap hit in AUTO_RESOLVE) → same shape
    as Case 1. Audit fields surface if present in state."""
    audit_report = {
        "verdict": "auto_resolve",
        "severity": "warn",
        "evidence": ["still failing"],
        "action_taken": "auditor_giveup",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-05-16T11:59:00Z",
        "retry_count_at_audit": 3,
    }
    final_state: dict[str, Any] = {
        "halt_reason": "auditor_giveup",
        "final_result": "auditor cap hit; no more retries",
        "audit_report": audit_report,
        "audit_retry_count": 3,
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "auditor_giveup"
    assert body.get("is_pending", False) is not True
    # Audit fields surfaced.
    assert body["audit_report"] is audit_report
    assert body["audit_retry_count"] == 3


def test_finalize_body_generic_halt_ambiguous_no_is_pending() -> None:
    """halt_reason='ambiguous' (auditor ESCALATE specialist halt) → same
    shape. Specifically verifies the no-is_pending invariant under the
    halt_reason that drove half of the #1096 incident."""
    final_state: dict[str, Any] = {
        "halt_reason": "ambiguous",
        "final_result": "cannot decide between A and B",
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "ambiguous"
    assert body.get("is_pending", False) is not True


# ---------------------------------------------------------------------------
# Case 3: HITL pause — interrupt + question_payload
# ---------------------------------------------------------------------------


def _make_interrupt(value: Any) -> Any:
    """Construct a stand-in for langgraph.types.Interrupt (which is an
    attrs/pydantic-ish object with a `.value`). SimpleNamespace gives us
    the attribute access the worker reads (`pause.value`)."""
    return SimpleNamespace(value=value)


def test_finalize_body_hitl_pause_question_only() -> None:
    """A bare question (no options) → halt_reason='question',
    interaction_kind='question', NO is_pending=True."""
    final_state: dict[str, Any] = {
        "__interrupt__": [
            _make_interrupt({"question": "What is the user's name?"})
        ],
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "question"
    assert body["interaction_kind"] == "question"
    assert body["question_payload"]["question"] == "What is the user's name?"
    assert "options" not in body["question_payload"]
    # The HITL pause body was already correct pre-#1096; guard the invariant.
    assert body.get("is_pending", False) is not True


def test_finalize_body_hitl_pause_decision_with_options() -> None:
    """Options-bearing payload → halt_reason='decision', options preserved,
    NO is_pending=True."""
    final_state: dict[str, Any] = {
        "__interrupt__": [
            _make_interrupt(
                {
                    "question": "Deploy where?",
                    "options": ["staging", "prod"],
                }
            )
        ],
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "decision"
    assert body["interaction_kind"] == "decision"
    assert body["question_payload"]["question"] == "Deploy where?"
    assert body["question_payload"]["options"] == ["staging", "prod"]
    assert body.get("is_pending", False) is not True


# ---------------------------------------------------------------------------
# Case 4: DONE — halt_reason=None, no interrupt
# ---------------------------------------------------------------------------


def test_finalize_body_done_clean_run() -> None:
    """halt_reason=None and no __interrupt__ → DONE body with completed_at,
    no halt_reason, no is_pending."""
    final_state: dict[str, Any] = {
        "halt_reason": None,
        "final_result": "Implemented /api/login with JWT validation; tests pass.",
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_DONE
    assert body["completed_at"] == _FAKE_COMPLETED_AT
    assert "halt_reason" not in body
    assert body.get("is_pending", False) is not True
    assert "JWT" in body["status_change_reason"]


def test_finalize_body_done_carries_audit_fields() -> None:
    """A clean run with auditor outputs surfaces audit_report +
    audit_retry_count on the DONE body."""
    audit_report = {
        "verdict": "pass",
        "severity": "info",
        "evidence": ["clean run; final_result='Implemented foo'"],
        "action_taken": "auto_pass",
        "escalation_payload": None,
        "llm_skipped": True,
        "audited_at": "2026-05-16T12:00:00Z",
        "retry_count_at_audit": 1,
    }
    final_state: dict[str, Any] = {
        "halt_reason": None,
        "final_result": "Implemented foo; tests pass.",
        "audit_report": audit_report,
        "audit_retry_count": 1,
    }
    body = _build_finalize_body(final_state, completed_at=_FAKE_COMPLETED_AT)

    assert body["process_status"] == STATUS_DONE
    assert body["audit_report"] is audit_report
    assert body["audit_retry_count"] == 1
