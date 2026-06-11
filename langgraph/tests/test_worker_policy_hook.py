"""Worker approval-policy hook tests — Kanban #957 Phase 1.

Pins the `_poll_once` policy branch:

  - HITL pause + no policies          → BLOCKED+question_payload (unchanged)
  - HITL pause + auto_approve match   → graph resumed with default_answer,
                                        no BLOCKED PATCH
  - HITL pause + auto_deny match      → BLOCKED + halt_reason='operator_rejected',
                                        status_change_reason names the policy
  - HITL pause + no policy match      → REQUIRE_ATTENTION (normal HITL pause)
  - Non-HITL halt                     → policy hook skipped (no extra GET)
  - DONE                              → policy hook skipped
  - Project fetch fails (404 / 500)   → fall back to REQUIRE_ATTENTION
  - HTTP error on fetch               → fall back; cache NOT poisoned

Uses httpx.MockTransport (no real network) and the SimpleNamespace-graph
stand-in (no real LangGraph). The compiled graph's `ainvoke` is a plain
async function; for HITL pauses it returns final_state with __interrupt__
populated; for resume it returns final_state with halt_reason=None.

The compiled graph also exposes `aget_state` for the resume's
checkpoint-presence check. We return a SimpleNamespace with
`created_at='2026-05-17T00:00:00Z'` so `has_checkpoint` returns True
(the worker resumes the real `Command(resume=...)`-path) — except in
tests that explicitly verify no-resume behavior.
"""

from __future__ import annotations

import json
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
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clear_policy_cache() -> None:
    """Reset both in-process caches before each test so prior fetches
    don't leak.

    Pre-warms the required_binaries cache for project 1 with None so the
    required_binaries GET (which fires before IN_PROGRESS) is skipped in all
    policy-hook tests — these tests focus on the approval-policy path and
    the required_binaries check is already covered in test_worker_prereq_gate.py.
    Without this warm-up, run-order artefacts would cause an extra GET to appear
    whenever the cache is cold (e.g. the first test in an isolated run).
    """
    import time
    worker._policy_cache_clear()
    worker._required_binaries_cache_clear()
    # Seed required_binaries cache: project 1 has no requirements (None).
    worker._required_binaries_cache[1] = (time.monotonic(), None)
    yield
    worker._policy_cache_clear()
    worker._required_binaries_cache_clear()


def _valid_env(monkeypatch: pytest.MonkeyPatch, project_id: str = "1") -> None:
    monkeypatch.setenv("LANGGRAPH_PROJECT_ID", project_id)


def _cfg(monkeypatch: pytest.MonkeyPatch) -> WorkerConfig:
    _valid_env(monkeypatch)
    return WorkerConfig()


def _headers(cfg: WorkerConfig) -> dict[str, str]:
    return {
        "X-Project-Id": str(cfg.project_id),
        "Content-Type": "application/json",
    }


class _RequestLog:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []


def _body(req: httpx.Request) -> dict[str, Any]:
    raw = req.content
    if not raw:
        return {}
    return json.loads(raw)


def _make_graph_module(
    ainvoke_impl, aget_state_impl=None
) -> SimpleNamespace:
    """Stand-in for the imported `graph` module — exposes `.graph` whose
    `ainvoke` is `ainvoke_impl` and `aget_state` is `aget_state_impl`.

    Default aget_state returns a state with created_at set so
    has_checkpoint returns True (so resume's checkpoint guard passes).
    """
    if aget_state_impl is None:
        async def _default_aget_state(config):
            return SimpleNamespace(created_at="2026-05-17T00:00:00Z")
        aget_state_impl = _default_aget_state
    stub_graph = SimpleNamespace(ainvoke=ainvoke_impl, aget_state=aget_state_impl)
    return SimpleNamespace(graph=stub_graph)


def _make_client(handler, log: _RequestLog) -> httpx.AsyncClient:
    def _wrap(req: httpx.Request) -> httpx.Response:
        log.requests.append(req)
        return handler(req)

    transport = httpx.MockTransport(_wrap)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


def _hitl_pause_state(question: str = "Approve $2 LLM spend?", options=None) -> dict:
    """Build a final_state dict mimicking a langgraph __interrupt__ pause."""
    payload: dict = {"question": question}
    if options:
        payload["options"] = options
    return {
        "__interrupt__": [SimpleNamespace(value=payload)],
    }


def _next_task(task_id: int = 42) -> dict:
    return {
        "id": task_id,
        "title": "approve spend",
        "description": "Brief here",
        "assigned_role": 2,
    }


def _next_autorun_response(task: dict | None = None) -> dict:
    return {
        "next_task": task or _next_task(),
        "resume_tasks": [],
        "pending_questions": [],
        "blocked_count": 0,
    }


# ---------------------------------------------------------------------------
# 1. HITL pause + no policies → normal HITL pause body (unchanged)
# ---------------------------------------------------------------------------


async def test_hitl_pause_no_policies_normal_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            # approval_policies = None
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": None}
            )
        if req.method == "PATCH" and req.url.path == "/api/tasks/42":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state()

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    # Sequence: GET next-autorun, PATCH IN_PROGRESS, GET projects/{id}, PATCH BLOCKED.
    # (required_binaries cache pre-warmed by fixture — no extra GET before IN_PROGRESS)
    methods = [r.method for r in log.requests]
    paths = [r.url.path for r in log.requests]
    assert methods == ["GET", "PATCH", "GET", "PATCH"], (methods, paths)
    assert paths[0] == "/api/tasks/next-autorun"
    assert paths[2] == f"/api/projects/{cfg.project_id}"

    final = _body(log.requests[3])
    assert final["process_status"] == STATUS_BLOCKED
    assert final["halt_reason"] == "question"
    assert "question_payload" in final
    assert final["question_payload"]["question"] == "Approve $2 LLM spend?"


# ---------------------------------------------------------------------------
# 2. HITL pause + auto_approve policy → resume, no BLOCKED PATCH
# ---------------------------------------------------------------------------


async def test_hitl_pause_auto_approve_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    policies = {
        "rules": [
            {
                "name": "approve small llm",
                "match": {"text_contains": "llm", "amount_usd_lt": 5.0},
                "action": "auto_approve",
                "default_answer": "accept",
            }
        ]
    }

    invoke_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path == f"/api/projects/{cfg.project_id}":
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": policies}
            )
        if req.method == "PATCH" and req.url.path == "/api/tasks/42":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause_then_done(state, config):
        invoke_count["n"] += 1
        if invoke_count["n"] == 1:
            return _hitl_pause_state()
        # Resume call — return DONE.
        return {
            "halt_reason": None,
            "final_result": "spent $2, done",
        }

    async with _make_client(handler, log) as client:
        await _poll_once(
            client, _make_graph_module(pause_then_done), cfg, _headers(cfg)
        )

    # Expected sequence:
    #   GET next-autorun
    #   PATCH IN_PROGRESS
    #   GET projects/{id}                  (policy fetch)
    #   PATCH DONE (from resume — no BLOCKED PATCH)
    methods = [r.method for r in log.requests]
    paths = [r.url.path for r in log.requests]
    assert methods == ["GET", "PATCH", "GET", "PATCH"], (methods, paths)
    assert paths[2] == f"/api/projects/{cfg.project_id}"

    # Last PATCH must be DONE — not BLOCKED. That's the load-bearing assertion.
    final = _body(log.requests[3])
    assert final["process_status"] == STATUS_DONE
    assert final.get("halt_reason") is None
    # status_change_reason carries the policy attribution.
    assert "auto-approved by policy" in final["status_change_reason"]
    assert "approve small llm" in final["status_change_reason"]

    # The graph saw the resume call (invoke_count incremented twice).
    assert invoke_count["n"] == 2


# ---------------------------------------------------------------------------
# 3. HITL pause + auto_deny policy → BLOCKED + operator_rejected
# ---------------------------------------------------------------------------


async def test_hitl_pause_auto_deny_halts_as_operator_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    policies = {
        "rules": [
            {
                "name": "deny rm -rf",
                "match": {"text_contains": "rm -rf"},
                "action": "auto_deny",
            }
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path == f"/api/projects/{cfg.project_id}":
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": policies}
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state(question="Run rm -rf /tmp/foo?")

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    # Sequence: GET next-autorun, PATCH IN_PROGRESS, GET projects, PATCH BLOCKED-operator_rejected.
    methods = [r.method for r in log.requests]
    assert methods == ["GET", "PATCH", "GET", "PATCH"], methods

    final = _body(log.requests[3])
    assert final["process_status"] == STATUS_BLOCKED
    assert final["halt_reason"] == "operator_rejected"
    assert "deny rm -rf" in final["status_change_reason"]
    # No question_payload on the deny path — it's a structured halt.
    assert "question_payload" not in final


# ---------------------------------------------------------------------------
# 4. HITL pause + no rule match → REQUIRE_ATTENTION (normal HITL pause)
# ---------------------------------------------------------------------------


async def test_hitl_pause_no_rule_match_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    policies = {
        "rules": [
            {
                "name": "approve deploy",
                "match": {"text_contains": "deploy"},
                "action": "auto_approve",
            }
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": policies}
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state(question="Approve $2 LLM spend?")  # no 'deploy'

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    final = _body(log.requests[3])
    assert final["process_status"] == STATUS_BLOCKED
    assert final["halt_reason"] == "question"  # normal HITL pause
    assert "question_payload" in final


# ---------------------------------------------------------------------------
# 5. Non-HITL halt → policy hook skipped entirely (no extra GET)
# ---------------------------------------------------------------------------


async def test_non_hitl_halt_skips_policy_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            raise AssertionError(
                "policy hook MUST NOT fetch project on non-HITL halt"
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def halts(state, config):
        return {
            "halt_reason": "transient_error",
            "final_result": "specialist halted: tool timeout",
        }

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(halts), cfg, _headers(cfg))

    # No GET /api/projects/{id} call at all.
    methods = [r.method for r in log.requests]
    paths = [r.url.path for r in log.requests]
    assert methods == ["GET", "PATCH", "PATCH"], methods
    assert paths == ["/api/tasks/next-autorun", "/api/tasks/42", "/api/tasks/42"]

    final = _body(log.requests[2])
    assert final["halt_reason"] == "transient_error"


# ---------------------------------------------------------------------------
# 6. DONE → policy hook skipped
# ---------------------------------------------------------------------------


async def test_done_skips_policy_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            raise AssertionError("policy hook MUST NOT fetch project on DONE")
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def done(state, config):
        return {"halt_reason": None, "final_result": "did the work"}

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(done), cfg, _headers(cfg))

    methods = [r.method for r in log.requests]
    assert methods == ["GET", "PATCH", "PATCH"], methods
    final = _body(log.requests[2])
    assert final["process_status"] == STATUS_DONE


# ---------------------------------------------------------------------------
# 7. Project fetch returns 404 → fall back to REQUIRE_ATTENTION
# ---------------------------------------------------------------------------


async def test_policy_fetch_404_falls_back_to_require_attention(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            return httpx.Response(404, json={"detail": "not found"})
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state()

    with caplog.at_level("WARNING", logger="langgraph.worker"):
        async with _make_client(handler, log) as client:
            await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    # Final PATCH falls back to BLOCKED+question (normal HITL pause).
    final = _body(log.requests[-1])
    assert final["process_status"] == STATUS_BLOCKED
    assert final["halt_reason"] == "question"
    # Warning logged.
    assert any(
        "approval_policies fetch" in rec.message and "404" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# 8. Project fetch HTTP error → fall back; cache NOT poisoned
# ---------------------------------------------------------------------------


async def test_policy_fetch_500_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            return httpx.Response(500, text="internal error")
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state()

    with caplog.at_level("WARNING", logger="langgraph.worker"):
        async with _make_client(handler, log) as client:
            await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    final = _body(log.requests[-1])
    assert final["process_status"] == STATUS_BLOCKED
    assert final["halt_reason"] == "question"
    # Cache must NOT contain a poisoned entry — the next tick should retry.
    assert cfg.project_id not in worker._policy_cache


# ---------------------------------------------------------------------------
# 9. Decision (options) + auto_approve uses options[0] when no default_answer
# ---------------------------------------------------------------------------


async def test_decision_auto_approve_uses_options_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    policies = {
        "rules": [
            {
                "name": "approve deploys",
                "match": {"text_contains": "deploy"},
                "action": "auto_approve",
            }
        ]
    }

    invoke_count = {"n": 0}
    resume_answer = {"value": None}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": policies}
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def graph_invoke(state_or_cmd, config):
        invoke_count["n"] += 1
        if invoke_count["n"] == 1:
            return _hitl_pause_state(
                question="Deploy where?", options=["staging", "prod"]
            )
        # Second invoke is the resume — capture the Command(resume=...) value.
        # `state_or_cmd` here is a langgraph.types.Command instance whose
        # `.resume` attribute carries the answer.
        resume_answer["value"] = getattr(state_or_cmd, "resume", None)
        return {"halt_reason": None, "final_result": "deployed"}

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(graph_invoke), cfg, _headers(cfg))

    # The resume was called with the FIRST option (no explicit default_answer).
    assert resume_answer["value"] == "staging", resume_answer

    final = _body(log.requests[-1])
    assert final["process_status"] == STATUS_DONE
    assert "auto-approved by policy" in final["status_change_reason"]


# ---------------------------------------------------------------------------
# 10. Policy cache hits within TTL — only ONE GET /api/projects/{id} across two ticks
# ---------------------------------------------------------------------------


async def test_policy_cache_skips_second_fetch_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive ticks both pause for HITL; the second should reuse the
    cached policies (cache TTL ~10s). Saves API load."""
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    policies_call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path == f"/api/projects/{cfg.project_id}":
            policies_call_count["n"] += 1
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": None}
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state()

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))
        await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    # Only one policy GET despite two ticks (cache hit on the second).
    assert policies_call_count["n"] == 1, policies_call_count


# ---------------------------------------------------------------------------
# 11. Malformed approval_policies → fall back to REQUIRE_ATTENTION (no crash)
# ---------------------------------------------------------------------------


async def test_malformed_policies_falls_back_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            # Garbage shape — rules is a string.
            return httpx.Response(
                200,
                json={"id": cfg.project_id, "approval_policies": {"rules": "nope"}},
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def pause(state, config):
        return _hitl_pause_state()

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(pause), cfg, _headers(cfg))

    final = _body(log.requests[-1])
    # Fell back to normal HITL pause.
    assert final["process_status"] == STATUS_BLOCKED
    assert final["halt_reason"] == "question"


# ---------------------------------------------------------------------------
# 12. Auto-approve when graph re-pauses on resume → BLOCKED (not DONE)
# ---------------------------------------------------------------------------


async def test_auto_approve_then_fresh_interrupt_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph pauses, gets auto-approved, the resume starts and the graph
    pauses AGAIN (a multi-step HITL flow). The second pause MUST land BLOCKED
    rather than DONE — operator gets visibility on the next step.
    """
    cfg = _cfg(monkeypatch)
    log = _RequestLog()

    policies = {
        "rules": [
            {
                "name": "approve all",
                "match": {"text_contains": "Approve"},
                "action": "auto_approve",
            }
        ]
    }

    invoke_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/api/tasks/next-autorun":
            return httpx.Response(200, json=_next_autorun_response())
        if req.method == "GET" and req.url.path.startswith("/api/projects/"):
            return httpx.Response(
                200, json={"id": cfg.project_id, "approval_policies": policies}
            )
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": 42})
        raise AssertionError(f"unexpected request: {req.method} {req.url.path}")

    async def two_pauses(state, config):
        invoke_count["n"] += 1
        # First and second invocations both return interrupts.
        return _hitl_pause_state(question=f"Approve step {invoke_count['n']}?")

    async with _make_client(handler, log) as client:
        await _poll_once(client, _make_graph_module(two_pauses), cfg, _headers(cfg))

    final = _body(log.requests[-1])
    assert final["process_status"] == STATUS_BLOCKED
    # halt_reason on a fresh interrupt during resume = 'question'
    assert final["halt_reason"] == "question"
