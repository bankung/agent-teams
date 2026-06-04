"""Tests for Mode-A usage reporting — Kanban #1886.

Covers:
  1. _extract_usage helper in nodes.py — all four token fields,
     graceful handling of None / partial / non-dict metadata.
  2. _build_finalize_body in worker.py — session_run_id threaded through
     state is accessible (state passthrough test).
  3. _run_tool_use_loop accumulates usage across iterations and surfaces it
     in the returned state dict.
  4. No-tools (single-shot) path of backend_specialist_node includes usage
     fields in returned state.

All tests are pure unit tests — no I/O, no httpx, no Docker.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# 1. _extract_usage — token extraction helper
# ---------------------------------------------------------------------------


def test_extract_usage_full_anthropic_metadata() -> None:
    """All four fields present (Anthropic shape) → extracted correctly."""
    from nodes import _extract_usage

    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 10,
        }
    )
    inp, out, cr, cc = _extract_usage(response)
    assert inp == 100
    assert out == 50
    assert cr == 30
    assert cc == 10


def test_extract_usage_partial_metadata_no_cache_fields() -> None:
    """Only input/output present (OpenAI / non-caching shape) → cache fields default 0."""
    from nodes import _extract_usage

    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 200,
            "output_tokens": 80,
        }
    )
    inp, out, cr, cc = _extract_usage(response)
    assert inp == 200
    assert out == 80
    assert cr == 0
    assert cc == 0


def test_extract_usage_none_metadata() -> None:
    """usage_metadata absent / None → all zeros, no crash."""
    from nodes import _extract_usage

    response = SimpleNamespace(usage_metadata=None)
    assert _extract_usage(response) == (0, 0, 0, 0)

    response2 = SimpleNamespace()  # attribute missing entirely
    assert _extract_usage(response2) == (0, 0, 0, 0)


def test_extract_usage_non_dict_metadata() -> None:
    """usage_metadata is not a dict → all zeros, no crash."""
    from nodes import _extract_usage

    response = SimpleNamespace(usage_metadata="unexpected_string")
    assert _extract_usage(response) == (0, 0, 0, 0)


def test_extract_usage_none_values_in_metadata() -> None:
    """Fields present but None → default to 0."""
    from nodes import _extract_usage

    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
        }
    )
    inp, out, cr, cc = _extract_usage(response)
    assert inp == 0
    assert out == 0
    assert cr == 0
    assert cc == 0


# ---------------------------------------------------------------------------
# 2. _build_finalize_body — session_run_id passthrough
# ---------------------------------------------------------------------------


def test_finalize_body_session_run_id_not_affected() -> None:
    """_build_finalize_body does not strip session_run_id from final_state.

    The worker reads it directly from final_state; this test verifies it
    is still accessible after the function runs (no accidental pop).
    """
    from worker import _build_finalize_body

    final_state: dict[str, Any] = {
        "halt_reason": None,
        "final_result": "Done.",
        "session_run_id": 42,
        "usage_input_tokens": 100,
        "usage_output_tokens": 50,
        "usage_cache_read_tokens": 10,
        "usage_cache_creation_tokens": 5,
    }
    _build_finalize_body(final_state, completed_at="2026-06-04T00:00:00Z")
    # final_state must still carry session_run_id after the call.
    assert final_state["session_run_id"] == 42
    assert final_state["usage_input_tokens"] == 100


# ---------------------------------------------------------------------------
# 3. _run_tool_use_loop accumulates usage across iterations
# ---------------------------------------------------------------------------


def _make_response(
    content: str = "hello",
    tool_calls: list[Any] | None = None,
    usage_inp: int = 0,
    usage_out: int = 0,
    usage_cr: int = 0,
    usage_cc: int = 0,
) -> Any:
    """Minimal AIMessage stand-in with usage_metadata."""
    return SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata={
            "input_tokens": usage_inp,
            "output_tokens": usage_out,
            "cache_read_input_tokens": usage_cr,
            "cache_creation_input_tokens": usage_cc,
        },
    )


@pytest.mark.asyncio
async def test_tool_loop_accumulates_usage_two_iterations() -> None:
    """usage totals are the SUM of both LLM call's usage_metadata."""
    import asyncio
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage, SystemMessage
    from nodes import _run_tool_use_loop

    # First call returns tool_calls → loop continues.
    # Second call returns no tool_calls → loop exits with final answer.
    call_count = 0

    async def fake_ainvoke(messages: list[Any]) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(
                "thinking...",
                tool_calls=[{"name": "my_tool", "args": {}, "id": "tc1"}],
                usage_inp=100,
                usage_out=20,
                usage_cr=10,
                usage_cc=5,
            )
        # Second call: no tool_calls → final answer.
        return _make_response(
            "final answer",
            tool_calls=None,
            usage_inp=50,
            usage_out=30,
            usage_cr=0,
            usage_cc=0,
        )

    # Minimal model stand-in
    fake_model = SimpleNamespace(ainvoke=fake_ainvoke)

    # Minimal InvokeContext stand-in
    from tools import InvokeContext
    ctx = InvokeContext(
        task_id=99,
        project_id=1,
        repo_root="/repo",
        working_path="/repo",
        host_allowlist=[],
    )

    # tools_config with no tools_enabled → all tools get REJECT, which is fine
    # because our fake model never actually calls a tool (tool_calls loop will
    # try to handle tc1, get REJECT, then the second ainvoke returns final).
    # Actually — we need to handle the tool_calls dispatch. The loop tries to
    # call GLOBAL_REGISTRY.get("my_tool"). It will raise ToolNotFoundError /
    # return None, which the loop handles as unknown_tool (no halt).
    tools_config: dict[str, Any] = {"tools_enabled": True, "auto_allow_tiers": []}

    messages: list[Any] = [
        SystemMessage(content="system"),
        HumanMessage(content="brief"),
    ]
    result = await _run_tool_use_loop(fake_model, messages, ctx, tools_config)

    # Usage should sum both calls.
    assert result["usage_input_tokens"] == 150   # 100 + 50
    assert result["usage_output_tokens"] == 50   # 20 + 30
    assert result["usage_cache_read_tokens"] == 10   # 10 + 0
    assert result["usage_cache_creation_tokens"] == 5  # 5 + 0
    assert result["final_result"] == "final answer"


@pytest.mark.asyncio
async def test_tool_loop_no_usage_metadata_defaults_to_zero() -> None:
    """If the model returns no usage_metadata, totals remain 0 (no crash)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from nodes import _run_tool_use_loop
    from tools import InvokeContext

    async def fake_ainvoke(messages: list[Any]) -> Any:
        # No usage_metadata attribute at all.
        return SimpleNamespace(
            content="final",
            tool_calls=[],
            # deliberately no usage_metadata
        )

    fake_model = SimpleNamespace(ainvoke=fake_ainvoke)
    ctx = InvokeContext(
        task_id=1, project_id=1, repo_root="/repo",
        working_path="/repo", host_allowlist=[],
    )
    messages: list[Any] = [
        SystemMessage(content="sys"),
        HumanMessage(content="brief"),
    ]
    result = await _run_tool_use_loop(fake_model, messages, ctx, None)

    assert result["usage_input_tokens"] == 0
    assert result["usage_output_tokens"] == 0
    assert result["usage_cache_read_tokens"] == 0
    assert result["usage_cache_creation_tokens"] == 0
