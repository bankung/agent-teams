"""Pydantic schemas for action templates (Kanban #1006).

Templates are YAML files stored under .claude/templates/actions/ and exposed
read-only via GET /api/templates/actions.  They carry opinionated defaults for
task_kind, task_type, priority, and acceptance_criteria so operators can
quickly file common action tasks with a single template_id reference.

No DB table — templates are version-controlled YAML; the loader caches them
for the app's lifetime (reset on restart).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Wire Literals — kept in lockstep with TaskKindLiteral / TaskTypeLiteral in
# schemas/task.py.  Duplicated here (rather than imported) to keep the schema
# self-contained and avoid a circular-import risk as the template loader is
# imported from the tasks router.
_TaskKindLiteral = Literal["ai", "human"]
_TaskTypeLiteral = Literal["bug", "feature", "chore", "docs", "refactor", "audit"]
_PriorityLiteral = Literal[1, 2, 3, 4]


class ActionTemplateRead(BaseModel):
    """Response shape for a single action template.

    `from_attributes=False` — instances are constructed from parsed YAML dicts,
    not from SQLAlchemy ORM rows.

    Fields mirror the locked YAML schema from AC1 (Kanban #1006).  Optional
    fields (`hints`, `suggested_attachments`) are omitted when absent in the
    YAML; the API always serializes them (defaulting to empty list).
    """

    model_config = ConfigDict(from_attributes=False)

    # `id` is the template's `name` field — used as the action_template_id key
    # on POST /api/tasks.  Kept as `id` on the wire (FE chip row renders
    # `template.id` as the value it POSTs back).
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=500)
    default_task_type: _TaskTypeLiteral
    default_task_kind: _TaskKindLiteral
    default_priority: _PriorityLiteral
    ac_outline: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    suggested_attachments: list[str] = Field(default_factory=list)
