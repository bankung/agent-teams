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

    async def _fake_audit(task_id, tool, args, result, decision, *, project_id=None):
        captured.append(
            {
                "task_id": task_id,
                "tool_name": getattr(tool, "name", None),
                "tier": getattr(tool.tier, "value", str(tool.tier)),
                "args": args,
                "result": result,
                "decision": getattr(decision, "value", str(decision)),
                "project_id": project_id,
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


class _StubWriteTool(Tool):
    name = "stub_write"
    description = "stub write-tier tool"
    tier = Tier.WRITE
    input_schema = _StubInput
    timeout_sec = 5

    async def _run(self, input_obj, context):
        return ToolResult(success=True, output=f"wrote foo={input_obj.foo}")


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


@pytest.fixture
def stub_write_tool():
    """Register `stub_write` (WRITE tier) for the duration of one test. Auto-cleanup."""
    if "stub_write" in GLOBAL_REGISTRY._tools:
        del GLOBAL_REGISTRY._tools["stub_write"]
    GLOBAL_REGISTRY._tools["stub_write"] = _StubWriteTool()
    yield _StubWriteTool
    GLOBAL_REGISTRY._tools.pop("stub_write", None)


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


def test_halt_on_first_of_multi_call_no_orphans(
    monkeypatch, stub_read_tool
) -> None:
    """Kanban #1720 fix H-1 — HALT fires on the FIRST tool_call of an AIMessage
    that carries >=2 tool_calls.

    Before the fix: only the halted call got a ToolMessage; the remaining
    call ids were orphaned → checkpointed state caused OpenAI/Anthropic 400
    on resume.

    After the fix: every tool_call id in the response AIMessage must have a
    paired ToolMessage in the returned state['messages'].
    """
    cfg = {
        "tools_enabled": True,
        "auto_allow_tiers": [],
        "halt_tiers": ["read"],
        "http_hosts": [],
    }
    _patch_tools_config(monkeypatch, cfg)
    _patch_audit_recorder(monkeypatch)

    # AIMessage with THREE tool_calls — HALT fires on the first one.
    multi = AIMessage(content="batched halt test")
    multi.tool_calls = [
        {"name": "stub_read", "args": {"foo": "a"}, "id": "tc_halt_1"},
        {"name": "stub_read", "args": {"foo": "b"}, "id": "tc_halt_2"},
        {"name": "stub_read", "args": {"foo": "c"}, "id": "tc_halt_3"},
    ]
    model = _ScriptedModel([multi])
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 77, "brief": "multi halt", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    # Loop did halt.
    assert out.get("halt_reason") is not None

    returned_messages = out["messages"]
    # The response AIMessage must be present.
    assert any(isinstance(m, AIMessage) for m in returned_messages)

    # Every tool_call id in the AIMessage must have a paired ToolMessage.
    call_ids: set[str] = set()
    for m in returned_messages:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if cid is not None:
                    call_ids.add(cid)

    tool_msg_ids: set[str] = {
        m.tool_call_id for m in returned_messages if isinstance(m, ToolMessage)
    }

    assert call_ids == {"tc_halt_1", "tc_halt_2", "tc_halt_3"}, (
        f"expected all 3 call ids, got: {call_ids}"
    )
    assert call_ids == tool_msg_ids, (
        f"orphans detected — call_ids={call_ids}, tool_msg_ids={tool_msg_ids}"
    )

    # The 2nd and 3rd stubs must carry halted_before_execution.
    stub_contents = {
        m.tool_call_id: m.content
        for m in returned_messages
        if isinstance(m, ToolMessage) and m.tool_call_id != "tc_halt_1"
    }
    for cid, content in stub_contents.items():
        assert "halted_before_execution" in content, (
            f"expected stub content for {cid}, got: {content!r}"
        )


def test_halt_on_LATER_call_keeps_pre_halt_toolmsgs(
    monkeypatch, stub_read_tool, stub_write_tool
) -> None:
    """Kanban #1720 fix H-2 — HALT fires on the SECOND tool_call of an AIMessage
    that carries [read(auto_allow), write(halt)].

    The pre-halt `read` call executes and its ToolMessage is appended to
    `messages` before the inner loop reaches `write`. The HALT branch must
    return the whole turn slice (response_start:) so both `read` AND `write`
    tool_call ids have paired ToolMessages in the returned state.

    This test MUST fail on the incomplete H-1 code (which used
    messages[-num_appended:] and missed pre-halt executed TMs) and MUST
    pass after the H-2 fix.
    """
    # auto_allow read, halt write.
    cfg = {
        "tools_enabled": True,
        "auto_allow_tiers": ["read"],
        "halt_tiers": ["write"],
        "http_hosts": [],
    }
    _patch_tools_config(monkeypatch, cfg)
    _patch_audit_recorder(monkeypatch)

    # AIMessage with two tool_calls: read first (auto_allow → executes),
    # write second (halt → loop exits early).
    mixed = AIMessage(content="mixed auto+halt")
    mixed.tool_calls = [
        {"name": "stub_read", "args": {"foo": "x"}, "id": "tc_read_first"},
        {"name": "stub_write", "args": {"foo": "y"}, "id": "tc_write_halt"},
    ]
    model = _ScriptedModel([mixed])
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    state = {"task_id": 88, "brief": "mixed halt", "assigned_role": 2}
    out = asyncio.run(nodes.backend_specialist_node(state))

    # Loop did halt on the write call.
    assert out.get("halt_reason") is not None

    returned_messages = out["messages"]

    # Collect all tool_call ids from the AIMessage.
    call_ids: set[str] = set()
    for m in returned_messages:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if cid is not None:
                    call_ids.add(cid)

    tool_msg_ids: set[str] = {
        m.tool_call_id for m in returned_messages if isinstance(m, ToolMessage)
    }

    # Both call ids must be present — no orphan for the pre-halt read.
    assert call_ids == {"tc_read_first", "tc_write_halt"}, (
        f"expected both call ids, got: {call_ids}"
    )
    assert call_ids == tool_msg_ids, (
        f"orphans detected — call_ids={call_ids}, tool_msg_ids={tool_msg_ids}"
    )

    # The read tool actually executed (auto_allow).
    assert stub_read_tool.invocation_count == 1

    # The read ToolMessage must NOT carry halted_before_execution.
    read_tm = next(
        m for m in returned_messages
        if isinstance(m, ToolMessage) and m.tool_call_id == "tc_read_first"
    )
    assert "halted_before_execution" not in read_tm.content

    # The write ToolMessage (the halted one) must carry the halt result, not a stub.
    write_tm = next(
        m for m in returned_messages
        if isinstance(m, ToolMessage) and m.tool_call_id == "tc_write_halt"
    )
    assert "halted_before_execution" not in write_tm.content


# ---------------------------------------------------------------------------
# Kanban #2231 — multi-board audit: X-Project-Id must come from state["project_id"]
# ---------------------------------------------------------------------------


def test_specialist_audit_carries_project_id_from_state(
    monkeypatch, stub_read_tool
) -> None:
    """Regression guard for #2231 multi-board audit header bug.

    In multi-board mode LANGGRAPH_PROJECT_ID env-var is unset, so
    resolve_project_id() returns None and the audit POST built
    X-Project-Id from an empty header → 400 from the API.

    The fix: nodes._audit() forwards project_id=ctx.project_id to
    record_tool_invocation, which prefers the explicit value over the
    env-var fallback.

    This test verifies that:
      1. The fake record_tool_invocation receives project_id=691 (the
         value injected via state["project_id"], not via env-var).
      2. LANGGRAPH_PROJECT_ID env-var is unset throughout (simulates
         multi-board mode — the env fallback would return None).
    """
    # Multi-board: env-var unset.
    monkeypatch.delenv("LANGGRAPH_PROJECT_ID", raising=False)

    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    audit = _patch_audit_recorder(monkeypatch)

    responses = [
        _ai_with_tool_call("stub_read", {"foo": "multiboard"}, call_id="tc_mb"),
        _ai_final("done"),
    ]
    model = _ScriptedModel(responses)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    # project_id injected via state (the fix path), NOT via env-var.
    state = {
        "task_id": 2231,
        "brief": "multiboard audit test",
        "assigned_role": 2,
        "project_id": 691,
    }
    out = asyncio.run(nodes.backend_specialist_node(state))

    assert out["final_result"] == "done"
    assert len(audit) == 1, f"expected 1 audit row, got {len(audit)}"
    assert audit[0]["project_id"] == 691, (
        f"audit POST must carry project_id=691 (from state, not env-var); "
        f"got project_id={audit[0]['project_id']!r}"
    )
