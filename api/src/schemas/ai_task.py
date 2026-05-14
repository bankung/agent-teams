"""Pydantic schemas for POST /api/tasks/ai-parse (Kanban #856).

The endpoint takes a free-text request and returns a PROPOSED set of
TaskCreate fields — extracted by an LLM. The endpoint does NOT create a row;
the FE (Kanban #857) presents the proposal in a pre-fill form before the
user confirms via the existing POST /api/tasks.

Field choices vs TaskCreate:
- `ProposedTask` is a SUBSET — only fields the LLM can plausibly extract from
  free text. Lifecycle / recurrence / template / sort_order / blocked_by-as-id
  inference cannot be done reliably from a 1-sentence task description.
- The integer-code fields (`priority`, `assigned_role`) carry the same Literal
  constraints the LLM is instructed to emit. Out-of-range values fail at
  Pydantic-validation time → the router converts to a 422 with a clear
  diagnostic (the LLM hallucinated an invalid code).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Wire enum for the LLM's output. Mirrors TaskTypeLiteral in schemas/task.py.
ProposedTaskTypeLiteral = Literal["bug", "feature", "chore", "docs", "refactor"]

# Priority codes mirror TaskPriority.ALL (1..4). The LLM is instructed to
# return the integer code directly so the FE pre-fill maps 1:1 onto TaskCreate.
ProposedPriorityLiteral = Literal[1, 2, 3, 4]

# Role codes mirror TaskRole.ALL (1..5). `None` is the explicit "no signal"
# value — the LLM is instructed to return null when assigned_role is not
# inferable from the text rather than guessing.
ProposedRoleLiteral = Literal[1, 2, 3, 4, 5]


class ParseRequest(BaseModel):
    """POST /api/tasks/ai-parse request body."""

    model_config = ConfigDict(extra="forbid")

    # min_length=1 makes empty / whitespace-only-empty land as 422 directly
    # through Pydantic validation (no router-side branch). max_length is a
    # defensive ceiling to bound the LLM prompt size + cost — a typical
    # description is under 200 chars; 2000 covers a long paragraph.
    text: str = Field(min_length=1, max_length=2000)


class ProposedTask(BaseModel):
    """The LLM's structured proposal — subset of TaskCreate fields.

    `extra='forbid'` rejects unknown keys at 422 (the LLM should not invent
    fields). Tool-use / structured-output JSON schemas are derived from this
    class via `model_json_schema()`.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        description="Short noun-phrase task title (under ~80 chars).",
    )
    description: str = Field(
        description=(
            "Free-text description. May echo the original request or a "
            "cleaned / clarified version."
        ),
    )
    task_type: ProposedTaskTypeLiteral = Field(
        description=(
            "One of 'bug' (defect / crash / regression), 'feature' (new "
            "capability), 'chore' (housekeeping / config), 'docs' "
            "(documentation), 'refactor' (internal restructure with no "
            "behavior change). Default to 'feature' if uncertain."
        ),
    )
    priority: ProposedPriorityLiteral = Field(
        description=(
            "Integer code: 1=low, 2=normal, 3=high, 4=urgent. Default to "
            "2 if uncertain. 'urgent' is reserved for production blockers "
            "/ outages; 'high' is the right code for 'high priority' "
            "phrasing in user input."
        ),
    )
    assigned_role: ProposedRoleLiteral | None = Field(
        default=None,
        description=(
            "Integer role code or null when not inferable. Mappings: "
            "1=frontend (UI / client / browser), 2=backend (API / server "
            "/ database), 3=devops (deploy / infra / docker), 4=qa "
            "(testing / verification), 5=reviewer (code review)."
        ),
    )
    blocked_by: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Numeric task ID that blocks this task (set when the user "
            "explicitly mentions 'blocked by #N' or similar). Null when "
            "no blocker is mentioned."
        ),
    )


class ParseResponse(BaseModel):
    """POST /api/tasks/ai-parse response body.

    Single-wrap so future fields (e.g. `warnings`, `cost_usd`, `model`) can
    be added without breaking the FE consumer's `data.proposed.*` access
    pattern.
    """

    proposed: ProposedTask
