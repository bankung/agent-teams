"""Tests for the L17 content-safety gate on POST /invoke (Kanban #2839).

`worker.py`'s auto-pickup path (`_poll_once`, ~L662) already runs
`scan_task_content` before invoking the graph — see test_worker_l17_gate.py.
POST /invoke is a second, independent entrypoint into the same compiled
graph (used by `langgraph dev` / direct callers) that was missing the same
gate. These tests pin the fix: a destructive brief is refused before
`graph.ainvoke` is ever called; a benign brief behaves exactly as before.

The tests call the `invoke()` handler function directly (not via
TestClient/httpx) — importing `graph` is safe at module scope (no DB I/O
happens until the lifespan runs; see test_database_uri_validation.py's
docstring for the same pattern), and calling the FastAPI route function
directly avoids standing up the full ASGI app + lifespan for a unit test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import graph as graph_module
from graph import InvokeRequest


@pytest.fixture(autouse=True)
def _graph_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the /invoke handler's readiness check pass regardless of test order."""
    monkeypatch.setattr(graph_module, "graph_ready", True)


def _stub_graph(ainvoke_impl) -> SimpleNamespace:
    return SimpleNamespace(ainvoke=ainvoke_impl)


# ---------------------------------------------------------------------------
# Destructive brief — refused, ainvoke never called
# ---------------------------------------------------------------------------


async def test_destructive_brief_refused_without_invoking_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A brief containing `DROP TABLE users` is refused with 422 and
    `graph.ainvoke` is NEVER called (zero token / zero side-effect spend,
    same contract as the worker's L17 gate)."""
    ainvoke_called = {"count": 0}

    async def must_not_be_called(state, config):
        ainvoke_called["count"] += 1
        raise AssertionError("L17 gate failed: ainvoke must NOT be called")

    monkeypatch.setattr(graph_module, "graph", _stub_graph(must_not_be_called))

    req = InvokeRequest(
        task_id=1234,
        brief="Run DROP TABLE users to clean up the old schema.",
        assigned_role=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await graph_module.invoke(req)

    assert ainvoke_called["count"] == 0
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "destructive_intent_detected"
    assert "DROP_TABLE" in exc_info.value.detail["matched"]


async def test_destructive_brief_other_patterns_also_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity-check a second pattern (TRUNCATE) also trips the gate — pins
    that the check is a real scan, not a hardcoded string match."""

    async def must_not_be_called(state, config):
        raise AssertionError("ainvoke must not be called")

    monkeypatch.setattr(graph_module, "graph", _stub_graph(must_not_be_called))

    req = InvokeRequest(
        task_id=1235,
        brief="TRUNCATE tasks_history nightly.",
        assigned_role=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await graph_module.invoke(req)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["matched"] == ["TRUNCATE"]


# ---------------------------------------------------------------------------
# Benign brief — reaches ainvoke, behaves exactly as today
# ---------------------------------------------------------------------------


async def test_benign_brief_reaches_ainvoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean brief passes the gate untouched and the graph result is
    returned as before — proves the gate is a strict pass-through for
    non-matching content."""
    ainvoke_called = {"count": 0}

    async def ainvoke(state, config):
        ainvoke_called["count"] += 1
        assert state["brief"] == "Implement POST /auth/login with JWT issuance."
        return {
            "task_id": 4321,
            "assigned_role": 2,
            "final_result": "endpoint wired",
            "halt_reason": None,
            "messages": [],
        }

    monkeypatch.setattr(graph_module, "graph", _stub_graph(ainvoke))

    req = InvokeRequest(
        task_id=4321,
        brief="Implement POST /auth/login with JWT issuance.",
        assigned_role=2,
    )

    result = await graph_module.invoke(req)

    assert ainvoke_called["count"] == 1
    assert result["final_result"] == "endpoint wired"
    assert result["halt_reason"] is None
