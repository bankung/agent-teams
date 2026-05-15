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
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from audit import record_tool_invocation
from llm import make_chat_model, resolve_provider
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

# Compose-internal hostname for the Kanban API. Mirrors worker.py's default.
# Env-var override (LANGGRAPH_KANBAN_API_BASE) honoured at call time.
_DEFAULT_API_BASE = "http://api:8456"

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
    project_id = _project_id_from_env()
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

    initial_messages: list[Any] = [
        SystemMessage(content=_SYSTEM_PROMPT),
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

    for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
        response = await _ainvoke_model(model, messages)
        last_response = response
        messages.append(response)

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
        for tc in tool_calls:
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
                # HALT path — append a ToolMessage so the conversation is
                # well-formed in the checkpoint, then return early.
                messages.append(
                    ToolMessage(
                        content=outcome.tool_result.model_dump_json(),
                        tool_call_id=tc_id,
                    )
                )
                return {
                    "messages": [response, messages[-1]],
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
    logger.warning(
        "tool_loop_max_iterations exceeded: task_id=%s — halting", task_id
    )
    return {
        "messages": [last_response] if last_response is not None else [],
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


def _api_base() -> str:
    """Lazy lookup of the api base URL (mirrors worker.py / audit.py)."""
    return (
        os.getenv("LANGGRAPH_KANBAN_API_BASE", _DEFAULT_API_BASE).strip().rstrip("/")
    )


def _project_id_from_env() -> int | None:
    """Resolve LANGGRAPH_PROJECT_ID (the project this engine is bound to).

    Used as the fallback when the task row's project_id isn't in state.
    The worker injects task_id into state, but project_id isn't part of
    AgentState. The engine container is bound to ONE project, so the
    env-var is authoritative.
    """
    raw = os.getenv("LANGGRAPH_PROJECT_ID", "").strip()
    if not raw or not raw.isdigit():
        return None
    return int(raw)


async def _fetch_tools_config(project_id: int | None) -> dict[str, Any] | None:
    """GET /api/projects/{id}.tools_config; return None on any failure.

    None → permission gate rejects everything (defensive default). The
    caller logs at INFO level on the fallback path so ops can grep.
    """
    if project_id is None:
        logger.info("specialist_node: no project_id — tools_config=None")
        return None
    url = f"{_api_base()}/api/projects/{project_id}"
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


def _stub_specialist(role_name: str) -> dict:
    """Helper for the not-yet-implemented specialists. Keeps the graph
    well-formed (every conditional-edge target exists and returns) so #852 can
    smoke-test routing for every role code before #853 fills these in.
    """
    msg = (
        f"{role_name} specialist not implemented yet "
        "(Kanban #850 ships backend only; full multi-provider rollout in #853)"
    )
    return {
        "messages": [AIMessage(content=msg)],
        "final_result": msg,
    }


def frontend_specialist_node(state: AgentState) -> dict:
    return _stub_specialist("frontend")


def devops_specialist_node(state: AgentState) -> dict:
    return _stub_specialist("devops")


def tester_specialist_node(state: AgentState) -> dict:
    return _stub_specialist("tester")


def reviewer_specialist_node(state: AgentState) -> dict:
    return _stub_specialist("reviewer")


def general_node(state: AgentState) -> dict:
    """Fallback node for unknown / None roles. Sets halt_reason='error' so the
    poll loop (#852) surfaces this to the user instead of silently looping."""
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
