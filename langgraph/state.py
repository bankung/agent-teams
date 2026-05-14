"""AgentState — the TypedDict that flows through the supervisor graph.

`messages` carries the conversation; the `add_messages` reducer means each node
returns *new* messages and LangGraph appends them (rather than replacing the
whole list). Other fields are last-write-wins — nodes return a partial dict
keyed by the field they want to update.

`assigned_role` mirrors `api/src/constants.py::TaskRole` (1=frontend, 2=backend,
3=devops, 4=tester/QA, 5=reviewer). None means "no specific specialist" — the
supervisor routes to the `general` node in that case.

`halt_reason` is set by a node when human intervention is required or an
unrecoverable error occurred. Downstream (Kanban #852) the poll loop will
read this and pause the task; for #850 the field is reserved.

`total=False` so nodes can return partial updates without having to mention
every key.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

HaltReason = Literal["question", "decision", "error"] | None


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    task_id: int
    assigned_role: int | None  # 1..5 per TaskRole; None → general
    brief: str  # task description / spec — input to the specialist
    intermediate_results: dict[str, str]
    final_result: str
    halt_reason: HaltReason
