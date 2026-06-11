"""Kanban #2215 — per-project working_path resolution + ask-where-to-save HALT.

Covers the node-side half of the Mode-B fs-tool guard:

  - `_resolve_paths` precedence: env override > API value > None.
  - `_fetch_project_working_path` caching + per-project isolation, with the
    cache resettable across tests (#2187 lesson).
  - The specialist node converting a `working_path_unset` destination violation
    into a HALT (HITL ask-where-to-save) — and letting a /repo/_scratch write
    through to the (unchanged) tier gate.

The destination-guard logic itself (subtree / unmounted / allowlist) is unit
tested directly against `fs_boundary_check` in tools/test_sandbox.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import nodes

# Capture the REAL fetch fn at import time, BEFORE the autouse conftest fixture
# (`_isolate_working_path_resolution`) stubs nodes._fetch_project_working_path
# to return None. Tests that exercise the real fetch restore this over the stub.
_REAL_FETCH_WORKING_PATH = nodes._fetch_project_working_path


# ---------------------------------------------------------------------------
# _resolve_paths — precedence + caching
# ---------------------------------------------------------------------------


def test_resolve_paths_env_override_wins_over_api(monkeypatch) -> None:
    """env LANGGRAPH_WORKING_PATH (non-empty) overrides the API value."""
    monkeypatch.setenv("LANGGRAPH_WORKING_PATH", "/repo/override")

    async def _api_value(project_id):  # type: ignore[no-untyped-def]
        raise AssertionError("API must NOT be consulted when env override is set")

    monkeypatch.setattr(nodes, "_fetch_project_working_path", _api_value)

    wp, repo_root = asyncio.run(nodes._resolve_paths(661))
    assert wp == "/repo/override"
    assert repo_root == "/repo"


def test_resolve_paths_falls_back_to_api_value(monkeypatch) -> None:
    """No env override → the API working_path value is used."""
    monkeypatch.delenv("LANGGRAPH_WORKING_PATH", raising=False)

    async def _api_value(project_id):  # type: ignore[no-untyped-def]
        assert project_id == 691
        return "/repo/projects/p691"

    monkeypatch.setattr(nodes, "_fetch_project_working_path", _api_value)

    wp, _ = asyncio.run(nodes._resolve_paths(691))
    assert wp == "/repo/projects/p691"


def test_resolve_paths_none_when_no_override_and_api_null(monkeypatch) -> None:
    """No env override + API returns None → working_path None."""
    monkeypatch.delenv("LANGGRAPH_WORKING_PATH", raising=False)

    async def _api_value(project_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(nodes, "_fetch_project_working_path", _api_value)

    wp, _ = asyncio.run(nodes._resolve_paths(661))
    assert wp is None


def test_resolve_paths_empty_env_override_is_not_override(monkeypatch) -> None:
    """An empty / whitespace env var is NOT an override → API value is used."""
    monkeypatch.setenv("LANGGRAPH_WORKING_PATH", "   ")

    async def _api_value(project_id):  # type: ignore[no-untyped-def]
        return "/repo/from-api"

    monkeypatch.setattr(nodes, "_fetch_project_working_path", _api_value)

    wp, _ = asyncio.run(nodes._resolve_paths(1))
    assert wp == "/repo/from-api"


def test_fetch_working_path_cached_and_isolated_across_projects(monkeypatch) -> None:
    """The API fetch is cached per-project AND the cache is resettable.

    Drives `_fetch_project_working_path` (not _resolve_paths) so the env layer
    is out of the picture. Uses a fake httpx.AsyncClient so no live network.
    """
    # Restore the real fetch over the autouse stub — this test drives it.
    monkeypatch.setattr(nodes, "_fetch_project_working_path", _REAL_FETCH_WORKING_PATH)
    nodes._working_path_cache_clear()
    monkeypatch.delenv("LANGGRAPH_WORKING_PATH", raising=False)

    call_count = {"n": 0}

    class _FakeResp:
        status_code = 200

        def __init__(self, wp: str | None) -> None:
            self._wp = wp

        def json(self) -> dict[str, Any]:
            return {"working_path": self._wp}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            call_count["n"] += 1
            # Vary the value by project id encoded in the URL.
            if url.endswith("/100"):
                return _FakeResp("/repo/p100")
            return _FakeResp("/repo/p200")

    monkeypatch.setattr(nodes.httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    # First fetch for 100 → 1 HTTP call; second fetch hits the cache → no call.
    assert asyncio.run(nodes._fetch_project_working_path(100)) == "/repo/p100"
    assert asyncio.run(nodes._fetch_project_working_path(100)) == "/repo/p100"
    assert call_count["n"] == 1

    # A DIFFERENT project_id is a cache miss → a fresh HTTP call (isolation).
    assert asyncio.run(nodes._fetch_project_working_path(200)) == "/repo/p200"
    assert call_count["n"] == 2

    # After a reset, project 100 fetches again (cache cleared).
    nodes._working_path_cache_clear()
    assert asyncio.run(nodes._fetch_project_working_path(100)) == "/repo/p100"
    assert call_count["n"] == 3


def test_fetch_working_path_none_project_id_no_network(monkeypatch) -> None:
    """project_id=None short-circuits to None without any HTTP call."""

    monkeypatch.setattr(nodes, "_fetch_project_working_path", _REAL_FETCH_WORKING_PATH)

    def _boom(*a, **k):
        raise AssertionError("must not construct an httpx client for project_id=None")

    monkeypatch.setattr(nodes.httpx, "AsyncClient", _boom)
    assert asyncio.run(nodes._fetch_project_working_path(None)) is None


# ---------------------------------------------------------------------------
# Node integration — working_path_unset → HALT; /repo/_scratch → through
# ---------------------------------------------------------------------------


class _OneToolCallModel:
    """Fake bound model that emits exactly one file_write tool_call, then stops."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._emitted = False

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if not self._emitted:
            self._emitted = True
            msg = AIMessage(content="writing...")
            msg.tool_calls = [
                {
                    "name": "file_write",
                    "args": {"path": self._path, "content": "data"},
                    "id": "tc_w",
                }
            ]
            return msg
        return AIMessage(content="done")


def _drive_node_with_write(monkeypatch, path: str) -> dict[str, Any]:
    """Run backend_specialist_node with tools enabled + a single file_write call.

    working_path resolves to None (the autouse conftest fixture stubs the API
    fetch to None and clears the cache), so the #2215 NULL-working_path rules
    apply: /repo/_scratch allowed through to the tier gate; elsewhere → HALT.
    """
    async def _cfg(project_id):  # type: ignore[no-untyped-def]
        return {
            "tools_enabled": True,
            # Auto-allow write so that IF the destination guard lets a write
            # through, the tier gate would invoke it (not the path under test).
            "auto_allow_tiers": ["write"],
            "halt_tiers": [],
            "http_hosts": [],
        }

    monkeypatch.setattr(nodes, "_fetch_tools_config", _cfg)

    async def _no_audit(*a, **k):
        return None

    monkeypatch.setattr(nodes, "record_tool_invocation", _no_audit)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: _OneToolCallModel(path))

    state = {"task_id": 555, "brief": "write a file", "assigned_role": 2}
    return asyncio.run(nodes.backend_specialist_node(state))


def test_node_null_working_path_elsewhere_halts_ask_where_to_save(monkeypatch) -> None:
    """NULL working_path + write outside _scratch → node HALTs with the unset signal."""
    out = _drive_node_with_write(monkeypatch, "/repo/api/src/models/x.py")
    halt = out.get("halt_reason")
    assert halt is not None
    assert halt.startswith("working_path_unset:")


def test_node_null_working_path_scratch_goes_through_to_tier_gate(monkeypatch) -> None:
    """NULL working_path + /repo/_scratch write → NOT halted by the destination guard.

    With auto_allow write the tier gate lets it through and the file_write tool
    runs (it fails to create the dir, but that's a tool-level result, not a
    destination HALT). The key invariant: halt_reason is NOT working_path_unset.
    This mirrors the S5 pack flow (the live pack uses halt_tiers=[write] so the
    tier gate HALTs there; here we prove the destination guard itself does not).

    N3 (#2215 review): assert the precise non-destination outcome rather than a
    vacuous `halt is None OR ...`. With auto_allow write and halt_tiers=[], the
    destination guard passes the _scratch write THROUGH, the tier gate
    auto-allows it, the tool runs, and the node finishes with NO halt at all —
    so halt_reason must be exactly None (not merely "not a working_path_unset
    halt", which a stray other-halt could vacuously satisfy).
    """
    out = _drive_node_with_write(monkeypatch, "/repo/_scratch/wp2215/out.txt")
    assert out.get("halt_reason") is None
