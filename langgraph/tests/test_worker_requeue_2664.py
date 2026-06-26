"""Regression tests for Kanban #2664 — re-queued TODO task must run FRESH, not
resume a stale LangGraph checkpoint.

Bug (observed live): an AI task that already ran, then dragged / reset / PATCHed
back to TODO, was re-picked by `next-autorun` and re-invoked with the SAME
thread_id (`task-<id>`). LangGraph RESUMED the stale checkpoint instead of
starting fresh — ignoring DB changes (e.g. a freshly-set `assigned_role`) and
carrying over old graph state (e.g. `audit_retry_count=3`). The task got stuck
~9.5 min in an auditor retry loop with `assigned_role=None`.

Fix (worker layer A): on a FRESH `next_task` pickup, if a checkpoint exists for
`task-<id>`, CLEAR it BEFORE the invoke (hitl.clear_checkpoint →
graph.checkpointer.adelete_thread). The clear lives in `_poll_once` ONLY (the
fresh-pickup path), so the HITL resume path (`_resume_hitl_task` /
`resume_graph`) and the in-pickup transient-retry resume are both unaffected.

This file proves BOTH AC3 paths:
  (a) a HITL-BLOCKED answered task still resumes — clear_checkpoint is NOT called
      and the checkpoint survives.
  (b) a re-queued TODO task with a prior checkpoint runs fresh — clear_checkpoint
      IS called and the invoke uses initial_state (a DB assigned_role set while
      re-queued takes effect; a prior audit_retry_count does NOT carry over).

Harness mirrors test_worker_retry.py (fresh _poll_once path) and
test_worker_resume_decision.py (HITL resume path).
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
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    WorkerConfig,
    _maybe_resume_hitl_task,
    _poll_once,
)


# ---------------------------------------------------------------------------
# Fixtures + harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LANGGRAPH_PROJECT_ID",
        "LANGGRAPH_POLL_INTERVAL_SEC",
        "LANGGRAPH_KANBAN_API_BASE",
        "LANGGRAPH_TRANSIENT_RETRIES",
        "LANGGRAPH_RETRY_BACKOFF_SEC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")


def _cfg() -> WorkerConfig:
    return WorkerConfig()


def _headers(cfg: WorkerConfig) -> dict[str, str]:
    return {"X-Project-Id": str(cfg.project_id), "Content-Type": "application/json"}


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _body(req: httpx.Request) -> dict[str, Any]:
    raw = req.content
    return json.loads(raw) if raw else {}


def _standard_handler(task: dict[str, Any]):
    """Serves a single next_task on the next-autorun GET, 200 on PATCHes, and a
    no-required_binaries project GET (matches test_worker_retry.py)."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(
                200,
                json={
                    "next_task": task,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            return httpx.Response(200, json={"id": 1, "required_binaries": None})
        return httpx.Response(200, json={"id": task["id"]})

    return handler


# ---------------------------------------------------------------------------
# Test (b) — FRESH re-queued TODO pickup with a prior checkpoint runs clean
# ---------------------------------------------------------------------------


class _CheckpointerSpy:
    """Stub checkpointer recording adelete_thread calls (the clear path)."""

    def __init__(self, *, has_prior: bool) -> None:
        self.has_prior = has_prior
        self.deleted_threads: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)
        # After a clear, the thread no longer has a checkpoint.
        self.has_prior = False


def _make_graph_module_with_spy(
    ainvoke_impl, spy: _CheckpointerSpy
) -> SimpleNamespace:
    """Graph stub whose aget_state reflects the spy's has_prior flag (so
    has_checkpoint() sees the STALE checkpoint), exposes .checkpointer for
    clear_checkpoint, and records every ainvoke input for assertion."""

    async def _aget_state(config):
        # created_at non-None ⇒ has_checkpoint() True (stale checkpoint present).
        return SimpleNamespace(
            created_at="2026-06-26T00:00:00Z" if spy.has_prior else None
        )

    graph = SimpleNamespace(
        ainvoke=ainvoke_impl,
        aget_state=_aget_state,
        checkpointer=spy,
    )
    return SimpleNamespace(graph=graph)


async def test_requeued_todo_with_stale_checkpoint_clears_and_runs_fresh() -> None:
    """A re-queued TODO task that has a STALE checkpoint:
    POSITIVE — clear_checkpoint IS called (adelete_thread('task-<id>')) so the
               run starts clean, AND the invoke receives initial_state carrying
               the CURRENT DB assigned_role.
    NEGATIVE — invoke_input is NOT None (would mean stale resume), and the old
               graph state (audit_retry_count) does NOT leak into the input."""
    cfg = _cfg()
    task = {
        "id": 2664,
        "description": "re-queued after a reset",
        # assigned_role was CHANGED while the task sat re-queued in TODO. The
        # fresh run must honor this, not the None the stale checkpoint carried.
        "assigned_role": "dev-backend",
    }

    spy = _CheckpointerSpy(has_prior=True)
    seen_inputs: list[Any] = []

    async def ainvoke(state, config):
        seen_inputs.append(state)
        return {"task_id": 2664, "halt_reason": None, "final_result": "fresh run ok"}

    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    graph_module = _make_graph_module_with_spy(ainvoke, spy)
    async with _make_client(handler) as client:
        await _poll_once(client, graph_module, cfg, _headers(cfg))

    # POSITIVE 1 — the stale checkpoint was cleared, on THIS task's thread.
    assert spy.deleted_threads == ["task-2664"], (
        f"expected one clear of task-2664, got {spy.deleted_threads!r}"
    )

    # POSITIVE 2 — exactly one invoke, and it ran from initial_state (not a
    # resume): invoke_input is a dict, not None.
    assert len(seen_inputs) == 1
    invoke_input = seen_inputs[0]
    assert invoke_input is not None, "fresh run must pass initial_state, not None"
    assert isinstance(invoke_input, dict)

    # POSITIVE 3 — the CURRENT DB assigned_role is in the fresh initial_state.
    assert invoke_input["assigned_role"] == "dev-backend"

    # NEGATIVE — old graph state from the prior run did NOT carry over into the
    # fresh input (initial_state never contains audit_retry_count).
    assert "audit_retry_count" not in invoke_input

    # Task finished DONE on the fresh run.
    patches = [r for r in requests if r.method == "PATCH"]
    statuses = [_body(p)["process_status"] for p in patches]
    assert statuses == [STATUS_IN_PROGRESS, STATUS_DONE], (
        f"unexpected patch sequence: {statuses}"
    )


async def test_fresh_pickup_no_prior_checkpoint_does_not_clear() -> None:
    """A genuinely-new task (no prior checkpoint) must NOT call adelete_thread —
    the clear only fires when has_checkpoint() is True (guards against an
    unnecessary delete on the common first-run path)."""
    cfg = _cfg()
    task = {"id": 2665, "description": "never ran before", "assigned_role": "dev-backend"}

    spy = _CheckpointerSpy(has_prior=False)

    async def ainvoke(state, config):
        return {"task_id": 2665, "halt_reason": None, "final_result": "ok"}

    def handler(req: httpx.Request) -> httpx.Response:
        return _standard_handler(task)(req)

    graph_module = _make_graph_module_with_spy(ainvoke, spy)
    async with _make_client(handler) as client:
        await _poll_once(client, graph_module, cfg, _headers(cfg))

    # No checkpoint existed → clear_checkpoint must NOT have been called.
    assert spy.deleted_threads == [], (
        f"clear fired on a no-checkpoint pickup: {spy.deleted_threads!r}"
    )


# ---------------------------------------------------------------------------
# Test (a) — HITL-BLOCKED answered task still RESUMES; clear is NOT called
# ---------------------------------------------------------------------------


class _MiniState(TypedDict, total=False):
    foo: str
    answer: str
    final_result: str
    halt_reason: str


def _build_mini_graph_with_clear_guard(node_fn: Any) -> tuple[Any, dict[str, int]]:
    """Real InMemorySaver-backed one-node graph (so the resume actually loads
    the checkpoint), with adelete_thread wrapped to record any call. Returns the
    compiled graph + a counter dict the test asserts stays at 0 on the resume
    path."""
    builder = StateGraph(_MiniState)
    builder.add_node("only", node_fn)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    compiled = builder.compile(checkpointer=InMemorySaver())

    delete_calls = {"n": 0}
    _orig_adelete = compiled.checkpointer.adelete_thread

    async def _spy_adelete(thread_id: str) -> None:
        delete_calls["n"] += 1
        await _orig_adelete(thread_id)

    compiled.checkpointer.adelete_thread = _spy_adelete  # type: ignore[method-assign]
    return compiled, delete_calls


class _PatchCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []


def _make_patch_client(capture: _PatchCapture) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH":
            capture.calls.append((req.url.path, _body(req)))
            return httpx.Response(200, json={"id": 1})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def test_hitl_resume_does_not_clear_checkpoint_and_completes() -> None:
    """A HITL-BLOCKED task that is answered resumes via _resume_hitl_task —
    POSITIVE: the resume completes (DONE PATCH carries the answer).
    NEGATIVE: clear_checkpoint / adelete_thread is NEVER called on the resume
              path, so the interrupt checkpoint survives the resume."""

    def node(state):
        ans = request_user_input({"question": "Describe the bug"})
        return {"answer": ans, "final_result": f"got: {ans}"}

    graph, delete_calls = _build_mini_graph_with_clear_guard(node)
    # Pause first so there's a real checkpoint to resume from.
    await graph.ainvoke({"foo": "start"}, config=resume_config(3001))

    task_row = {
        "id": 3001,
        "interaction_kind": "question",
        "halt_reason": "question",
        "question_payload": {
            "question": "Describe the bug",
            "answer_history": [
                {
                    "value": "it crashes on load",
                    "answered_by": "user",
                    "answered_at": "2026-06-26T11:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _cfg()
    capture = _PatchCapture()
    async with _make_patch_client(capture) as client:
        await _maybe_resume_hitl_task(
            client,
            SimpleNamespace(graph=graph),
            cfg,
            task_row,
            _headers(cfg),
        )

    # NEGATIVE — the resume path never cleared the checkpoint.
    assert delete_calls["n"] == 0, (
        "HITL resume must NOT clear the checkpoint (would wipe a live interrupt)"
    )

    # POSITIVE — the resume finalized DONE carrying the answer.
    assert len(capture.calls) == 1
    _, body = capture.calls[0]
    assert body["process_status"] == STATUS_DONE
    assert "got: it crashes on load" in body["status_change_reason"]
