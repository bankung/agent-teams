"""Graph nodes — supervisor + specialists.

The supervisor's job (for #850) is minimal: stamp a system message into the
conversation announcing the routing decision, then let the conditional edge
function (`route_from_supervisor`) actually pick the next node. The supervisor
is intentionally dumb here because Kanban #852 (Kanban integration) will move
real routing logic into the API poll loop; this node is a placeholder that
keeps the graph topology honest.

`backend_specialist_node` is the production specialist (#977/#979/#980/#981):
it constructs the LLM via `make_chat_model()`, binds the global tool registry
via `model.bind_tools(...)`, then runs a multi-turn tool-use loop with the
permission gate + sandbox guards + audit-trail wiring layered around every
tool invocation. See `_run_tool_use_loop` for the full sequence + safety
primitives. Other specialist stubs return a canned "not implemented" message
so the graph can be exercised end-to-end for any role without crashing.

All nodes return PARTIAL state dicts. LangGraph merges them via the reducer
declared on each TypedDict field (messages → add_messages; everything else →
last-write-wins). `backend_specialist_node` is `async` because tool
invocations are coroutines; LangGraph happily awaits async node functions.

## HITL (human-in-the-loop) emission (Kanban #986)

Any specialist that needs user input mid-execution calls
`hitl.request_user_input(payload)` (re-exported here for convenience). On the
first call within a node it raises GraphInterrupt → LangGraph checkpoints
state → worker PATCHes the task to BLOCKED + halt_reason. On resume (worker
invokes `graph.ainvoke(Command(resume=<answer>))`), the function returns the
user's answer string. The payload dict shape mirrors Kanban's question_payload
column (`{"question": ..., "options": [...]}`) so the worker can forward it
to the DB unchanged. See `hitl.py` for the engine-side glue + validation rules.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from audit import record_tool_invocation
from config import resolve_api_base, resolve_project_id, utc_now
from hitl import request_user_input  # noqa: F401 — re-exported for specialist authors
from llm import (
    build_cached_system_content,
    build_system_message,
    make_chat_model,
    resolve_provider,
)
from state import AgentState
from tools import (
    GLOBAL_REGISTRY,
    MAX_TOOL_LOOP_ITERATIONS,
    TOOL_LOOP_HALT_REASON,
    InvokeContext,
    PermissionDecision,
    ToolNotFoundError,
    ToolResult,
    apply_sandbox,
    check_permission,
    fs_boundary_check,
)

logger = logging.getLogger("langgraph.nodes")

# Role codes mirror api/src/constants.py::TaskRole. Duplicated intentionally —
# the langgraph container does not import the api package (separate
# pyproject + container). Keep in sync; the supervisor routing unit test pins
# the mapping so any drift surfaces immediately.
ROLE_FRONTEND = 1
ROLE_BACKEND = 2
ROLE_DEVOPS = 3
ROLE_QA = 4
ROLE_REVIEWER = 5


# ---------------------------------------------------------------------------
# Conversation-history compaction (Kanban #1717)
# ---------------------------------------------------------------------------
#
# The tool-use loop re-sends the FULL `messages` list to the model every
# iteration. Each ToolMessage payload is capped at 100KB by the sandbox, so a
# multi-step task accumulates context fast → degraded quality, higher cost,
# eventual provider overflow. `_compact_messages` (below) trims the history at
# the top of each loop iteration with a deterministic heuristic — NO LLM
# summarization call in v1, NO tiktoken dependency.

DEFAULT_CONTEXT_TOKEN_BUDGET: int = 60_000
"""Fallback token budget when LANGGRAPH_CONTEXT_TOKEN_BUDGET is unset/invalid."""

CONTEXT_RECENT_TURNS_KEPT: int = 3
"""Most-recent N turns kept VERBATIM (never stubbed, never dropped). A "turn"
is one AIMessage + its paired ToolMessage(s)."""


def _resolve_context_token_budget() -> int:
    """Read LANGGRAPH_CONTEXT_TOKEN_BUDGET → positive int, else the default.

    Mirrors the worker.py env-int idiom: strip, validate as a positive
    integer, fall back on anything malformed (empty, non-numeric, <= 0).
    """
    raw = os.getenv(
        "LANGGRAPH_CONTEXT_TOKEN_BUDGET", str(DEFAULT_CONTEXT_TOKEN_BUDGET)
    ).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_TOKEN_BUDGET
    if value <= 0:
        return DEFAULT_CONTEXT_TOKEN_BUDGET
    return value


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


def supervisor_node(state: AgentState) -> dict:
    """Record the routing decision in the conversation log.

    The conditional edge (`route_from_supervisor`) does the actual routing —
    this node just emits a SystemMessage so checkpoints carry a breadcrumb of
    which specialist was selected for which task_id.
    """
    role = state.get("assigned_role")
    task_id = state.get("task_id", "?")
    target = route_from_supervisor(state)
    return {
        "messages": [
            SystemMessage(
                content=f"supervisor: task_id={task_id} role={role} → {target}"
            )
        ]
    }


def route_from_supervisor(state: AgentState) -> str:
    """Conditional-edge function. Returns the next node's name.

    Defensive default: any unknown / None role routes to `general` rather than
    raising. The graph stays well-formed even if upstream (#852) hands us a new
    role code before this module learns about it; the `general` node returns a
    halt_reason='error' so the failure is visible.
    """
    role = state.get("assigned_role")
    if role == ROLE_FRONTEND:
        return "frontend"
    if role == ROLE_BACKEND:
        return "backend"
    if role == ROLE_DEVOPS:
        return "devops"
    if role == ROLE_QA:
        return "tester"
    if role == ROLE_REVIEWER:
        return "reviewer"
    return "general"


# ---------------------------------------------------------------------------
# Specialists
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are an expert technical assistant. Answer the user's question or "
    "request directly and concisely. "
    "Do not propose unrelated scaffolding, project plans, requirements "
    "lists, or implementation steps unless explicitly asked. "
    "If the request asks for a definition, give the definition. "
    "If it asks for code, give code. If it asks for a list, give a list. "
    "Prefer accurate brevity over verbose explanation. "
    "When tools are available, you MUST use them to perform any action "
    "the user requests — reading files, editing files, running commands, "
    "checking state. DO NOT paraphrase or describe what a tool would do; "
    "INVOKE the tool. If the user's request can be satisfied by a tool, "
    "calling the tool is mandatory."
)


async def backend_specialist_node(state: AgentState) -> dict:
    """Production specialist with tool-use loop (Kanban #977/#979/#980/#981).

    Sequence:
      1. Resolve the per-project `tools_config` from the Kanban API
         (sourced from `projects.tools_config`). On fetch failure we fall
         back to None — the permission gate then rejects every tool call,
         which collapses gracefully to a tool-less single-shot answer.
      2. Build the LLM with `bind_tools(GLOBAL_REGISTRY.all_tools_as_langchain())`.
         Ollama doesn't support tool-use; we feature-flag with `bind_tools`
         in a try/except so an ollama deployment still runs (just without
         tools).
      3. Loop up to `MAX_TOOL_LOOP_ITERATIONS` times:
         - call `model.invoke(messages)`.
         - if the response has no `tool_calls` → final answer, exit loop.
         - else for each tool call:
            a. `check_permission(tools_config, tool)` → auto_allow / halt / reject.
            b. `fs_boundary_check(tool, ctx, args)` (pre-flight; write tools).
            c. on auto_allow: invoke the tool, then `apply_sandbox(...)`.
            d. on halt: emit halt_reason + return early.
            e. on reject: synthesise a `ToolResult(success=False, error_code='tier_not_allowed')`
               and feed it back to the LLM so it can adapt.
            f. ALWAYS audit via `record_tool_invocation(...)`.
         - append a `ToolMessage` per tool call so the next loop iteration
           sees the result.
      4. After MAX iterations exhausted → halt with `TOOL_LOOP_HALT_REASON`.

    The prompt-shape regression tests (#907) pin the SystemMessage +
    HumanMessage content; those assertions still hold because the prompt
    constant lives at module scope and the initial messages are the same.
    """
    brief = state.get("brief", "")
    task_id = state.get("task_id")
    # Project id sourced from LANGGRAPH_PROJECT_ID — the engine container is
    # bound to a single project. A future multi-project engine would fetch
    # task → project_id via the api.
    project_id = resolve_project_id()
    tools_config = await _fetch_tools_config(project_id)
    working_path, repo_root = _resolve_paths(project_id)

    ctx = InvokeContext(
        task_id=task_id,
        project_id=project_id,
        repo_root=repo_root or "/repo",
        working_path=working_path,
        host_allowlist=list((tools_config or {}).get("http_hosts") or []),
    )

    model = make_chat_model()
    # Feature-flag tool binding by provider. Ollama returns a model that
    # doesn't support tool-use; bind_tools may raise or silently drop the
    # tools. We try, and on failure log + fall back to the no-tools path.
    bound = _bind_tools_safely(model, project_id, tools_config)

    # Kanban #1116 — wrap role brief with safety prelude (L22 prevention).
    # Kanban #1186 — inflate stable context (safety prelude + CLAUDE.md + team
    # playbook + agent definition) and attach `cache_control: ephemeral` on
    # the stable bundle block. Stable prefix lands ~10K tokens (above the 1024
    # minimum); role_brief remains a separate non-cached block per-call. On
    # non-anthropic providers (openai/ollama) the helper returns a flat string
    # so the message shape stays compatible with those providers' formatters.
    initial_messages: list[Any] = [
        SystemMessage(
            content=build_cached_system_content(
                _SYSTEM_PROMPT, team="dev", agent_name="dev-backend"
            )
        ),
        HumanMessage(content=brief),
    ]

    if bound is None:
        # No tools available (ollama, or registry empty, or bind_tools raised).
        # Preserve the pre-#981 single-shot path so the worker still completes
        # task that don't need tools.
        response = await _ainvoke_model(model, initial_messages)
        content = _stringify_content(response.content)
        return {
            "messages": [response],
            "final_result": content,
        }

    return await _run_tool_use_loop(bound, initial_messages, ctx, tools_config)


# ---------------------------------------------------------------------------
# Compaction internals (Kanban #1717)
# ---------------------------------------------------------------------------


def _estimate_tokens(content: Any) -> int:
    """Deterministic per-message token heuristic: `len(str(content)) // 4`.

    Anthropic content can be a list of blocks; OpenAI/Ollama is a plain
    string. `str(content)` stringifies either shape uniformly. No LLM call,
    no tiktoken — the estimate only needs to be monotonic + cheap for the
    budget comparison, not provider-exact.
    """
    return len(str(content)) // 4


def _total_tokens(messages: list[Any]) -> int:
    return sum(_estimate_tokens(getattr(m, "content", "")) for m in messages)


def _split_turns(tail: list[Any]) -> tuple[list[Any], list[list[Any]]]:
    """Partition the post-brief message tail into whole turns.

    A turn = one AIMessage followed by its paired ToolMessage(s). Walking
    left-to-right: an AIMessage opens a new turn; every following
    ToolMessage (or any non-AIMessage) attaches to the open turn. Any
    messages BEFORE the first AIMessage are returned as `preamble` and kept
    verbatim (defensive — the production loop never produces this, but it
    keeps a malformed list from silently dropping content).

    Returns `(preamble, turns)` where each turn is a list whose first
    element is the AIMessage and the rest are its paired ToolMessages.
    """
    preamble: list[Any] = []
    turns: list[list[Any]] = []
    for msg in tail:
        if isinstance(msg, AIMessage):
            turns.append([msg])
        elif turns:
            turns[-1].append(msg)
        else:
            # Stray message before any AIMessage — keep verbatim.
            preamble.append(msg)
    return preamble, turns


def _stub_turn(turn: list[Any]) -> None:
    """Replace each ToolMessage payload in an OLD turn with a short stub.

    Mutates the ToolMessage objects in place (KEEP the object → preserves
    the `tool_call_id` pairing with its parent AIMessage's tool_call). The
    parent AIMessage is left untouched so its `.tool_calls` ids still match.
    Stubbing an already-stubbed turn is a no-op-ish re-stub (idempotent
    shape), so calling this twice is safe.
    """
    for msg in turn:
        if not isinstance(msg, ToolMessage):
            continue
        original = msg.content
        char_len = len(str(original))
        msg.content = (
            f"[elided: {msg.tool_call_id} result, {char_len} chars]"
        )


def _compact_messages(messages: list[Any], budget_tokens: int) -> list[Any]:
    """Trim conversation history to fit `budget_tokens` (deterministic, v1).

    Invariants (correctness is the whole point — Kanban #1717):
      1. messages[0] (system) + messages[1] (original brief) kept VERBATIM.
      2. The most-recent CONTEXT_RECENT_TURNS_KEPT turns kept VERBATIM.
      3. Older turns over budget: each ToolMessage's `.content` replaced with
         a `[elided: <id> result, <N> chars]` stub — the ToolMessage OBJECT
         is kept so its `tool_call_id` still pairs with the parent AIMessage.
      4. Still over budget: drop WHOLE oldest turns (AIMessage + ALL its
         paired ToolMessages together, as a unit). Never drop system/brief.
      5. After compaction every retained AIMessage tool_call has its
         ToolMessage and vice-versa — NO orphans — because we only ever
         operate on whole turns. OpenAI + Anthropic 400 on orphaned calls.

    Returns a NEW list (head + retained turns); does not reorder. The turn
    objects (and their ToolMessages, when stubbed) are mutated in place, so
    the caller should reassign: `messages = _compact_messages(messages, b)`.
    Under budget → returns an equivalent list with no stubbing/dropping.
    """
    # Head = system + brief, always verbatim. With fewer than 2 messages there
    # is nothing to compact (degenerate test/edge case) — return as-is.
    if len(messages) <= 2:
        return list(messages)

    head = messages[:2]
    preamble, turns = _split_turns(messages[2:])

    def _assemble() -> list[Any]:
        out: list[Any] = list(head)
        out.extend(preamble)
        for t in turns:
            out.extend(t)
        return out

    # Fast path: already under budget → no mutation, default path preserved.
    if _total_tokens(_assemble()) <= budget_tokens:
        return _assemble()

    # The most-recent N turns are sacrosanct. Only turns before them are
    # candidates for stubbing then dropping.
    recent = CONTEXT_RECENT_TURNS_KEPT
    old_count = max(0, len(turns) - recent)

    # Phase 1: stub the ToolMessage payloads of the OLD turns (oldest first).
    # M-1: maintain a running total instead of re-summing the full list each
    # iteration (O(n) per stub → O(1) per stub with a delta). Kanban #1720.
    running_tokens = _total_tokens(_assemble())
    for i in range(old_count):
        # Compute how much the stub will save BEFORE mutating the turn.
        delta = sum(
            _estimate_tokens(msg.content)
            for msg in turns[i]
            if isinstance(msg, ToolMessage)
        )
        _stub_turn(turns[i])
        stub_cost = sum(
            _estimate_tokens(msg.content)
            for msg in turns[i]
            if isinstance(msg, ToolMessage)
        )
        running_tokens = running_tokens - delta + stub_cost
        if running_tokens <= budget_tokens:
            return _assemble()

    # Phase 2: still over budget → drop WHOLE oldest turns as units. Never
    # drop into the recent-N window; never drop head/preamble.
    while old_count > 0 and _total_tokens(_assemble()) > budget_tokens:
        turns.pop(0)
        old_count -= 1

    # M-2: if the recent-N window alone still exceeds the budget, warn so
    # a silent provider-overflow is visible in logs. The return value is
    # unchanged — recent-N is always preserved per #1717 design. Kanban #1720.
    final = _assemble()
    final_tokens = _total_tokens(final)
    if final_tokens > budget_tokens:
        logger.warning(
            "_compact_messages: recent-%d turns window (%d estimated tokens) "
            "exceeds budget (%d tokens) — provider may overflow on small-context models",
            CONTEXT_RECENT_TURNS_KEPT,
            final_tokens,
            budget_tokens,
        )
    return final


async def _run_tool_use_loop(
    model: Any,
    messages: list[Any],
    ctx: InvokeContext,
    tools_config: dict[str, Any] | None,
) -> dict:
    """Drive the tool-use loop; return a state dict for LangGraph.

    Exits via one of three paths:
      (a) the LLM stops emitting tool_calls → success, return the final text.
      (b) a tool call decision is HALT → return early with halt_reason.
      (c) loop budget exhausted → halt with TOOL_LOOP_HALT_REASON.
    """
    task_id = ctx.task_id
    last_response: Any = None
    # Kanban #1717 — resolve the compaction budget once per loop (env is
    # stable for the lifetime of one task).
    context_budget = _resolve_context_token_budget()

    for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
        # Compact BEFORE invoking — trims older tool-result payloads so the
        # full re-sent history stays within budget. Reassign: the call
        # returns a new list (head + retained turns), preserving tool_call
        # pairing by construction. See `_compact_messages`.
        messages = _compact_messages(messages, context_budget)
        response = await _ainvoke_model(model, messages)
        last_response = response
        messages.append(response)
        # response_start marks the index of `response` in messages. Used by
        # both the HALT branch and the loop-budget-exhausted return to slice
        # the whole current turn (response + ALL ToolMessages for this turn)
        # so no tool_call id is left without a paired ToolMessage. Kanban #1720 fix H-2.
        response_start = len(messages) - 1

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            # No more tool calls — the LLM has emitted its final answer.
            content = _stringify_content(response.content)
            return {
                "messages": [response],
                "final_result": content,
            }

        # Each tool call gets its own ToolMessage in the next turn. On HALT
        # we return early; on REJECT or AUTO_ALLOW we keep going.
        for tc_idx, tc in enumerate(tool_calls):
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "unknown")
            tc_args = tc_args or {}

            outcome = await _handle_one_tool_call(
                task_id=task_id,
                tool_name=tc_name,
                args=tc_args,
                ctx=ctx,
                tools_config=tools_config,
            )

            if outcome.halt_reason is not None:
                # HALT path — append a ToolMessage for the halted call, then
                # stub ALL remaining (unexecuted) tool_calls so the checkpoint
                # is well-formed. OpenAI/Anthropic 400 on orphaned tool_call
                # ids (no paired ToolMessage). Kanban #1720 fix H-1.
                _stub_payload = json.dumps(
                    {"success": False, "error_code": "halted_before_execution"}
                )
                halted_tm = ToolMessage(
                    content=outcome.tool_result.model_dump_json(),
                    tool_call_id=tc_id,
                )
                messages.append(halted_tm)
                # Build stub ToolMessages for every call AFTER the current one.
                for remaining_tc in tool_calls[tc_idx + 1 :]:
                    remaining_id = (
                        remaining_tc.get("id")
                        if isinstance(remaining_tc, dict)
                        else getattr(remaining_tc, "id", "unknown")
                    )
                    messages.append(
                        ToolMessage(content=_stub_payload, tool_call_id=remaining_id)
                    )
                # Slice from response_start to capture: response + any
                # ToolMessages for calls BEFORE the halt (already appended in
                # earlier iterations of the inner loop) + the halted TM +
                # post-halt stubs. This guarantees every tool_call id in
                # response has a paired ToolMessage. Kanban #1720 fix H-2.
                return {
                    "messages": messages[response_start:],
                    "halt_reason": outcome.halt_reason,
                    "final_result": (
                        f"Halted for review: {outcome.halt_reason}"
                    ),
                }

            messages.append(
                ToolMessage(
                    content=outcome.tool_result.model_dump_json(),
                    tool_call_id=tc_id,
                )
            )

    # Loop budget exhausted.
    # `last_response` had tool_calls (that's why the loop kept going) and its
    # ToolMessages were appended during the final iteration's inner for-loop
    # before the next outer iteration started. Return messages[response_start:]
    # to include both last_response and all its paired ToolMessages, so no
    # tool_call id is orphaned. Kanban #1720 fix H-2.
    logger.warning(
        "tool_loop_max_iterations exceeded: task_id=%s — halting", task_id
    )
    return {
        "messages": messages[response_start:] if last_response is not None else [],
        "halt_reason": TOOL_LOOP_HALT_REASON,
        "final_result": f"Halted: {TOOL_LOOP_HALT_REASON}",
    }


class _ToolCallOutcome:
    """Internal carrier — result of one tool-call handle pass.

    `halt_reason` non-None → the loop must exit early (HALT decision).
    `tool_result` is always populated (audit row gets one regardless).
    """

    __slots__ = ("tool_result", "halt_reason")

    def __init__(self, tool_result: ToolResult, halt_reason: str | None = None) -> None:
        self.tool_result = tool_result
        self.halt_reason = halt_reason


async def _handle_one_tool_call(
    task_id: int | None,
    tool_name: str | None,
    args: dict[str, Any],
    ctx: InvokeContext,
    tools_config: dict[str, Any] | None,
) -> _ToolCallOutcome:
    """Run the full per-tool-call sequence: permission, sandbox, invoke, audit.

    Every exit point through this function produces (a) a ToolResult that
    goes back to the LLM via a ToolMessage, and (b) an audit row. The
    INVARIANT — every invocation has a paired audit row — lives here.
    """
    # 1. Lookup. An unknown tool name is the LLM hallucinating; surface as
    #    an internal error so the LLM can try a different tool. No halt,
    #    no permission check (the tool we'd be gating doesn't exist).
    try:
        tool = GLOBAL_REGISTRY.get(tool_name) if tool_name else None
    except ToolNotFoundError:
        tool = None

    if tool is None:
        result = ToolResult(
            success=False,
            error_code="unknown_tool",
            error_msg=(
                f"No tool registered with name {tool_name!r}. "
                f"Available: {GLOBAL_REGISTRY.list()}."
            ),
            retry_safe=True,
        )
        # No `tool` to audit against — record a synthetic minimal entry so
        # the timeline shows the hallucination. We use task_id directly via
        # an httpx POST in `record_tool_invocation`, which reads `tool.name`
        # and `tool.tier` — both would NoneAttribute on a missing tool. To
        # keep the audit invariant, we emit a log-only line for unknown
        # tools and skip the audit POST.
        logger.warning(
            "specialist_node: LLM hallucinated unknown tool %r (task=%s); "
            "returning error_code=unknown_tool to LLM (no audit row)",
            tool_name,
            task_id,
        )
        return _ToolCallOutcome(result)

    # 2. Permission gate.
    decision = check_permission(tools_config, tool)

    if decision is PermissionDecision.REJECT:
        result = ToolResult(
            success=False,
            error_code="tier_not_allowed",
            error_msg=(
                f"Tool {tool.name!r} is at tier {tool.tier.value!r}, which is "
                "not allowed by this project's tools_config. The LLM may not "
                "invoke it. (To enable, set tools_enabled=true and add the "
                "tier to auto_allow_tiers or halt_tiers.)"
            ),
            retry_safe=False,
        )
        await _audit(task_id, tool, args, result, decision)
        return _ToolCallOutcome(result)

    if decision is PermissionDecision.HALT:
        result = ToolResult(
            success=False,
            error_code="halt_for_review",
            error_msg=(
                f"Tool {tool.name!r} at tier {tool.tier.value!r} requires "
                "human review (halt_tiers). Task halted until a human "
                "approves or rejects this call."
            ),
            retry_safe=False,
        )
        await _audit(task_id, tool, args, result, decision)
        halt_reason = (
            f"tool_permission_review: {tool.name} tier={tool.tier.value}"
        )
        return _ToolCallOutcome(result, halt_reason=halt_reason)

    # decision == AUTO_ALLOW
    # 3. Pre-flight: fs-boundary check (only writes/destructive with path).
    violation = fs_boundary_check(tool, ctx, args)
    if violation is not None:
        await _audit(task_id, tool, args, violation, decision)
        return _ToolCallOutcome(violation)

    # 4. Invoke + post-flight sandbox guards.
    requested_timeout = args.get("timeout_s") if isinstance(args, dict) else None
    try:
        raw_result = await tool.invoke(args, ctx)
    except Exception as exc:
        # Tool.invoke already wraps internal errors, but defensive belt:
        # a misbehaving Tool subclass that violates the contract still
        # must not crash the loop.
        logger.exception(
            "specialist_node: tool.invoke raised (Tool contract violation): tool=%s",
            tool.name,
        )
        raw_result = ToolResult(
            success=False,
            error_code="internal_error",
            error_msg=f"Tool {tool.name!r} raised an unhandled exception: {exc!r}",
            retry_safe=False,
        )

    result = apply_sandbox(
        tool, raw_result, requested_timeout_s=requested_timeout
    )

    # 5. Audit. Always.
    await _audit(task_id, tool, args, result, decision)
    return _ToolCallOutcome(result)


async def _audit(
    task_id: int | None,
    tool: Any,
    args: dict[str, Any],
    result: ToolResult,
    decision: Any,
) -> None:
    """Audit-write with defensive task_id check + failure isolation.

    Calls `record_tool_invocation` (which is best-effort over httpx).
    A missing task_id (state didn't carry one — only happens in unit
    tests) skips the audit gracefully.
    """
    if task_id is None:
        logger.debug(
            "specialist_node: skipping audit (no task_id in state) — tool=%s",
            tool.name,
        )
        return
    try:
        await record_tool_invocation(task_id, tool, args, result, decision)
    except Exception:
        # record_tool_invocation already swallows httpx errors; this catch
        # is only for truly unexpected failures. Never let audit break the loop.
        logger.exception(
            "specialist_node: audit failure (continuing): task=%s tool=%s",
            task_id,
            tool.name,
        )


def _bind_tools_safely(
    model: Any, project_id: int | None, tools_config: dict[str, Any] | None
) -> Any | None:
    """`model.bind_tools(...)` with provider feature-flag + empty-registry guard.

    Returns the bound model or None if tools cannot be used (caller falls
    back to the no-tools single-shot path).

    Cases where we return None:
      - Provider is ollama AND http tools were excluded from the registry,
        but the underlying model doesn't support `bind_tools` at all.
      - The registry is empty (test fixtures that clear it).
      - tools_config is missing or kill-switch off → no tool would auto-allow
        anyway; skip bind_tools to save a round-trip.
      - bind_tools raises (provider really doesn't support tool-use).
    """
    if not tools_config or not tools_config.get("tools_enabled"):
        logger.info(
            "specialist_node: tools disabled or no config (project=%s) — "
            "skipping bind_tools, falling back to single-shot",
            project_id,
        )
        return None

    tools = GLOBAL_REGISTRY.all_tools_as_langchain()
    if not tools:
        logger.info(
            "specialist_node: empty tool registry — skipping bind_tools"
        )
        return None

    try:
        provider = resolve_provider()
    except Exception:
        provider = "?"

    try:
        return model.bind_tools(tools)
    except Exception as exc:
        logger.warning(
            "specialist_node: bind_tools failed on provider=%s (tool-use not "
            "supported on this provider?): %r — falling back to single-shot",
            provider,
            exc,
        )
        return None


async def _ainvoke_model(model: Any, messages: list[Any]) -> Any:
    """Async-invoke the model; fall back to sync `invoke` for test fakes.

    Real langchain BaseChatModel exposes `ainvoke`; the prompt-shape tests
    in `test_nodes_prompt.py` use a `SimpleNamespace(invoke=...)` fake
    that only has the sync method. We support both.
    """
    ainvoke = getattr(model, "ainvoke", None)
    if ainvoke is not None:
        return await ainvoke(messages)
    # Sync fallback — wrap in asyncio.to_thread? For tests, just call inline.
    return model.invoke(messages)


def _stringify_content(content: Any) -> str:
    """Coerce an LLM message's `content` field into a plain string.

    Anthropic returns a list of content blocks; OpenAI/Ollama return a
    plain string. Tests fake both shapes.
    """
    if isinstance(content, str):
        return content
    return str(content)


# ---------------------------------------------------------------------------
# Per-task config fetch — Kanban API client
# ---------------------------------------------------------------------------


# API base URL and project-id resolution delegated to config.py.
# _api_base() → config.resolve_api_base()
# _project_id_from_env() → config.resolve_project_id()


async def _fetch_tools_config(project_id: int | None) -> dict[str, Any] | None:
    """GET /api/projects/{id}.tools_config; return None on any failure.

    None → permission gate rejects everything (defensive default). The
    caller logs at INFO level on the fallback path so ops can grep.
    """
    if project_id is None:
        logger.info("specialist_node: no project_id — tools_config=None")
        return None
    url = f"{resolve_api_base()}/api/projects/{project_id}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        logger.warning(
            "specialist_node: fetch tools_config failed (project=%s): %r",
            project_id,
            exc,
        )
        return None
    if resp.status_code != 200:
        logger.warning(
            "specialist_node: GET /api/projects/%s returned %d: %s",
            project_id,
            resp.status_code,
            resp.text[:200],
        )
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    cfg = body.get("tools_config")
    if cfg is None:
        # Legacy / hand-edited row pre-migration 0027. Treated by the gate
        # as kill-switch on — over-block beats under-block.
        logger.info(
            "specialist_node: project %s has tools_config=null (gate will reject)",
            project_id,
        )
    return cfg


def _resolve_paths(project_id: int | None) -> tuple[str | None, str | None]:
    """Return (working_path, repo_root) for the InvokeContext.

    `working_path` is the per-project sandbox root (drives fs-boundary).
    `repo_root` is the langgraph-container path the host worktree is
    bind-mounted to — defaults to `/repo` (the live compose default).

    For V1 the working_path is sourced from the env-var
    `LANGGRAPH_WORKING_PATH`. A future iteration could fetch it from
    `projects.working_path` via the api (similar to `_fetch_tools_config`).
    """
    working_path = os.getenv("LANGGRAPH_WORKING_PATH", "").strip() or None
    repo_root = os.getenv("LANGGRAPH_REPO_ROOT", "").strip() or "/repo"
    return working_path, repo_root


def make_stub_node(role_name: str):
    """Factory — returns a LangGraph node function for a not-yet-implemented
    specialist.  Keeps the graph well-formed (every conditional-edge target
    exists and returns) so #852 can smoke-test routing for every role code
    before #853 fills these in.

    The returned function is identical in behavior to the four individual
    wrapper functions that previously existed; the response text is
    byte-identical.
    """
    msg = (
        f"{role_name} specialist not implemented yet "
        "(Kanban #850 ships backend only; full multi-provider rollout in #853)"
    )

    def _stub_node(state: AgentState) -> dict:  # noqa: ARG001
        return {
            "messages": [AIMessage(content=msg)],
            "final_result": msg,
        }

    _stub_node.__name__ = f"{role_name}_specialist_node"
    _stub_node.__qualname__ = f"{role_name}_specialist_node"
    return _stub_node


frontend_specialist_node = make_stub_node("frontend")
devops_specialist_node = make_stub_node("devops")
tester_specialist_node = make_stub_node("tester")
reviewer_specialist_node = make_stub_node("reviewer")


def general_node(state: AgentState) -> dict:
    """Fallback node for unknown / None roles. Sets halt_reason='error' so the
    poll loop (#852) surfaces this to the user instead of silently looping.

    HITL demo branch (Kanban #1073) — tasks whose brief starts with
    "HITL demo —" exercise the engine's interrupt / Command(resume=) loop
    against the live stack. Env-gated behind HITL_DEMO_ENABLED=1 so that in
    production (env unset / != "1") any user-supplied title with that prefix
    falls through to the halt path. The dev `docker-compose.yml` defaults
    HITL_DEMO_ENABLED=1; production deployments leave it unset. See WARN-2
    fix in Kanban #1107 (CWE-489 / OWASP A05) for the security rationale.

    AUDITOR retry demo branch (Kanban #1083, AC6) — tasks whose brief starts
    with "AUDITOR retry demo —" simulate a recoverable transient error on
    first pass (audit_retry_count=0 → halt_reason='transient_error',
    final_result=''), then succeed on retry (audit_retry_count>=1 →
    final_result='resolved on retry', halt_reason=None). The auditor's LLM
    classifies the first run as AUTO_RESOLVE → supervisor loops; second run
    triggers heuristic-bypass (final_result < 20 chars) → LLM classifies PASS.

    AUDITOR escalate demo branch (Kanban #1083, AC7) — tasks whose brief
    starts with "AUDITOR escalate demo —" emit halt_reason='ambiguous' on the
    first pass; the auditor's LLM classifies as ESCALATE → request_user_input
    fires → graph pauses for operator decision. On RESUME (audit_retry_count
    >= 1 after the auditor's operator-driven retry_with_X branch incremented
    it), the demo returns a clean final_result so the second pass auditor PASSes.
    """
    brief = state.get("brief", "")

    if (
        os.environ.get("HITL_DEMO_ENABLED") == "1"
        and brief.startswith("HITL demo —")
    ):
        # Env-gated demo branch (Kanban #1107 — WARN-2 fix). Without the env
        # var, this whole block is skipped and the task falls through to the
        # halt path below. Payload shape mirrors the Kanban QuestionPayload
        # schema (api/src/schemas/...): `question` is the required prompt
        # string, `options` is the list of valid answers (decision task).
        answer = request_user_input({
            "question": "Deploy to staging or prod?",
            "options": ["staging", "prod"],
        })
        return {
            "messages": [AIMessage(content=f"HITL demo answered: {answer}")],
            "final_result": f"decision resolved: {answer}",
        }

    if (
        os.environ.get("HITL_DEMO_ENABLED") == "1"
        and brief.startswith("AUDITOR retry demo —")
    ):
        # AC6 — recoverable retry demo. audit_retry_count is carried on state
        # by the auditor's AUTO_RESOLVE loop (state.py declares the field).
        # Env-gated (Kanban #1680 — safety fix): same HITL_DEMO_ENABLED guard
        # as the HITL demo branch — without the env var this block is skipped.
        retry_count = int(state.get("audit_retry_count") or 0)
        if retry_count == 0:
            return {
                "messages": [
                    AIMessage(content="AUDITOR retry demo: simulated transient error")
                ],
                "final_result": "",
                "halt_reason": "transient_error",
            }
        return {
            "messages": [
                AIMessage(content="AUDITOR retry demo: resolved on retry")
            ],
            "final_result": "resolved on retry",
            "halt_reason": None,
        }

    if (
        os.environ.get("HITL_DEMO_ENABLED") == "1"
        and brief.startswith("AUDITOR escalate demo —")
    ):
        # AC7 — escalate-to-HITL demo. On first pass (audit_retry_count=0)
        # emit halt_reason='ambiguous' so the auditor LLM classifies as
        # ESCALATE → request_user_input fires. On RESUME after the operator
        # picks 'retry_with_X', the auditor increments audit_retry_count and
        # clears halt_reason so the supervisor loops here with retry_count>=1;
        # emit a clean final_result so the second-pass auditor PASSes and the
        # task completes.
        # Env-gated (Kanban #1680 — safety fix): same HITL_DEMO_ENABLED guard
        # as the HITL demo branch — without the env var this block is skipped.
        retry_count = int(state.get("audit_retry_count") or 0)
        if retry_count == 0:
            return {
                "messages": [
                    AIMessage(
                        content="AUDITOR escalate demo: cannot decide between A and B"
                    )
                ],
                "final_result": "cannot decide between options A and B",
                "halt_reason": "ambiguous",
            }
        return {
            "messages": [
                AIMessage(
                    content="AUDITOR escalate demo: resolved by operator pick"
                )
            ],
            "final_result": "resolved by operator decision",
            "halt_reason": None,
        }

    role = state.get("assigned_role")
    msg = (
        f"general fallback: no specialist matched assigned_role={role!r}; "
        "halting for human review."
    )
    return {
        "messages": [AIMessage(content=msg)],
        "final_result": msg,
        "halt_reason": "error",
    }


# ---------------------------------------------------------------------------
# Auditor — Kanban #952
# ---------------------------------------------------------------------------
#
# Sits between specialists and END (or back-edge to supervisor on AUTO-RESOLVE).
# Locked design (Q1-Q6 all = A); see _scratch/auditor-design.md.
#
# Three verdicts:
#   - PASS         → END. `audit_report.action_taken = "auto_pass"` (heuristic
#                    skip) or `"llm_pass"` (LLM-evaluated clean run).
#   - AUTO_RESOLVE → supervisor (loop). Brief is appended with a NOTE so the
#                    specialist sees the adjustment. Retry counter increments
#                    BEFORE the loop edge; cap halts with 'auditor_giveup'.
#   - ESCALATE     → auditor calls request_user_input directly. Graph pauses
#                    via the same __interrupt__ mechanism HITL ships. On
#                    resume the answer drives one of:
#                      accept       → END (PASS-equivalent)
#                      retry_with_X → loop back to supervisor
#                      reject       → END with halt_reason='operator_rejected'
#
# Heuristic pre-filter (Q4=A): all-structural, no string-grep. Skip LLM when:
#   - state.halt_reason is None, AND
#   - state.final_result is a non-empty string longer than 20 chars, AND
#   - no ToolMessage in state.messages has a payload indicating tool error
#     (ToolResult JSON with success=false OR an 'error' key surfacing).
# If any condition fails → run the LLM.

AUDITOR_RETRY_CAP_DEFAULT = 3
"""Hardcoded cap on AUTO-RESOLVE retry loops for v1. Per-project tuning column
deferred (out of scope for #952 — future sibling task)."""

_AUDITOR_GIVEUP_REASON = "auditor_giveup"
"""halt_reason stamped when the retry cap is hit on an AUTO-RESOLVE verdict."""

_AUDITOR_MIN_FINAL_RESULT_CHARS = 20
"""Heuristic pre-filter threshold for `final_result` length."""

_AUDITOR_LLM_SYSTEM_PROMPT = (
    "You are an auditor agent. Given a task brief and a specialist's output, "
    "classify the outcome:\n\n"
    "- PASS: the specialist solved the task cleanly.\n"
    "- AUTO_RESOLVE: the specialist failed in a way that suggests a retry "
    "with a small adjustment would succeed (e.g., transient error, missing "
    "context the brief didn't supply, off-by-one in a tool call).\n"
    "- ESCALATE: the failure needs a human decision (ambiguity in the brief, "
    "missing approval, conflict between two valid approaches, "
    "irreversible-action confirmation).\n\n"
    "Respond with exactly ONE JSON object:\n"
    '{"verdict":"pass|auto_resolve|escalate",'
    '"severity":"info|warn|critical",'
    '"evidence":["..."],'
    '"action_taken":"...",'
    '"escalation_payload":null OR '
    '{"question":"...","options":["accept","retry_with_<label>","reject"]}}'
)


# UTC timestamp helper delegated to config.utc_now() — see config.py.


def _heuristic_clean(state: AgentState) -> bool:
    """Structural pre-filter — True iff the specialist's run looks clean.

    Q4=A locked. All three conditions must hold:
      1. halt_reason is None / absent.
      2. final_result is a string >20 chars.
      3. No ToolMessage in messages has a payload with success=False / error.
    """
    if state.get("halt_reason") is not None:
        return False
    final_result = state.get("final_result") or ""
    if not isinstance(final_result, str) or len(final_result.strip()) < _AUDITOR_MIN_FINAL_RESULT_CHARS:
        return False
    messages = state.get("messages") or []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        # ToolMessage.content is the ToolResult JSON dump (see
        # _run_tool_use_loop in this module). Parse it; on parse failure
        # treat as a tool-call result we can't reason about — safer to
        # invoke the LLM than to false-positive PASS.
        raw = msg.content
        if not isinstance(raw, str):
            return False
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("success") is False:
            return False
        if payload.get("error") or payload.get("error_code") or payload.get("error_msg"):
            return False
    return True


def _build_pass_report(*, llm_skipped: bool, retry_count: int, evidence: list[str]) -> dict[str, Any]:
    return {
        "verdict": "pass",
        "severity": "info",
        "evidence": evidence,
        "action_taken": "auto_pass" if llm_skipped else "llm_pass",
        "escalation_payload": None,
        "llm_skipped": llm_skipped,
        "audited_at": utc_now(),
        "retry_count_at_audit": retry_count,
    }


def _parse_llm_verdict(raw_text: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object from the LLM's raw response.

    Ollama / Anthropic / OpenAI may wrap the JSON in prose. We attempt a strict
    `json.loads` first; on failure we fall back to a balanced-brace scan that
    locates the first `{...}` substring and tries again. None on total failure
    — caller defaults to ESCALATE (fail safe; operator decides).
    """
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            return candidate
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    # Balanced-brace scan: take the first top-level {...} substring.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        return None
    try:
        candidate = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(candidate, dict):
        return None
    return candidate


def _normalise_llm_verdict(parsed: dict[str, Any], retry_count: int) -> dict[str, Any]:
    """Clamp the LLM's parsed JSON to the locked audit_report shape.

    Unknown verdict / severity / missing fields → default to safe values
    (verdict=escalate so the operator decides; severity=warn; evidence=[]).
    """
    verdict_raw = str(parsed.get("verdict", "")).lower().strip()
    if verdict_raw not in ("pass", "auto_resolve", "escalate"):
        verdict_raw = "escalate"
    severity_raw = str(parsed.get("severity", "")).lower().strip()
    if severity_raw not in ("info", "warn", "critical"):
        severity_raw = "warn"
    evidence_raw = parsed.get("evidence")
    if not isinstance(evidence_raw, list):
        evidence_raw = []
    evidence = [str(e)[:200] for e in evidence_raw][:5]
    action = str(parsed.get("action_taken") or _default_action_for(verdict_raw))
    escalation_payload = parsed.get("escalation_payload")
    if verdict_raw == "escalate" and not isinstance(escalation_payload, dict):
        escalation_payload = {
            "question": (
                "Auditor flagged this task for human review; please choose:"
            ),
            "options": ["accept", "retry_with_adjustment", "reject"],
        }
    elif verdict_raw != "escalate":
        escalation_payload = None
    return {
        "verdict": verdict_raw,
        "severity": severity_raw,
        "evidence": evidence,
        "action_taken": action,
        "escalation_payload": escalation_payload,
        "llm_skipped": False,
        "audited_at": utc_now(),
        "retry_count_at_audit": retry_count,
    }


def _default_action_for(verdict: str) -> str:
    if verdict == "pass":
        return "llm_pass"
    if verdict == "auto_resolve":
        return "retry_with_adjustment"
    return "hitl_escalate"


def _build_specialist_excerpt(state: AgentState) -> str:
    """Compact view of the specialist's output for the LLM prompt — caps at
    ~800 chars (Q4 ollama context budget). Includes final_result + the last
    three messages' text content."""
    chunks: list[str] = []
    final_result = (state.get("final_result") or "").strip()
    if final_result:
        chunks.append(f"final_result: {final_result[:500]}")
    messages = state.get("messages") or []
    tail = messages[-3:]
    for m in tail:
        kind = m.__class__.__name__
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        chunks.append(f"[{kind}] {str(content)[:200]}")
    return "\n".join(chunks)[:800]


async def auditor_node(state: AgentState) -> dict[str, Any]:
    """Classify the specialist's output → PASS / AUTO_RESOLVE / ESCALATE.

    Flow:
      1. Heuristic pre-filter (Q4=A). Clean → emit PASS verdict, skip LLM.
      2. Otherwise build a small prompt + invoke `make_chat_model()`.
      3. Parse the LLM's JSON; on malformed → default to ESCALATE (fail safe).
      4. If verdict == AUTO_RESOLVE and retry_count >= cap → emit halt_reason
         'auditor_giveup' instead of looping forever. Conditional edge
         (route_from_auditor) sees the giveup state and routes to END.
      5. If verdict == ESCALATE → call `request_user_input` with the
         escalation_payload. On resume the answer string drives the
         post-resume routing (`auditor_resolve` → END or back to supervisor).
      6. Always populate `state.audit_report` (the worker writes it to
         tasks.audit_report on finalize).
    """
    retry_count = int(state.get("audit_retry_count") or 0)
    task_id = state.get("task_id")

    # 1) Heuristic pre-filter (Q4=A).
    if _heuristic_clean(state):
        final_result = state.get("final_result") or ""
        excerpt = final_result.strip()[:200]
        report = _build_pass_report(
            llm_skipped=True,
            retry_count=retry_count,
            evidence=[f"clean run; final_result={excerpt!r}"],
        )
        logger.info(
            "auditor: task=%s verdict=pass (heuristic skip)", task_id
        )
        return {
            "audit_verdict": "pass",
            "audit_report": report,
            "messages": [
                SystemMessage(content=f"auditor: verdict=pass (heuristic skip) task_id={task_id}")
            ],
        }

    # 2) LLM path.
    brief = state.get("brief", "")
    excerpt = _build_specialist_excerpt(state)
    user_prompt = (
        f"Task brief:\n{brief[:500]}\n\n"
        f"Specialist output (final_result + last 3 messages):\n{excerpt}"
    )

    raw_text = ""
    try:
        model = make_chat_model()
        # Kanban #1116 — same safety-prelude wrap as the backend specialist.
        messages: list[Any] = [
            SystemMessage(content=build_system_message(_AUDITOR_LLM_SYSTEM_PROMPT)),
            HumanMessage(content=user_prompt),
        ]
        ainvoke = getattr(model, "ainvoke", None)
        if ainvoke is not None:
            response = await ainvoke(messages)
        else:
            response = model.invoke(messages)
        raw_text = _stringify_content(getattr(response, "content", ""))
    except Exception as exc:
        # LLM-side failure → fail safe to ESCALATE.
        logger.warning(
            "auditor: LLM invoke failed for task=%s (%r); defaulting to escalate",
            task_id,
            exc,
        )
        raw_text = ""

    parsed = _parse_llm_verdict(raw_text)
    if parsed is None:
        # Malformed JSON / empty response → fail safe to ESCALATE.
        logger.warning(
            "auditor: LLM returned malformed response for task=%s; defaulting to escalate",
            task_id,
        )
        report = {
            "verdict": "escalate",
            "severity": "warn",
            "evidence": ["auditor LLM returned malformed response"],
            "action_taken": "hitl_escalate",
            "escalation_payload": {
                "question": "Auditor LLM returned malformed output; please decide:",
                "options": ["accept", "retry_with_adjustment", "reject"],
            },
            "llm_skipped": False,
            "audited_at": utc_now(),
            "retry_count_at_audit": retry_count,
        }
    else:
        report = _normalise_llm_verdict(parsed, retry_count)

    verdict = report["verdict"]

    # 3) AUTO_RESOLVE retry cap. Q6=A: hardcoded constant. Check BEFORE
    # emitting the verdict so cap-hit halts immediately.
    if verdict == "auto_resolve":
        if retry_count >= AUDITOR_RETRY_CAP_DEFAULT:
            logger.info(
                "auditor: task=%s auto_resolve at cap (%d); halting with %s",
                task_id,
                retry_count,
                _AUDITOR_GIVEUP_REASON,
            )
            # Re-tag the report so the action_taken reflects the giveup.
            report["action_taken"] = _AUDITOR_GIVEUP_REASON
            return {
                "audit_verdict": "auto_resolve",
                "audit_report": report,
                "halt_reason": _AUDITOR_GIVEUP_REASON,
                "messages": [
                    SystemMessage(
                        content=(
                            f"auditor: verdict=auto_resolve at cap "
                            f"({retry_count}/{AUDITOR_RETRY_CAP_DEFAULT}); "
                            f"halting with halt_reason={_AUDITOR_GIVEUP_REASON}"
                        )
                    )
                ],
            }
        # Under the cap → increment and loop. The conditional edge
        # `route_from_auditor` reads the incremented count; the supervisor
        # sees the appended NOTE on next iteration.
        new_count = retry_count + 1
        adjustment_note = (
            f"\n\nNOTE (auditor retry {new_count}/{AUDITOR_RETRY_CAP_DEFAULT}): "
            f"previous attempt was flagged for retry — "
            f"{report.get('evidence', ['no evidence given'])[0] if report.get('evidence') else 'no evidence given'}"
        )
        new_brief = (state.get("brief") or "") + adjustment_note
        logger.info(
            "auditor: task=%s verdict=auto_resolve retry %d/%d",
            task_id,
            new_count,
            AUDITOR_RETRY_CAP_DEFAULT,
        )
        return {
            "audit_verdict": "auto_resolve",
            "audit_report": report,
            "audit_retry_count": new_count,
            "brief": new_brief,
            # Clear the specialist's halt_reason on loop so the supervisor
            # re-entry sees a fresh state. route_from_auditor reads
            # state.halt_reason to short-circuit to END; without this clear,
            # the auditor's auto_resolve loop is dead-on-arrival.
            "halt_reason": None,
            "messages": [
                SystemMessage(
                    content=(
                        f"auditor: verdict=auto_resolve retry={new_count}/"
                        f"{AUDITOR_RETRY_CAP_DEFAULT}; looping to supervisor"
                    )
                )
            ],
        }

    # 4) ESCALATE — call request_user_input directly. On first pass this
    # raises GraphInterrupt; on resume it returns the operator's answer
    # string which we use to drive the post-resume routing.
    if verdict == "escalate":
        payload = report.get("escalation_payload") or {
            "question": "Auditor flagged this task; please decide:",
            "options": ["accept", "retry_with_adjustment", "reject"],
        }
        logger.info(
            "auditor: task=%s verdict=escalate; emitting HITL interrupt", task_id
        )
        # request_user_input raises GraphInterrupt on first call, then on
        # resume it returns the answer string. We DO NOT catch the
        # interrupt — let LangGraph propagate it; the worker handles the
        # __interrupt__ marker in finalize. On resume the function returns
        # the answer; we then map it to the next action.
        answer = request_user_input(payload)
        return _apply_escalation_resume(state, report, answer, retry_count)

    # 5) PASS path (LLM agreed it's clean).
    logger.info("auditor: task=%s verdict=pass (LLM)", task_id)
    return {
        "audit_verdict": "pass",
        "audit_report": report,
        # Clear specialist's stale halt_reason. LLM-PASS only fires when
        # heuristic_clean returned False (halt_reason was set OR final_result
        # was too short). The auditor's PASS verdict overrides specialist's
        # halt — task is DONE clean. Without this, route_from_auditor sees
        # state.halt_reason still set and routes to END with halt body shape.
        "halt_reason": None,
        "messages": [
            SystemMessage(
                content=f"auditor: verdict=pass (LLM-evaluated) task_id={task_id}"
            )
        ],
    }


def _apply_escalation_resume(
    state: AgentState,
    report: dict[str, Any],
    answer: str,
    retry_count: int,
) -> dict[str, Any]:
    """Translate the operator's answer to the auditor's HITL escalate into a
    state update.

      - 'accept'         → PASS (END).
      - 'reject'         → END with halt_reason='operator_rejected'.
      - 'retry_with_<X>' → AUTO_RESOLVE; supervisor sees the X label as the
                           adjustment, retry counter increments. Cap applies.
    """
    normalised = (answer or "").strip().lower()
    audit_verdict_field = "escalate"  # carry the original verdict in the report
    report = dict(report)  # copy so we can mutate action_taken
    audited_at = utc_now()
    report["audited_at"] = audited_at

    if normalised == "accept":
        report["action_taken"] = "operator_accept"
        return {
            "audit_verdict": "pass",  # routes to END
            "audit_report": report,
            # Clear the specialist's halt_reason on PASS-equivalent accept so
            # the worker's finalize body lands on the DONE path
            # (halt_reason is None → process_status=5), not the BLOCKED branch.
            "halt_reason": None,
            "messages": [
                SystemMessage(content="auditor escalate resolved: accept → END")
            ],
        }

    if normalised == "reject":
        report["action_taken"] = "operator_reject"
        return {
            "audit_verdict": "escalate",  # but with halt_reason → END
            "audit_report": report,
            "halt_reason": "operator_rejected",
            "messages": [
                SystemMessage(
                    content="auditor escalate resolved: reject → halt_reason=operator_rejected"
                )
            ],
        }

    # Default / retry_with_X case → loop back to supervisor (capped).
    if retry_count >= AUDITOR_RETRY_CAP_DEFAULT:
        report["action_taken"] = _AUDITOR_GIVEUP_REASON
        return {
            "audit_verdict": "auto_resolve",
            "audit_report": report,
            "halt_reason": _AUDITOR_GIVEUP_REASON,
            "messages": [
                SystemMessage(
                    content=(
                        f"auditor escalate resolved: {normalised!r} but cap hit "
                        f"({retry_count}/{AUDITOR_RETRY_CAP_DEFAULT}); halting"
                    )
                )
            ],
        }
    new_count = retry_count + 1
    label = normalised
    if label.startswith("retry_with_"):
        label = label[len("retry_with_"):] or "operator_adjustment"
    note = (
        f"\n\nNOTE (auditor retry {new_count}/{AUDITOR_RETRY_CAP_DEFAULT}, "
        f"operator-driven): retry with {label!r}"
    )
    report["action_taken"] = f"retry_with_{label}"
    return {
        "audit_verdict": "auto_resolve",
        "audit_report": report,
        "audit_retry_count": new_count,
        "brief": (state.get("brief") or "") + note,
        # Clear the specialist's halt_reason on loop so the supervisor
        # re-entry sees a fresh state. Mirrors the auto_resolve under-cap
        # branch in auditor_node.
        "halt_reason": None,
        "messages": [
            SystemMessage(
                content=(
                    f"auditor escalate resolved: retry_with_{label} "
                    f"(retry {new_count}/{AUDITOR_RETRY_CAP_DEFAULT})"
                )
            )
        ],
    }


def route_from_auditor(state: AgentState) -> str:
    """Conditional-edge function — returns the next node's name.

    Possible outcomes:
      - 'supervisor' → AUTO_RESOLVE under the cap (loop).
      - END           → PASS, or AUTO_RESOLVE/ESCALATE that emitted a halt_reason
                        (giveup / operator_rejected).
                        Returned as the LangGraph constant `END` — the graph
                        builder maps this to the END sentinel.
    """
    # Halt of any kind short-circuits to END (worker reads halt_reason).
    if state.get("halt_reason") is not None:
        return "END"
    verdict = state.get("audit_verdict")
    if verdict == "auto_resolve":
        return "supervisor"
    # 'pass' or anything else → END.
    return "END"
