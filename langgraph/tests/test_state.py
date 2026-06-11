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
        # Kanban #952 (2026-05-16) — in-graph auditor fields.
        "audit_verdict",
        "audit_report",
        "audit_retry_count",
        # Kanban #1123 (L16, 2026-05-17) — sanitized prior halt context for
        # the LLM. Set by worker.py via agent_context_sanitizer.
        "prior_halt_reason",
        "prior_status_change_reason",
        # Kanban #1886 (2026-06-04) — Mode-A usage reporting.
        # session_run_id injected by worker; token totals accumulated by nodes.
        "session_run_id",
        "usage_input_tokens",
        "usage_output_tokens",
        "usage_cache_read_tokens",
        "usage_cache_creation_tokens",
        # Kanban #2185 (2026-06-10) — multi-board tool fix: worker injects
        # project_id so nodes can fetch tools_config for the correct project
        # even when LANGGRAPH_PROJECT_ID env is unset.
        "project_id",
        # Kanban #2300 (2026-06-11) — resolved Anthropic effort lever, injected
        # by worker at spawn (carrier > project effort_mode > off). Specialist
        # nodes read it to pick an effort-bound model; forwarded to
        # session_runs.effort on finalize. None = off.
        "effort",
    }
    annotations = set(AgentState.__annotations__.keys())
    assert annotations == expected
