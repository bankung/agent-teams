"""Regression tests for Kanban #1695 — HITL decision-task resume finalize loop.

Bug (reproduced live on project 598, task #1694, provider=ollama): a decision
task (interaction_kind='decision'), once HITL-answered, is resumed by the
worker. The graph returns a final state with NO halt_reason → the worker mapped
'halt_reason absent → DONE' and PATCHed process_status=5 WITHOUT setting
question_payload.chosen_id. The api's #1007 done-flip validator rejected the
PATCH 422 ('decision task requires chosen_id to be set before flipping to
DONE'). The idempotency cursor (resume_context.last_consumed_answered_at) was
bundled into that SAME rejected PATCH → never persisted → `_needs_resume`
re-resumed every poll (10s) FOREVER. Pre-existing victims: tasks #1081, #1094.

Fix A — set chosen_id on decision finalize:
    The DONE PATCH for a DECISION task now merges question_payload with
    chosen_id = the validated answer (a valid option id) + chosen_at = now,
    preserving the existing payload. Mirrors the #1007 / /decide contract
    (chosen_id in question_payload + chosen_at UTC Z-suffix). QUESTION tasks
    (interaction_kind != 'decision') are NOT given a chosen_id (no regression).

Fix B — no infinite loop on finalize failure (defense-in-depth):
    If the finalize PATCH returns non-2xx, the worker issues ONE structured
    give-up PATCH (halt_reason='resume_finalize_failed' + cursor advanced) so
    `_needs_resume` returns False next poll instead of looping forever.

Strategy mirrors test_hitl.py: a tiny InMemorySaver-backed graph for the
engine-level resume, httpx.MockTransport for the worker PATCH assertions.
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
    _maybe_resume_hitl_task,
    _needs_resume,
)


# ---------------------------------------------------------------------------
# Fixtures + harness (mirrors test_hitl.py / test_worker.py)
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


class _MiniState(TypedDict, total=False):
    foo: str
    answer: str
    final_result: str
    halt_reason: str


def _build_mini_graph(node_fn: Any) -> Any:
    builder = StateGraph(_MiniState)
    builder.add_node("only", node_fn)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    return builder.compile(checkpointer=InMemorySaver())


def _make_cfg() -> WorkerConfig:
    return WorkerConfig()


def _make_graph_module(graph_obj: Any) -> SimpleNamespace:
    return SimpleNamespace(graph=graph_obj)


def _body(req: httpx.Request) -> dict[str, Any]:
    raw = req.content
    if not raw:
        return {}
    return json.loads(raw)


class _PatchCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []


def _make_patch_client(capture: _PatchCapture) -> httpx.AsyncClient:
    """PATCH handler captures the body + always responds 200 (happy path)."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH":
            capture.calls.append((req.url.path, _body(req)))
            return httpx.Response(200, json={"id": 1})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _make_patch_client_first_422(
    capture: _PatchCapture, reject_detail: str
) -> httpx.AsyncClient:
    """PATCH handler that rejects the FIRST PATCH with 422 (mirrors the api's
    #1007 done-flip validator) then 200s every subsequent PATCH — so the
    give-up PATCH (Fix B) lands cleanly and is observable in `capture.calls`."""

    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH":
            capture.calls.append((req.url.path, _body(req)))
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(422, json={"detail": reject_detail})
            return httpx.Response(200, json={"id": 1})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


_DECISION_REJECT = "decision task requires chosen_id to be set before flipping to DONE"


# ---------------------------------------------------------------------------
# Test 1 (Fix A) — DECISION resume sets chosen_id + chosen_at on the DONE PATCH
# ---------------------------------------------------------------------------


async def test_resume_decision_finalize_sets_chosen_id() -> None:
    """Resuming a DECISION task with a valid answer → the finalize PATCH body
    sets process_status=5 AND question_payload.chosen_id = the answer (+
    chosen_at), preserving the existing payload (question/options/history).
    This mirrors the api #1007 contract so the DONE-flip passes."""

    def node(state):
        ans = request_user_input({"question": "Deploy where?", "options": ["staging", "prod"]})
        return {"answer": ans, "final_result": f"deployed to {ans}"}

    graph = _build_mini_graph(node)
    # Pause first so there's a checkpoint to resume.
    await graph.ainvoke({"foo": "start"}, config=resume_config(1694))

    task_row = {
        "id": 1694,
        "interaction_kind": "decision",
        "halt_reason": "decision",
        "question_payload": {
            "question": "Deploy where?",
            "options": ["staging", "prod"],
            "answer_history": [
                {
                    "value": "prod",
                    "answered_by": "user",
                    "answered_at": "2026-05-29T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    capture = _PatchCapture()
    async with _make_patch_client(capture) as client:
        await _maybe_resume_hitl_task(
            client,
            _make_graph_module(graph),
            cfg,
            task_row,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    # Exactly one PATCH (the clean DONE flip — no give-up needed).
    assert len(capture.calls) == 1
    path, body = capture.calls[0]
    assert path == "/api/tasks/1694"
    assert body["process_status"] == STATUS_DONE

    # Fix A — chosen_id matches the validated answer (a valid option id); the
    # api's validate_decision_payload requires chosen_id ∈ options[].id, and
    # for string-shaped options the option string IS the id.
    qp = body["question_payload"]
    assert qp["chosen_id"] == "prod"
    assert "chosen_at" in qp and qp["chosen_at"]  # UTC Z-suffix via _now_iso
    # Existing payload preserved (question / options / answer_history intact).
    assert qp["question"] == "Deploy where?"
    assert qp["options"] == ["staging", "prod"]
    assert len(qp["answer_history"]) == 1

    # Cursor stamped on the (successful) DONE PATCH.
    assert (
        body["resume_context"]["last_consumed_answered_at"]
        == "2026-05-29T10:00:00Z"
    )

    # Batch C (#1695) — status_change_reason must reflect the resolution, not a
    # misleading 'general fallback: halting for human review' string.
    assert "prod" in body["status_change_reason"]
    assert "halting" not in body["status_change_reason"]


# ---------------------------------------------------------------------------
# Test 2 (Fix B) — finalize PATCH rejected → give-up PATCH, no re-resume
# ---------------------------------------------------------------------------


async def test_resume_decision_finalize_rejected_gives_up_no_reresume() -> None:
    """If the finalize PATCH is rejected (422), the worker issues a give-up
    PATCH (halt_reason='resume_finalize_failed' + cursor advanced) and the
    resulting task state is NOT re-resumable (_needs_resume → False)."""

    def node(state):
        ans = request_user_input({"question": "Deploy where?", "options": ["staging", "prod"]})
        return {"answer": ans, "final_result": f"deployed to {ans}"}

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(1081))

    answered_at = "2026-05-16T10:00:00Z"
    task_row = {
        "id": 1081,
        "interaction_kind": "decision",
        "halt_reason": "decision",
        "question_payload": {
            "question": "Deploy where?",
            "options": ["staging", "prod"],
            "answer_history": [
                {
                    "value": "prod",
                    "answered_by": "user",
                    "answered_at": answered_at,
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    capture = _PatchCapture()
    async with _make_patch_client_first_422(capture, _DECISION_REJECT) as client:
        await _maybe_resume_hitl_task(
            client,
            _make_graph_module(graph),
            cfg,
            task_row,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    # TWO PATCHes: the rejected finalize, then the give-up.
    assert len(capture.calls) == 2

    # First PATCH was the (rejected) DONE finalize.
    _, finalize_body = capture.calls[0]
    assert finalize_body["process_status"] == STATUS_DONE

    # Second PATCH is the structured give-up.
    _, giveup_body = capture.calls[1]
    assert giveup_body["process_status"] == STATUS_BLOCKED
    assert giveup_body["halt_reason"] == "resume_finalize_failed"
    # Cursor advanced (decoupled from DONE success) so the next poll skips it.
    assert (
        giveup_body["resume_context"]["last_consumed_answered_at"] == answered_at
    )

    # Simulate the row AFTER the give-up PATCH persisted, then assert the
    # worker would NOT re-resume it next poll. Both guards hold:
    #   - halt_reason 'resume_finalize_failed' ∉ {question, decision}
    #   - cursor caught up to the consumed answer's answered_at
    post_giveup_task = {
        "id": 1081,
        "interaction_kind": "decision",
        "halt_reason": giveup_body["halt_reason"],
        "question_payload": task_row["question_payload"],
        "resume_context": giveup_body["resume_context"],
    }
    needs, _ = _needs_resume(post_giveup_task)
    assert needs is False


# ---------------------------------------------------------------------------
# Test 3 (no regression) — QUESTION resume finalizes WITHOUT chosen_id
# ---------------------------------------------------------------------------


async def test_resume_question_finalize_omits_chosen_id() -> None:
    """A QUESTION (non-decision) resume still finalizes DONE WITHOUT chosen_id
    — only decision tasks carry the chosen_id requirement (no regression)."""

    def node(state):
        ans = request_user_input({"question": "Describe the bug"})
        return {"answer": ans, "final_result": f"got: {ans}"}

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(2001))

    task_row = {
        "id": 2001,
        "interaction_kind": "question",
        "halt_reason": "question",
        "question_payload": {
            "question": "Describe the bug",
            "answer_history": [
                {
                    "value": "it crashes on load",
                    "answered_by": "user",
                    "answered_at": "2026-05-29T11:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    capture = _PatchCapture()
    async with _make_patch_client(capture) as client:
        await _maybe_resume_hitl_task(
            client,
            _make_graph_module(graph),
            cfg,
            task_row,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    assert len(capture.calls) == 1
    _, body = capture.calls[0]
    assert body["process_status"] == STATUS_DONE
    assert body["halt_reason"] is None
    assert body["is_pending"] is False
    # No chosen_id merge for a question task. The worker does NOT set
    # question_payload at all on a question DONE flip — assert the key is
    # absent (and if a future change adds it, it must not carry chosen_id).
    assert "chosen_id" not in (body.get("question_payload") or {})
    assert "used: " not in body.get("status_change_reason", "")
    assert "got: it crashes on load" in body["status_change_reason"]
