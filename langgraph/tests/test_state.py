"""Sanity tests for the AgentState TypedDict.

TypedDict has no runtime enforcement, so these tests pin the *intended*
shape — they document the contract and catch accidental rename/removal of
keys (since a typo would still type-check as `dict[str, object]`).
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from state import AgentState


def test_agent_state_accepts_minimal_payload() -> None:
    s: AgentState = {"task_id": 1, "brief": "hello"}
    assert s["task_id"] == 1
    assert s["brief"] == "hello"


def test_agent_state_accepts_full_payload() -> None:
    s: AgentState = {
        "task_id": 42,
        "assigned_role": 2,
        "brief": "build endpoint",
        "messages": [HumanMessage(content="go")],
        "intermediate_results": {"plan": "step 1, 2, 3"},
        "final_result": "done",
        "halt_reason": None,
    }
    assert s["assigned_role"] == 2
    assert len(s["messages"]) == 1
    assert s["intermediate_results"]["plan"].startswith("step")


def test_agent_state_keys_are_stable() -> None:
    """If you rename a field, this list must be updated AND every node that
    reads/writes the field. The list is the canonical set."""
    expected = {
        "messages",
        "task_id",
        "assigned_role",
        "brief",
        "intermediate_results",
        "final_result",
        "halt_reason",
    }
    annotations = set(AgentState.__annotations__.keys())
    assert annotations == expected
