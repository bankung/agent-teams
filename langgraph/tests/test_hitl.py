"""HITL engine integration tests — Kanban #986 (#950 sub-task 1).

Covers:
  - happy path: specialist node calls `request_user_input` → checkpoint
    persisted → resume with answer → graph completes with the answer in state.
  - validate_answer (strict): missing payload / empty / not-in-options.
  - resume idempotency: re-issuing Command(resume=...) on a thread that has
    already advanced past the interrupt is a no-op (verified node-body call
    count stays at 1 across two resumes).
  - failure modes:
      * checkpoint missing (resume a thread that never paused) →
        CheckpointMissingError raised when `checkpoint_required=True`.
      * engine crash on resume → EngineCrashError wraps the cause.
  - worker integration: `_maybe_resume_hitl_task` filtering (cursor + halt
    code + valid-answer rules), resume → DONE PATCH body shape, and the
    invalid-answer / checkpoint-missing / crash paths each PATCH the
    expected halt_reason.

Strategy:
  - Tiny in-memory graph (InMemorySaver) for engine-level assertions. The
    production graph uses AsyncPostgresSaver but the checkpoint semantics
    (load on aget_state, advance via Command.resume) are identical at the
    StateGraph layer per LangGraph 1.2.0 — verified by the resume probe
    captured in _scratch/hitl-resume-design.md notes.
  - httpx.MockTransport for the worker tests (mirrors test_worker.py).
"""

from __future__ import annotations

# Namespace-collision note: the local project dir is named `langgraph/` and
# shadows the upstream LangGraph PyPI package during pytest collection. The
# fix lives in `tests/conftest.py` (runs at session start before any test
# module imports), which swaps a synthetic namespace-pkg shim into sys.modules
# pointing at the real upstream tree.

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

import hitl
import worker
from hitl import (
    AnswerNotInOptionsError,
    CheckpointMissingError,
    EmptyAnswerError,
    EngineCrashError,
    MissingQuestionPayloadError,
    has_checkpoint,
    request_user_input,
    resume_config,
    resume_graph,
    thread_id_for_task,
    validate_answer,
)
from worker import (
    STATUS_BLOCKED,
    STATUS_DONE,
    WorkerConfig,
    _last_valid_answer,
    _maybe_resume_hitl_task,
    _needs_resume,
    _poll_once,
)


# ---------------------------------------------------------------------------
# Fixtures
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


# ---------------------------------------------------------------------------
# Mini graph for engine-level tests
# ---------------------------------------------------------------------------


class _MiniState(TypedDict, total=False):
    foo: str
    answer: str
    final_result: str
    halt_reason: str


def _build_mini_graph(node_fn: Any) -> Any:
    """Compile a one-node graph with an InMemorySaver. The node body is the
    test-supplied callable so each test can exercise interrupt / non-interrupt
    / crash behaviour without rewiring the graph each time."""
    builder = StateGraph(_MiniState)
    builder.add_node("only", node_fn)
    builder.add_edge(START, "only")
    builder.add_edge("only", END)
    return builder.compile(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# validate_answer — strict (Q3=A)
# ---------------------------------------------------------------------------


def test_validate_answer_decision_match() -> None:
    """Decision task with options=['a','b','c'] accepts exact match."""
    payload = {"question": "pick one", "options": ["a", "b", "c"]}
    assert validate_answer(payload, "b") == "b"


def test_validate_answer_decision_strips_whitespace() -> None:
    """Leading/trailing whitespace is stripped before the in-options check."""
    payload = {"question": "pick one", "options": ["staging", "prod"]}
    assert validate_answer(payload, "  staging  ") == "staging"


def test_validate_answer_decision_rejects_unknown() -> None:
    """Mismatch against options raises AnswerNotInOptionsError with halt_code."""
    payload = {"question": "pick one", "options": ["a", "b"]}
    with pytest.raises(AnswerNotInOptionsError) as excinfo:
        validate_answer(payload, "c")
    assert excinfo.value.halt_code == "invalid_answer_not_in_options"
    # The halt_reason wire format: <halt_code>:<message>
    assert excinfo.value.as_halt_reason().startswith(
        "invalid_answer_not_in_options:"
    )


def test_validate_answer_question_accepts_free_text() -> None:
    """Question task (no options) accepts any non-empty string."""
    payload = {"question": "describe the bug"}
    assert validate_answer(payload, "kapow") == "kapow"


def test_validate_answer_question_empty_options_treated_as_question() -> None:
    """options=[] is the same as options=None — free-text answer accepted."""
    payload = {"question": "describe the bug", "options": []}
    assert validate_answer(payload, "ok") == "ok"


def test_validate_answer_missing_payload_raises() -> None:
    with pytest.raises(MissingQuestionPayloadError) as excinfo:
        validate_answer(None, "anything")
    assert excinfo.value.halt_code == "invalid_answer_missing_payload"


def test_validate_answer_empty_string_raises() -> None:
    payload = {"question": "x"}
    with pytest.raises(EmptyAnswerError):
        validate_answer(payload, "")


def test_validate_answer_whitespace_only_raises() -> None:
    payload = {"question": "x"}
    with pytest.raises(EmptyAnswerError):
        validate_answer(payload, "   \t  ")


def test_validate_answer_none_answer_raises() -> None:
    payload = {"question": "x"}
    with pytest.raises(EmptyAnswerError):
        validate_answer(payload, None)


def test_validate_answer_coerces_non_string() -> None:
    """Non-string answers get str()-coerced; numeric 42 → '42' is accepted
    when no options gate it (free-text question)."""
    payload = {"question": "how many?"}
    assert validate_answer(payload, 42) == "42"


# ---------------------------------------------------------------------------
# thread_id + resume_config
# ---------------------------------------------------------------------------


def test_thread_id_format() -> None:
    assert thread_id_for_task(7) == "task-7"


def test_resume_config_shape() -> None:
    assert resume_config(42) == {"configurable": {"thread_id": "task-42"}}


def test_resume_thread_id_matches_worker() -> None:
    """The worker builds thread_id inline as f'task-{task_id}'; hitl mirrors.
    This pins the convention — drift on either side trips this test."""
    # Mirror the literal at worker.py:_poll_once (config = {"configurable":
    # {"thread_id": f"task-{task_id}"}}).
    assert thread_id_for_task(123) == "task-123"


# ---------------------------------------------------------------------------
# Engine — happy path: interrupt → checkpoint → resume → completes
# ---------------------------------------------------------------------------


async def test_interrupt_pauses_graph_and_persists_checkpoint() -> None:
    """A specialist that calls request_user_input pauses; checkpoint persists
    in the saver (verifiable via aget_state(state.created_at is set))."""

    def node(state):
        # First invocation: emit interrupt with the canonical payload shape.
        ans = request_user_input(
            {"question": "Deploy where?", "options": ["staging", "prod"]}
        )
        return {"answer": ans, "foo": "after-resume"}

    graph = _build_mini_graph(node)
    cfg = resume_config(99)

    # First invoke → hits interrupt, returns result-with-__interrupt__.
    result = await graph.ainvoke({"foo": "before"}, config=cfg)
    assert "__interrupt__" in result
    interrupt_list = result["__interrupt__"]
    assert len(interrupt_list) == 1
    assert interrupt_list[0].value == {
        "question": "Deploy where?",
        "options": ["staging", "prod"],
    }

    # Checkpoint persisted: aget_state finds it.
    assert await has_checkpoint(graph, 99) is True


async def test_resume_with_answer_completes_graph() -> None:
    """Resume with Command(resume=<answer>) continues the node body; the
    answer is returned by request_user_input + lands in final state."""
    captured: dict[str, Any] = {}

    def node(state):
        ans = request_user_input({"question": "?", "options": ["a", "b"]})
        captured["answer_in_node"] = ans
        return {"answer": ans, "foo": "done"}

    graph = _build_mini_graph(node)
    cfg = resume_config(100)

    # 1st: pause.
    await graph.ainvoke({"foo": "start"}, config=cfg)
    # Resume.
    final = await resume_graph(graph, 100, "a")
    assert captured["answer_in_node"] == "a"
    assert final["answer"] == "a"
    assert final["foo"] == "done"
    assert "__interrupt__" not in final


# ---------------------------------------------------------------------------
# Resume idempotency (AC #3)
# ---------------------------------------------------------------------------


async def test_resume_idempotent_no_double_execution() -> None:
    """Re-resuming a thread that's already advanced past the interrupt does
    NOT re-execute the node. Verified by counting node-body invocations
    across two resume calls."""
    call_count = {"n": 0}

    def node(state):
        call_count["n"] += 1
        ans = request_user_input({"question": "?"})
        return {"answer": ans, "foo": "done"}

    graph = _build_mini_graph(node)
    cfg = resume_config(200)

    await graph.ainvoke({"foo": "start"}, config=cfg)
    assert call_count["n"] == 1  # the interrupt run counts as 1 partial exec

    # First resume — completes the node (second body invocation).
    first = await resume_graph(graph, 200, "answer-1")
    assert call_count["n"] == 2
    assert first["answer"] == "answer-1"

    # Second resume — graph is fully done; node body should NOT re-run.
    second = await resume_graph(graph, 200, "answer-2")
    assert call_count["n"] == 2  # NO additional call
    # State is unchanged (the second answer is ignored — the graph already
    # consumed answer-1 and reached END).
    assert second["answer"] == "answer-1"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


async def test_resume_checkpoint_missing_raises() -> None:
    """Resuming a thread that was never started raises CheckpointMissingError
    when checkpoint_required=True (production default)."""

    def node(state):
        return {"foo": "x"}

    graph = _build_mini_graph(node)
    # task 999 has never been invoked — no checkpoint.
    with pytest.raises(CheckpointMissingError) as excinfo:
        await resume_graph(graph, 999, "any")
    assert excinfo.value.halt_code == "checkpoint_missing"
    assert "task-999" in str(excinfo.value)


async def test_resume_checkpoint_required_false_skips_check() -> None:
    """The check is opt-out for tests / advanced callers. With
    checkpoint_required=False, resume_graph proceeds even on a fresh thread —
    the graph will then run from START."""

    def node(state):
        return {"foo": "fresh"}

    graph = _build_mini_graph(node)
    # No pause — resume proceeds; graph runs the node body once.
    result = await resume_graph(
        graph, 1234, "ignored", checkpoint_required=False
    )
    assert result["foo"] == "fresh"


async def test_resume_engine_crash_wraps_cause() -> None:
    """A graph-side exception during resume is wrapped in EngineCrashError
    with the original cause attached via `__cause__`."""

    def node(state):
        # Pause first so the test can observe the resume crash.
        request_user_input({"question": "?"})
        # On resume, raise.
        raise RuntimeError("graph kapow")

    graph = _build_mini_graph(node)
    cfg = resume_config(300)
    await graph.ainvoke({"foo": "start"}, config=cfg)

    with pytest.raises(EngineCrashError) as excinfo:
        await resume_graph(graph, 300, "any")
    assert excinfo.value.halt_code == "engine_crash"
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "graph kapow" in str(excinfo.value)
    assert excinfo.value.as_halt_reason().startswith("engine_crash:")


# ---------------------------------------------------------------------------
# Worker filter — _needs_resume + _last_valid_answer
# ---------------------------------------------------------------------------


def test_last_valid_answer_picks_newest_valid() -> None:
    payload = {
        "question": "?",
        "answer_history": [
            {
                "value": "old",
                "answered_at": "2026-05-15T10:00:00Z",
                "is_valid": False,
            },
            {
                "value": "current",
                "answered_at": "2026-05-15T11:00:00Z",
                "is_valid": True,
            },
        ],
    }
    assert _last_valid_answer(payload)["value"] == "current"


def test_last_valid_answer_none_when_all_invalid() -> None:
    payload = {
        "question": "?",
        "answer_history": [
            {"value": "x", "answered_at": "t", "is_valid": False},
        ],
    }
    assert _last_valid_answer(payload) is None


def test_last_valid_answer_none_when_empty() -> None:
    assert _last_valid_answer({"question": "?"}) is None
    assert _last_valid_answer({"question": "?", "answer_history": []}) is None
    assert _last_valid_answer(None) is None


def test_needs_resume_yes_when_halt_question_with_fresh_answer() -> None:
    task = {
        "id": 1,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "go",
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }
    needs, ans = _needs_resume(task)
    assert needs is True
    assert ans == "go"


def test_needs_resume_yes_when_halt_decision_with_fresh_answer() -> None:
    task = {
        "id": 1,
        "halt_reason": "decision",
        "question_payload": {
            "question": "?",
            "options": ["a", "b"],
            "answer_history": [
                {
                    "value": "b",
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": {},
    }
    needs, ans = _needs_resume(task)
    assert needs is True
    assert ans == "b"


def test_needs_resume_no_when_halt_unrelated() -> None:
    """halt_reason set to something other than question/decision (e.g.,
    'tool_permission_review: ...') is NOT consumed by the HITL resume path."""
    task = {
        "id": 1,
        "halt_reason": "tool_permission_review: file_edit tier=write",
        "question_payload": {
            "answer_history": [
                {"value": "go", "answered_at": "t", "is_valid": True}
            ]
        },
    }
    needs, ans = _needs_resume(task)
    assert needs is False


def test_needs_resume_no_when_no_answer_yet() -> None:
    """halt_reason=question but answer_history is empty → still awaiting input."""
    task = {
        "id": 1,
        "halt_reason": "question",
        "question_payload": {"question": "?", "answer_history": []},
    }
    needs, _ans = _needs_resume(task)
    assert needs is False


def test_needs_resume_no_when_cursor_caught_up() -> None:
    """resume_context.last_consumed_answered_at >= latest answer's
    answered_at → idempotency cursor skip."""
    task = {
        "id": 1,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "go",
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": {
            "last_consumed_answered_at": "2026-05-16T10:00:00Z"
        },
    }
    needs, _ans = _needs_resume(task)
    assert needs is False


def test_needs_resume_yes_when_new_answer_after_cursor() -> None:
    """Newer answer than cursor → resumable again (user invalidated prior +
    re-answered)."""
    task = {
        "id": 1,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "go",
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": {
            "last_consumed_answered_at": "2026-05-16T09:00:00Z"
        },
    }
    needs, ans = _needs_resume(task)
    assert needs is True
    assert ans == "go"


# ---------------------------------------------------------------------------
# Worker — _maybe_resume_hitl_task: full happy-path PATCH shape
# ---------------------------------------------------------------------------


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
    """AsyncClient whose PATCH handler captures the body + always responds 200."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH":
            capture.calls.append((req.url.path, _body(req)))
            return httpx.Response(200, json={"id": 1})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def test_resume_hitl_task_happy_path_patches_done() -> None:
    """End-to-end: pending_questions task with answer → graph resumes →
    worker PATCHes DONE + clears halt_reason + stamps cursor."""

    def node(state):
        ans = request_user_input({"question": "?", "options": ["a", "b"]})
        return {"answer": ans, "foo": "done", "final_result": f"used: {ans}"}

    graph = _build_mini_graph(node)
    # Pause first so there's a checkpoint to resume.
    await graph.ainvoke({"foo": "start"}, config=resume_config(42))

    task_row = {
        "id": 42,
        "halt_reason": "decision",
        "question_payload": {
            "question": "Deploy where?",
            "options": ["a", "b"],
            "answer_history": [
                {
                    "value": "a",
                    "answered_by": "user",
                    "answered_at": "2026-05-16T10:00:00Z",
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
    path, body = capture.calls[0]
    assert path == "/api/tasks/42"
    assert body["process_status"] == STATUS_DONE
    assert body["halt_reason"] is None  # cleared on successful resume
    assert body["is_pending"] is False
    assert "used: a" in body["status_change_reason"]
    # Idempotency cursor stamped.
    assert (
        body["resume_context"]["last_consumed_answered_at"]
        == "2026-05-16T10:00:00Z"
    )


async def test_resume_hitl_task_invalid_answer_patches_blocked() -> None:
    """An answer not in the options list never reaches the graph; the worker
    PATCHes BLOCKED with halt_reason='invalid_answer_not_in_options:<msg>'."""

    def node(state):
        # Should NOT be called — validation fails before resume.
        raise AssertionError("graph must not run on invalid answer")

    graph = _build_mini_graph(node)
    # Still pause so there is a checkpoint (validation runs before resume).
    # Use a different node that pauses (to keep the assertion above honest:
    # this graph's body must NOT run during resume).
    def pausing_node(state):
        request_user_input({"question": "?", "options": ["a", "b"]})
        # If we get here on resume, the test fails (assertion below).
        raise AssertionError("resume should not have proceeded")

    paused_graph = _build_mini_graph(pausing_node)
    await paused_graph.ainvoke({"foo": "start"}, config=resume_config(50))

    task_row = {
        "id": 50,
        "halt_reason": "decision",
        "question_payload": {
            "question": "?",
            "options": ["a", "b"],
            "answer_history": [
                {
                    "value": "c",  # NOT in options
                    "answered_at": "2026-05-16T10:00:00Z",
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
            _make_graph_module(paused_graph),
            cfg,
            task_row,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    assert len(capture.calls) == 1
    _, body = capture.calls[0]
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"].startswith("invalid_answer_not_in_options:")
    assert body["is_pending"] is True
    # Cursor stamped so the worker doesn't retry the same bad answer next tick.
    assert (
        body["resume_context"]["last_consumed_answered_at"]
        == "2026-05-16T10:00:00Z"
    )


async def test_resume_hitl_task_checkpoint_missing_patches_blocked() -> None:
    """No checkpoint exists for the task → worker PATCHes BLOCKED with
    halt_reason='checkpoint_missing:<msg>'."""

    def node(state):
        return {"foo": "x"}

    graph = _build_mini_graph(node)
    # task-77 has never been invoked.

    task_row = {
        "id": 77,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "valid-but-no-state",
                    "answered_at": "2026-05-16T10:00:00Z",
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
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"].startswith("checkpoint_missing:")
    assert body["is_pending"] is True


async def test_resume_hitl_task_engine_crash_patches_blocked() -> None:
    """Graph raises during resume → worker PATCHes BLOCKED with
    halt_reason='engine_crash:<ClassName: msg>'."""

    def node(state):
        request_user_input({"question": "?"})
        raise RuntimeError("boom on resume")

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(88))

    task_row = {
        "id": 88,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "anything",
                    "answered_at": "2026-05-16T10:00:00Z",
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
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"].startswith("engine_crash:")
    assert "RuntimeError" in body["halt_reason"]
    assert "boom on resume" in body["halt_reason"]


async def test_resume_hitl_task_re_interrupt_keeps_blocked() -> None:
    """If the specialist node emits ANOTHER interrupt during resume (multi-step
    HITL), the worker keeps the task BLOCKED rather than flipping DONE."""

    interrupt_calls = {"n": 0}

    def node(state):
        # Two-step HITL: ask twice.
        interrupt_calls["n"] += 1
        ans1 = request_user_input({"question": "first?"})
        ans2 = request_user_input({"question": "second?"})
        return {"answer": f"{ans1}+{ans2}", "foo": "done"}

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(91))

    task_row = {
        "id": 91,
        "halt_reason": "question",
        "question_payload": {
            "question": "first?",
            "answer_history": [
                {
                    "value": "one",
                    "answered_at": "2026-05-16T10:00:00Z",
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
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"] == "question"
    assert body["is_pending"] is True


# ---------------------------------------------------------------------------
# Worker — _poll_once integration with pending_questions
# ---------------------------------------------------------------------------


async def test_poll_once_invokes_resume_for_pending_question_with_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_poll_once walks pending_questions; tasks that need resume get PATCHed."""

    def node(state):
        ans = request_user_input({"question": "?"})
        return {"answer": ans, "final_result": "ok"}

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(42))

    pending_task = {
        "id": 42,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "yes",
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    captured_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_paths.append(f"{req.method} {req.url.path}")
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(
                200,
                json={
                    "next_task": None,
                    "resume_tasks": [],
                    "pending_questions": [pending_task],
                    "blocked_count": 0,
                },
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected: {req.method} {req.url.path}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    ) as client:
        await _poll_once(
            client,
            _make_graph_module(graph),
            cfg,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    # GET next-autorun + PATCH for the resume.
    assert captured_paths == [
        "GET /api/tasks/next-autorun",
        "PATCH /api/tasks/42",
    ]


async def test_poll_once_skips_pending_question_without_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pending_questions that have no fresh answer are walked but NOT PATCHed."""

    def node(state):
        request_user_input({"question": "?"})
        return {"foo": "done"}

    graph = _build_mini_graph(node)
    await graph.ainvoke({"foo": "start"}, config=resume_config(60))

    pending_task = {
        "id": 60,
        "halt_reason": "question",
        "question_payload": {"question": "?", "answer_history": []},
        "resume_context": None,
    }

    cfg = _make_cfg()
    captured_paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_paths.append(f"{req.method} {req.url.path}")
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": None,
                    "resume_tasks": [],
                    "pending_questions": [pending_task],
                    "blocked_count": 0,
                },
            )
        raise AssertionError(f"no PATCH expected; got {req.method}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    ) as client:
        await _poll_once(
            client,
            _make_graph_module(graph),
            cfg,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    # Only the GET — no PATCH because no answer yet.
    assert captured_paths == ["GET /api/tasks/next-autorun"]


async def test_poll_once_hitl_resume_failure_does_not_block_tick(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One bad resume task (checkpoint missing, say) does NOT prevent the
    rest of the tick (other pending_questions + next_task) from running."""

    def good_node(state):
        ans = request_user_input({"question": "?"})
        return {"final_result": f"got: {ans}"}

    graph = _build_mini_graph(good_node)
    # Set up a checkpoint only for task 200 — task 100 will hit
    # checkpoint_missing.
    await graph.ainvoke({"foo": "start"}, config=resume_config(200))

    bad_task = {
        "id": 100,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "x",
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }
    good_task = {
        "id": 200,
        "halt_reason": "question",
        "question_payload": {
            "question": "?",
            "answer_history": [
                {
                    "value": "yes",
                    "answered_at": "2026-05-16T10:01:00Z",
                    "is_valid": True,
                }
            ],
        },
        "resume_context": None,
    }

    cfg = _make_cfg()
    patched_ids: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "next_task": None,
                    "resume_tasks": [],
                    "pending_questions": [bad_task, good_task],
                    "blocked_count": 0,
                },
            )
        if req.method == "PATCH":
            tid = int(req.url.path.rsplit("/", 1)[-1])
            patched_ids.append(tid)
            return httpx.Response(200, json={"id": tid})
        raise AssertionError(f"unexpected: {req.method} {req.url.path}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0
    ) as client:
        await _poll_once(
            client,
            _make_graph_module(graph),
            cfg,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    # Both tasks got a PATCH — the bad one BLOCKED with checkpoint_missing,
    # the good one DONE.
    assert sorted(patched_ids) == [100, 200]


async def test_resume_hitl_task_preserves_prior_resume_context_on_invalid_answer() -> None:
    """Regression net for M1 (#986 review): `_build_resume_halt_body` must
    preserve free-form keys the upstream caller stashed in resume_context.
    Today only `last_consumed_answered_at` is written, but the contract on
    `_stamped_resume_context` claims any prior keys survive — so a failure
    PATCH (invalid_answer / checkpoint_missing / engine_crash) must echo
    those keys back, not wipe them."""

    def pausing_node(state):
        request_user_input({"question": "?", "options": ["a", "b"]})
        raise AssertionError("resume should not have proceeded")

    paused_graph = _build_mini_graph(pausing_node)
    await paused_graph.ainvoke({"foo": "start"}, config=resume_config(51))

    task_row = {
        "id": 51,
        "halt_reason": "decision",
        "question_payload": {
            "question": "?",
            "options": ["a", "b"],
            "answer_history": [
                {
                    "value": "c",  # NOT in options → InvalidAnswerError
                    "answered_at": "2026-05-16T10:00:00Z",
                    "is_valid": True,
                }
            ],
        },
        # Prior resume_context with a free-form key that MUST survive the halt.
        "resume_context": {
            "last_consumed_answered_at": "2026-05-16T09:00:00Z",
            "custom_key": "value-X",
        },
    }

    cfg = _make_cfg()
    capture = _PatchCapture()
    async with _make_patch_client(capture) as client:
        await _maybe_resume_hitl_task(
            client,
            _make_graph_module(paused_graph),
            cfg,
            task_row,
            {"X-Project-Id": "1", "Content-Type": "application/json"},
        )

    assert len(capture.calls) == 1
    _, body = capture.calls[0]
    assert body["process_status"] == STATUS_BLOCKED
    assert body["halt_reason"].startswith("invalid_answer_not_in_options:")
    # The new cursor stamp won.
    assert (
        body["resume_context"]["last_consumed_answered_at"]
        == "2026-05-16T10:00:00Z"
    )
    # The free-form key SURVIVED the failure PATCH (M1 contract).
    assert body["resume_context"]["custom_key"] == "value-X"
