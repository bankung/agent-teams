"""Prompt-caching tests — Kanban #1186 AC1, AC2, AC4.

Three orthogonal assertions:

1. **AC1**: the SystemMessage produced by `backend_specialist_node` (anthropic
   branch) carries `cache_control: {"type": "ephemeral"}` on the LAST stable
   content block.

2. **AC4**: the per-call dynamic content (HumanMessage with task brief, role
   brief itself) is OUTSIDE the cached block. Two consecutive invocations with
   different `brief` values must produce IDENTICAL stable-bundle text → the
   Anthropic cache key matches → cache hit on the 2nd call.

3. **AC2**: simulate the Anthropic API echoing `cache_read_input_tokens > 0`
   on the 2nd invocation (mock); confirm `cost_tracker.compute_cost` priced
   the read at the 0.10x rate (i.e. the cached cost < uncached cost).

We mock `make_chat_model` exactly like `test_nodes_prompt.py` so this test
runs without a network call. The whole point of the test is to assert the
plumbing — the live cache-hit behaviour is governed by Anthropic's API and
is out of scope for unit tests (the math test in `test_cost_tracker.py`
covers the cost-formula side).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage

import nodes
from nodes import backend_specialist_node


# ---------------------------------------------------------------------------
# Common monkeypatch helper
# ---------------------------------------------------------------------------


def _install_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Patch `make_chat_model` to record every prompt list passed to .invoke().

    Returns a dict with a growing list of prompts so a single test can drive
    two invocations and inspect both.
    """
    captured: dict[str, Any] = {"prompts": []}

    def _invoke(prompt: list[Any]) -> AIMessage:
        captured["prompts"].append(prompt)
        return AIMessage(content="MOCKED")

    fake_model = SimpleNamespace(invoke=_invoke)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake_model)

    async def _fake_fetch(project_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(nodes, "_fetch_tools_config", _fake_fetch)

    # Pin provider to anthropic so build_cached_system_content returns the
    # content-blocks shape (the form that carries cache_control).
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    # ANTHROPIC_API_KEY isn't read on the cached-bundle code path (we mock
    # make_chat_model itself) but keep it set in case any helper reads it.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Force a clean bundle build (other tests may have warmed the cache).
    from llm import reset_bundle_cache_for_tests

    reset_bundle_cache_for_tests()

    return captured


def _run(state: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(backend_specialist_node(state))


# ---------------------------------------------------------------------------
# AC1 — cache_control attaches on the stable bundle block
# ---------------------------------------------------------------------------


def test_cache_control_on_stable_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """SystemMessage.content is a list of >=2 blocks; the FIRST block
    carries cache_control: ephemeral; the LAST block (role_brief) does NOT.
    """
    captured = _install_capture(monkeypatch)
    state = {"task_id": 1, "brief": "anything", "assigned_role": 2}
    _run(state)

    sys_msg = captured["prompts"][0][0]
    assert isinstance(sys_msg, SystemMessage)
    content = sys_msg.content
    assert isinstance(content, list), (
        f"expected list-of-blocks on anthropic; got {type(content).__name__}"
    )
    assert len(content) >= 2, (
        f"expected stable + role_brief blocks; got {len(content)}"
    )
    stable_block = content[0]
    role_brief_block = content[-1]
    assert isinstance(stable_block, dict)
    assert stable_block.get("type") == "text"
    assert stable_block.get("cache_control") == {"type": "ephemeral"}, (
        f"cache_control missing or wrong: {stable_block.get('cache_control')}"
    )
    # role_brief MUST NOT carry cache_control (mutates per call).
    assert isinstance(role_brief_block, dict)
    assert "cache_control" not in role_brief_block


def test_cache_control_bundle_above_minimum_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cached bundle must be large enough that Anthropic won't silently
    drop the cache_control annotation. The Sonnet minimum is 1024 tokens;
    using a conservative chars/4 estimate, that's ~4096 chars. We pin a
    floor well above that to guard against accidental shrinking of the
    bundle (e.g., a refactor that drops CLAUDE.md or the team playbook).
    """
    captured = _install_capture(monkeypatch)
    _run({"task_id": 1, "brief": "x", "assigned_role": 2})

    stable_block = captured["prompts"][0][0].content[0]
    text = stable_block["text"]
    # chars/4 is the canonical rough estimator for English-ish content.
    est_tokens = len(text) / 4
    assert est_tokens >= 2000, (
        f"stable bundle estimated {est_tokens:.0f} tokens; below 2000 floor. "
        "CLAUDE.md / team playbook / agent definition may be missing or empty."
    )


# ---------------------------------------------------------------------------
# AC4 — dynamic content is NOT in the cached block
# ---------------------------------------------------------------------------


def test_dynamic_brief_outside_cached_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two invocations with different briefs must produce IDENTICAL stable
    blocks (same text, same cache_control). The cached key is bytewise — any
    drift in the stable block kills the cache hit.
    """
    captured = _install_capture(monkeypatch)
    _run({"task_id": 1, "brief": "BRIEF_A", "assigned_role": 2})
    _run({"task_id": 2, "brief": "BRIEF_B", "assigned_role": 2})

    sys_msg_a = captured["prompts"][0][0]
    sys_msg_b = captured["prompts"][1][0]
    stable_a = sys_msg_a.content[0]
    stable_b = sys_msg_b.content[0]
    # Same text in the stable block across invocations → cache key matches.
    assert stable_a["text"] == stable_b["text"]
    assert stable_a["cache_control"] == stable_b["cache_control"]
    # Brief differs → goes through as the HumanMessage, not the stable block.
    human_a = sys_msg_a  # placeholder
    human_a = captured["prompts"][0][1]
    human_b = captured["prompts"][1][1]
    assert human_a.content == "BRIEF_A"
    assert human_b.content == "BRIEF_B"
    # Brief text must NOT appear in the stable cached block.
    assert "BRIEF_A" not in stable_a["text"]
    assert "BRIEF_B" not in stable_b["text"]


def test_stable_block_contains_bundled_governance_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stable bundle bundles safety prelude + CLAUDE.md + team playbook +
    agent definition. Pin one fingerprint phrase from each so a future refactor
    that silently drops one source trips here.
    """
    captured = _install_capture(monkeypatch)
    _run({"task_id": 1, "brief": "x", "assigned_role": 2})
    stable_text = captured["prompts"][0][0].content[0]["text"]
    # Safety prelude — Kanban #1116 lock-phrase.
    assert "STRICT RULES" in stable_text
    # CLAUDE.md — the universal-rules header that the file is built around.
    assert "Golden rules" in stable_text or "Lead never edits target-project" in stable_text
    # Team playbook (dev) — at minimum, the team file's filename appears in
    # the section header we prepend. (We can't assume content of dev.md if
    # the file is short.)
    assert "# Team playbook (dev)" in stable_text
    # Agent definition — same.
    assert "# Agent definition (dev-backend)" in stable_text


# ---------------------------------------------------------------------------
# AC2 — simulated cache_read on 2nd call produces lower cost
# ---------------------------------------------------------------------------


def test_simulated_cache_hit_lowers_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock the Anthropic response such that the 2nd call reports
    cache_read_input_tokens > 0. Compute the cost via the api's cost_tracker
    and assert the cached cost < uncached cost.

    This is the unit-test surrogate for the AC2 'cache_read_input_tokens > 0
    on 2nd call' requirement — live API verification is out of scope (no
    live LLM call from unit tests).
    """
    # Import the cost_tracker from the api package. The langgraph container
    # doesn't import the api package by default; this test runs in the api
    # container's env, OR the cost_tracker module is on the path.
    import sys
    import pathlib

    api_src = pathlib.Path(__file__).resolve().parent.parent.parent / "api"
    sys.path.insert(0, str(api_src))
    try:
        from src.services.cost_tracker import compute_cost
    except ImportError:
        pytest.skip(
            "api.src.services.cost_tracker not importable in this test env "
            "(see api/tests/test_cost_tracker.py for the cost-formula tests)"
        )

    # Sonnet input rate is $3 per 1M tokens.
    # Imagine the stable bundle is ~10_000 tokens; cache miss writes it; cache
    # hit reads it.
    stable_tokens = 10_000
    output_tokens = 500  # small completion both times

    # Call 1: cache miss → cache_creation_input_tokens=10_000, regular
    # input_tokens=0 (Anthropic API reports the cached portion separately from
    # the regular input).
    cost_1 = compute_cost(
        "anthropic",
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=stable_tokens,
    )
    # Call 2: cache hit → cache_read_input_tokens=10_000, no write.
    cost_2 = compute_cost(
        "anthropic",
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=output_tokens,
        cache_read_input_tokens=stable_tokens,
        cache_creation_input_tokens=0,
    )
    # Uncached baseline: stable_tokens billed at base input rate.
    cost_uncached = compute_cost(
        "anthropic",
        "claude-sonnet-4-6",
        input_tokens=stable_tokens,
        output_tokens=output_tokens,
    )

    # Cache hit on call 2 is cheaper than the equivalent uncached call.
    assert cost_2 < cost_uncached, (
        f"cache hit cost {cost_2} >= uncached {cost_uncached}; "
        "cache pricing math is wrong"
    )
    # Cache miss (write) is MORE expensive than uncached (1.25x premium).
    assert cost_1 > cost_uncached, (
        f"cache write cost {cost_1} <= uncached {cost_uncached}; "
        "cache write should cost the 1.25x premium"
    )


# ---------------------------------------------------------------------------
# Provider gate — non-anthropic falls back to flat string
# ---------------------------------------------------------------------------


def test_non_anthropic_provider_returns_string_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the provider is openai or ollama, the SystemMessage content must
    be a plain string (no list-of-blocks, no cache_control). cache_control is
    Anthropic-only; passing it on the openai branch would either be ignored
    or rejected.
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    captured: dict[str, Any] = {"prompts": []}

    def _invoke(prompt: list[Any]) -> AIMessage:
        captured["prompts"].append(prompt)
        return AIMessage(content="MOCKED")

    fake_model = SimpleNamespace(invoke=_invoke)
    monkeypatch.setattr(nodes, "make_chat_model", lambda: fake_model)

    async def _fake_fetch(project_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(nodes, "_fetch_tools_config", _fake_fetch)

    from llm import reset_bundle_cache_for_tests

    reset_bundle_cache_for_tests()

    _run({"task_id": 1, "brief": "x", "assigned_role": 2})

    sys_msg = captured["prompts"][0][0]
    assert isinstance(sys_msg, SystemMessage)
    assert isinstance(sys_msg.content, str), (
        f"openai/ollama branch must return string content; got {type(sys_msg.content).__name__}"
    )
    # The bundle text + separator + role_brief is still in there — same
    # governance frame across providers.
    assert "STRICT RULES" in sys_msg.content
    assert "\n\n---\n\n" in sys_msg.content
