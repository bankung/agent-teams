"""Auditor demo-branch tests — Kanban #1083 (ACs 6 + 7) + #1096 contract guards.

Covers the two demo branches added to `general_node` in nodes.py that drive
the live auditor smokes:

  - AC6 (`AUDITOR retry demo —` prefix): first call emits
    halt_reason='transient_error' + final_result=''; subsequent calls
    (audit_retry_count >= 1) emit final_result='resolved on retry' +
    halt_reason=None.
  - AC7 (`AUDITOR escalate demo —` prefix): first call emits
    halt_reason='ambiguous'; on retry (audit_retry_count >= 1) emits a clean
    final_result='resolved by operator decision' so the post-escalate-resume
    loop reaches DONE.

Strategy: call `general_node` directly with an AgentState dict for the demo
branches. For the auditor state-merge contract (Kanban #1096), call
`auditor_node` / `_apply_escalation_resume` directly and inspect the returned
dict — the branches are pure synchronous early-returns / state mutations.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import nodes
from nodes import (
    AUDITOR_RETRY_CAP_DEFAULT,
    _apply_escalation_resume,
    auditor_node,
    general_node,
)
from state import AgentState


# ---------------------------------------------------------------------------
# AC6 — recoverable retry demo (`AUDITOR retry demo —` prefix)
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
# AC7 — escalate-to-HITL demo (`AUDITOR escalate demo —` prefix)
# ---------------------------------------------------------------------------


def test_auditor_escalate_demo_first_invocation_returns_ambiguous() -> None:
    """Escalate demo on first pass (audit_retry_count=0) emits
    halt_reason='ambiguous' + a final_result that primes the auditor LLM
    toward an ESCALATE verdict."""
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


def test_auditor_escalate_demo_retry_returns_clean() -> None:
    """After the operator picks retry_with_X, the auditor increments
    audit_retry_count + clears halt_reason → general_node re-fires with
    retry_count>=1 → demo emits clean final_result + halt_reason=None
    so the second-pass auditor PASSes."""
    state: AgentState = {
        "task_id": 1002,
        "brief": "AUDITOR escalate demo — operator-decision needed",
        "messages": [],
        "audit_retry_count": 1,
    }
    result = general_node(state)
    assert result["halt_reason"] is None
    assert result["final_result"] == "resolved by operator decision"
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


# ---------------------------------------------------------------------------
# Kanban #1096 — auditor state-merge contract
#
# When the auditor loops (AUTO_RESOLVE) or accepts the operator's verdict,
# it must clear the specialist's halt_reason from state. Otherwise
# `route_from_auditor` (which checks `state.get("halt_reason") is not None`)
# short-circuits to END and the auto_resolve loop never executes.
# ---------------------------------------------------------------------------


class _FakeChatModel:
    """Stand-in for langchain BaseChatModel — captures prompt + returns
    a pre-canned response (sync `invoke` only; the auditor's `ainvoke`
    path falls back to sync if `ainvoke` is absent)."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        return SimpleNamespace(content=self.response_text)


def _install_fake_llm(
    monkeypatch: pytest.MonkeyPatch, verdict_json: dict[str, Any]
) -> _FakeChatModel:
    fake = _FakeChatModel(json.dumps(verdict_json))
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake)
    return fake


async def test_auditor_auto_resolve_under_cap_clears_halt_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTO_RESOLVE under cap: return-dict must carry halt_reason=None so
    route_from_auditor edges to 'supervisor' (not 'END')."""
    _install_fake_llm(
        monkeypatch,
        {
            "verdict": "auto_resolve",
            "severity": "warn",
            "evidence": ["transient error; retry"],
            "action_taken": "retry_with_adjustment",
            "escalation_payload": None,
        },
    )
    state: AgentState = {
        "task_id": 9001,
        "brief": "Do the thing",
        "final_result": "tool error: timeout",
        "halt_reason": "transient_error",  # specialist halted; forces LLM path
        "messages": [],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    assert result["audit_verdict"] == "auto_resolve"
    assert result["audit_retry_count"] == 1
    # Contract: halt_reason MUST be explicitly None (not absent — the merge
    # reducer overwrites only on present keys; absent leaves the stale value).
    assert "halt_reason" in result, (
        "auditor MUST emit halt_reason key on AUTO_RESOLVE under cap "
        "(even if value is None) to overwrite specialist's stale halt"
    )
    assert result["halt_reason"] is None


async def test_apply_escalation_resume_accept_clears_halt_reason() -> None:
    """`accept` operator answer: report carries action_taken='operator_accept',
    audit_verdict='pass', and halt_reason=None so route_from_auditor edges
    to END via the PASS path (not the halt-short-circuit path)."""
    report = {
        "verdict": "escalate",
        "severity": "warn",
        "evidence": [],
        "action_taken": "hitl_escalate",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-05-16T00:00:00Z",
        "retry_count_at_audit": 0,
    }
    state: AgentState = {
        "task_id": 9002,
        "brief": "Do something",
        "halt_reason": "ambiguous",  # specialist's stale halt
        "messages": [],
        "audit_retry_count": 0,
    }
    result = _apply_escalation_resume(state, report, "accept", retry_count=0)
    assert result["audit_verdict"] == "pass"
    assert "halt_reason" in result
    assert result["halt_reason"] is None
    assert result["audit_report"]["action_taken"] == "operator_accept"


async def test_apply_escalation_resume_retry_with_x_under_cap_clears_halt_reason() -> None:
    """`retry_with_X` operator answer under cap: report carries
    action_taken='retry_with_<label>', audit_verdict='auto_resolve',
    audit_retry_count incremented, halt_reason=None so the supervisor loop
    re-fires (not the halt-short-circuit path)."""
    report = {
        "verdict": "escalate",
        "severity": "warn",
        "evidence": [],
        "action_taken": "hitl_escalate",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-05-16T00:00:00Z",
        "retry_count_at_audit": 0,
    }
    state: AgentState = {
        "task_id": 9003,
        "brief": "Do something ambiguous",
        "halt_reason": "ambiguous",  # specialist's stale halt
        "messages": [],
        "audit_retry_count": 0,
    }
    result = _apply_escalation_resume(state, report, "retry_with_pick_a", retry_count=0)
    assert result["audit_verdict"] == "auto_resolve"
    assert result["audit_retry_count"] == 1
    assert "halt_reason" in result
    assert result["halt_reason"] is None
    # Note: _apply_escalation_resume lowercases the operator answer before
    # building the action_taken label.
    assert result["audit_report"]["action_taken"] == "retry_with_pick_a"


# ---------------------------------------------------------------------------
# Negative-coverage guards: paths that MUST NOT clear halt_reason
# ---------------------------------------------------------------------------


async def test_auditor_auto_resolve_at_cap_keeps_giveup_halt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the retry cap, AUTO_RESOLVE halts the graph with
    halt_reason='auditor_giveup' — the clearing fix MUST NOT bleed into this
    branch. This is a regression guard for the Kanban #1096 surgery."""
    _install_fake_llm(
        monkeypatch,
        {
            "verdict": "auto_resolve",
            "severity": "warn",
            "evidence": ["still failing"],
            "action_taken": "retry_with_adjustment",
            "escalation_payload": None,
        },
    )
    state: AgentState = {
        "task_id": 9004,
        "brief": "Do the thing",
        "final_result": "tool error: timeout",
        "halt_reason": "transient_error",
        "messages": [],
        "audit_retry_count": AUDITOR_RETRY_CAP_DEFAULT,
    }
    result = await auditor_node(state)
    assert result["audit_verdict"] == "auto_resolve"
    assert result["halt_reason"] == "auditor_giveup"


async def test_apply_escalation_resume_reject_keeps_operator_rejected_halt() -> None:
    """`reject` operator answer stamps halt_reason='operator_rejected' —
    must NOT be cleared by the Kanban #1096 surgery."""
    report = {
        "verdict": "escalate",
        "severity": "warn",
        "evidence": [],
        "action_taken": "hitl_escalate",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-05-16T00:00:00Z",
        "retry_count_at_audit": 0,
    }
    state: AgentState = {
        "task_id": 9005,
        "brief": "Do something",
        "halt_reason": "ambiguous",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = _apply_escalation_resume(state, report, "reject", retry_count=0)
    assert result["halt_reason"] == "operator_rejected"


async def test_apply_escalation_resume_retry_with_x_at_cap_keeps_giveup_halt() -> None:
    """`retry_with_X` operator answer AT cap stamps
    halt_reason='auditor_giveup' — must NOT be cleared."""
    report = {
        "verdict": "escalate",
        "severity": "warn",
        "evidence": [],
        "action_taken": "hitl_escalate",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-05-16T00:00:00Z",
        "retry_count_at_audit": AUDITOR_RETRY_CAP_DEFAULT,
    }
    state: AgentState = {
        "task_id": 9006,
        "brief": "Do something",
        "halt_reason": "ambiguous",
        "messages": [],
        "audit_retry_count": AUDITOR_RETRY_CAP_DEFAULT,
    }
    result = _apply_escalation_resume(
        state, report, "retry_with_X", retry_count=AUDITOR_RETRY_CAP_DEFAULT
    )
    assert result["halt_reason"] == "auditor_giveup"


async def test_auditor_llm_pass_clears_halt_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM-PASS overrides specialist's stale halt — return-dict must carry
    halt_reason=None. Triggered when heuristic_clean returned False (because
    halt_reason was set OR final_result was too short) but the LLM judges
    the output PASS-worthy. Without the clear, route_from_auditor sees the
    stale halt and short-circuits the worker into the BLOCKED halt body
    instead of the DONE body (the live #1094 escalate-resume bug)."""
    _install_fake_llm(
        monkeypatch,
        {
            "verdict": "pass",
            "severity": "info",
            "evidence": ["LLM judged the output clean"],
            "action_taken": "llm_pass",
            "escalation_payload": None,
        },
    )
    state: AgentState = {
        "task_id": 9007,
        "brief": "Do the thing",
        "final_result": "ok",
        "halt_reason": "ambiguous",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    assert result["audit_verdict"] == "pass"
    assert "halt_reason" in result, (
        "auditor MUST emit halt_reason key on LLM-PASS (even None) to "
        "overwrite specialist's stale halt"
    )
    assert result["halt_reason"] is None
