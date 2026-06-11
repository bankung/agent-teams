"""Auditor node tests — Kanban #952.

Covers ACs 3 + 4 of the locked design:
  - heuristic pre-filter (skip LLM on clean run)
  - AUTO_RESOLVE retry counter + cap → halt with 'auditor_giveup'
  - ESCALATE emits a __interrupt__ via request_user_input
  - PASS writes audit_report to state

Strategy:
  - Direct calls to `auditor_node` for the heuristic-skip path (no LLM
    dependency — the structural check covers it without invoking make_chat_model).
  - For LLM-path tests: monkeypatch `make_chat_model` to return a stub that
    yields a canned JSON verdict. Avoids touching the real ollama base URL.
  - For escalate path: build a one-node graph wrapping `auditor_node` with
    InMemorySaver; the first ainvoke returns final_state with __interrupt__
    populated (mirrors the HITL pattern in test_hitl.py).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

import nodes
from nodes import (
    AUDITOR_RETRY_CAP_DEFAULT,
    auditor_node,
    route_from_auditor,
)
from state import AgentState


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip provider env-vars so tests don't accidentally hit live LLMs."""
    for var in (
        "LANGGRAPH_LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class _FakeChatModel:
    """Stand-in for a langchain BaseChatModel — captures the prompt and
    returns a pre-canned response. The auditor uses `ainvoke` (preferred)
    or falls back to sync `invoke`; we expose only `invoke` so the sync
    fallback path is exercised."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[list[Any]] = []

    def invoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        return SimpleNamespace(content=self.response_text)


def _install_fake_llm(
    monkeypatch: pytest.MonkeyPatch, verdict_json: dict[str, Any]
) -> _FakeChatModel:
    """Patch nodes.make_chat_model to return _FakeChatModel emitting the JSON."""
    fake = _FakeChatModel(json.dumps(verdict_json))
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake)
    return fake


def _install_failing_llm(monkeypatch: pytest.MonkeyPatch) -> _FakeChatModel:
    """Patch make_chat_model to track NOT being called (fails on access)."""
    sentinel = _FakeChatModel("MUST NOT BE INVOKED")

    def _trap() -> _FakeChatModel:
        raise AssertionError(
            "make_chat_model was invoked but the heuristic should have skipped"
        )

    monkeypatch.setattr(nodes, "make_chat_model", _trap)
    return sentinel


# ---------------------------------------------------------------------------
# Test 1: heuristic pre-filter skips LLM on clean run
# ---------------------------------------------------------------------------


async def test_heuristic_pre_filter_skips_llm_on_clean_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A specialist that returns halt_reason=None, a non-empty final_result,
    and no failing tool calls should auto-PASS without invoking the LLM."""
    _install_failing_llm(monkeypatch)  # raises if make_chat_model is called

    state: AgentState = {
        "task_id": 1,
        "brief": "Add a login endpoint",
        "final_result": "Implemented /api/login with JWT validation; tests pass.",
        "messages": [
            HumanMessage(content="Add a login endpoint"),
            AIMessage(content="Done — see _scratch/login.md"),
        ],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    assert result["audit_verdict"] == "pass"
    report = result["audit_report"]
    assert report["verdict"] == "pass"
    assert report["llm_skipped"] is True
    assert report["action_taken"] == "auto_pass"
    assert report["severity"] == "info"
    assert "retry_count_at_audit" in report
    assert report["retry_count_at_audit"] == 0


# ---------------------------------------------------------------------------
# Test 2: heuristic pre-filter does NOT skip on halt
# ---------------------------------------------------------------------------


async def test_heuristic_pre_filter_does_not_skip_on_halt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """halt_reason set → heuristic skip path is disabled, LLM is invoked."""
    fake = _install_fake_llm(
        monkeypatch,
        {
            "verdict": "auto_resolve",
            "severity": "warn",
            "evidence": ["specialist halted with error"],
            "action_taken": "retry_with_adjustment",
            "escalation_payload": None,
        },
    )
    state: AgentState = {
        "task_id": 2,
        "brief": "Refactor something",
        "final_result": "Halted: tool_loop_max_iterations exceeded",
        "halt_reason": "error",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    # LLM was called.
    assert len(fake.calls) == 1
    # Verdict came from the LLM JSON.
    assert result["audit_verdict"] == "auto_resolve"
    assert result["audit_report"]["llm_skipped"] is False


# ---------------------------------------------------------------------------
# Test 3: heuristic pre-filter does NOT skip on empty final_result
# ---------------------------------------------------------------------------


async def test_heuristic_pre_filter_does_not_skip_on_empty_final_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short / empty final_result triggers the LLM path even if no halt."""
    fake = _install_fake_llm(
        monkeypatch,
        {
            "verdict": "escalate",
            "severity": "warn",
            "evidence": ["specialist returned no output"],
            "action_taken": "hitl_escalate",
            "escalation_payload": {
                "question": "Specialist returned no output; what now?",
                "options": ["accept", "retry_with_reprompt", "reject"],
            },
        },
    )
    # Even though halt_reason is None, final_result is too short.
    state: AgentState = {
        "task_id": 3,
        "brief": "Write a hello world",
        "final_result": "ok",  # 2 chars; below threshold
        "messages": [],
        "audit_retry_count": 0,
    }
    # The LLM verdict is 'escalate' which would normally call request_user_input
    # outside of a graph context (raises GraphInterrupt). Use an InMemorySaver
    # wrapper here so the interrupt is caught by LangGraph. Use AgentState
    # directly (not a custom mini-state) so the `messages` channel reducer
    # matches the auditor_node's expectations (add_messages).
    builder = StateGraph(AgentState)
    builder.add_node("only", auditor_node)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "task-3"}}
    final = await graph.ainvoke(state, config=cfg)
    # LLM was invoked AND escalate fired (graph paused via interrupt).
    assert len(fake.calls) == 1
    assert "__interrupt__" in final  # escalate triggered the HITL pause


# ---------------------------------------------------------------------------
# Test 4: AUTO_RESOLVE increments retry counter
# ---------------------------------------------------------------------------


async def test_auto_resolve_retry_count_increments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auto_resolve verdict under the cap returns audit_retry_count = old + 1."""
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
        "task_id": 4,
        "brief": "Do the thing",
        "final_result": "tool error: timeout",
        "halt_reason": "error",  # forces LLM path
        "messages": [],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    assert result["audit_verdict"] == "auto_resolve"
    assert result["audit_retry_count"] == 1
    # halt_reason NOT set since we're under the cap
    assert result.get("halt_reason") is None
    # The brief should have an appended NOTE for the next specialist pass.
    new_brief = result["brief"]
    assert "NOTE (auditor retry 1/3" in new_brief

    # Apply twice more — should reach 3 at the cap.
    state2 = {**state, "audit_retry_count": 1}
    r2 = await auditor_node(state2)
    assert r2["audit_retry_count"] == 2


# ---------------------------------------------------------------------------
# Test 5: AUTO_RESOLVE at cap halts with 'auditor_giveup'
# ---------------------------------------------------------------------------


async def test_auto_resolve_cap_halts_with_giveup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When audit_retry_count == AUDITOR_RETRY_CAP_DEFAULT, an auto_resolve
    verdict short-circuits to halt_reason='auditor_giveup' instead of looping."""
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
        "task_id": 5,
        "brief": "Do the thing",
        "final_result": "tool error: timeout",
        "halt_reason": "error",  # forces LLM path
        "messages": [],
        "audit_retry_count": AUDITOR_RETRY_CAP_DEFAULT,
    }
    result = await auditor_node(state)
    assert result["audit_verdict"] == "auto_resolve"
    assert result["halt_reason"] == "auditor_giveup"
    assert result["audit_report"]["action_taken"] == "auditor_giveup"
    # The conditional edge routes to END when halt_reason is set.
    assert route_from_auditor(result) == "END"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Test 6: ESCALATE emits an interrupt with the question_payload
# ---------------------------------------------------------------------------


async def test_escalate_emits_interrupt_with_question_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An LLM verdict=escalate triggers request_user_input → the graph pauses
    with __interrupt__ set and the payload mirrors the locked shape."""
    escalation_payload = {
        "question": "Ambiguous brief — what should we do?",
        "options": ["accept", "retry_with_clarify", "reject"],
    }
    _install_fake_llm(
        monkeypatch,
        {
            "verdict": "escalate",
            "severity": "critical",
            "evidence": ["brief is ambiguous"],
            "action_taken": "hitl_escalate",
            "escalation_payload": escalation_payload,
        },
    )
    builder = StateGraph(AgentState)
    builder.add_node("only", auditor_node)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "task-6"}}

    state: AgentState = {
        "task_id": 6,
        "brief": "Do something ambiguous",
        "final_result": "I am uncertain about what to do",
        "halt_reason": "error",  # forces LLM path
        "messages": [],
        "audit_retry_count": 0,
    }
    final = await graph.ainvoke(state, config=cfg)
    assert "__interrupt__" in final
    interrupts = final["__interrupt__"]
    assert len(interrupts) == 1
    payload = interrupts[0].value
    assert isinstance(payload, dict)
    assert payload.get("question") == "Ambiguous brief — what should we do?"
    assert payload.get("options") == ["accept", "retry_with_clarify", "reject"]


# ---------------------------------------------------------------------------
# Test 7: PASS writes audit_report to state
# ---------------------------------------------------------------------------


async def test_pass_writes_audit_report_jsonb_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean specialist run produces a fully-shaped audit_report dict in
    the returned state update — the worker reads this and writes it to
    tasks.audit_report on finalize."""
    _install_failing_llm(monkeypatch)
    state: AgentState = {
        "task_id": 7,
        "brief": "Trivial task",
        "final_result": "Done — added the missing import and re-ran tests.",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    report = result["audit_report"]
    # Shape lock — exactly the keys the design doc Q5=A specifies.
    expected_keys = {
        "verdict",
        "severity",
        "evidence",
        "action_taken",
        "escalation_payload",
        "llm_skipped",
        "audited_at",
        "retry_count_at_audit",
    }
    assert set(report.keys()) == expected_keys
    assert report["verdict"] == "pass"
    assert report["llm_skipped"] is True
    assert report["escalation_payload"] is None
    assert isinstance(report["evidence"], list) and report["evidence"]
    assert isinstance(report["audited_at"], str) and report["audited_at"].endswith(
        "Z"
    )
    # Conditional edge routes a clean PASS to END.
    assert route_from_auditor(result) == "END"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Bonus coverage: heuristic blocks on failing tool result
# ---------------------------------------------------------------------------


async def test_heuristic_blocks_on_failing_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ToolMessage with success=False payload disables the skip path."""
    fake = _install_fake_llm(
        monkeypatch,
        {
            "verdict": "auto_resolve",
            "severity": "warn",
            "evidence": ["tool failed"],
            "action_taken": "retry_with_adjustment",
            "escalation_payload": None,
        },
    )
    bad_tool_msg = ToolMessage(
        content=json.dumps(
            {
                "success": False,
                "error_code": "internal_error",
                "error_msg": "tool blew up",
            }
        ),
        tool_call_id="tc-1",
    )
    state: AgentState = {
        "task_id": 8,
        "brief": "Do the thing",
        "final_result": "Some long-enough output to pass the length check.",
        "messages": [bad_tool_msg],
        "audit_retry_count": 0,
    }
    await auditor_node(state)
    # The LLM was invoked → heuristic correctly blocked the auto-pass path.
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# #1973 — parser robustness tests
# ---------------------------------------------------------------------------


def test_fenced_json_verdict_parses():
    """#1973: _parse_llm_verdict strips ```json fences before attempting parse.

    POSITIVE: fenced JSON returns the dict.
    NEGATIVE: the same content without fences also parses (proves non-vacuous).
    """
    from nodes import _parse_llm_verdict

    payload = {
        "verdict": "pass",
        "severity": "info",
        "evidence": ["looks clean"],
        "action_taken": "llm_pass",
        "escalation_payload": None,
    }
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    assert _parse_llm_verdict(fenced) == payload
    # Same content bare also parses.
    assert _parse_llm_verdict(json.dumps(payload)) == payload


def test_fenced_invalid_json_brace_scan_finds_embedded_object():
    """Brace-scan fallback runs on the de-fenced string.

    Input: a code fence whose content is NOT valid JSON at the top level
    (extra prose before the object), so strict json.loads fails.  The
    brace-scan must find the embedded {...} inside the de-fenced text.

    POSITIVE: embedded JSON dict returned.
    NEGATIVE: raw fenced text (with fence markers) would confuse the brace
    scan; the fix ensures scan runs on defenced text so the result is non-None.
    """
    from nodes import _parse_llm_verdict

    inner = json.dumps({
        "verdict": "pass",
        "severity": "info",
        "evidence": [],
        "action_taken": "llm_pass",
        "escalation_payload": None,
    })
    # Wrap with prose INSIDE the fence so strict json.loads on defenced fails,
    # but the brace scan should still extract the embedded object.
    fenced_with_prose = f"```json\nHere is the verdict: {inner}\n```"
    result = _parse_llm_verdict(fenced_with_prose)
    assert result is not None, "brace-scan on defenced text failed to find embedded JSON"
    assert result["verdict"] == "pass"


def test_prose_wrapped_json_parses_via_brace_scan():
    """#1973: prose before/after a JSON object is handled by the brace scan.

    POSITIVE: brace-scan extracts the embedded object.
    """
    from nodes import _parse_llm_verdict

    payload = {"verdict": "escalate", "severity": "warn", "evidence": [], "action_taken": "hitl_escalate", "escalation_payload": None}
    wrapped = "Here is my verdict:\n" + json.dumps(payload) + "\nThat's it."
    result = _parse_llm_verdict(wrapped)
    assert result is not None
    assert result["verdict"] == "escalate"


async def test_malformed_then_valid_uses_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1973: first call returns garbage, second returns valid JSON → verdict honored,
    exactly 2 LLM calls made.
    """
    valid_verdict = {
        "verdict": "pass",
        "severity": "info",
        "evidence": ["ok on retry"],
        "action_taken": "llm_pass",
        "escalation_payload": None,
    }

    class _TwoShotFake:
        def __init__(self) -> None:
            self.calls: list[list[Any]] = []

        def invoke(self, messages: list[Any]) -> Any:
            self.calls.append(messages)
            if len(self.calls) == 1:
                return SimpleNamespace(content="NOT JSON AT ALL !!!")
            return SimpleNamespace(content=json.dumps(valid_verdict))

    fake = _TwoShotFake()
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake)

    state: AgentState = {
        "task_id": 20,
        "brief": "Do a thing",
        "final_result": "Halted unexpectedly",
        "halt_reason": "error",
        "messages": [],
        "audit_retry_count": 0,
    }
    result = await auditor_node(state)
    assert len(fake.calls) == 2, f"expected 2 LLM calls, got {len(fake.calls)}"
    assert result["audit_verdict"] == "pass"
    assert result["audit_report"]["verdict"] == "pass"


async def test_malformed_twice_escalates_and_logs_raw(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#1973: both attempts return garbage → default escalate + raw text logged.

    POSITIVE: audit_verdict == 'escalate' (fail-safe default).
    NEGATIVE: the raw LLM output appears in the WARNING log so incidents are
    investigable (not a bare 'malformed response' with no detail).
    """
    import logging

    class _AlwaysBadFake:
        def __init__(self) -> None:
            self.calls: list[list[Any]] = []

        def invoke(self, messages: list[Any]) -> Any:
            self.calls.append(messages)
            return SimpleNamespace(content="TOTALLY UNPARSEABLE OUTPUT XYZ")

    fake = _AlwaysBadFake()
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake)

    state: AgentState = {
        "task_id": 21,
        "brief": "Something",
        "final_result": "error happened",
        "halt_reason": "error",
        "messages": [],
        "audit_retry_count": 0,
    }

    builder = StateGraph(AgentState)
    builder.add_node("only", auditor_node)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "task-21"}}

    with caplog.at_level(logging.WARNING, logger="langgraph.nodes"):
        final = await graph.ainvoke(state, config=cfg)

    # Both LLM calls were made.
    assert len(fake.calls) == 2, f"expected 2 LLM calls, got {len(fake.calls)}"
    # Escalate path fires (graph pauses via interrupt).
    assert "__interrupt__" in final
    # Raw text appears in at least one WARNING log entry.
    raw_logs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("TOTALLY UNPARSEABLE OUTPUT XYZ" in m for m in raw_logs), (
        f"raw LLM text not found in warnings: {raw_logs}"
    )


# ---------------------------------------------------------------------------
# #2194 — heuristic skip suppressed after auto_resolve / prior audit history
# ---------------------------------------------------------------------------
# Reproduces the 2193 incident shape: state LOOKS structurally clean
# (halt_reason=None, final_result is a >20-char planning narration, zero
# failing ToolMessages) but the run already had audit history, so the
# heuristic skip must be suppressed and the LLM path must run.


_NARRATION_FINAL_RESULT = "I will now run the tools. (Invoking git_"  # 40 chars, >20


async def test_heuristic_skip_suppressed_when_retry_count_gt0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2194 (a): audit_retry_count=1 + prior audit_report → LLM path runs.

    State structurally matches a clean run (halt_reason=None, long final_result,
    no failing ToolMessages) but audit_retry_count=1 signals a prior auto_resolve
    spin. The heuristic skip must be suppressed; make_chat_model IS invoked and
    the returned verdict is the mocked LLM verdict, NOT auto_pass / llm_skipped.
    """
    prior_report = {
        "verdict": "auto_resolve",
        "severity": "warn",
        "evidence": ["zero tool calls on mandatory-tool brief"],
        "action_taken": "retry_with_adjustment",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-06-10T00:00:00Z",
        "retry_count_at_audit": 0,
    }
    fake = _install_fake_llm(
        monkeypatch,
        {
            "verdict": "auto_resolve",
            "severity": "warn",
            "evidence": ["still no tool calls after retry"],
            "action_taken": "retry_with_adjustment",
            "escalation_payload": None,
        },
    )
    state: AgentState = {
        "task_id": 2194,
        "brief": "Run git_diff and git_status on the repo.",
        "final_result": _NARRATION_FINAL_RESULT,
        "halt_reason": None,
        "messages": [],
        "audit_retry_count": 1,
        "audit_report": prior_report,
    }
    result = await auditor_node(state)
    # LLM was invoked — heuristic skip was suppressed.
    assert len(fake.calls) == 1, (
        "make_chat_model was NOT called — heuristic skip should have been suppressed"
    )
    # Verdict comes from the mocked LLM, not from auto_pass.
    assert result["audit_verdict"] == "auto_resolve"
    report = result["audit_report"]
    assert report["llm_skipped"] is False
    assert report["action_taken"] != "auto_pass"


async def test_heuristic_skip_suppressed_when_prior_audit_report_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2194 (b): audit_retry_count=0 but audit_report non-None → LLM path runs.

    Belt-and-braces: even if the retry counter wasn't incremented, a non-None
    audit_report means a prior audit pass happened in this run. The skip must
    be suppressed.
    """
    prior_report = {
        "verdict": "auto_resolve",
        "severity": "warn",
        "evidence": ["prior audit via alternate path"],
        "action_taken": "retry_with_adjustment",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-06-10T00:00:00Z",
        "retry_count_at_audit": 0,
    }
    fake = _install_fake_llm(
        monkeypatch,
        {
            "verdict": "escalate",
            "severity": "critical",
            "evidence": ["narration only, no tool calls"],
            "action_taken": "hitl_escalate",
            "escalation_payload": {
                "question": "Specialist only narrated; escalate?",
                "options": ["accept", "retry_with_reprompt", "reject"],
            },
        },
    )
    # Use an InMemorySaver graph so the escalate interrupt is caught.
    builder = StateGraph(AgentState)
    builder.add_node("only", auditor_node)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "task-2194b"}}

    state: AgentState = {
        "task_id": 2194,
        "brief": "Run git_diff and git_status on the repo.",
        "final_result": _NARRATION_FINAL_RESULT,
        "halt_reason": None,
        "messages": [],
        "audit_retry_count": 0,
        "audit_report": prior_report,
    }
    await graph.ainvoke(state, config=cfg)
    # LLM was invoked — heuristic skip was suppressed by prior audit_report.
    assert len(fake.calls) == 1, (
        "make_chat_model was NOT called — heuristic skip should have been suppressed"
    )


async def test_heuristic_skip_still_fires_on_genuine_first_clean_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2194 (c): regression guard — clean first-pass run (retry=0, no prior report)
    still skips the LLM. The fix must not break the happy-path heuristic skip.
    """
    _install_failing_llm(monkeypatch)  # raises if make_chat_model is called

    state: AgentState = {
        "task_id": 9999,
        "brief": "Write a hello world",
        "final_result": "Done — created hello.py and ran it successfully. Output: Hello, world!",
        "halt_reason": None,
        "messages": [
            HumanMessage(content="Write a hello world"),
            AIMessage(content="Done — see hello.py"),
        ],
        "audit_retry_count": 0,
        # audit_report absent (no prior audit history)
    }
    result = await auditor_node(state)
    # Heuristic skip fires — LLM not called.
    assert result["audit_verdict"] == "pass"
    report = result["audit_report"]
    assert report["llm_skipped"] is True
    assert report["action_taken"] == "auto_pass"
