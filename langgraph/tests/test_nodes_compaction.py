"""Kanban #1717 — conversation-history compaction unit tests.

`_compact_messages(messages, budget_tokens)` trims the tool-use loop's
re-sent history with a deterministic heuristic (NO LLM call, NO tiktoken).
These tests are PURE: no live model, no DB, no httpx — they call the helper
directly with hand-built message lists.

Coverage:
  - under-budget history → returned UNCHANGED (default path preserved).
  - over-budget history → result fits budget; system + brief retained;
    last N turns verbatim.
  - pairing invariant (the key test) → after compacting an over-budget
    multi-tool-call history, NO orphans: every retained AIMessage tool_call
    id has its ToolMessage and vice-versa.
  - a turn with MULTIPLE tool_calls is dropped/kept as a UNIT (no partial).
  - env reader → LANGGRAPH_CONTEXT_TOKEN_BUDGET parsed; malformed → default.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import nodes
from nodes import (
    CONTEXT_RECENT_TURNS_KEPT,
    DEFAULT_CONTEXT_TOKEN_BUDGET,
    _compact_messages,
    _estimate_tokens,
    _resolve_context_token_budget,
)


# ---------------------------------------------------------------------------
# Builders — mirror the production message shape.
# ---------------------------------------------------------------------------


def _ai_turn(call_specs: list[tuple[str, str]], text: str = "thinking") -> AIMessage:
    """AIMessage with one or more tool_calls.

    `call_specs` is a list of (tool_call_id, tool_name) pairs.
    """
    msg = AIMessage(content=text)
    msg.tool_calls = [
        {"name": name, "args": {}, "id": cid} for (cid, name) in call_specs
    ]
    return msg


def _tool_msg(call_id: str, payload: str) -> ToolMessage:
    return ToolMessage(content=payload, tool_call_id=call_id)


def _head() -> list:
    """System (idx0) + brief HumanMessage (idx1)."""
    return [
        SystemMessage(content="SYSTEM PROMPT (sacrosanct)"),
        HumanMessage(content="ORIGINAL BRIEF (sacrosanct)"),
    ]


def _all_tool_call_ids(messages: list) -> set[str]:
    ids: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if cid is not None:
                    ids.add(cid)
    return ids


def _all_tool_message_ids(messages: list) -> set[str]:
    return {
        m.tool_call_id for m in messages if isinstance(m, ToolMessage)
    }


def _assert_no_orphans(messages: list) -> None:
    """Every retained AIMessage tool_call id has a ToolMessage and vice-versa."""
    call_ids = _all_tool_call_ids(messages)
    tool_ids = _all_tool_message_ids(messages)
    assert call_ids == tool_ids, (
        f"orphans detected: call_ids-only={call_ids - tool_ids}, "
        f"toolmsg-only={tool_ids - call_ids}"
    )


# ---------------------------------------------------------------------------
# Under-budget → unchanged
# ---------------------------------------------------------------------------


def test_under_budget_history_returned_unchanged() -> None:
    """A small history that already fits the budget is returned verbatim —
    no stubbing, no dropping. Default path preserved."""
    messages = _head() + [
        _ai_turn([("c1", "stub_read")]),
        _tool_msg("c1", "small result one"),
        _ai_turn([("c2", "stub_read")]),
        _tool_msg("c2", "small result two"),
        _ai_turn([]),  # final answer, no tool_calls
    ]
    # Huge budget → nothing to do.
    out = _compact_messages(messages, budget_tokens=1_000_000)

    # Same content, same order — equality on the message objects.
    assert out == messages
    # ToolMessage payloads are untouched (no [elided ...] stub).
    tool_contents = [m.content for m in out if isinstance(m, ToolMessage)]
    assert tool_contents == ["small result one", "small result two"]
    _assert_no_orphans(out)


def test_under_budget_short_list_no_turns() -> None:
    """A list with only system+brief (no turns) compacts to itself."""
    messages = _head()
    out = _compact_messages(messages, budget_tokens=10)
    assert out == messages


# ---------------------------------------------------------------------------
# Over-budget → fits, system+brief+recent-N retained
# ---------------------------------------------------------------------------


def test_over_budget_history_fits_after_compaction() -> None:
    """Many large ToolMessages over budget → result fits the budget; system +
    brief retained; the last N turns kept verbatim.

    Recent-N turns here are SMALL so the budget is achievable by stubbing +
    dropping the older big turns. (When the recent-N window alone exceeds
    budget the spec preserves it anyway — that floor case is covered by
    `test_recent_turns_kept_verbatim_when_budget_allows_trim`.)"""
    big = "X" * 8000  # ~2000 tokens each via len//4
    small = "ok"
    turns: list = []
    # 3 OLD big turns.
    for i in range(3):
        cid = f"old{i}"
        turns.append(_ai_turn([(cid, "stub_read")]))
        turns.append(_tool_msg(cid, big))
    # 3 RECENT small turns (the protected window — cheap, so budget is met).
    for i in range(3):
        cid = f"rec{i}"
        turns.append(_ai_turn([(cid, "stub_read")]))
        turns.append(_tool_msg(cid, small))
    messages = _head() + turns

    budget = 3000  # under the raw total (~6000+ tokens) but above the recent-N floor
    out = _compact_messages(messages, budget_tokens=budget)

    # Fits the budget (recent-N is cheap → achievable).
    assert nodes._total_tokens(out) <= budget
    # System + brief retained verbatim at the head.
    assert out[0] is messages[0]
    assert out[1] is messages[1]
    # The most-recent N turns are kept VERBATIM (their small payload intact).
    recent_tool_payloads = [
        m.content for m in out if isinstance(m, ToolMessage) and m.content == small
    ]
    assert len(recent_tool_payloads) == CONTEXT_RECENT_TURNS_KEPT
    _assert_no_orphans(out)


def test_recent_turns_kept_verbatim_when_budget_allows_trim() -> None:
    """With a budget that fits exactly the head + recent-N turns, the older
    turns get stubbed/dropped and the recent-N stay byte-identical."""
    big = "Y" * 4000  # ~1000 tokens
    turns: list = []
    for i in range(8):
        cid = f"t{i}"
        turns.append(_ai_turn([(cid, "stub_read")]))
        turns.append(_tool_msg(cid, big))
    messages = _head() + turns

    # Budget sized so older turns must shrink; recent-N (3 turns) preserved.
    out = _compact_messages(messages, budget_tokens=4000)

    # The last 3 turns' tool payloads are the original `big` (verbatim).
    out_tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    verbatim = [m for m in out_tool_msgs if m.content == big]
    assert len(verbatim) == CONTEXT_RECENT_TURNS_KEPT
    # The verbatim ones are the LAST three tool ids (t5,t6,t7).
    assert {m.tool_call_id for m in verbatim} == {"t5", "t6", "t7"}
    _assert_no_orphans(out)


# ---------------------------------------------------------------------------
# Pairing invariant — THE key test
# ---------------------------------------------------------------------------


def test_pairing_invariant_multi_tool_call_history_no_orphans() -> None:
    """After compacting an over-budget history that includes AIMessages with
    MULTIPLE tool_calls, every retained tool_call id has its ToolMessage and
    every retained ToolMessage has its parent AIMessage. NO orphans."""
    big = "Z" * 6000  # ~1500 tokens per ToolMessage
    turns: list = []

    # Turn 0: single tool call.
    turns.append(_ai_turn([("a0", "stub_read")]))
    turns.append(_tool_msg("a0", big))
    # Turn 1: TWO tool calls in one AIMessage → two paired ToolMessages.
    turns.append(_ai_turn([("b0", "stub_read"), ("b1", "stub_read")]))
    turns.append(_tool_msg("b0", big))
    turns.append(_tool_msg("b1", big))
    # Turn 2: THREE tool calls.
    turns.append(_ai_turn([("c0", "x"), ("c1", "y"), ("c2", "z")]))
    turns.append(_tool_msg("c0", big))
    turns.append(_tool_msg("c1", big))
    turns.append(_tool_msg("c2", big))
    # Turn 3: single.
    turns.append(_ai_turn([("d0", "stub_read")]))
    turns.append(_tool_msg("d0", big))
    # Turn 4: single.
    turns.append(_ai_turn([("e0", "stub_read")]))
    turns.append(_tool_msg("e0", big))

    messages = _head() + turns

    out = _compact_messages(messages, budget_tokens=2500)

    # The whole point: provider would 400 on orphans; assert there are none.
    _assert_no_orphans(out)
    # System + brief still present.
    assert out[0] is messages[0]
    assert out[1] is messages[1]
    # Result is within budget (or the recent-N floor, which is preserved).
    # Recent-N = turns 2,3,4 → turn 2 has 3 ToolMessages all verbatim.
    recent_ids = _all_tool_message_ids(out)
    assert {"c0", "c1", "c2", "d0", "e0"} <= recent_ids


def test_multi_tool_call_turn_dropped_as_unit() -> None:
    """An OLD turn with MULTIPLE tool_calls is dropped as a UNIT — never the
    AIMessage without its ToolMessages, never a partial subset of pairs."""
    big = "Q" * 10000  # ~2500 tokens each — force aggressive dropping
    turns: list = []

    # OLD turn (index 0) with THREE tool calls — must drop whole or not at all.
    turns.append(_ai_turn([("old0", "x"), ("old1", "y"), ("old2", "z")]))
    turns.append(_tool_msg("old0", big))
    turns.append(_tool_msg("old1", big))
    turns.append(_tool_msg("old2", big))
    # Three recent single-call turns (the protected window).
    for i in range(3):
        cid = f"r{i}"
        turns.append(_ai_turn([(cid, "stub_read")]))
        turns.append(_tool_msg(cid, big))

    messages = _head() + turns

    # Tiny budget → stubbing the old turn won't be enough; it must drop whole.
    out = _compact_messages(messages, budget_tokens=1000)

    out_call_ids = _all_tool_call_ids(out)
    out_tool_ids = _all_tool_message_ids(out)

    # The old multi-call turn is gone ENTIRELY — none of its ids survive in
    # EITHER the AIMessage tool_calls OR the ToolMessages. No partial unit.
    for cid in ("old0", "old1", "old2"):
        assert cid not in out_call_ids, f"{cid} AIMessage tool_call leaked"
        assert cid not in out_tool_ids, f"{cid} ToolMessage leaked"

    # And no orphans among what remains.
    _assert_no_orphans(out)
    # The recent window survives intact.
    assert {"r0", "r1", "r2"} <= out_tool_ids


def test_old_turn_stubbed_not_dropped_when_stub_suffices() -> None:
    """If stubbing the old turns' payloads brings the total under budget, the
    old turns are KEPT (objects retained) with stubbed content — not dropped.
    This preserves the tool_call pairing for older turns too."""
    big = "W" * 4000  # ~1000 tokens
    turns: list = []
    # 2 old turns + 3 recent turns.
    for i in range(5):
        cid = f"s{i}"
        turns.append(_ai_turn([(cid, "stub_read")]))
        turns.append(_tool_msg(cid, big))
    messages = _head() + turns

    # Budget chosen so: full=~5000 tokens; after stubbing the 2 old turns'
    # payloads (each → tiny stub) we drop ~2000 tokens → ~3000+stub, under a
    # 3500 budget without dropping any turn.
    out = _compact_messages(messages, budget_tokens=3500)

    out_tool_ids = _all_tool_message_ids(out)
    # All 5 turns' ToolMessages still present (none dropped).
    assert {"s0", "s1", "s2", "s3", "s4"} == out_tool_ids
    # The 2 OLD ones are stubbed.
    by_id = {
        m.tool_call_id: m.content
        for m in out
        if isinstance(m, ToolMessage)
    }
    assert by_id["s0"].startswith("[elided: s0 result,")
    assert by_id["s1"].startswith("[elided: s1 result,")
    # The 3 RECENT ones are verbatim.
    assert by_id["s2"] == big
    assert by_id["s3"] == big
    assert by_id["s4"] == big
    _assert_no_orphans(out)


# ---------------------------------------------------------------------------
# Helpers + env reader
# ---------------------------------------------------------------------------


def test_estimate_tokens_handles_str_and_list_content() -> None:
    """The heuristic stringifies list-of-blocks content uniformly."""
    assert _estimate_tokens("abcd") == 1  # 4 // 4
    assert _estimate_tokens("a" * 40) == 10
    # Anthropic list-of-blocks shape → str()'d then len//4. Just assert it's a
    # positive int (deterministic, no crash on list content).
    blocks = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
    assert isinstance(_estimate_tokens(blocks), int)
    assert _estimate_tokens(blocks) > 0


def test_resolve_budget_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("LANGGRAPH_CONTEXT_TOKEN_BUDGET", raising=False)
    assert _resolve_context_token_budget() == DEFAULT_CONTEXT_TOKEN_BUDGET


def test_resolve_budget_parses_valid_int(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_CONTEXT_TOKEN_BUDGET", "  12345 ")
    assert _resolve_context_token_budget() == 12345


def test_resolve_budget_falls_back_on_malformed(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_CONTEXT_TOKEN_BUDGET", "not-a-number")
    assert _resolve_context_token_budget() == DEFAULT_CONTEXT_TOKEN_BUDGET


def test_resolve_budget_falls_back_on_non_positive(monkeypatch) -> None:
    monkeypatch.setenv("LANGGRAPH_CONTEXT_TOKEN_BUDGET", "0")
    assert _resolve_context_token_budget() == DEFAULT_CONTEXT_TOKEN_BUDGET
    monkeypatch.setenv("LANGGRAPH_CONTEXT_TOKEN_BUDGET", "-50")
    assert _resolve_context_token_budget() == DEFAULT_CONTEXT_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# M-2 — recent-N window exceeds budget → logger.warning emitted
# ---------------------------------------------------------------------------


def test_recent_n_window_exceeds_budget_emits_warning(caplog) -> None:
    """Kanban #1720 fix M-2 — when the recent-N window alone exceeds the budget,
    a logger.warning must fire with the token count and budget. The returned
    messages must still contain the recent-N turns verbatim (floor preserved).
    """
    import logging

    big = "R" * 10000  # ~2500 tokens each via len//4
    turns: list = []
    # Build exactly CONTEXT_RECENT_TURNS_KEPT turns, each with a big payload,
    # so the window ALONE exceeds a tiny budget.
    for i in range(CONTEXT_RECENT_TURNS_KEPT):
        cid = f"w{i}"
        turns.append(_ai_turn([(cid, "stub_read")]))
        turns.append(_tool_msg(cid, big))
    messages = _head() + turns

    # Budget far below the recent-N window cost.
    tiny_budget = 10

    with caplog.at_level(logging.WARNING, logger="langgraph.nodes"):
        out = _compact_messages(messages, budget_tokens=tiny_budget)

    # Warning must have fired.
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "exceeds budget" in r.message
    ]
    assert warning_records, (
        "Expected a logger.warning about recent-N exceeding budget, got none. "
        f"All log records: {[r.message for r in caplog.records]}"
    )
    # The warning must mention the estimated token count and the budget.
    warn_msg = warning_records[0].message
    assert str(tiny_budget) in warn_msg

    # Return value still contains the recent-N turns verbatim.
    out_tool_ids = {m.tool_call_id for m in out if isinstance(m, ToolMessage)}
    expected_ids = {f"w{i}" for i in range(CONTEXT_RECENT_TURNS_KEPT)}
    assert expected_ids == out_tool_ids, (
        f"recent-N turns dropped — expected {expected_ids}, got {out_tool_ids}"
    )
    # No orphans in the returned list.
    _assert_no_orphans(out)


# ---------------------------------------------------------------------------
# H-4 regression — _stub_turn clones ToolMessage, never mutates the original
# ---------------------------------------------------------------------------


def test_stub_turn_clones_toolmessage_original_unchanged() -> None:
    """H-4 fix regression: _stub_turn must NOT mutate the original ToolMessage
    objects — it must replace each slot with a new object.

    NEGATIVE assertion: original message content is unchanged after compaction.
    POSITIVE assertion: the compacted list contains a different object with
    the elided stub content (proving the clone replaced the slot).
    """
    from nodes import _stub_turn

    original_content = "big result payload " * 50
    cid = "tc-h4-test"
    original_msg = _tool_msg(cid, original_content)
    # Capture the id of the original object.
    original_id = id(original_msg)

    turn: list = [
        _ai_turn([(cid, "some_tool")]),
        original_msg,
    ]

    _stub_turn(turn)

    # NEGATIVE: the original object's content is unchanged.
    assert original_msg.content == original_content, (
        f"_stub_turn mutated the original ToolMessage in place; "
        f"content is now: {original_msg.content!r}"
    )
    # POSITIVE: the slot in the turn now holds a DIFFERENT object with elided content.
    stubbed = next(m for m in turn if isinstance(m, ToolMessage))
    assert id(stubbed) != original_id, (
        "_stub_turn must replace the slot with a new ToolMessage, not mutate the old one"
    )
    assert stubbed.content.startswith(f"[elided: {cid} result,"), (
        f"unexpected stub content: {stubbed.content!r}"
    )
    assert stubbed.tool_call_id == cid, "stub clone must preserve tool_call_id"


def test_compact_messages_does_not_mutate_input_toolmessages() -> None:
    """Integration form of H-4: _compact_messages triggers _stub_turn internally;
    the original message objects in the input list must be unchanged after the call.

    This is the scenario that breaks on checkpoint resume: the state['messages']
    list shares ToolMessage objects with the turn list _compact_messages receives.
    """
    big = "Y" * 8000  # ~2000 tokens via len//4; need at least 2 old turns to stub

    # Build 5 turns: 2 old (will be stubbed) + 3 recent (protected window).
    all_orig_msgs: list = []
    turns: list = []
    for i in range(5):
        cid = f"h4-{i}"
        ai_msg = _ai_turn([(cid, "tool")])
        tm = _tool_msg(cid, big)
        all_orig_msgs.append(tm)
        turns.extend([ai_msg, tm])

    messages = _head() + turns
    # Snapshot the original content of every ToolMessage BEFORE compaction.
    before = {m.tool_call_id: m.content for m in messages if isinstance(m, ToolMessage)}

    # Budget that requires stubbing the first 2 turns (old window).
    _compact_messages(messages, budget_tokens=3500)

    # NEGATIVE: none of the original ToolMessage objects should have been mutated.
    for tm in all_orig_msgs:
        assert tm.content == before[tm.tool_call_id], (
            f"_compact_messages mutated original ToolMessage {tm.tool_call_id!r} in place; "
            f"was {before[tm.tool_call_id]!r[:40]}, now {tm.content!r[:40]}"
        )
