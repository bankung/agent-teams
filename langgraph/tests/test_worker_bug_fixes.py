"""Regression tests for Kanban #2498 / #2499 bug fixes in worker.py.

Coverage:
  H-2 — bad LANGGRAPH_TRANSIENT_RETRIES fails at WorkerConfig init, not
         mid-task (task never reaches IN_PROGRESS on a junk env value).
  H-3 — on transient retry after attempt 1, _poll_once resumes from the
         checkpoint (None input) instead of re-injecting initial_state;
         the brief does NOT appear twice in any ainvoke call's input.
"""

from __future__ import annotations

import asyncio
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
# Shared fixtures (mirror test_worker_retry.py style)
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
        "LANGGRAPH_TRANSIENT_RETRIES",
        "LANGGRAPH_RETRY_BACKOFF_SEC",
    ):
        monkeypatch.delenv(var, raising=False)


def _cfg(monkeypatch: pytest.MonkeyPatch) -> WorkerConfig:
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")
    return WorkerConfig()


def _headers(cfg: WorkerConfig) -> dict[str, str]:
    return {"X-Project-Id": str(cfg.project_id), "Content-Type": "application/json"}


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _make_graph_module(ainvoke_impl, aget_state_impl=None) -> SimpleNamespace:
    # #2664 — default aget_state(created_at=None) so the fresh-pickup clear in
    # _poll_once (has_checkpoint -> compiled.aget_state) is SKIPPED unless a test
    # supplies its own aget_state. checkpointer.adelete_thread is a no-op stub so
    # any test that DOES reach the clear (e.g. h3-retry monkeypatches
    # has_checkpoint -> True) doesn't AttributeError on compiled.checkpointer.
    if aget_state_impl is None:
        async def aget_state_impl(config):  # noqa: ANN001 — test stub
            return SimpleNamespace(created_at=None)

    async def _adelete_thread(thread_id):
        return None

    graph = SimpleNamespace(
        ainvoke=ainvoke_impl,
        aget_state=aget_state_impl,
        checkpointer=SimpleNamespace(adelete_thread=_adelete_thread),
    )
    return SimpleNamespace(graph=graph)


def _body(req: httpx.Request) -> dict[str, Any]:
    import json
    raw = req.content
    return json.loads(raw) if raw else {}


def _standard_handler(task: dict[str, Any]):
    """Serves a single task on GET and 200 on PATCHes."""
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


class _FakeTransientError(Exception):
    """Duck-typed 503 — classifies as transient:server_error."""
    status_code = 503


# ---------------------------------------------------------------------------
# H-2 — bad LANGGRAPH_TRANSIENT_RETRIES raises at WorkerConfig init
# ---------------------------------------------------------------------------


def test_h2_bad_transient_retries_raises_at_workerconfig_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-2 regression: a malformed LANGGRAPH_TRANSIENT_RETRIES must raise
    RuntimeError at WorkerConfig.__init__, not mid-task.

    NEGATIVE: WorkerConfig() must NOT succeed (task never reaches IN_PROGRESS).
    POSITIVE: RuntimeError is raised with a message naming the env var.
    """
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "not-a-number")

    with pytest.raises(RuntimeError, match="LANGGRAPH_TRANSIENT_RETRIES"):
        WorkerConfig()


def test_h2_negative_transient_retries_raises_at_workerconfig_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-2 regression: a negative LANGGRAPH_TRANSIENT_RETRIES must also raise
    at WorkerConfig init (non-negative constraint)."""
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "-1")

    with pytest.raises(RuntimeError, match="LANGGRAPH_TRANSIENT_RETRIES"):
        WorkerConfig()


def test_h2_valid_zero_retries_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-2 positive path: zero retries is valid (immediate-halt on first error)."""
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", "1")
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "0")
    # Must not raise.
    cfg = WorkerConfig()
    assert cfg is not None


# ---------------------------------------------------------------------------
# H-3 — retry does not double-inject the brief
# ---------------------------------------------------------------------------


async def test_h3_retry_resumes_from_checkpoint_not_reinject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-3 regression: on transient retry after attempt 1, when a checkpoint
    exists, _poll_once must pass None (resume-from-checkpoint) to ainvoke
    instead of re-injecting initial_state.

    NEGATIVE: initial_state (with the brief) must NOT be passed to ainvoke on
    attempt 2 when the checkpoint exists.
    POSITIVE: None is passed to ainvoke on the second attempt.
    """
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "1")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    async def fake_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    # Simulate: has_checkpoint returns True after attempt 1 (checkpoint was written).
    checkpoint_check_count = {"n": 0}

    async def fake_has_checkpoint(graph, task_id: int) -> bool:
        checkpoint_check_count["n"] += 1
        return True  # checkpoint always present after first attempt

    monkeypatch.setattr(worker, "has_checkpoint", fake_has_checkpoint)

    task = {"id": 42, "description": "the-brief", "assigned_role": None}
    invoke_inputs: list = []
    call_count = {"n": 0}

    async def ainvoke(state, config):
        invoke_inputs.append(state)
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _FakeTransientError("first attempt fails")
        # Second attempt: success.
        return {"task_id": 42, "halt_reason": None, "final_result": "ok"}

    async def aget_state(config):
        # Simulate a checkpoint existing (created_at non-None).
        return SimpleNamespace(created_at="2026-06-19T00:00:00Z")

    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    graph_module = _make_graph_module(ainvoke, aget_state_impl=aget_state)

    cfg = _cfg(monkeypatch)
    async with _make_client(handler) as client:
        await _poll_once(client, graph_module, cfg, _headers(cfg))

    assert call_count["n"] == 2, f"expected exactly 2 ainvoke calls, got {call_count['n']}"

    # NEGATIVE: second call must NOT have received initial_state (i.e. not a dict
    # with 'brief' set). It must have received None (resume path).
    second_input = invoke_inputs[1]
    assert second_input is None, (
        f"H-3: on retry with checkpoint present, ainvoke must receive None "
        f"(resume-from-checkpoint), but got: {second_input!r}"
    )

    # POSITIVE: first call received the real initial_state with the brief.
    first_input = invoke_inputs[0]
    assert isinstance(first_input, dict) and first_input.get("brief") == "the-brief", (
        f"first ainvoke call should receive initial_state with brief, got: {first_input!r}"
    )

    # Task should finish DONE.
    patches = [r for r in requests if r.method == "PATCH"]
    statuses = [_body(p).get("process_status") for p in patches]
    assert STATUS_DONE in statuses, f"task should finish DONE; patch statuses: {statuses}"


async def test_h3_retry_reinjects_when_no_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-3 positive guard: if no checkpoint exists after attempt 1 (e.g. the
    graph errored before writing one), the retry must still pass initial_state —
    falling back to the full initial_state on None-checkpoint is the safe path.
    """
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "1")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    async def fake_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    async def fake_has_checkpoint(graph, task_id: int) -> bool:
        return False  # no checkpoint — graph errored before writing state

    monkeypatch.setattr(worker, "has_checkpoint", fake_has_checkpoint)

    task = {"id": 43, "description": "no-cp-brief", "assigned_role": None}
    invoke_inputs: list = []
    call_count = {"n": 0}

    async def ainvoke(state, config):
        invoke_inputs.append(state)
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _FakeTransientError("error before checkpoint written")
        return {"task_id": 43, "halt_reason": None, "final_result": "ok"}

    async def aget_state(config):
        return SimpleNamespace(created_at=None)

    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    graph_module = _make_graph_module(ainvoke, aget_state_impl=aget_state)

    cfg = _cfg(monkeypatch)
    async with _make_client(handler) as client:
        await _poll_once(client, graph_module, cfg, _headers(cfg))

    assert call_count["n"] == 2

    # POSITIVE: second call also receives initial_state (not None) because
    # there was no checkpoint to resume from.
    second_input = invoke_inputs[1]
    assert isinstance(second_input, dict) and "brief" in second_input, (
        f"H-3: with no checkpoint, retry must re-send initial_state; got: {second_input!r}"
    )
