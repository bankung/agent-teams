"""Tests for Kanban #2136 — structured halt taxonomy + bounded transient retry.

Coverage:
  1. classify_exception — classification matrix:
       httpx.TimeoutException   → (transient, timeout)
       httpx.ConnectError       → (transient, connection)
       duck-typed 429           → (transient, rate_limit)
       duck-typed 500 / 503     → (transient, server_error)
       duck-typed 400           → (permanent, bad_request)
       duck-typed 401 / 403     → (permanent, auth)
       exc.response.status_code → same as direct attr
       ValueError (no attr)     → (permanent, unknown)
       class-name heuristic     → (transient, rate_limit) for *RateLimit* name

  2. Retry behavior:
       transient × 2 then success → exactly 3 ainvoke calls, task finishes DONE
       permanent               → exactly 1 ainvoke call, task BLOCKED immediately
       retries exhausted       → halt_reason ends with '(after N retries)'
       sleep is monkeypatched → tests run instantly

  3. halt_reason format — new '<kind>:<short_class>: <detail>' shape;
     asyncio.CancelledError passthrough unaffected.
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
    classify_exception,
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


def _make_graph_module(ainvoke_impl) -> SimpleNamespace:
    return SimpleNamespace(graph=SimpleNamespace(ainvoke=ainvoke_impl))


def _body(req: httpx.Request) -> dict[str, Any]:
    import json
    raw = req.content
    return json.loads(raw) if raw else {}


def _standard_handler(task: dict[str, Any]):
    """Returns a handler that serves a single task on first GET and 200 on PATCHes."""
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
# 1. classify_exception — classification matrix
# ---------------------------------------------------------------------------


def test_classify_httpx_timeout():
    exc = httpx.TimeoutException("timed out")
    assert classify_exception(exc) == ("transient", "timeout")


def test_classify_httpx_connect_error():
    exc = httpx.ConnectError("connection refused")
    assert classify_exception(exc) == ("transient", "connection")


def test_classify_duck_429():
    class FakeRateLimitError(Exception):
        status_code = 429
    assert classify_exception(FakeRateLimitError()) == ("transient", "rate_limit")


def test_classify_duck_500():
    class FakeServerError(Exception):
        status_code = 500
    assert classify_exception(FakeServerError()) == ("transient", "server_error")


def test_classify_duck_503():
    class FakeServiceUnavailable(Exception):
        status_code = 503
    assert classify_exception(FakeServiceUnavailable()) == ("transient", "server_error")


def test_classify_duck_400():
    class FakeBadRequest(Exception):
        status_code = 400
    assert classify_exception(FakeBadRequest()) == ("permanent", "bad_request")


def test_classify_duck_401():
    class FakeUnauthorized(Exception):
        status_code = 401
    assert classify_exception(FakeUnauthorized()) == ("permanent", "auth")


def test_classify_duck_403():
    class FakeForbidden(Exception):
        status_code = 403
    assert classify_exception(FakeForbidden()) == ("permanent", "auth")


def test_classify_response_status_code_429():
    """exc.response.status_code path (httpx-style wrapped errors)."""
    class FakeResp:
        status_code = 429
    class FakeWrappedError(Exception):
        response = FakeResp()
    assert classify_exception(FakeWrappedError()) == ("transient", "rate_limit")


def test_classify_response_status_code_500():
    class FakeResp:
        status_code = 500
    class FakeWrappedError(Exception):
        response = FakeResp()
    assert classify_exception(FakeWrappedError()) == ("transient", "server_error")


def test_classify_value_error_unknown():
    assert classify_exception(ValueError("bad value")) == ("permanent", "unknown")


def test_classify_class_name_heuristic_ratelimit():
    """Class-name heuristic for SDKs that use *RateLimit* without status_code."""
    class AnthropicRateLimitError(Exception):
        pass
    assert classify_exception(AnthropicRateLimitError()) == ("transient", "rate_limit")


def test_classify_class_name_heuristic_timeout():
    class ReadTimeoutError(Exception):
        pass
    assert classify_exception(ReadTimeoutError()) == ("transient", "timeout")


# ---------------------------------------------------------------------------
# 2. Retry behavior
# ---------------------------------------------------------------------------


class _FakeTransientError(Exception):
    """Duck-typed 503 — will classify as transient:server_error."""
    status_code = 503


async def test_transient_twice_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """transient error × 2, success on 3rd attempt → exactly 3 ainvoke calls,
    task finishes DONE."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "2")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    # Monkeypatch sleep so test is instant
    sleep_calls: list[float] = []
    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    task = {"id": 55, "description": "retry-me", "assigned_role": None}
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    call_count = {"n": 0}

    async def ainvoke(state, config):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise _FakeTransientError("service unavailable")
        return {"task_id": 55, "halt_reason": None, "final_result": "done after retries"}

    async with _make_client(handler) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    assert call_count["n"] == 3, f"expected exactly 3 ainvoke calls, got {call_count['n']}"

    patches = [r for r in requests if r.method == "PATCH"]
    statuses = [_body(p)["process_status"] for p in patches]
    assert statuses == [STATUS_IN_PROGRESS, STATUS_DONE], f"unexpected patch sequence: {statuses}"

    # Sleep was called twice (between attempt 1→2 and 2→3)
    assert len(sleep_calls) == 2


async def test_permanent_error_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permanent error → exactly 1 ainvoke call, task immediately BLOCKED."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "2")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    sleep_calls: list[float] = []
    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    task = {"id": 66, "description": "perm-fail", "assigned_role": None}
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    call_count = {"n": 0}

    async def ainvoke(state, config):
        call_count["n"] += 1
        raise ValueError("bad input — permanent")

    async with _make_client(handler) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    assert call_count["n"] == 1, f"expected exactly 1 ainvoke call, got {call_count['n']}"
    assert len(sleep_calls) == 0, "no sleep for permanent errors"

    patches = [r for r in requests if r.method == "PATCH"]
    statuses = [_body(p)["process_status"] for p in patches]
    assert statuses == [STATUS_IN_PROGRESS, STATUS_BLOCKED], f"unexpected: {statuses}"


# ---------------------------------------------------------------------------
# 3. halt_reason format
# ---------------------------------------------------------------------------


async def test_halt_reason_format_permanent_no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permanent error → halt_reason = 'permanent:<class>: <detail>' (no suffix)."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "0")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    task = {"id": 77, "description": "x", "assigned_role": None}
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    async def ainvoke(state, config):
        raise ValueError("something broke")

    async with _make_client(handler) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    patches = [r for r in requests if r.method == "PATCH"]
    blocked = _body(patches[-1])
    halt = blocked["halt_reason"]
    assert halt.startswith("permanent:unknown:"), f"unexpected halt prefix: {halt!r}"
    assert "ValueError" in halt
    assert "something broke" in halt
    assert "retries" not in halt


async def test_halt_reason_format_transient_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient retries exhausted → halt_reason ends with '(after N retries)'."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "2")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    async def fake_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    task = {"id": 88, "description": "x", "assigned_role": None}
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    async def ainvoke(state, config):
        raise _FakeTransientError("upstream overloaded")

    async with _make_client(handler) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    patches = [r for r in requests if r.method == "PATCH"]
    blocked = _body(patches[-1])
    halt = blocked["halt_reason"]
    assert halt.startswith("transient:server_error:"), f"unexpected prefix: {halt!r}"
    assert "_FakeTransientError" in halt
    assert "upstream overloaded" in halt
    assert "(after 2 retries)" in halt


async def test_cancelled_error_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.CancelledError propagates through the retry wrapper unchanged."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "2")

    task = {"id": 99, "description": "cancel-me", "assigned_role": None}

    def handler(req: httpx.Request) -> httpx.Response:
        return _standard_handler(task)(req)

    async def ainvoke(state, config):
        raise asyncio.CancelledError()

    async with _make_client(handler) as client:
        with pytest.raises(asyncio.CancelledError):
            await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))


# ---------------------------------------------------------------------------
# 4. halt_msg: retry suffix survives long-detail truncation
# ---------------------------------------------------------------------------


async def test_halt_reason_long_detail_retry_suffix_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A very long exc message must not push the '(after N retries)' suffix off
    the end of halt_reason.

    POSITIVE: '(after 2 retries)' present in the final halt_reason.
    NEGATIVE: halt_reason must be <= _HALT_REASON_MAX chars (no silent overflow).
    """
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "2")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "0")

    async def fake_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    # Construct an exc message that is much longer than _HALT_REASON_MAX so
    # naive truncation of the full detail string would eat the suffix.
    long_msg = "x" * 1000

    class _LongTransientError(Exception):
        status_code = 503

    task = {"id": 101, "description": "long-detail", "assigned_role": None}
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return _standard_handler(task)(req)

    async def ainvoke(state, config):
        raise _LongTransientError(long_msg)

    async with _make_client(handler) as client:
        await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))

    patches = [r for r in requests if r.method == "PATCH"]
    blocked = _body(patches[-1])
    halt = blocked["halt_reason"]

    # Suffix must survive.
    assert "(after 2 retries)" in halt, (
        f"retry suffix missing from halt_reason: {halt!r}"
    )
    # Total length must not exceed the cap.
    assert len(halt) <= worker._HALT_REASON_MAX, (
        f"halt_reason length {len(halt)} exceeds cap {worker._HALT_REASON_MAX}"
    )


# ---------------------------------------------------------------------------
# 5. Junk env values raise RuntimeError
# ---------------------------------------------------------------------------


async def test_junk_transient_retries_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LANGGRAPH_TRANSIENT_RETRIES='not-a-number' raises RuntimeError with a
    helpful message, NOT a bare ValueError."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "not-a-number")

    task = {"id": 102, "description": "junk-env", "assigned_role": None}

    def handler(req: httpx.Request) -> httpx.Response:
        return _standard_handler(task)(req)

    async def ainvoke(state, config):
        return {"halt_reason": None, "final_result": "ok"}

    async with _make_client(handler) as client:
        with pytest.raises(RuntimeError, match="LANGGRAPH_TRANSIENT_RETRIES"):
            await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))


async def test_junk_retry_backoff_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LANGGRAPH_RETRY_BACKOFF_SEC='abc' raises RuntimeError with a helpful message."""
    cfg = _cfg(monkeypatch)
    monkeypatch.setenv("LANGGRAPH_TRANSIENT_RETRIES", "1")
    monkeypatch.setenv("LANGGRAPH_RETRY_BACKOFF_SEC", "abc")

    task = {"id": 103, "description": "junk-backoff", "assigned_role": None}

    def handler(req: httpx.Request) -> httpx.Response:
        return _standard_handler(task)(req)

    async def ainvoke(state, config):
        return {"halt_reason": None, "final_result": "ok"}

    async with _make_client(handler) as client:
        with pytest.raises(RuntimeError, match="LANGGRAPH_RETRY_BACKOFF_SEC"):
            await _poll_once(client, _make_graph_module(ainvoke), cfg, _headers(cfg))


# ---------------------------------------------------------------------------
# 6. classify_exception — Google 429 / RESOURCE_EXHAUSTED (Kanban #2274)
# ---------------------------------------------------------------------------


class ChatGoogleGenerativeAIError(Exception):
    """Local stub — mirrors the real class name without importing langchain_google_genai."""


_GOOGLE_429_MSG = (
    "ChatGoogleGenerativeAIError: Error calling model 'gemini-2.5-flash-lite'"
    " (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429,"
    " 'message': 'You exceeded your current quota, please check your plan and"
    " billing details. For more information on this error, head to:"
    " https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your"
    " current usage, head to: https://ai.dev/rate-limit. \\n* Quota exceeded"
    " for metric: generativelanguage.googleapis.com/generate_content_free"
)


def test_classify_google_resource_exhausted_message_heuristic():
    """Real-shape repro: ChatGoogleGenerativeAIError with RESOURCE_EXHAUSTED message,
    no status attrs, no cause → (transient, rate_limit) via message heuristic."""
    exc = ChatGoogleGenerativeAIError(_GOOGLE_429_MSG)
    assert classify_exception(exc) == ("transient", "rate_limit")


def test_classify_cause_chain_429():
    """Cause-chain unwrap: outer wrapper with no status, inner exc with status_code=429
    → (transient, rate_limit)."""
    class _InnerRateLimited(Exception):
        status_code = 429

    inner = _InnerRateLimited("quota hit")
    outer = Exception("wrapper")
    outer.__cause__ = inner
    assert classify_exception(outer) == ("transient", "rate_limit")


def test_classify_cause_chain_5xx():
    """Cause-chain unwrap: inner exc with status_code=503 → (transient, server_error)."""
    class _InnerServerError(Exception):
        status_code = 503

    inner = _InnerServerError("backend down")
    outer = Exception("wrapper")
    outer.__cause__ = inner
    assert classify_exception(outer) == ("transient", "server_error")


def test_classify_bare_429_without_quota_context_stays_unknown():
    """Negative guard: '429' in message without quota/rate context word
    → (permanent, unknown) — no misclassification of e.g. 'error at line 429'."""
    exc = ValueError("parse failed at line 429 of config")
    assert classify_exception(exc) == ("permanent", "unknown")
