"""Prompt-shape regression tests for backend_specialist_node (Kanban #907 + #981).

Real-LLM smoke runs against test-headless (project 590) on 2026-05-14 produced
identical wander patterns across two unrelated models (llama3.2:3b #902 +
qwen3:8b #906) — both veered into "build a FastAPI + PostgreSQL backend"
scaffolding when given the orthogonal prompt "List 3 reasons to use Pydantic
over dataclasses". Identical wander direction across models = system-prompt
bias, not model quality. Root cause: the prompt anchored the persona to
"FastAPI + PostgreSQL specialist" + asked for "a concise plan", and wrapped
the brief in "Task #N" framing — three reinforcing nudges into project-mode.

These tests pin the corrected prompt shape so any regression (anchor returns,
plan-noun returns, Task-N wrapper returns) trips here BEFORE another real-LLM
smoke cycle is needed. We mock `make_chat_model` at the `nodes` module level
(it's imported `from llm import make_chat_model`, so the binding lives on
`nodes`) and capture the prompt list passed to `.invoke()`.

#981 made `backend_specialist_node` `async def` to accommodate the tool-use
loop. Existing prompt tests still cover the no-tools fallback path: when
`tools_config` is None (the test default — we don't stub the api), the loop
falls back to single-shot inference. So the SystemMessage + HumanMessage
shape assertions remain load-bearing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import nodes
from nodes import backend_specialist_node


def _install_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch nodes.make_chat_model to a fake whose .invoke() records the prompt.

    Returns a dict that will have key 'prompt' populated after the node runs.
    The fake exposes ONLY `invoke` (sync) — no `ainvoke` — which exercises
    the `_ainvoke_model` sync-fallback path. This is the cheapest way to
    keep these tests independent of asyncio mocking machinery.
    """
    captured: dict[str, Any] = {}

    def _invoke(prompt: list[Any]) -> AIMessage:
        captured["prompt"] = prompt
        # No tool_calls attribute → loop exits immediately on first turn
        # (the post-#981 single-shot path).
        return AIMessage(content="MOCKED")

    fake_model = SimpleNamespace(invoke=_invoke)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake_model)
    # Force the no-tools fallback by stubbing the tools_config fetch.
    # The prompt tests don't care about tool wiring; they only need the
    # first model.invoke() to receive [SystemMessage, HumanMessage].
    async def _fake_fetch(project_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(nodes, "_fetch_tools_config", _fake_fetch)
    return captured


def _run(state: dict[str, Any]) -> dict[str, Any]:
    """Run the (async) node from a sync test context.

    The existing tests were sync; #981 promoted the node to `async def`.
    Wrapping each call in `asyncio.run` keeps the test file shape unchanged.
    """
    import asyncio

    return asyncio.run(backend_specialist_node(state))


def test_backend_prompt_drops_fastapi_postgres_specialist_plan_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The four bias tokens from the pre-#907 prompt must be gone.

    'fastapi' / 'postgresql' / 'specialist' = domain anchor that biased
    every brief into project scaffolding. 'produce a concise plan' = the
    exact phrase that made the LLM design things instead of answering.
    The new prompt may legitimately mention 'plan' inside an anti-scaffold
    directive ("do not propose ... project plans ..."), so we assert the
    specific old-phrase 'produce a concise plan' is gone rather than the
    bare word.

    #1116 note: the safety prelude (prepended by build_system_message) DOES
    contain the word "fastapi" (rule 1: "DB writes go through FastAPI
    endpoints ONLY"). We inspect only the role-brief portion (text AFTER
    the `\\n\\n---\\n\\n` separator) so the historical anti-anchor check
    keeps holding for what it was designed to guard — the persona prompt.
    """
    captured = _install_capture(monkeypatch)
    state = {
        "task_id": 42,
        "brief": "List 3 reasons to use Pydantic over dataclasses",
        "assigned_role": 2,
    }
    _run(state)

    sys_msg = captured["prompt"][0]
    assert isinstance(sys_msg, SystemMessage)
    # Split on the safety-prelude separator; check only the role-brief half.
    parts = sys_msg.content.split("\n\n---\n\n", 1)
    assert len(parts) == 2, "system message missing safety-prelude separator"
    role_brief_lower = parts[1].lower()
    assert "fastapi" not in role_brief_lower, parts[1]
    assert "postgresql" not in role_brief_lower, parts[1]
    assert "specialist" not in role_brief_lower, parts[1]
    assert "produce a concise plan" not in role_brief_lower, parts[1]


def test_backend_prompt_contains_generic_persona_and_anti_scaffolding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replacement prompt must carry the generic persona + the
    load-bearing anti-scaffolding directive.
    """
    captured = _install_capture(monkeypatch)
    state = {
        "task_id": 1,
        "brief": "anything",
        "assigned_role": 2,
    }
    _run(state)

    sys_lower = captured["prompt"][0].content.lower()
    assert "expert technical assistant" in sys_lower
    assert "scaffolding" in sys_lower


def test_backend_prompt_brief_sent_verbatim_no_task_n_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HumanMessage must equal the brief exactly — no 'Task #N' prefix,
    no 'Brief:' label, no surrounding whitespace tweaks.
    """
    captured = _install_capture(monkeypatch)
    brief = "List 3 reasons to use Pydantic over dataclasses"
    state = {"task_id": 42, "brief": brief, "assigned_role": 2}
    _run(state)

    human_msg = captured["prompt"][1]
    assert isinstance(human_msg, HumanMessage)
    assert human_msg.content == brief
    # Belt-and-suspenders: the regression we're guarding against is the
    # explicit "Task #N" wrapper from the pre-#907 prompt.
    assert "Task #" not in human_msg.content
    assert "Brief:" not in human_msg.content


def test_backend_prompt_returns_state_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Function signature is unchanged — returns dict with messages +
    final_result. Lead-locked invariant.
    """
    _install_capture(monkeypatch)
    state = {"task_id": 7, "brief": "what is JWT", "assigned_role": 2}
    out = _run(state)

    assert isinstance(out, dict)
    assert "messages" in out
    assert "final_result" in out
    assert out["final_result"] == "MOCKED"
    assert len(out["messages"]) == 1


@pytest.mark.parametrize(
    "brief",
    [
        "Define idempotency in HTTP",                       # definition shape
        "Write a Python function that reverses a string",   # code shape
        "List 3 reasons to use Pydantic over dataclasses",  # list shape
    ],
)
def test_backend_prompt_preserves_brief_across_shapes(
    monkeypatch: pytest.MonkeyPatch, brief: str
) -> None:
    """Shape-mirroring intent lock — whatever shape the brief takes
    (definition / code / list), it must arrive at the LLM verbatim. The
    SystemMessage's "definition → definition, code → code, list → list"
    directive only works if the brief isn't rewritten on the way through.
    """
    captured = _install_capture(monkeypatch)
    state = {"task_id": 100, "brief": brief, "assigned_role": 2}
    _run(state)

    human_msg = captured["prompt"][1]
    assert human_msg.content == brief


def test_backend_prompt_empty_brief_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state.get('brief', '') fallback must keep working — empty brief
    arrives at the LLM as empty string; the LLM (not this node) decides
    how to handle it.
    """
    captured = _install_capture(monkeypatch)
    state = {"task_id": 1, "assigned_role": 2}  # no brief key at all
    out = _run(state)

    assert captured["prompt"][1].content == ""
    assert "final_result" in out
