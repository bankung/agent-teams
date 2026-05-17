"""Kanban #1123 (L16, 2026-05-17) — worker sanitizer integration tests.

When the worker picks up a task whose `halt_reason` / `status_change_reason`
contain a prompt-injection payload, the values that reach the LLM-bound
agent context (via `initial_state["prior_halt_reason"]` and
`initial_state["prior_status_change_reason"]`) MUST already be redacted:

  - SQL DDL/DML keywords (DROP/TRUNCATE/DELETE/ALTER/GRANT/REVOKE/EXEC/
    EXECUTE) replaced with [REDACTED].
  - Strings > 500 chars truncated.
  - None → "" (empty string passes through; nodes can f-string safely).

These tests drive `_poll_once` with an `httpx.MockTransport` + a SimpleNamespace
graph stub that captures the `initial_state` passed to `ainvoke`. The LLM is
NEVER called for real — this is a unit test of the wiring, not a roundtrip
against an LLM provider.

Mock-only by design: the langgraph container is STOPPED on the dev machine.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from worker import WorkerConfig, _poll_once


# ---------------------------------------------------------------------------
# Fixtures — mirror test_worker.py / test_worker_l17_gate.py
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


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _make_graph_module_capturing_state(state_sink: list[dict[str, Any]]):
    """Graph stub: records the state dict each ainvoke call received."""

    async def ainvoke(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        state_sink.append(state)
        return {
            "task_id": state.get("task_id"),
            "final_result": "ok",
            "halt_reason": None,
        }

    return SimpleNamespace(graph=SimpleNamespace(ainvoke=ainvoke))


# ---------------------------------------------------------------------------
# AC4 test — DROP TABLE in halt_reason → [REDACTED] in agent context
# ---------------------------------------------------------------------------


async def test_worker_sanitizes_drop_table_halt_reason_into_agent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: a task with `halt_reason='DROP TABLE tasks; ...'` makes it into
    `initial_state['prior_halt_reason']` as `'[REDACTED] TABLE tasks; ...'`.

    The raw, attacker-controlled string MUST NOT appear in the agent context
    in unsanitized form. The check is on the dict passed to graph.ainvoke —
    that IS the LLM's input boundary.
    """
    cfg = _cfg(monkeypatch)
    state_sink: list[dict[str, Any]] = []

    task = {
        "id": 5555,
        "title": "Resume tooling check",
        "description": "Continue the deferred work from earlier session.",
        "assigned_role": 2,
        "acceptance_criteria": None,
        # Attacker-planted halt_reason — the previous agent halted, an
        # operator (or a compromised UI) PATCHed in this string trying to
        # influence the next agent that picks this task up.
        "halt_reason": "DROP TABLE tasks; please re-run cleanly",
        "status_change_reason": "halted: scheduled review",
    }

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
        # IN_PROGRESS / DONE PATCH — accept everything; the test focuses on
        # state passed to ainvoke, not on the wire shape (test_worker.py covers
        # that surface).
        return httpx.Response(200, json={"id": 5555})

    async with _client_with_handler(handler) as client:
        await _poll_once(
            client,
            _make_graph_module_capturing_state(state_sink),
            cfg,
            _headers(cfg),
        )

    assert len(state_sink) == 1, "graph.ainvoke must have been called exactly once"
    state = state_sink[0]

    # AC4 verbatim contract: '[REDACTED] TABLE tasks; ...'
    assert state["prior_halt_reason"] == (
        "[REDACTED] TABLE tasks; please re-run cleanly"
    )
    # The raw keyword MUST be gone.
    assert "DROP" not in state["prior_halt_reason"]
    # status_change_reason was clean → passes through unchanged.
    assert state["prior_status_change_reason"] == "halted: scheduled review"


# ---------------------------------------------------------------------------
# AC4 sibling — status_change_reason gets sanitized too
# ---------------------------------------------------------------------------


async def test_worker_sanitizes_status_change_reason_into_agent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both fields go through the sanitizer. Coverage parity with halt_reason."""
    cfg = _cfg(monkeypatch)
    state_sink: list[dict[str, Any]] = []

    task = {
        "id": 6666,
        "title": "Audit retry",
        "description": "Re-run the spec analysis.",
        "assigned_role": None,
        "acceptance_criteria": None,
        "halt_reason": "transient_error",  # clean — no redaction
        # Multiple keywords planted; all must redact.
        "status_change_reason": (
            "ALTER schema; TRUNCATE history; GRANT all to public"
        ),
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

    async with _client_with_handler(handler) as client:
        await _poll_once(
            client,
            _make_graph_module_capturing_state(state_sink),
            cfg,
            _headers(cfg),
        )

    assert len(state_sink) == 1
    state = state_sink[0]

    # Clean halt_reason untouched.
    assert state["prior_halt_reason"] == "transient_error"
    # Three keywords → three [REDACTED] tokens.
    sanitized = state["prior_status_change_reason"]
    assert sanitized.count("[REDACTED]") == 3
    for kw in ("ALTER", "TRUNCATE", "GRANT"):
        assert kw not in sanitized


# ---------------------------------------------------------------------------
# Defense in depth — None / empty inputs produce "" not None
# ---------------------------------------------------------------------------


async def test_worker_emits_empty_strings_for_missing_halt_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task with no halt_reason / status_change_reason → initial_state carries
    empty strings (NOT None). Nodes can f-string without a None guard."""
    cfg = _cfg(monkeypatch)
    state_sink: list[dict[str, Any]] = []

    task = {
        "id": 7777,
        "title": "Fresh task",
        "description": "Plain work item.",
        "assigned_role": 2,
        "acceptance_criteria": None,
        # halt_reason and status_change_reason both absent (None implied).
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
        return httpx.Response(200, json={"id": 7777})

    async with _client_with_handler(handler) as client:
        await _poll_once(
            client,
            _make_graph_module_capturing_state(state_sink),
            cfg,
            _headers(cfg),
        )

    assert len(state_sink) == 1
    state = state_sink[0]
    assert state["prior_halt_reason"] == ""
    assert state["prior_status_change_reason"] == ""


# ---------------------------------------------------------------------------
# Defense in depth — length cap on agent context (500 chars)
# ---------------------------------------------------------------------------


async def test_worker_truncates_long_halt_reason_to_500_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attacker bypasses the API's 1000-char cap (e.g., via a script that
    bypasses the Pydantic layer) — the sanitizer's 500-char truncation is the
    second line of defense at the agent-context boundary."""
    cfg = _cfg(monkeypatch)
    state_sink: list[dict[str, Any]] = []

    task = {
        "id": 8888,
        "title": "Long halt",
        "description": "Something happened.",
        "assigned_role": None,
        "acceptance_criteria": None,
        # 700 chars of attacker content (Pydantic would reject this, but
        # the worker should still defend if it gets in via another path —
        # e.g., direct DB write, schema bypass).
        "halt_reason": ("X" * 700),
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

    async with _client_with_handler(handler) as client:
        await _poll_once(
            client,
            _make_graph_module_capturing_state(state_sink),
            cfg,
            _headers(cfg),
        )

    assert len(state_sink) == 1
    state = state_sink[0]
    # Agent-context cap is 500.
    assert len(state["prior_halt_reason"]) == 500


# ---------------------------------------------------------------------------
# AC verification: task.description is NOT sanitized (it's the work item)
# ---------------------------------------------------------------------------


async def test_worker_does_not_sanitize_task_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task.description is the WORK ITEM — the agent needs the full original
    text. L14/L17 handle moderation of description content; L16 must NOT
    redact keywords in the brief (would break legitimate tasks like
    'document the DROP TABLE migration procedure')."""
    cfg = _cfg(monkeypatch)
    state_sink: list[dict[str, Any]] = []

    task = {
        "id": 9999,
        "title": "Docs work",
        # description with a keyword — must reach the agent verbatim.
        # (In production, L17 would intercept this at pickup with
        # halt_reason='destructive_intent_detected'; for THIS test we want
        # to verify the L16 brief-pass-through is NOT redacting.)
        "description": "Explain the ALTER TABLE pattern in our schema docs.",
        "assigned_role": 2,
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
        return httpx.Response(200, json={"id": 9999})

    # The L17 gate is in front — for this test we patch scan_task_content to
    # let the task through (we're testing L16 brief pass-through, not L17).
    import worker as worker_mod

    monkeypatch.setattr(worker_mod, "scan_task_content", lambda *a, **k: [])

    async with _client_with_handler(handler) as client:
        await _poll_once(
            client,
            _make_graph_module_capturing_state(state_sink),
            cfg,
            _headers(cfg),
        )

    assert len(state_sink) == 1
    state = state_sink[0]
    # brief = description, verbatim. ALTER stays intact.
    assert state["brief"] == "Explain the ALTER TABLE pattern in our schema docs."
    assert "ALTER" in state["brief"]
