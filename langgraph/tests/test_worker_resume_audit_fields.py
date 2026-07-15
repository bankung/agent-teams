"""Regression tests for Kanban #2840 — audit fields must survive HITL resume.

Bug: `_build_finalize_body` (the initial finalize) forwards `audit_report` /
`audit_retry_count` from `final_state` onto every PATCH body it builds
(Kanban #952 — the worker is the sole writer of these two `tasks` columns).
`_resume_hitl_task` (the HITL-resume path) built its own PATCH body from
scratch and never did the equivalent forwarding — so a resumed ESCALATE task
(auditor flags something, human reviews it, `nodes._apply_escalation_resume`
re-stamps `audit_report` fresh on the resume) silently LOST its audit trail
on the very PATCH that reports the resolution. Usage-token accounting on
this same path (`_patch_session_run_usage`, ~10 lines below the fix site)
was already forwarded correctly — this bug was audit_report's twin that
never got the same treatment.

Fix: both `_build_finalize_body` and `_resume_hitl_task` now call a shared
`_forward_audit_fields(body, final_state)` helper.

Strategy mirrors test_worker_hitl_usage.py: a tiny InMemorySaver-backed graph
for the engine-level pause/resume, httpx.MockTransport for the PATCH
assertions. See test_worker_policy_hook.py for the OTHER caller of
_resume_hitl_task (the auto-approve-policy path in _poll_once) — that file's
own test for the #2840 fix lives alongside its existing auto-approve suite.
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
    WorkerConfig,
    _resume_hitl_task,
)


# ---------------------------------------------------------------------------
# Fixtures + harness (mirrors test_worker_hitl_usage.py / test_worker_resume_decision.py)
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


class _AuditState(TypedDict, total=False):
    foo: str
    final_result: str
    audit_report: dict[str, Any]
    audit_retry_count: int


def _build_mini_graph(node_fn: Any) -> Any:
    builder = StateGraph(_AuditState)
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


def _headers(cfg: WorkerConfig) -> dict[str, str]:
    return {"X-Project-Id": str(cfg.project_id), "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Test 1 (POSITIVE) — audit_report / audit_retry_count carried on resume
# ---------------------------------------------------------------------------


async def test_resume_forwards_audit_fields_when_present() -> None:
    """A resumed ESCALATE task's finalize body carries audit_report /
    audit_retry_count. The resumed node's return shape mirrors
    nodes._apply_escalation_resume's 'accept' branch: audit_report re-stamped
    fresh on resume (action_taken='operator_accept'), halt_reason cleared."""

    def node(state):
        ans = request_user_input({"question": "Auditor escalated; accept?"})
        return {
            "final_result": f"resolved: {ans}",
            "audit_report": {
                "verdict": "escalate",
                "severity": "warn",
                "evidence": ["operator reviewed"],
                "action_taken": "operator_accept",
                "escalation_payload": None,
                "llm_skipped": False,
                "audited_at": "2026-07-15T10:00:00Z",
                "retry_count_at_audit": 1,
            },
            "audit_retry_count": 1,
        }

    graph = _build_mini_graph(node)
    # Pause first so there's a checkpoint to resume.
    await graph.ainvoke({"foo": "start"}, config=resume_config(2840))

    task_row = {
        "id": 2840,
        "interaction_kind": "question",
        "halt_reason": "question",
        "question_payload": {
            "question": "Auditor escalated; accept?",
            "answer_history": [
                {
                    "value": "accept",
                    "answered_by": "user",
                    "answered_at": "2026-07-15T09:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    capture = _PatchCapture()
    async with _make_patch_client(capture) as client:
        await _resume_hitl_task(
            client,
            _make_graph_module(graph),
            cfg,
            task_row,
            "accept",
            _headers(cfg),
        )

    assert len(capture.calls) == 1
    path, body = capture.calls[0]
    assert path == "/api/tasks/2840"
    assert body["process_status"] == STATUS_DONE

    # The load-bearing assertion — this is exactly what previously vanished.
    assert body["audit_report"]["action_taken"] == "operator_accept"
    assert body["audit_report"]["retry_count_at_audit"] == 1
    assert body["audit_retry_count"] == 1


# ---------------------------------------------------------------------------
# Test 2 (NEGATIVE) — no spurious audit keys when final_state has none
# ---------------------------------------------------------------------------


async def test_resume_omits_audit_fields_when_absent() -> None:
    """A plain (non-auditor) resume — final_state carries no audit_report /
    audit_retry_count — must NOT inject spurious keys into the PATCH body.
    Pairs with test 1: proves the forwarding is conditional on presence in
    final_state, not an unconditional pass-through of whatever `task` had."""

    def node(state):
        ans = request_user_input({"question": "Plain question, no auditor involved"})
        return {"final_result": f"answered: {ans}"}

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(2841))

    task_row = {
        "id": 2841,
        "interaction_kind": "question",
        "halt_reason": "question",
        "question_payload": {
            "question": "Plain question, no auditor involved",
            "answer_history": [
                {
                    "value": "ok",
                    "answered_by": "user",
                    "answered_at": "2026-07-15T09:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    capture = _PatchCapture()
    async with _make_patch_client(capture) as client:
        await _resume_hitl_task(
            client,
            _make_graph_module(graph),
            cfg,
            task_row,
            "ok",
            _headers(cfg),
        )

    assert len(capture.calls) == 1
    _, body = capture.calls[0]
    assert body["process_status"] == STATUS_DONE
    # NEGATIVE lock: no spurious audit keys when final_state carries none.
    assert "audit_report" not in body
    assert "audit_retry_count" not in body
