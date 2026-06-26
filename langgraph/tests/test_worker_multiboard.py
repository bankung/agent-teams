"""Tests for multi-board worker mode (Kanban #2184).

Coverage:
  - Eligibility matrix (each exclusion criterion + happy row).
  - Multi-board tick: 2 projects, work on 2nd -> picked with correct X-Project-Id.
  - Eligible list refresh after N ticks.
  - Session ensure: first run creates; second reuses; creation failure -> no crash.
  - Env-set fallback: LANGGRAPH_PROJECT_ID set -> no /api/projects call, single-board.

All tests use httpx.MockTransport (no real network) and autouse conftest fixtures
(_strip_session_id + the per-file _clean_env below).

For the loop-level tests (_run_multi_board_loop creates its own AsyncClient
internally) we monkeypatch the module-level helpers (_fetch_all_projects,
_multiboard_has_work, _ensure_project_session, _poll_once) — matching the
same monkeypatching strategy used by test_worker.py for run_worker_loop.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import multiboard
import worker
from worker import WorkerConfig, _ensure_project_session, _run_multi_board_loop


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all worker + multiboard env-vars; clear process-level caches."""
    for var in (
        "LANGGRAPH_PROJECT_ID",
        "LANGGRAPH_POLL_INTERVAL_SEC",
        "LANGGRAPH_KANBAN_API_BASE",
        "LANGGRAPH_LLM_PROVIDER",
        "LANGGRAPH_MULTIBOARD_REFRESH_TICKS",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    multiboard.session_cache_clear()
    worker._policy_cache_clear()
    worker._required_binaries_cache_clear()


def _body(req: httpx.Request) -> dict[str, Any]:
    raw = req.content
    return json.loads(raw) if raw else {}


def _make_graph_module(ainvoke_impl) -> SimpleNamespace:
    stub = SimpleNamespace(ainvoke=ainvoke_impl)
    return SimpleNamespace(graph=stub)


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# ---------------------------------------------------------------------------
# Eligibility matrix (pure unit tests — no I/O)
# ---------------------------------------------------------------------------

_BASE_PROJECT: dict[str, Any] = {
    "id": 10,
    "name": "testproj",
    "is_active": True,
    "auto_run_consent_at": "2026-01-01T00:00:00Z",
    "tools_config": {"tools_enabled": True},
    "is_paused": False,
    "is_killed": False,
}


def _proj(**overrides) -> dict[str, Any]:
    return {**_BASE_PROJECT, **overrides}


def test_eligibility_happy_row() -> None:
    assert multiboard.is_eligible(_proj()) is True


def test_eligibility_consent_null() -> None:
    assert multiboard.is_eligible(_proj(auto_run_consent_at=None)) is False


def test_eligibility_tools_disabled_still_eligible() -> None:
    # #2707: tools_enabled no longer gates eligibility — it's the permission-gate
    # capability switch (see test_permission_gate reject-when-disabled coverage).
    assert multiboard.is_eligible(_proj(tools_config={"tools_enabled": False})) is True


def test_eligibility_no_tools_config_still_eligible() -> None:
    # #2707: tools_enabled no longer gates eligibility — it's the permission-gate
    # capability switch (see test_permission_gate reject-when-disabled coverage).
    assert multiboard.is_eligible(_proj(tools_config=None)) is True


def test_eligibility_inactive() -> None:
    assert multiboard.is_eligible(_proj(is_active=False)) is False


def test_eligibility_paused() -> None:
    assert multiboard.is_eligible(_proj(is_paused=True)) is False


def test_eligibility_killed() -> None:
    assert multiboard.is_eligible(_proj(is_killed=True)) is False


def test_filter_eligible_stable_order() -> None:
    """filter_eligible returns id-ascending sorted subset."""
    projects = [
        _proj(id=20),
        _proj(id=5),
        _proj(id=15, is_paused=True),  # excluded
        _proj(id=10),
    ]
    result = multiboard.filter_eligible(projects)
    assert [p["id"] for p in result] == [5, 10, 20]


# ---------------------------------------------------------------------------
# Multi-board tick: first project idle, second has work -> correct X-Project-Id
# ---------------------------------------------------------------------------


async def test_multiboard_tick_picks_second_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two eligible projects; project 1 idle, project 2 has a task.

    The worker must:
      - Peek next-autorun for project 1: no work -> continue.
      - Peek next-autorun for project 2: work found.
      - Call _poll_once with X-Project-Id=2 headers.
      - Stop (serial: first actionable project ends the tick).

    Monkeypatches _fetch_all_projects, _multiboard_has_work, _ensure_project_session,
    and _poll_once to stay in-process (loop creates its own AsyncClient).
    """
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("LANGGRAPH_MULTIBOARD_REFRESH_TICKS", "1")

    cfg = WorkerConfig()
    assert cfg.multi_board is True

    projects = [_proj(id=1, name="p1"), _proj(id=2, name="p2")]

    async def fake_fetch_all(client, api_base):
        return projects

    # Project 1 idle, project 2 has work.
    async def fake_has_work(client, api_base, headers):
        return headers.get("X-Project-Id") == "2"

    async def fake_ensure_session(client, cfg, project_id, project_name):
        return project_id * 100  # deterministic

    poll_once_calls: list[dict] = []

    async def fake_poll_once(client, graph_module, cfg, headers, session_id_override=None):
        poll_once_calls.append({"pid": headers.get("X-Project-Id")})

    monkeypatch.setattr(worker, "_fetch_all_projects", fake_fetch_all)
    monkeypatch.setattr(worker, "_multiboard_has_work", fake_has_work)
    monkeypatch.setattr(worker, "_ensure_project_session", fake_ensure_session)
    monkeypatch.setattr(worker, "_poll_once", fake_poll_once)

    cancel_after = 1

    async def fake_sleep(delay):
        nonlocal cancel_after
        cancel_after -= 1
        if cancel_after <= 0:
            raise asyncio.CancelledError()

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    graph_module = _make_graph_module(None)
    with pytest.raises(asyncio.CancelledError):
        await _run_multi_board_loop(cfg, graph_module)

    # _poll_once called exactly once, for project 2
    assert len(poll_once_calls) == 1, f"expected 1 _poll_once call, got {len(poll_once_calls)}"
    assert poll_once_calls[0]["pid"] == "2", (
        f"_poll_once must be called with X-Project-Id=2, got {poll_once_calls[0]['pid']}"
    )


# ---------------------------------------------------------------------------
# Eligible list refresh after N ticks
# ---------------------------------------------------------------------------


async def test_multiboard_refresh_reflects_new_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After refresh_ticks ticks, a newly-eligible project appears in the set.

    Monkeypatches _fetch_all_projects (returns different lists per call count)
    and _multiboard_has_work + _poll_once (idle, so only counts are checked).
    """
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("LANGGRAPH_MULTIBOARD_REFRESH_TICKS", "2")

    cfg = WorkerConfig()
    fetch_call_count = {"n": 0}
    fetch_at_ticks: list[int] = []
    ticks_elapsed = {"n": 0}

    async def fake_fetch_all(client, api_base):
        fetch_at_ticks.append(ticks_elapsed["n"])
        fetch_call_count["n"] += 1
        # First call: only project 1. Second call+: project 1 + 2.
        if fetch_call_count["n"] < 2:
            return [_proj(id=1, name="p1")]
        return [_proj(id=1, name="p1"), _proj(id=2, name="p2")]

    async def fake_has_work(client, api_base, headers):
        return False  # idle throughout

    async def fake_poll_once(*args, **kwargs):
        pass

    monkeypatch.setattr(worker, "_fetch_all_projects", fake_fetch_all)
    monkeypatch.setattr(worker, "_multiboard_has_work", fake_has_work)
    monkeypatch.setattr(worker, "_poll_once", fake_poll_once)

    async def fake_sleep(delay):
        ticks_elapsed["n"] += 1
        if ticks_elapsed["n"] >= 4:
            raise asyncio.CancelledError()

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    graph_module = _make_graph_module(None)
    with pytest.raises(asyncio.CancelledError):
        await _run_multi_board_loop(cfg, graph_module)

    # With refresh_ticks=2: refresh on tick 0 (initial) and tick 2
    assert fetch_call_count["n"] >= 2, "expected at least 2 fetches (ticks 0 and 2)"
    assert fetch_at_ticks[0] == 0, "first fetch must happen before any sleep (tick 0)"


# ---------------------------------------------------------------------------
# Session ensure: first run creates, second reuses, failure -> no crash
# ---------------------------------------------------------------------------


async def test_ensure_project_session_creates_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call creates a session; second call returns cached id."""
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    cfg = WorkerConfig()

    create_calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/sessions":
            create_calls["n"] += 1
            body = _body(req)
            assert body.get("project_id") == 42
            assert "harness-worker" in body.get("process_label", "")
            return httpx.Response(201, json={"id": 777})
        raise AssertionError(f"unexpected: {req.method} {req.url.path}")

    async with _make_client(handler) as client:
        sid1 = await _ensure_project_session(client, cfg, 42, "my-project")
        sid2 = await _ensure_project_session(client, cfg, 42, "my-project")

    assert sid1 == 777
    assert sid2 == 777  # cache hit
    assert create_calls["n"] == 1  # only one POST


async def test_ensure_project_session_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session creation failure returns None; no crash."""
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    cfg = WorkerConfig()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/sessions":
            return httpx.Response(500, text="internal error")
        raise AssertionError(f"unexpected: {req.method} {req.url.path}")

    async with _make_client(handler) as client:
        result = await _ensure_project_session(client, cfg, 99, "broken-proj")

    assert result is None
    # Cache must NOT have stored the failure
    assert multiboard.get_cached_session(99) is None


async def test_multiboard_session_failure_no_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session create failure doesn't crash the worker; _poll_once still called
    (session_run is skipped because LANGGRAPH_SESSION_ID won't be set).

    Monkeypatches loop helpers to isolate from the internal AsyncClient.
    """
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("LANGGRAPH_MULTIBOARD_REFRESH_TICKS", "1")

    cfg = WorkerConfig()

    async def fake_fetch_all(client, api_base):
        return [_proj(id=3, name="p3")]

    async def fake_has_work(client, api_base, headers):
        return True  # project 3 has work

    # Session creation fails — returns None
    async def fake_ensure_session(client, cfg, project_id, project_name):
        return None

    poll_once_calls: list[dict] = []

    async def fake_poll_once(client, graph_module, cfg, headers, session_id_override=None):
        # Capture the LANGGRAPH_SESSION_ID env at invocation time.
        import os
        poll_once_calls.append({
            "pid": headers.get("X-Project-Id"),
            "session_id_env": os.environ.get("LANGGRAPH_SESSION_ID"),
        })

    monkeypatch.setattr(worker, "_fetch_all_projects", fake_fetch_all)
    monkeypatch.setattr(worker, "_multiboard_has_work", fake_has_work)
    monkeypatch.setattr(worker, "_ensure_project_session", fake_ensure_session)
    monkeypatch.setattr(worker, "_poll_once", fake_poll_once)

    cancel_after = 1

    async def fake_sleep(delay):
        nonlocal cancel_after
        cancel_after -= 1
        if cancel_after <= 0:
            raise asyncio.CancelledError()

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    graph_module = _make_graph_module(None)
    with pytest.raises(asyncio.CancelledError):
        await _run_multi_board_loop(cfg, graph_module)

    # Worker did not crash; _poll_once was called
    assert len(poll_once_calls) == 1, "worker should still call _poll_once even if session create failed"
    # LANGGRAPH_SESSION_ID must NOT be set when session_id is None
    assert poll_once_calls[0]["session_id_env"] is None, (
        "LANGGRAPH_SESSION_ID must not be set when session creation failed"
    )


# ---------------------------------------------------------------------------
# Env-set fallback: LANGGRAPH_PROJECT_ID set -> single-board, no /api/projects call
# ---------------------------------------------------------------------------


async def test_singleboard_no_project_list_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LANGGRAPH_PROJECT_ID is set, /api/projects must NOT be called."""
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "661")
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

    cfg = WorkerConfig()
    assert cfg.multi_board is False
    assert cfg.project_id == 661

    projects_fetch_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/projects":
            projects_fetch_count["n"] += 1
            return httpx.Response(200, json=[])
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json={
                "next_task": None, "resume_tasks": [], "pending_questions": [], "blocked_count": 0,
            })
        raise AssertionError(f"unexpected: {req.method} {req.url.path}")

    async def fake_poll(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_poll_once", fake_poll)

    graph_module = _make_graph_module(None)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_worker_loop(graph_module)

    assert projects_fetch_count["n"] == 0, "/api/projects must not be called in single-board mode"


async def test_singleboard_session_env_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In single-board mode, LANGGRAPH_SESSION_ID env is passed through to resolve_session_id."""
    import config as _config
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")
    monkeypatch.setenv("LANGGRAPH_SESSION_ID", "55")
    # resolve_session_id reads from env at call time
    assert _config.resolve_session_id() == 55


# ---------------------------------------------------------------------------
# Kanban #2185 — project_id threaded into graph initial_state (multi-board fix)
# ---------------------------------------------------------------------------


async def test_multiboard_poll_injects_project_id_into_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-board tick: _poll_once must receive initial_state["project_id"]
    equal to the picked task's project_id (not None, not a different project).

    This is the regression guard for the #2185 bug: in multi-board mode
    LANGGRAPH_PROJECT_ID env is unset, so nodes.resolve_project_id() returns
    None — tools_config fetch fails, tools are never bound, and the model
    emits tool calls as plain text.  The fix passes project_id via state
    instead.

    We drive _poll_once directly with a MockTransport handler that serves
    a minimal task and a stubbed compiled graph that captures initial_state.
    LANGGRAPH_PROJECT_ID is unset (multi-board), X-Project-Id=691 in headers.
    """
    import os

    monkeypatch.delenv("LANGGRAPH_PROJECT_ID", raising=False)
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    # Zero retries so the test doesn't sleep.
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "0")

    cfg = WorkerConfig()
    assert cfg.multi_board is True
    assert cfg.project_id is None

    # The effective project for this tick is 691 (as in the bisect evidence).
    headers = {"X-Project-Id": "691", "Content-Type": "application/json"}

    captured_state: dict = {}

    async def fake_ainvoke(state, config=None):
        captured_state.update(state)
        # Return a minimal DONE state so _build_finalize_body doesn't blow up.
        return {
            "task_id": state.get("task_id"),
            "halt_reason": None,
            "final_result": "done",
            "messages": [],
            "intermediate_results": {},
        }

    # #2664 — aget_state(created_at=None) => has_checkpoint False => the
    # fresh-pickup clear in _poll_once is SKIPPED (this test asserts the
    # initial_state project_id, not the clear); checkpointer stub for completeness.
    async def _aget_state(config):
        return SimpleNamespace(created_at=None)

    async def _adelete_thread(thread_id):
        return None

    graph_stub = SimpleNamespace(
        ainvoke=fake_ainvoke,
        aget_state=_aget_state,
        checkpointer=SimpleNamespace(adelete_thread=_adelete_thread),
    )
    graph_module = SimpleNamespace(graph=graph_stub)

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        method = req.method

        if method == "GET" and path == "/api/tasks/next-autorun":
            return httpx.Response(200, json={
                "next_task": {
                    "id": 42,
                    "title": "fix the thing",
                    "description": "do work",
                    "assigned_role": 2,
                    "halt_reason": None,
                    "status_change_reason": None,
                },
                "resume_tasks": [],
                "pending_questions": [],
                "blocked_count": 0,
            })

        if method == "PATCH" and "/api/tasks/" in path:
            return httpx.Response(200, json={"id": 42})

        # Scan / content-safety: GET /api/projects/691 for required_binaries
        if method == "GET" and path == "/api/projects/691":
            return httpx.Response(200, json={
                "id": 691,
                "name": "proj-691",
                "required_binaries": None,
                "approval_policies": None,
                "tools_config": {"tools_enabled": True},
            })

        # Allow session_run creation to fail gracefully (not our concern here).
        if method == "POST" and "/api/sessions/" in path:
            return httpx.Response(404, text="not found")

        raise AssertionError(f"unexpected request: {method} {path}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    ) as client:
        await worker._poll_once(client, graph_module, cfg, headers)

    # Core assertion: project_id in initial_state must equal the X-Project-Id
    # value (691), NOT None and NOT a different project.
    assert "project_id" in captured_state, (
        "initial_state must contain 'project_id' key"
    )
    assert captured_state["project_id"] == 691, (
        f"initial_state['project_id'] must be 691 (the task's project), "
        f"got {captured_state['project_id']!r}"
    )


# ---------------------------------------------------------------------------
# Kanban #2298 — unanswered parked question on A must not starve board B
# ---------------------------------------------------------------------------


def _pending_question(
    *,
    task_id: int = 100,
    halt_reason: str = "question",
    answered_at: str | None = None,
    last_consumed_answered_at: str | None = None,
) -> dict:
    """Build a minimal pending_questions TaskRead dict for predicate tests."""
    answer_history = []
    if answered_at is not None:
        answer_history.append({
            "value": "yes",
            "answered_at": answered_at,
            "answered_by": "operator",
            "is_valid": True,
        })
    resume_context = None
    if last_consumed_answered_at is not None:
        resume_context = {"last_consumed_answered_at": last_consumed_answered_at}
    return {
        "id": task_id,
        "halt_reason": halt_reason,
        "question_payload": {"question": "approve?", "answer_history": answer_history},
        "resume_context": resume_context,
    }


async def test_has_work_unanswered_question_is_not_actionable() -> None:
    """_multiboard_has_work returns False when the only pending question has no
    operator answer yet (the 661/task-2283 parked-debris shape). Kanban #2298.
    """
    unanswered = _pending_question(task_id=2283, halt_reason="question", answered_at=None)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "next_task": None,
            "resume_tasks": [],
            "pending_questions": [unanswered],
            "blocked_count": 1,
        })

    async with _make_client(handler) as client:
        result = await worker._multiboard_has_work(client, "http://test", {"X-Project-Id": "661"})

    assert result is False, (
        "_multiboard_has_work must return False for a board whose only pending "
        "question has no operator answer (unanswered parked question is not actionable)"
    )


async def test_has_work_answered_question_is_actionable() -> None:
    """_multiboard_has_work returns True when a pending question has a fresh
    unconsumed operator answer — resume path must still fire. Kanban #2298.
    """
    answered = _pending_question(
        task_id=2283,
        halt_reason="question",
        answered_at="2026-06-11T10:00:00Z",
        last_consumed_answered_at=None,  # cursor not yet advanced
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "next_task": None,
            "resume_tasks": [],
            "pending_questions": [answered],
            "blocked_count": 1,
        })

    async with _make_client(handler) as client:
        result = await worker._multiboard_has_work(client, "http://test", {"X-Project-Id": "661"})

    assert result is True, (
        "_multiboard_has_work must return True when a pending question has an "
        "unconsumed operator answer (resume path is actionable)"
    )


async def test_multiboard_starvation_unanswered_question_board_a_does_not_block_board_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starvation repro (Kanban #2298): board 661 has ONLY an unanswered parked
    question (the 2283 debris shape). Board 691 has a runnable task.
    Before the fix, has_work(661)=True caused 691 to starve every tick.
    After the fix, the unanswered question is not actionable so 691 is polled.

    Uses fake_has_work that encodes the corrected predicate (not the old one)
    so the loop-level starvation path is exercised.
    """
    monkeypatch.setenv("LANGGRAPH_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("LANGGRAPH_MULTIBOARD_REFRESH_TICKS", "1")

    cfg = WorkerConfig()
    assert cfg.multi_board is True

    projects = [_proj(id=661, name="gemini-harness-test"), _proj(id=691, name="mini-secretary")]

    async def fake_fetch_all(client, api_base):
        return projects

    # Board 661: unanswered question only — NOT actionable (fixed predicate).
    # Board 691: has a runnable task — actionable.
    async def fake_has_work(client, api_base, headers):
        pid = headers.get("X-Project-Id")
        if pid == "661":
            return False  # unanswered question is not actionable after fix
        if pid == "691":
            return True
        return False

    async def fake_ensure_session(client, cfg, project_id, project_name):
        return project_id * 10

    poll_once_calls: list[str] = []

    async def fake_poll_once(client, graph_module, cfg, headers, session_id_override=None):
        poll_once_calls.append(headers.get("X-Project-Id"))

    cancel_after = 1

    async def fake_sleep(delay):
        nonlocal cancel_after
        cancel_after -= 1
        if cancel_after <= 0:
            raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_fetch_all_projects", fake_fetch_all)
    monkeypatch.setattr(worker, "_multiboard_has_work", fake_has_work)
    monkeypatch.setattr(worker, "_ensure_project_session", fake_ensure_session)
    monkeypatch.setattr(worker, "_poll_once", fake_poll_once)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    graph_module = _make_graph_module(None)
    with pytest.raises(asyncio.CancelledError):
        await _run_multi_board_loop(cfg, graph_module)

    # Board 691 must be polled; board 661's unanswered question must not block it.
    assert poll_once_calls == ["691"], (
        f"board 691 must be polled (unanswered question on 661 is not actionable); "
        f"got poll_once_calls={poll_once_calls}"
    )


async def test_has_work_next_task_always_actionable() -> None:
    """Board with next_task (no questions) is actionable — regression guard. #2298"""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "next_task": {"id": 5, "title": "do work"},
            "resume_tasks": [],
            "pending_questions": [],
            "blocked_count": 0,
        })

    async with _make_client(handler) as client:
        result = await worker._multiboard_has_work(client, "http://test", {"X-Project-Id": "691"})

    assert result is True, "_multiboard_has_work must return True when next_task is present"
