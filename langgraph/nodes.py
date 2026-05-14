"""Graph nodes — supervisor + specialists.

The supervisor's job (for #850) is minimal: stamp a system message into the
conversation announcing the routing decision, then let the conditional edge
function (`route_from_supervisor`) actually pick the next node. The supervisor
is intentionally dumb here because Kanban #852 (Kanban integration) will move
real routing logic into the API poll loop; this node is a placeholder that
keeps the graph topology honest.

`backend_specialist_node` is the one real node for AC1 — it constructs the LLM
via `make_chat_model()`, runs a single inference over the brief, and writes
back to `final_result`. The other specialist stubs return a canned "not
implemented" message so the graph can be exercised end-to-end for any role
without crashing.

All nodes return PARTIAL state dicts. LangGraph merges them via the reducer
declared on each TypedDict field (messages → add_messages; everything else →
last-write-wins).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from llm import make_chat_model
from state import AgentState

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


def backend_specialist_node(state: AgentState) -> dict:
    """Real specialist for AC1 — single-shot LLM call over the brief.

    Kept deliberately simple: no tool-use, no multi-turn. Kanban #853/#852 will
    extend this into a ReAct loop with tool access (Kanban API read/write,
    git, etc.). For #850 the AC only requires "at least ONE specialist node"
    that actually exercises the LLM path.
    """
    brief = state.get("brief", "")
    task_id = state.get("task_id", "?")
    model = make_chat_model()
    prompt = [
        SystemMessage(
            content=(
                "You are dev-backend, a FastAPI + PostgreSQL specialist. "
                "Given the task brief below, produce a concise plan or answer. "
                "Keep responses focused — no preamble, no apology."
            )
        ),
        HumanMessage(content=f"Task #{task_id}\n\nBrief:\n{brief}"),
    ]
    response = model.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    return {
        "messages": [response],
        "final_result": content,
    }


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
