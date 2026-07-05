"""Unit tests for the Kanban poll worker (Kanban #852).

Strategy:
  - `WorkerConfig` is sync env-var validation — pytest monkeypatch covers it.
  - `_poll_once` is async + does HTTP; we drive it with `httpx.MockTransport`
    so each assertion observes exactly the requests the worker issued, in
    order. No real network. No real api container. No real LLM.
  - The compiled graph is a tiny stub (object with an `ainvoke` coroutine)
    passed via a `types.SimpleNamespace` standing in for the graph module.

Tests are intentionally exhaustive on lifecycle semantics — the PATCH bodies
and their ordering are the contract the api/Kanban UI depends on, so a
regression here would silently corrupt task state in production.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import worker
from worker import (
    DEFAULT_API_BASE,
    DEFAULT_POLL_INTERVAL_SEC,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    WorkerConfig,
    _poll_once,
    run_worker_loop,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip worker env-vars so each test starts from a known baseline."""
    for var in (
        "LANGGRAPH_PROJECT_ID",
        "LANGGRAPH_POLL_INTERVAL_SEC",
        "LANGGRAPH_KANBAN_API_BASE",
        "LANGGRAPH_LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _valid_env(monkeypatch: pytest.MonkeyPatch, project_id: str = "1") -> None:
    """Helper — populate the minimum env-vars for a happy WorkerConfig."""
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", project_id)


# ---------------------------------------------------------------------------
# WorkerConfig — env-var validation
# ---------------------------------------------------------------------------


def test_worker_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _valid_env(monkeypatch)
    cfg = WorkerConfig()
    assert cfg.project_id == 1
    assert cfg.api_base == DEFAULT_API_BASE
    assert cfg.poll_interval_sec == DEFAULT_POLL_INTERVAL_SEC


def test_worker_config_full_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "42")
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "5")
    monkeypatch.setenv("LANGGRAPH_KANBAN_API_BASE", "http://api:8456/")
    cfg = WorkerConfig()
    assert cfg.project_id == 42
    assert cfg.poll_interval_sec == 5
    # Trailing slash is stripped so URL-building can always concat /api/...
    assert cfg.api_base == "http://api:8456"


@pytest.mark.parametrize("bad", ["abc", "1.5", "-1", "0"])
def test_worker_config_rejects_bad_project_id(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    # Kanban #2184: unset / empty / whitespace-only -> multi-board (no raise).
    # Only a SET but malformed value (non-digit, zero, negative) raises.
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", bad)
    with pytest.raises(RuntimeError) as excinfo:
        WorkerConfig()
    assert "LANGGRAPH_PROJECT_ID" in str(excinfo.value)


@pytest.mark.parametrize("unset_val", [None, "", "  "])
def test_worker_config_unset_project_id_is_multi_board(
    monkeypatch: pytest.MonkeyPatch, unset_val: str | None
) -> None:
    """Unset or blank LANGGRAPH_PROJECT_ID -> multi-board mode; no RuntimeError."""
    if unset_val is None:
        pass  # already deleted by _clean_env
    else:
        monkeypatch.setenv("LANGGRAPH_PROJECT_ID", unset_val)
    cfg = WorkerConfig()
    assert cfg.multi_board is True
    assert cfg.project_id is None


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", " "])
def test_worker_config_rejects_bad_poll_interval(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _valid_env(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", bad)
    with pytest.raises(RuntimeError) as excinfo:
        WorkerConfig()
    assert "LANGGRAPH_POLL_INTERVAL_SEC" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Helpers for _poll_once
# ---------------------------------------------------------------------------


class _RequestLog:
    """Collects every request the worker sends so the test can assert order
    + body shape after the coroutine returns."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def record(self, req: httpx.Request) -> None:
        self.requests.append(req)
        try:
            self.bodies.append(req.read() and __import__("json").loads(req.read()))
        except Exception:
            self.bodies.append({})


def _make_client(
    handler: callable, log: _RequestLog | None = None
) -> httpx.AsyncClient:
    """Build an AsyncClient backed by httpx.MockTransport(handler)."""

    def _wrap(req: httpx.Request) -> httpx.Response:
        if log is not None:
            log.requests.append(req)
        return handler(req)

    transport = httpx.MockTransport(_wrap)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


def _body(req: httpx.Request) -> dict[str, Any]:
    """Parse the JSON body of a recorded request."""
    import json

    raw = req.content
    if not raw:
        return {}
    return json.loads(raw)


def _make_graph_module(ainvoke_impl) -> SimpleNamespace:
    """Stand-in for the imported `graph` module — exposes `.graph` whose
    `ainvoke` is `ainvoke_impl` (a coroutine function).

    #2664 — also stubs `aget_state` (the fresh-pickup clear in _poll_once calls
    has_checkpoint -> compiled.aget_state). created_at=None => has_checkpoint
    False => the clear is SKIPPED, so these no-checkpoint tests keep their exact
    prior behavior. `checkpointer.adelete_thread` is a no-op stub for any test
    that does reach the clear."""
    async def _aget_state(config):
        return SimpleNamespace(created_at=None)

    async def _adelete_thread(thread_id):
        return None

    stub_graph = SimpleNamespace(
        ainvoke=ainvoke_impl,
        aget_state=_aget_state,
        checkpointer=SimpleNamespace(adelete_thread=_adelete_thread),
    )
    return SimpleNamespace(graph=stub_graph)


def _cfg(monkeypatch: pytest.MonkeyPatch) -> WorkerConfig:
    _valid_env(monkeypatch)
    return WorkerConfig()


def _headers(cfg: WorkerConfig) -> dict[str, str]:
    return {
        "X-Project-Id": str(cfg.project_id),
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# _poll_once — idle path
# ---------------------------------------------------------------------------


async def test_poll_once_idle_when_no_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """When next-autorun returns next_task=null, the worker MUST NOT PATCH."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(
                200,
                json={
                    "next_task": None,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def fail_ainvoke(*a, **kw):
        raise AssertionError("ainvoke must not be called when there is no task")

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(fail_ainvoke), cfg, _headers(cfg))

    paths = [r.url.path for r in log.requests]
    assert paths == ["/api/tasks/next-autorun"]


# ---------------------------------------------------------------------------
# _poll_once — happy path (DONE)
# ---------------------------------------------------------------------------


async def test_poll_once_happy_path_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful run: 1 GET + 2 PATCH (IN_PROGRESS, DONE) in order, with
    correct body shapes. final_result lands in status_change_reason."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    next_task = {
        "id": 123,
        "title": "Wire the thing",
        "description": "Brief here",
        "assigned_role": 2,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(
                200,
                json={
                    "next_task": next_task,
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        # Mode-B prereq gate (#1800) fetches the bound project's
        # required_binaries once per tick; answer with "no requirements" so the
        # gate is a no-op and the clean-path lifecycle is unaffected.
        if req.method == "GET" and req.url.path == "/api/projects/1":
            return httpx.Response(200, json={"id": 1, "required_binaries": None})
        if req.method == "PATCH" and req.url.path == "/api/tasks/123":
            return httpx.Response(200, json={"id": 123})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    invoked_with: dict[str, Any] = {}

    async def ainvoke(state, config):
        invoked_with["state"] = state
        invoked_with["config"] = config
        return {
            "task_id": 123,
            "assigned_role": 2,
            "final_result": "did the work",
            "halt_reason": None,
        }

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    # Task PATCHes in this exact order — IN_PROGRESS then DONE. The Mode-B
    # prereq gate (#1800) also issues a GET /api/projects/1 per tick, so the
    # raw request list is no longer a fixed ["GET","PATCH","PATCH"]; assert on
    # the task PATCHes (all target /api/tasks/123) instead.
    task_patches = [
        r for r in log.requests
        if r.method == "PATCH" and r.url.path == "/api/tasks/123"
    ]
    assert len(task_patches) == 2

    # Header propagated on every request.
    for r in log.requests:
        assert r.headers.get("X-Project-Id") == "1"

    # IN_PROGRESS body — process_status=2 + started_at populated.
    in_progress = _body(task_patches[0])
    assert in_progress["process_status"] == STATUS_IN_PROGRESS
    assert "started_at" in in_progress and in_progress["started_at"]

    # DONE body — process_status=5, completed_at, status_change_reason carries
    # the final_result.
    done = _body(task_patches[1])
    assert done["process_status"] == STATUS_DONE
    assert "completed_at" in done and done["completed_at"]
    assert done["status_change_reason"] == "did the work"
    # MUST NOT set is_pending on a clean DONE flip (would couple lanes).
    assert "is_pending" not in done
    assert "halt_reason" not in done

    # The graph saw the right initial state.
    state = invoked_with["state"]
    assert state["task_id"] == 123
    assert state["brief"] == "Brief here"
    assert state["assigned_role"] == 2
    assert invoked_with["config"] == {"configurable": {"thread_id": "task-123"}}


# ---------------------------------------------------------------------------
# _poll_once — graph crashes
# ---------------------------------------------------------------------------


async def test_poll_once_graph_raises_marks_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ainvoke raises, the worker PATCHes to BLOCKED with halt_reason
    containing the exception class name + message."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": {"id": 7, "description": "x", "assigned_role": None},
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        return httpx.Response(200, json={"id": 7})

    async def boom(state, config):
        raise ValueError("kapow")

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(boom), cfg, _headers(cfg))

    # IN_PROGRESS then BLOCKED. The Mode-B prereq gate (#1800) adds a GET
    # /api/projects/{id} per tick (here the handler answers any GET with the
    # next-autorun body → no required_binaries → gate no-op), so assert on the
    # task PATCHes rather than the raw request sequence.
    patches = [r for r in log.requests if r.method == "PATCH"]
    assert [_body(p)["process_status"] for p in patches] == [
        STATUS_IN_PROGRESS,
        STATUS_BLOCKED,
    ]
    blocked = _body(patches[1])
    assert blocked["process_status"] == STATUS_BLOCKED
    assert "halt_reason" in blocked
    # Kanban #2136: new format is '<kind>:<short_class>: <detail>'
    # ValueError with no status_code → permanent:unknown
    assert blocked["halt_reason"].startswith("permanent:unknown:")
    assert "ValueError" in blocked["halt_reason"]
    assert "kapow" in blocked["halt_reason"]


# ---------------------------------------------------------------------------
# _poll_once — graph returns halt_reason (question / decision)
# ---------------------------------------------------------------------------


async def test_poll_once_halt_reason_marks_blocked_without_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """halt_reason set in final_state (without __interrupt__) -> process_status=4,
    halt_reason carried through, is_pending OMITTED. Kanban #1096: the API
    validator rejects is_pending=True on any process_status != IN_PROGRESS,
    so the worker must not send the combo. The HITL-pause path (with
    __interrupt__) is covered by its own test below."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": {"id": 9, "description": "x", "assigned_role": None},
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        return httpx.Response(200, json={"id": 9})

    async def halts(state, config):
        return {
            "task_id": 9,
            "halt_reason": "question",
            "final_result": "need clarification on X",
        }

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(halts), cfg, _headers(cfg))

    blocked = _body(log.requests[2])
    assert blocked["process_status"] == STATUS_BLOCKED
    assert blocked["halt_reason"] == "question"
    # Kanban #1096 contract: is_pending must NOT be True on a non-IN_PROGRESS PATCH.
    assert blocked.get("is_pending", False) is not True
    assert blocked["status_change_reason"] == "need clarification on X"


# ---------------------------------------------------------------------------
# _poll_once — non-200 from next-autorun (no PATCH issued)
# ---------------------------------------------------------------------------


async def test_poll_once_swallows_next_autorun_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    async def fail_ainvoke(*a, **kw):
        raise AssertionError("must not invoke graph on poll failure")

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(fail_ainvoke), cfg, _headers(cfg))

    # Single GET, no PATCH.
    assert len(log.requests) == 1
    assert log.requests[0].method == "GET"


# ---------------------------------------------------------------------------
# _poll_once — IN_PROGRESS PATCH fails -> graph not invoked
# ---------------------------------------------------------------------------


async def test_poll_once_aborts_when_in_progress_patch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": {"id": 5, "description": "x", "assigned_role": None},
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        # The IN_PROGRESS PATCH fails.
        return httpx.Response(409, text="row mutated")

    async def fail_ainvoke(*a, **kw):
        raise AssertionError("must not invoke graph if IN_PROGRESS PATCH failed")

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(fail_ainvoke), cfg, _headers(cfg))

    # Exactly GET + one PATCH (no DONE/BLOCKED follow-up).
    assert len(log.requests) == 2
    assert log.requests[0].method == "GET"
    assert log.requests[1].method == "PATCH"


# ---------------------------------------------------------------------------
# _poll_once — missing compiled graph
# ---------------------------------------------------------------------------


async def test_poll_once_missing_compiled_graph_blocks_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the worker is somehow started before the graph compiles, the task
    is marked BLOCKED with a clear halt_reason."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": {"id": 11, "description": "x", "assigned_role": None},
                    "resume_tasks": [],
                    "pending_questions": [],
                    "blocked_count": 0,
                },
            )
        return httpx.Response(200, json={"id": 11})

    # graph attribute is None
    bad_module = SimpleNamespace(graph=None)

    async with _make_client(handler, log) as client:
        await _poll_once(client, bad_module, cfg, _headers(cfg))

    # GET + PATCH IN_PROGRESS + PATCH BLOCKED
    assert [r.method for r in log.requests] == ["GET", "PATCH", "PATCH"]
    blocked = _body(log.requests[2])
    assert blocked["process_status"] == STATUS_BLOCKED
    assert "lifespan ordering" in blocked["halt_reason"].lower() or "not initialized" in blocked["halt_reason"].lower()


# ---------------------------------------------------------------------------
# _poll_once — non-empty resume_tasks logs a notice (deferred to #852b)
# ---------------------------------------------------------------------------


async def test_poll_once_logs_resume_tasks_deferred_notice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "next_task": None,
                "resume_tasks": [{"id": 1}, {"id": 2}],
                "pending_questions": [],
                "blocked_count": 0,
            },
        )

    async def fail_ainvoke(*a, **kw):
        raise AssertionError("no task to invoke")

    with caplog.at_level("INFO", logger="langgraph.worker"):
        async with _make_client(handler, log) as client:
            await _poll_once(
                client, _make_graph_module(fail_ainvoke), cfg, _headers(cfg)
            )

    # The deferred-notice log line.
    assert any("852b" in rec.message or "HITL resume" in rec.message for rec in caplog.records), (
        "expected an INFO log mentioning the deferred HITL resume; got: "
        + repr([r.message for r in caplog.records])
    )


# ---------------------------------------------------------------------------
# run_worker_loop — error isolation + cooperative cancellation
# ---------------------------------------------------------------------------


async def test_run_worker_loop_continues_on_iteration_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If _poll_once raises (something we didn't anticipate), the loop must
    log + sleep + continue rather than crash."""
    _valid_env(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

    call_count = {"n": 0}

    async def fake_poll(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("boom")
        # After 3 calls cancel the task externally.
        raise asyncio.CancelledError()

    # Skip the sleep so we don't actually wait 1s between iterations.
    async def fake_sleep(delay):
        return None

    monkeypatch.setattr(worker, "_poll_once", fake_poll)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    graph_module = _make_graph_module(None)
    with pytest.raises(asyncio.CancelledError):
        await run_worker_loop(graph_module)

    assert call_count["n"] >= 3  # survived 2 crashes, exited on 3rd


async def test_run_worker_loop_clean_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.CancelledError propagates cleanly — the lifespan relies on this
    to shut the worker down within its 5s grace window."""
    _valid_env(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "60")  # would block long otherwise

    async def fake_poll(*args, **kwargs):
        return None  # idle every tick

    monkeypatch.setattr(worker, "_poll_once", fake_poll)

    task = asyncio.create_task(run_worker_loop(_make_graph_module(None)))
    # Yield once so the task starts.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
