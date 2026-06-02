"""Integration test for Mode-B Phase-1 host-prereq gate (Kanban #1800 / #1652).

When a task is picked up, the worker fetches the bound project's
`required_binaries` (GET /api/projects/{id}) and runs `shutil.which()` on each
BEFORE the IN_PROGRESS flip + the LLM call. Three cases:

  (a) all required binaries present → gate passes, normal lifecycle runs
      (IN_PROGRESS PATCH + ainvoke + DONE PATCH).
  (b) a required binary missing → single PATCH lands BLOCKED with
      halt_reason='runtime_prereq_missing' naming the missing binary; NO
      IN_PROGRESS PATCH, NO ainvoke call (zero token spend).
  (c) required_binaries None / empty → gate is a no-op; normal lifecycle runs.

Mirrors test_worker_l17_gate.py: httpx.MockTransport + SimpleNamespace graph
stub, shutil.which monkeypatched, _patch_task layer asserted via the request
log. No live API, no live LLM, no live container.
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
    _required_binaries_cache_clear,
)


# ---------------------------------------------------------------------------
# Fixtures — mirror test_worker_l17_gate.py
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
    # The required_binaries cache is process-local; clear it so a prior test's
    # value (keyed by project_id=1) can't bleed into the next.
    _required_binaries_cache_clear()


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


_CLEAN_TASK = {
    "id": 5555,
    "title": "Transcode a clip",
    "description": "Run the media pipeline on the uploaded asset.",
    "assigned_role": 2,
    "acceptance_criteria": [{"text": "output exists", "status": "pending"}],
}


def _handler_for(project_body: dict[str, Any], task_id: int):
    """Build a MockTransport handler answering next-autorun + GET project + PATCH."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(
                200,
                json={
                    "next_task": _CLEAN_TASK,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        if req.method == "GET" and req.url.path == "/api/projects/1":
            return httpx.Response(200, json=project_body)
        if req.method == "PATCH" and req.url.path == f"/api/tasks/{task_id}":
            return httpx.Response(200, json={"id": task_id})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    return handler


# ---------------------------------------------------------------------------
# (a) all required binaries present → gate passes, normal lifecycle
# ---------------------------------------------------------------------------


async def test_all_required_binaries_present_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    # shutil.which returns a fake path for any binary → all present.
    monkeypatch.setattr(
        worker.shutil, "which", lambda b: f"/usr/bin/{b}"
    )

    project_body = {"id": 1, "name": "p", "required_binaries": ["ffmpeg", "yt-dlp"]}
    handler = _handler_for(project_body, _CLEAN_TASK["id"])

    ainvoke_called = {"count": 0}

    async def ainvoke(state, config):
        ainvoke_called["count"] += 1
        return {"task_id": _CLEAN_TASK["id"], "final_result": "done", "halt_reason": None}

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    # Gate passed → ainvoke IS called, normal lifecycle.
    assert ainvoke_called["count"] == 1
    patches = [r for r in log.requests if r.method == "PATCH"]
    assert [_body(p)["process_status"] for p in patches] == [
        STATUS_IN_PROGRESS,
        STATUS_DONE,
    ], [_body(p) for p in patches]
    # No runtime_prereq_missing halt anywhere.
    assert all(
        _body(p).get("halt_reason") != "runtime_prereq_missing" for p in patches
    )


# ---------------------------------------------------------------------------
# (b) a required binary missing → BLOCKED runtime_prereq_missing, no LLM
# ---------------------------------------------------------------------------


async def test_missing_required_binary_halts_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    # ffmpeg present, yt-dlp missing.
    def fake_which(b: str):
        return "/usr/bin/ffmpeg" if b == "ffmpeg" else None

    monkeypatch.setattr(worker.shutil, "which", fake_which)

    project_body = {"id": 1, "name": "p", "required_binaries": ["ffmpeg", "yt-dlp"]}
    handler = _handler_for(project_body, _CLEAN_TASK["id"])

    ainvoke_called = {"count": 0}

    async def must_not_be_called(state, config):
        ainvoke_called["count"] += 1
        raise AssertionError("ainvoke must NOT be called when a prereq is missing")

    async with _make_client(handler, log) as client:
        await _poll_once(
            client, _make_graph_module(must_not_be_called), cfg, _headers(cfg)
        )

    # LLM NEVER called (zero token spend).
    assert ainvoke_called["count"] == 0

    patches = [r for r in log.requests if r.method == "PATCH"]
    # Exactly one PATCH — the BLOCKED halt. No IN_PROGRESS flip before it.
    assert len(patches) == 1, [_body(p) for p in patches]
    body = _body(patches[0])
    assert patches[0].url.path == f"/api/tasks/{_CLEAN_TASK['id']}"
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "runtime_prereq_missing"
    # The missing binary is named; the present one is NOT flagged missing.
    assert "yt-dlp" in body["status_change_reason"]
    assert "Mode-A-only" in body["status_change_reason"]
    # Lock: the PATCH is the BLOCKED halt, not an IN_PROGRESS flip.
    assert body["process_status"] != STATUS_IN_PROGRESS
    # Headers propagate (project-scoped PATCH gate).
    assert patches[0].headers.get("X-Project-Id") == "1"


# ---------------------------------------------------------------------------
# (c) required_binaries None / empty → gate is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("declared", [None, []])
async def test_no_required_binaries_is_noop(
    monkeypatch: pytest.MonkeyPatch, declared
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    # which() would return None for everything — but the gate must not even
    # consult it when there are no declared binaries.
    which_calls = {"count": 0}

    def fake_which(b: str):
        which_calls["count"] += 1
        return None

    monkeypatch.setattr(worker.shutil, "which", fake_which)

    project_body = {"id": 1, "name": "p", "required_binaries": declared}
    handler = _handler_for(project_body, _CLEAN_TASK["id"])

    ainvoke_called = {"count": 0}

    async def ainvoke(state, config):
        ainvoke_called["count"] += 1
        return {"task_id": _CLEAN_TASK["id"], "final_result": "done", "halt_reason": None}

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    # No-op gate → normal lifecycle (ainvoke called, IN_PROGRESS + DONE).
    assert ainvoke_called["count"] == 1
    # which() never consulted on an empty/None declaration.
    assert which_calls["count"] == 0
    patches = [r for r in log.requests if r.method == "PATCH"]
    assert [_body(p)["process_status"] for p in patches] == [
        STATUS_IN_PROGRESS,
        STATUS_DONE,
    ]
    assert all(
        _body(p).get("halt_reason") != "runtime_prereq_missing" for p in patches
    )
