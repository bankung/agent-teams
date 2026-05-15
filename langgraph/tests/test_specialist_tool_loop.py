"""Kanban #981 — specialist tool-use loop integration tests.

These tests drive the full `backend_specialist_node` async path with a
mocked LLM whose `ainvoke` returns canned responses, then verify:

  - auto_allow path invokes the tool and feeds ToolMessage back
  - reject path returns a synthetic ToolResult(error_code='tier_not_allowed')
    to the LLM
  - halt path returns early with halt_reason set
  - audit row is recorded for every tool invocation
  - unknown tool name → unknown_tool error returned to LLM

The audit-row test installs a fake `record_tool_invocation` and counts
calls + payloads.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import nodes
from tools import (
    GLOBAL_REGISTRY,
    InvokeContext,
    PermissionDecision,
    Tier,
    Tool,
    ToolInput,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_AUTO_ALLOW_ALL_CONFIG = {
    "tools_enabled": True,
    "auto_allow_tiers": ["read", "write", "network", "destructive"],
    "halt_tiers": [],
    "http_hosts": [],
}

_HALT_WRITE_CONFIG = {
    "tools_enabled": True,
    "auto_allow_tiers": ["read"],
    "halt_tiers": ["write", "network", "destructive"],
    "http_hosts": [],
}

_REJECT_ALL_CONFIG = {
    "tools_enabled": True,
    "auto_allow_tiers": [],
    "halt_tiers": [],
    "http_hosts": [],
}


def _patch_tools_config(monkeypatch, cfg: dict | None) -> None:
    """Stub `_fetch_tools_config` to return a fixed dict."""

    async def _fake(project_id):  # type: ignore[no-untyped-def]
        return cfg

    monkeypatch.setattr(nodes, "_fetch_tools_config", _fake)


def _patch_audit_recorder(monkeypatch) -> list[dict[str, Any]]:
    """Capture every record_tool_invocation call. Returns a list filled in-order."""
    captured: list[dict[str, Any]] = []

    async def _fake_audit(task_id, tool, args, result, decision):
        captured.append(
            {
                "task_id": task_id,
                "tool_name": getattr(tool, "name", None),
                "tier": getattr(tool.tier, "value", str(tool.tier)),
                "args": args,
                "result": result,
                "decision": getattr(decision, "value", str(decision)),
            }
        )

    monkeypatch.setattr(nodes, "record_tool_invocation", _fake_audit)
    return captured


class _ScriptedModel:
    """Fake LLM whose ainvoke returns one canned response per call.

    `responses` is a list of AIMessage objects (set `tool_calls` attr on
    each one to drive the loop). Calls past the end return the last one
    repeatedly — but tests should size the list to the expected count.
    """

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = responses
        self._idx = 0
        self.received_messages: list[list[Any]] = []

    def bind_tools(self, tools):
        return self  # bound model = self (we don't need a separate object)

    async def ainvoke(self, messages):
        self.received_messages.append(list(messages))
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return self._responses[-1]


def _ai_with_tool_call(tool_name: str, args: dict, call_id: str = "tc_1") -> AIMessage:
    """Build an AIMessage that the loop will treat as a tool_call response."""
    msg = AIMessage(content="thinking...")
    msg.tool_calls = [{"name": tool_name, "args": args, "id": call_id}]
    return msg


def _ai_final(text: str) -> AIMessage:
    """Build an AIMessage with no tool_calls — terminates the loop."""
    msg = AIMessage(content=text)
    msg.tool_calls = []
    return msg


# ---------------------------------------------------------------------------
# Stub tool — a no-op READ tier we can register fresh on each test.
# Keeping it isolated from the production tools means the test doesn't
# touch the real filesystem.
# ---------------------------------------------------------------------------


class _StubInput(ToolInput):
    foo: str = "bar"


class _StubReadTool(Tool):
    name = "stub_read"
    description = "stub read-tier tool"
    tier = Tier.READ
    input_schema = _StubInput
    timeout_sec = 5
    invocation_count = 0  # class-level — survives loop iterations within a test

    async def _run(self, input_obj, context):
        type(self).invocation_count += 1
        return ToolResult(success=True, output=f"ran with foo={input_obj.foo}")


@pytest.fixture
def stub_read_tool():
    """Register `stub_read` for the duration of one test. Auto-cleanup."""
    # Reset class-level counter to avoid bleed-over between tests.
    _StubReadTool.invocation_count = 0
    if "stub_read" in GLOBAL_REGISTRY._tools:
        del GLOBAL_REGISTRY._tools["stub_read"]
    GLOBAL_REGISTRY._tools["stub_read"] = _StubReadTool()
    yield _StubReadTool
    GLOBAL_REGISTRY._tools.pop("stub_read", None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_specialist_auto_allow_invokes_tool(
    monkeypatch, stub_read_tool
) -> None:
    """auto_allow → tool runs, ToolMessage appended, final answer returned."""
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    audit = _patch_audit_recorder(monkeypatch)

    responses = [
        _ai_with_tool_call("stub_read", {"foo": "hello"}, call_id="tc_a"),
        _ai_final("done"),
    ]
    model = _ScriptedModel(responses)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 42, "brief": "use the tool", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    # Tool was invoked once.
    assert stub_read_tool.invocation_count == 1
    # Final answer landed.
    assert out["final_result"] == "done"
    assert out.get("halt_reason") is None
    # The second invoke must have seen a ToolMessage in the messages list.
    second_call_msgs = model.received_messages[1]
    tool_messages = [m for m in second_call_msgs if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "tc_a"
    # Audit recorded one row, decision=auto_allow.
    assert len(audit) == 1
    assert audit[0]["decision"] == PermissionDecision.AUTO_ALLOW.value
    assert audit[0]["tool_name"] == "stub_read"
    assert audit[0]["result"].success is True


def test_specialist_reject_returns_tier_not_allowed_to_llm(
    monkeypatch, stub_read_tool
) -> None:
    """REJECT → synthetic ToolResult with error_code='tier_not_allowed' fed back.

    The loop does NOT halt on reject — it lets the LLM adapt (try a
    different tool or give up gracefully). Verify the LLM's next prompt
    contains the rejection ToolMessage.
    """
    _patch_tools_config(monkeypatch, _REJECT_ALL_CONFIG)
    audit = _patch_audit_recorder(monkeypatch)

    responses = [
        _ai_with_tool_call("stub_read", {"foo": "x"}, call_id="tc_reject"),
        _ai_final("ok i'll just answer"),
    ]
    model = _ScriptedModel(responses)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 7, "brief": "use the tool", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    # The tool's _run was NOT executed.
    assert stub_read_tool.invocation_count == 0
    # The LLM saw the rejection via a ToolMessage with tier_not_allowed.
    second_call_msgs = model.received_messages[1]
    tool_messages = [m for m in second_call_msgs if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "tier_not_allowed" in tool_messages[0].content
    # Audit row exists, decision=reject.
    assert len(audit) == 1
    assert audit[0]["decision"] == PermissionDecision.REJECT.value


def test_specialist_halt_sets_halt_reason(
    monkeypatch, stub_read_tool
) -> None:
    """HALT → loop exits early with halt_reason='tool_permission_review: <name> tier=<t>'."""
    cfg = {
        "tools_enabled": True,
        "auto_allow_tiers": [],
        "halt_tiers": ["read"],
        "http_hosts": [],
    }
    _patch_tools_config(monkeypatch, cfg)
    audit = _patch_audit_recorder(monkeypatch)

    responses = [
        _ai_with_tool_call("stub_read", {"foo": "y"}, call_id="tc_halt"),
        # Never reached.
        _ai_final("would never run"),
    ]
    model = _ScriptedModel(responses)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 99, "brief": "do something", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    assert out["halt_reason"] == "tool_permission_review: stub_read tier=read"
    # Only the first ainvoke fired.
    assert len(model.received_messages) == 1
    # Tool's _run was NOT executed.
    assert stub_read_tool.invocation_count == 0
    # Audit row written with decision=halt.
    assert len(audit) == 1
    assert audit[0]["decision"] == PermissionDecision.HALT.value


def test_specialist_audit_row_written_per_tool_call(
    monkeypatch, stub_read_tool
) -> None:
    """Two tool calls in one assistant response → two audit rows."""
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    audit = _patch_audit_recorder(monkeypatch)

    first = AIMessage(content="batched")
    first.tool_calls = [
        {"name": "stub_read", "args": {"foo": "a"}, "id": "tc_1"},
        {"name": "stub_read", "args": {"foo": "b"}, "id": "tc_2"},
    ]
    responses = [first, _ai_final("done")]
    model = _ScriptedModel(responses)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 11, "brief": "two", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    assert out["final_result"] == "done"
    assert stub_read_tool.invocation_count == 2
    assert len(audit) == 2
    assert audit[0]["args"]["foo"] == "a"
    assert audit[1]["args"]["foo"] == "b"


def test_specialist_no_tools_fallback_when_tools_disabled(monkeypatch) -> None:
    """tools_enabled=False → loop skipped, falls back to single-shot inference."""
    _patch_tools_config(monkeypatch, {"tools_enabled": False})
    audit = _patch_audit_recorder(monkeypatch)

    # Single-shot fallback uses `model.invoke()` (sync) not `ainvoke`. Use a
    # SimpleNamespace-style fake.
    captured_prompt: dict[str, Any] = {}

    def _sync_invoke(prompt):
        captured_prompt["prompt"] = prompt
        return AIMessage(content="single shot")

    class _SyncOnlyModel:
        invoke = staticmethod(_sync_invoke)
        # NO bind_tools — that's the test: we never reach bind_tools when
        # tools_enabled=False.

    monkeypatch.setattr(nodes, "make_chat_model", lambda: _SyncOnlyModel())

    state = {"task_id": 5, "brief": "answer plainly", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))
    assert out["final_result"] == "single shot"
    # No audit rows.
    assert audit == []


def test_specialist_unknown_tool_name_returns_error_to_llm(monkeypatch) -> None:
    """LLM hallucinates a tool name → unknown_tool ToolResult fed back."""
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    audit = _patch_audit_recorder(monkeypatch)

    responses = [
        _ai_with_tool_call("__does_not_exist__", {}, call_id="tc_ghost"),
        _ai_final("oh well"),
    ]
    model = _ScriptedModel(responses)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 13, "brief": "x", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    assert out["final_result"] == "oh well"
    # Unknown tool surfaces in the next prompt's ToolMessage.
    second_call_msgs = model.received_messages[1]
    tool_messages = [m for m in second_call_msgs if isinstance(m, ToolMessage)]
    assert any("unknown_tool" in m.content for m in tool_messages)
    # No audit row for the ghost tool (we can't audit what doesn't exist).
    assert audit == []


def test_specialist_loop_terminates_on_first_no_tool_call_response(
    monkeypatch, stub_read_tool
) -> None:
    """If the LLM never emits tool_calls, the loop exits after 1 iteration."""
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    _patch_audit_recorder(monkeypatch)

    model = _ScriptedModel([_ai_final("just answer")])
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 1, "brief": "no tools needed", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    assert out["final_result"] == "just answer"
    assert out.get("halt_reason") is None
    # Only one ainvoke.
    assert len(model.received_messages) == 1
    # Tool never ran.
    assert stub_read_tool.invocation_count == 0
