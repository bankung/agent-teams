"""Tests for Kanban #2155: HITL-interrupt runs must report real token usage.

Bug: _resume_hitl_task never called _patch_session_run_usage. Both the resume
path and a resume-that-interrupts-again path ended at the _patch_task call with
no usage PATCH, so session_run.total_input_tokens stayed 0 even when the
resumed graph made LLM calls (tracked as non-zero usage_* in final_state).

Fix (worker.py): after the _patch_task call in _resume_hitl_task, read
session_run_id from final_state (lives in the LangGraph checkpoint from the
initial-run start) and call _patch_session_run_usage. Mirrors lines 960-969 in
_run_task exactly.

Three tests:
  1. Resume-to-DONE with non-zero usage → _patch_session_run_usage fires with
     correct token totals.
  2. Resume-that-interrupts-again (fresh __interrupt__) → usage PATCH still
     fires (the run DID make LLM calls before pausing again).
  3. State key preservation guard → _build_finalize_body does not pop usage_*
     keys that _patch_session_run_usage reads afterwards.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from hitl import request_user_input, resume_config
from worker import (
    STATUS_BLOCKED,
    STATUS_DONE,
    WorkerConfig,
    _resume_hitl_task,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LANGGRAPH_PROJECT_ID",
        "LANGGRAPH_POLL_INTERVAL_SEC",
        "LANGGRAPH_KANBAN_API_BASE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")


class _UsageState(TypedDict, total=False):
    session_run_id: int | None
    usage_input_tokens: int
    usage_output_tokens: int
    usage_cache_read_tokens: int
    usage_cache_creation_tokens: int
    final_result: str
    halt_reason: str


def _make_cfg() -> WorkerConfig:
    return WorkerConfig()


def _make_graph_module(graph_obj: Any) -> SimpleNamespace:
    return SimpleNamespace(graph=graph_obj)


def _body(req: httpx.Request) -> dict[str, Any]:
    raw = req.content
    if not raw:
        return {}
    return json.loads(raw)


class _RequestLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []  # (method, path, body)


def _make_client(log: _RequestLog) -> httpx.AsyncClient:
    """Mock client: all PATCHes accepted (200); anything else raises."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = _body(req)
        log.calls.append((req.method, req.url.path, body))
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 1})
        raise AssertionError(f"unexpected {req.method} {req.url.path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _task_row(
    task_id: int,
    *,
    answered_at: str = "2026-06-12T10:00:00Z",
    session_run_id: int | None = None,
) -> dict[str, Any]:
    """Minimal task dict matching the shape _resume_hitl_task reads."""
    return {
        "id": task_id,
        "interaction_kind": "question",
        "halt_reason": "question",
        "question_payload": {
            "question": "Shall I proceed?",
            "answer_history": [
                {
                    "value": "yes",
                    "answered_by": "user",
                    "answered_at": answered_at,
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
        # session_run_id lives in graph state, not the task row — passing it
        # via the state pre-seed. The task row shape is irrelevant here; the
        # worker reads it from final_state["session_run_id"].
    }


# ---------------------------------------------------------------------------
# Test 1: resume-to-DONE with non-zero usage → usage PATCH fires
# ---------------------------------------------------------------------------


async def test_resume_to_done_patches_usage() -> None:
    """After a resumed run that returns DONE, _patch_session_run_usage is called
    with the non-zero token counts from final_state."""

    def node(state: _UsageState) -> _UsageState:
        """Pause on first call; emit usage tokens on resume."""
        request_user_input({"question": "Shall I proceed?"})
        # On resume, this line executes. Return usage tokens (simulating what
        # nodes.py does after a real LLM call).
        return {
            "final_result": "done",
            "usage_input_tokens": 120,
            "usage_output_tokens": 40,
            "usage_cache_read_tokens": 10,
            "usage_cache_creation_tokens": 5,
        }

    builder = StateGraph(_UsageState)
    builder.add_node("work", node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    # Pre-pause: run until the interrupt.
    await graph.ainvoke(
        {
            "session_run_id": 77,  # the run_id the usage PATCH should target
            "usage_input_tokens": 0,
            "usage_output_tokens": 0,
            "usage_cache_read_tokens": 0,
            "usage_cache_creation_tokens": 0,
        },
        config=resume_config(9001),
    )

    cfg = _make_cfg()
    log = _RequestLog()
    async with _make_client(log) as client:
        await _resume_hitl_task(
            client,
            _make_graph_module(graph),
            cfg,
            _task_row(9001),
            "yes",
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    # Expect two PATCHes: tasks (finalize) + session_runs (usage).
    patch_calls = [(m, p, b) for m, p, b in log.calls if m == "PATCH"]
    assert len(patch_calls) == 2, f"expected 2 PATCHes, got: {patch_calls}"

    # First PATCH: task finalize → DONE.
    _, path_task, body_task = patch_calls[0]
    assert "/api/tasks/" in path_task
    assert body_task["process_status"] == STATUS_DONE

    # Second PATCH: session_run usage → non-zero tokens (the bug fix).
    _, path_run, body_run = patch_calls[1]
    assert "session_runs" in path_run and "77" in path_run
    assert body_run["total_input_tokens"] == 120
    assert body_run["total_output_tokens"] == 40
    assert body_run["cache_read_input_tokens"] == 10
    assert body_run["cache_creation_input_tokens"] == 5
    assert body_run["status"] == "done"


# ---------------------------------------------------------------------------
# Test 2: resume-that-interrupts-again → usage PATCH still fires
# ---------------------------------------------------------------------------


async def test_resume_that_interrupts_again_patches_usage() -> None:
    """A resume that hits a second interrupt (multi-step HITL) still issues a
    usage PATCH — the resumed run DID make LLM calls before pausing again.

    Uses a stub graph (no InMemorySaver) whose ainvoke returns a fresh-interrupt
    state with non-zero usage tokens. This directly tests that _resume_hitl_task
    fires _patch_session_run_usage even when final_state carries __interrupt__.
    """
    # Stub graph: ainvoke always returns a fresh-interrupt state with usage.
    fresh_interrupt_state: dict[str, Any] = {
        "__interrupt__": [SimpleNamespace(value={"question": "Second question?"})],
        "session_run_id": 88,
        "usage_input_tokens": 75,
        "usage_output_tokens": 25,
        "usage_cache_read_tokens": 0,
        "usage_cache_creation_tokens": 0,
    }

    async def stub_ainvoke(state_or_command, config=None, **kw) -> dict[str, Any]:
        return fresh_interrupt_state

    async def stub_aget_state(config):
        # Checkpoint present so resume proceeds.
        return SimpleNamespace(created_at="2026-06-12T10:00:00Z")

    stub_graph = SimpleNamespace(ainvoke=stub_ainvoke, aget_state=stub_aget_state)
    graph_module = SimpleNamespace(graph=stub_graph)

    cfg = _make_cfg()
    log = _RequestLog()

    task = _task_row(9002)

    async with _make_client(log) as client:
        await _resume_hitl_task(
            client,
            graph_module,
            cfg,
            task,
            "yes",
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    patch_calls = [(m, p, b) for m, p, b in log.calls if m == "PATCH"]
    # Two PATCHes: task (BLOCKED — fresh interrupt) + session_run (usage).
    assert len(patch_calls) == 2, f"expected 2 PATCHes: {patch_calls}"

    _, path_task, body_task = patch_calls[0]
    assert "/api/tasks/" in path_task
    # Fresh interrupt → BLOCKED (resume-that-pauses-again path).
    assert body_task["process_status"] == STATUS_BLOCKED
    assert body_task.get("halt_reason") in ("question", "decision")

    # Usage PATCH must fire even on the fresh-interrupt path.
    _, path_run, body_run = patch_calls[1]
    assert "session_runs" in path_run and "88" in path_run
    assert body_run["total_input_tokens"] == 75
    assert body_run["total_output_tokens"] == 25
    assert body_run["status"] == "done"


# ---------------------------------------------------------------------------
# Test 3: state key preservation guard
# ---------------------------------------------------------------------------


def test_build_usage_body_nonzero_when_state_has_usage() -> None:
    """_patch_session_run_usage reads usage from final_state — pure unit guard.

    Verifies the body-building logic (_patch_session_run_usage call site) uses
    the correct key names from final_state. This does NOT test the HTTP call;
    it tests that _build_finalize_body does not accidentally erase the usage
    keys from final_state (which _patch_session_run_usage reads afterwards).

    Regression: if _build_finalize_body ever pops or modifies usage_* keys,
    the usage PATCH would silently report 0 tokens.
    """
    from worker import _build_finalize_body

    final_state: dict[str, Any] = {
        "halt_reason": None,
        "final_result": "Done.",
        "session_run_id": 42,
        "usage_input_tokens": 300,
        "usage_output_tokens": 100,
        "usage_cache_read_tokens": 20,
        "usage_cache_creation_tokens": 8,
        "effort": "high",
    }
    # Build the finalize body — must NOT touch usage_* keys.
    _build_finalize_body(final_state, completed_at="2026-06-12T00:00:00Z")

    # After the call, all usage keys must still be intact for _patch_session_run_usage.
    assert final_state["session_run_id"] == 42
    assert final_state["usage_input_tokens"] == 300
    assert final_state["usage_output_tokens"] == 100
    assert final_state["usage_cache_read_tokens"] == 20
    assert final_state["usage_cache_creation_tokens"] == 8
    assert final_state["effort"] == "high"
