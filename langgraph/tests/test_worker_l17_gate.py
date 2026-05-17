"""Integration test for L17 worker gate (Kanban #1114).

When a task with destructive content is picked up:
  1. The IN_PROGRESS PATCH MUST NOT fire (no transient status flip).
  2. The compiled graph's `ainvoke` MUST NOT be called (zero token spend).
  3. A single PATCH lands BLOCKED with halt_reason='destructive_intent_detected'
     and status_change_reason listing the matched patterns.

When a clean task is picked up:
  1. Normal lifecycle runs (IN_PROGRESS PATCH + ainvoke + DONE PATCH).

The tests use `httpx.MockTransport` + a SimpleNamespace graph stub — same
pattern as test_worker.py. No live API, no live LLM, no live container.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import worker
from worker import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    WorkerConfig,
    _poll_once,
)


# ---------------------------------------------------------------------------
# Fixtures — mirror test_worker.py
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LANGGRAPH_PROJECT_ID",
        "LANGGRAPH_POLL_INTERVAL_SEC",
        "LANGGRAPH_KANBAN_API_BASE",
        "LANGGRAPH_LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _cfg(monkeypatch: pytest.MonkeyPatch) -> WorkerConfig:
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")
    return WorkerConfig()


def _headers(cfg: WorkerConfig) -> dict[str, str]:
    return {"X-Project-Id": str(cfg.project_id), "Content-Type": "application/json"}


class _RequestLog:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []


def _make_client(handler, log: _RequestLog) -> httpx.AsyncClient:
    def _wrap(req: httpx.Request) -> httpx.Response:
        log.requests.append(req)
        return handler(req)

    return httpx.AsyncClient(transport=httpx.MockTransport(_wrap), timeout=5.0)


def _body(req: httpx.Request) -> dict[str, Any]:
    import json

    raw = req.content
    return json.loads(raw) if raw else {}


def _make_graph_module(ainvoke_impl) -> SimpleNamespace:
    return SimpleNamespace(graph=SimpleNamespace(ainvoke=ainvoke_impl))


# ---------------------------------------------------------------------------
# Test 1 — destructive description halts at gate; LLM NEVER called
# ---------------------------------------------------------------------------


async def test_destructive_description_halts_without_invoking_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task with TRUNCATE in description → single PATCH to BLOCKED with
    halt_reason='destructive_intent_detected'. No IN_PROGRESS PATCH. No
    ainvoke call."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    destructive_task = {
        "id": 9999,
        "title": "Nightly cleanup",
        "description": "Run TRUNCATE tasks_history every midnight UTC.",
        "assigned_role": None,
        "acceptance_criteria": None,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(
                200,
                json={
                    "next_task": destructive_task,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        if req.method == "PATCH" and req.url.path == "/api/tasks/9999":
            return httpx.Response(200, json={"id": 9999})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    ainvoke_called = {"count": 0}

    async def must_not_be_called(state, config):
        ainvoke_called["count"] += 1
        raise AssertionError(
            "L17 gate failed: ainvoke must NOT be called on destructive content"
        )

    async with _make_client(handler, log) as client:
        await _poll_once(
            client, _make_graph_module(must_not_be_called), cfg, _headers(cfg)
        )

    # AC4: LLM mock NEVER called.
    assert ainvoke_called["count"] == 0

    # Exactly 2 requests: GET + PATCH BLOCKED. No IN_PROGRESS PATCH between.
    assert [r.method for r in log.requests] == ["GET", "PATCH"]
    assert log.requests[1].url.path == "/api/tasks/9999"

    body = _body(log.requests[1])
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "destructive_intent_detected"
    assert "TRUNCATE" in body["status_change_reason"]
    assert "L17 worker gate" in body["status_change_reason"]
    # Headers propagate (project-scoped PATCH gate).
    assert log.requests[1].headers.get("X-Project-Id") == "1"


# ---------------------------------------------------------------------------
# Test 2 — destructive in AC text triggers the same halt
# ---------------------------------------------------------------------------


async def test_destructive_acceptance_criteria_halts_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Destructive pattern in AC text (not description) still triggers L17."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    task = {
        "id": 8888,
        "title": "Refactor",
        "description": "Plain text, nothing scary.",
        "assigned_role": None,
        "acceptance_criteria": [
            {"text": "Migration deletes via DROP TABLE legacy", "status": "pending"},
        ],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": task,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        return httpx.Response(200, json={"id": 8888})

    async def must_not_be_called(state, config):
        raise AssertionError("ainvoke must not be called")

    async with _make_client(handler, log) as client:
        await _poll_once(
            client, _make_graph_module(must_not_be_called), cfg, _headers(cfg)
        )

    assert [r.method for r in log.requests] == ["GET", "PATCH"]
    body = _body(log.requests[1])
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "destructive_intent_detected"
    assert "DROP_TABLE" in body["status_change_reason"]


# ---------------------------------------------------------------------------
# Test 3 — clean task proceeds normally (IN_PROGRESS + ainvoke + DONE)
# ---------------------------------------------------------------------------


async def test_clean_task_proceeds_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean task → IN_PROGRESS PATCH + ainvoke call + DONE PATCH (3 reqs:
    GET + 2 PATCH). L17 gate must be a strict pass-through for clean content."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    clean_task = {
        "id": 7777,
        "title": "Wire login endpoint",
        "description": "Implement POST /auth/login with JWT issuance.",
        "assigned_role": 2,
        "acceptance_criteria": [
            {"text": "Returns 200 on valid creds", "status": "pending"},
        ],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": clean_task,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        return httpx.Response(200, json={"id": 7777})

    ainvoke_called = {"count": 0}

    async def ainvoke(state, config):
        ainvoke_called["count"] += 1
        return {
            "task_id": 7777,
            "final_result": "endpoint wired",
            "halt_reason": None,
        }

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    # AC5: LLM mock IS called on clean content.
    assert ainvoke_called["count"] == 1

    # Normal lifecycle: GET + IN_PROGRESS PATCH + DONE PATCH.
    assert [r.method for r in log.requests] == ["GET", "PATCH", "PATCH"]
    in_progress = _body(log.requests[1])
    assert in_progress["process_status"] == STATUS_IN_PROGRESS
    done = _body(log.requests[2])
    assert done["process_status"] == STATUS_DONE
    assert done["status_change_reason"] == "endpoint wired"


# ---------------------------------------------------------------------------
# Test 4 — task with no title/description/AC (edge case)
# ---------------------------------------------------------------------------


async def test_task_with_no_scannable_content_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task with title=None / description=None / AC=None proceeds to LLM
    (nothing to match against). Real next-autorun payload shouldn't ever
    look like this, but we want defensive behaviour."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    task = {
        "id": 6666,
        "title": None,
        "description": None,
        "assigned_role": None,
        "acceptance_criteria": None,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": task,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        return httpx.Response(200, json={"id": 6666})

    called = {"count": 0}

    async def ainvoke(state, config):
        called["count"] += 1
        return {"task_id": 6666, "final_result": "ok", "halt_reason": None}

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    assert called["count"] == 1
    # GET + IN_PROGRESS + DONE — 3 requests, no halt.
    assert len(log.requests) == 3
