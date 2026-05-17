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

from typing import Annotated, Any, Literal, TypedDict

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
    # Kanban #1123 (L16, 2026-05-17) — sanitized snapshots of the prior
    # task.halt_reason / task.status_change_reason at pickup time. Already
    # passed through `agent_context_sanitizer.sanitize_for_agent_context`
    # by worker.py — SQL DDL/DML keywords redacted, capped at 500 chars.
    # Nodes that want to surface prior halt context in the LLM prompt MUST
    # read these fields (NOT raw task.* fields) — they are the only
    # injection-safe representation.
    prior_halt_reason: str
    prior_status_change_reason: str
    # Kanban #952 — in-graph auditor outputs. `audit_verdict` is the
    # conditional-edge selector after the auditor node runs: 'pass' → END,
    # 'auto_resolve' → supervisor (capped by retry counter), 'escalate' →
    # auditor's own HITL interrupt path. `audit_report` carries the structured
    # report dict the worker writes to tasks.audit_report on finalize.
    # `audit_retry_count` increments each time the auditor sends the task back
    # to supervisor via AUTO-RESOLVE; cap = AUDITOR_RETRY_CAP_DEFAULT.
    audit_verdict: Literal["pass", "auto_resolve", "escalate"] | None
    audit_report: dict[str, Any] | None
    audit_retry_count: int
