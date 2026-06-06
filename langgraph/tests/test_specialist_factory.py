"""Kanban #1944 — promote the four stub specialists to real factory nodes.

Before #1944, only `backend_specialist_node` was a real tool-loop node; the
other four (frontend/devops/tester/reviewer) were `make_stub_node(...)`
closures that returned a canned "specialist not implemented yet" message and
never invoked the LLM. #1944 extracts `make_specialist_node(agent_name)` from
the backend node and builds ALL FIVE from it — single source of truth.

These tests lock the promotion:

  1. NEGATIVE lock — none of the four promoted nodes returns the old stub
     message (`"... specialist not implemented yet ..."`); the stub path is
     gone. Paired with the POSITIVE assertion that they take the real
     tool-loop path (the mocked LLM's `.ainvoke` is actually called and its
     `final_result` is returned).

  2. agent_name routing — each node threads its own `agent_name`
     (`dev-frontend` / `dev-devops` / `dev-tester` / `dev-reviewer`) into
     `build_cached_system_content(...)`. We spy on that helper at the `nodes`
     module level and assert the captured `agent_name` per node.

  3. real state contract — each node returns the tool-loop state dict shape
     (`messages` + `final_result` + usage_* keys), not the 2-key stub dict.

  4. backend parity — `make_specialist_node("dev-backend")` passes
     `agent_name="dev-backend"`, confirming the factory reproduces the
     pre-factory backend behavior (the #907 prompt-shape suite covers the
     SystemMessage/HumanMessage content separately).

The LLM is mocked with the same `_ScriptedModel` shape used in
`test_specialist_tool_loop.py`; we force the tool-loop branch via an
auto-allow `tools_config` so `_bind_tools_safely` returns the bound model and
the node drives `_run_tool_use_loop` rather than the no-tools single-shot.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import nodes


# ---------------------------------------------------------------------------
# Shared fakes (mirror test_specialist_tool_loop.py so behavior is consistent)
# ---------------------------------------------------------------------------


_AUTO_ALLOW_ALL_CONFIG = {
    "tools_enabled": True,
    "auto_allow_tiers": ["read", "write", "network", "destructive"],
    "halt_tiers": [],
    "http_hosts": [],
}

# The canonical stub message fragment from the pre-#1944 make_stub_node. If a
# node ever returns this again, the promotion regressed.
_OLD_STUB_FRAGMENT = "specialist not implemented yet"


class _ScriptedModel:
    """Fake LLM whose `ainvoke` returns one canned response per call.

    `bind_tools` returns self so `_bind_tools_safely` yields a bound model and
    the node takes the tool-loop path (not the single-shot fallback).
    """

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = responses
        self._idx = 0
        self.ainvoke_calls = 0

    def bind_tools(self, tools):  # noqa: ARG002
        return self

    async def ainvoke(self, messages):  # noqa: ARG002
        self.ainvoke_calls += 1
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return self._responses[-1]


def _ai_final(text: str) -> AIMessage:
    """AIMessage with no tool_calls — terminates the loop on the first turn."""
    msg = AIMessage(content=text)
    msg.tool_calls = []
    return msg


def _patch_tools_config(monkeypatch: pytest.MonkeyPatch, cfg: dict | None) -> None:
    async def _fake(project_id):  # type: ignore[no-untyped-def]  # noqa: ARG001
        return cfg

    monkeypatch.setattr(nodes, "_fetch_tools_config", _fake)


def _spy_build_cached_system_content(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Replace nodes.build_cached_system_content with a spy.

    Returns a list that captures every call's kwargs. The spy returns a flat
    string so the SystemMessage stays well-formed regardless of provider.
    """
    captured: list[dict[str, Any]] = []

    def _spy(role_brief, team="dev", agent_name=None, provider=None):  # type: ignore[no-untyped-def]
        captured.append(
            {
                "role_brief": role_brief,
                "team": team,
                "agent_name": agent_name,
                "provider": provider,
            }
        )
        return f"SYSTEM[team={team} agent={agent_name}]\n\n---\n\n{role_brief}"

    monkeypatch.setattr(nodes, "build_cached_system_content", _spy)
    return captured


def _run_node(node, state: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(node(state))


# Map: module-level node object → expected agent_name threaded to the bundle.
_PROMOTED_NODES = [
    ("frontend_specialist_node", "dev-frontend"),
    ("devops_specialist_node", "dev-devops"),
    ("tester_specialist_node", "dev-tester"),
    ("reviewer_specialist_node", "dev-reviewer"),
]

_ALL_NODES = [("backend_specialist_node", "dev-backend")] + _PROMOTED_NODES


# ---------------------------------------------------------------------------
# 1) NEGATIVE + POSITIVE — promoted nodes take the real tool-loop path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node_attr,_agent", _PROMOTED_NODES)
def test_promoted_node_takes_tool_loop_not_stub(
    monkeypatch: pytest.MonkeyPatch, node_attr: str, _agent: str
) -> None:
    """Each promoted node drives the LLM (POSITIVE) and never emits the old
    stub message (NEGATIVE).

    POSITIVE: the mocked LLM's `ainvoke` is actually called and its content is
    returned as `final_result` — proving the node ran the tool-loop.
    NEGATIVE: `final_result` is NOT the pre-#1944 stub message.
    """
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    _spy_build_cached_system_content(monkeypatch)

    model = _ScriptedModel([_ai_final("REAL ANSWER")])
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    node = getattr(nodes, node_attr)
    out = _run_node(node, {"task_id": 1, "brief": "tiny", "assigned_role": 1})

    # POSITIVE — the real LLM was invoked and its answer flowed through.
    assert model.ainvoke_calls == 1, "node did not invoke the LLM (still a stub?)"
    assert out["final_result"] == "REAL ANSWER"

    # NEGATIVE — the old stub message must NOT appear anywhere in the output.
    assert _OLD_STUB_FRAGMENT not in str(out["final_result"])
    for m in out["messages"]:
        assert _OLD_STUB_FRAGMENT not in str(getattr(m, "content", ""))


# ---------------------------------------------------------------------------
# 2) agent_name routing — each node bundles its own definition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node_attr,expected_agent", _ALL_NODES)
def test_node_passes_correct_agent_name(
    monkeypatch: pytest.MonkeyPatch, node_attr: str, expected_agent: str
) -> None:
    """The node threads its own `agent_name` into build_cached_system_content.

    Covers all five (incl. backend) so the factory's single-parameter design
    is pinned: backend→dev-backend, frontend→dev-frontend, etc.
    """
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    captured = _spy_build_cached_system_content(monkeypatch)

    model = _ScriptedModel([_ai_final("ok")])
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    node = getattr(nodes, node_attr)
    _run_node(node, {"task_id": 2, "brief": "x", "assigned_role": 1})

    assert len(captured) == 1, "system content built exactly once per node run"
    assert captured[0]["agent_name"] == expected_agent
    assert captured[0]["team"] == "dev"
    # The role_brief threaded in is the shared generic _SYSTEM_PROMPT.
    assert captured[0]["role_brief"] == nodes._SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3) real state contract — tool-loop shape, not the 2-key stub dict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node_attr,_agent", _PROMOTED_NODES)
def test_promoted_node_returns_real_state_shape(
    monkeypatch: pytest.MonkeyPatch, node_attr: str, _agent: str
) -> None:
    """A real specialist returns the tool-loop dict: messages + final_result +
    usage_* accounting keys. The old stub returned only {messages, final_result}.
    """
    _patch_tools_config(monkeypatch, _AUTO_ALLOW_ALL_CONFIG)
    _spy_build_cached_system_content(monkeypatch)

    model = _ScriptedModel([_ai_final("done")])
    monkeypatch.setattr(nodes, "make_chat_model", lambda: model)

    node = getattr(nodes, node_attr)
    out = _run_node(node, {"task_id": 3, "brief": "y", "assigned_role": 1})

    assert isinstance(out, dict)
    assert "messages" in out
    assert "final_result" in out
    # usage_* keys are the tell-tale of the real loop's success return; the
    # stub never emitted them.
    for key in (
        "usage_input_tokens",
        "usage_output_tokens",
        "usage_cache_read_tokens",
        "usage_cache_creation_tokens",
    ):
        assert key in out, f"missing {key} — node not on the real tool-loop path"
    assert out.get("halt_reason") is None


# ---------------------------------------------------------------------------
# 4) factory ergonomics — node naming for clean checkpoints/logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_name,expected_node_name",
    [
        ("dev-backend", "backend_specialist_node"),
        ("dev-frontend", "frontend_specialist_node"),
        ("dev-devops", "devops_specialist_node"),
        ("dev-tester", "tester_specialist_node"),
        ("dev-reviewer", "reviewer_specialist_node"),
    ],
)
def test_factory_sets_node_name(agent_name: str, expected_node_name: str) -> None:
    """make_specialist_node mirrors make_stub_node's naming convention: the
    returned node's __name__/__qualname__ read `<role>_specialist_node`
    (dev- prefix stripped) so checkpoints + logs stay readable and graph
    wiring keeps the same module-level names.
    """
    node = nodes.make_specialist_node(agent_name)
    assert node.__name__ == expected_node_name
    assert node.__qualname__ == expected_node_name


def test_module_level_nodes_are_distinct_objects() -> None:
    """All five module-level specialist nodes are separate closures (each
    captures a different agent_name) — not aliases of one object.
    """
    objs = [
        nodes.backend_specialist_node,
        nodes.frontend_specialist_node,
        nodes.devops_specialist_node,
        nodes.tester_specialist_node,
        nodes.reviewer_specialist_node,
    ]
    assert len({id(o) for o in objs}) == 5
