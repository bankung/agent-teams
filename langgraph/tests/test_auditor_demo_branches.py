"""Auditor demo-branch tests — Kanban #1083 (ACs 6 + 7).

Covers the two demo branches added to `general_node` in nodes.py that drive
the live auditor smokes:

  - AC6 (`AUDITOR retry demo —` prefix): first call emits
    halt_reason='transient_error' + final_result=''; subsequent calls
    (audit_retry_count >= 1) emit final_result='resolved on retry' +
    halt_reason=None.
  - AC7 (`AUDITOR escalate demo —` prefix): emits
    halt_reason='ambiguous' + final_result='cannot decide between options
    A and B' so the auditor's LLM path classifies as ESCALATE.

Strategy: call `general_node` directly with an AgentState dict. No LLM,
no graph — the branches are pure synchronous early-returns in the node,
so the test surface is the returned partial-state dict.
"""

from __future__ import annotations

from nodes import general_node
from state import AgentState


# ---------------------------------------------------------------------------
# AC6 — recoverable retry demo
# ---------------------------------------------------------------------------


def test_auditor_retry_demo_first_invocation_returns_transient_error() -> None:
    """First-pass (audit_retry_count=0) returns halt_reason='transient_error'
    with empty final_result so the auditor's LLM classifies as AUTO_RESOLVE."""
    state: AgentState = {
        "task_id": 1001,
        "brief": "AUDITOR retry demo — simulate a recoverable transient error",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = general_node(state)
    assert result["halt_reason"] == "transient_error"
    assert result["final_result"] == ""
    # Sanity: a message was emitted so the conversation log carries a breadcrumb.
    assert len(result["messages"]) == 1


def test_auditor_retry_demo_second_invocation_returns_clean() -> None:
    """Retry (audit_retry_count=1) returns final_result='resolved on retry'
    + halt_reason=None so the auditor's LLM classifies as PASS."""
    state: AgentState = {
        "task_id": 1001,
        "brief": "AUDITOR retry demo — simulate a recoverable transient error",
        "messages": [],
        "audit_retry_count": 1,
    }
    result = general_node(state)
    assert result["final_result"] == "resolved on retry"
    assert result["halt_reason"] is None
    assert len(result["messages"]) == 1


def test_auditor_retry_demo_retry_count_missing_treated_as_zero() -> None:
    """Defensive: a state dict that didn't carry audit_retry_count (e.g.
    first-ever invocation before the auditor stamped one) behaves like
    retry_count=0 — emits transient_error."""
    state: AgentState = {
        "task_id": 1001,
        "brief": "AUDITOR retry demo — first ever pass",
        "messages": [],
        # NO audit_retry_count key.
    }
    result = general_node(state)
    assert result["halt_reason"] == "transient_error"
    assert result["final_result"] == ""


# ---------------------------------------------------------------------------
# AC7 — escalate-to-HITL demo
# ---------------------------------------------------------------------------


def test_auditor_escalate_demo_returns_ambiguous() -> None:
    """Escalate demo emits halt_reason='ambiguous' + a final_result that
    primes the auditor LLM toward an ESCALATE verdict."""
    state: AgentState = {
        "task_id": 1002,
        "brief": "AUDITOR escalate demo — operator-decision needed",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = general_node(state)
    assert result["halt_reason"] == "ambiguous"
    assert result["final_result"] == "cannot decide between options A and B"
    assert len(result["messages"]) == 1


# ---------------------------------------------------------------------------
# Normal-path regression — non-demo briefs unaffected
# ---------------------------------------------------------------------------


def test_normal_brief_unaffected_by_demo_branches() -> None:
    """A brief that doesn't match either demo prefix (and lacks HITL demo
    marker) falls through to the existing general-fallback halt path —
    halt_reason='error', not 'transient_error' or 'ambiguous'."""
    state: AgentState = {
        "task_id": 1003,
        "brief": "Ordinary task description — no demo markers",
        "messages": [],
        "audit_retry_count": 0,
        "assigned_role": None,
    }
    result = general_node(state)
    assert result["halt_reason"] == "error"
    # The fallback path returns the generic "no specialist matched" message
    # in BOTH `messages` and `final_result`.
    assert "general fallback" in result["final_result"]
    assert result["final_result"] != "resolved on retry"
    assert result["final_result"] != "cannot decide between options A and B"
