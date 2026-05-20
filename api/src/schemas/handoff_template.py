"""Pydantic schemas for the `handoff_templates` table (Kanban #1004).

Operator-CRUD-able recipes for auto-handoff. Validates the element shapes the
ORM accepts; the router (`api/src/routers/handoff_templates.py`) uses these
on POST / PATCH, and the spawn service (`api/src/services/handoff_spawn.py`)
relies on the same constraints.

`title_pattern` validation: at minimum the pattern must:
  1. Be a syntactically-valid Python `str.format` template (parsed by
     `string.Formatter().parse(...)`).
  2. Reference `{parent_title}` so the operator's intent ("interpolate parent
     title into child title") is structurally visible.

Per-field caps mirror `tasks.title` / `tasks.description` (Kanban #1115 L18
prevention) — keeps the recipe-level data bounded against attacker-controlled
fluff exactly the way the tasks API already does.
"""

from __future__ import annotations

import string
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.constants import TaskKind, TaskRole, TaskType

# Wire enums — lockstep guard at module bottom (mirrors schemas/task.py pattern).
_TaskKindLiteral = Literal["ai", "human"]
_TaskTypeLiteral = Literal["bug", "feature", "chore", "docs", "refactor", "audit"]
_PriorityLiteral = Literal[1, 2, 3, 4]


def _validate_title_pattern(v: str) -> str:
    """Title pattern must be a valid Python `.format` string referencing `{parent_title}`.

    Two failures:
      - Malformed `.format` syntax (unbalanced braces, etc.) → 422.
      - Missing `{parent_title}` placeholder → 422 (signal-of-intent gate;
        an unparameterized title makes the template useless for auto-handoff).

    Other placeholders (e.g. `{parent_id}`) are accepted only if the spawn
    service supplies them — V1 supplies ONLY `parent_title`. Operators that
    reference unknown placeholders will get a 422 from the spawn hook at
    DONE-flip time (handled in services/handoff_spawn.py). Documented in
    api-contracts.md.
    """
    try:
        parsed = list(string.Formatter().parse(v))
    except ValueError as exc:
        raise ValueError(
            f"title_pattern is not a valid Python format string: {exc}"
        ) from exc

    refs = {field_name for (_lit, field_name, _spec, _conv) in parsed if field_name}
    if "parent_title" not in refs:
        raise ValueError(
            "title_pattern must reference {parent_title} so the child title "
            "interpolates from the parent"
        )
    return v


def _validate_role_range(v: int | None) -> int | None:
    """Mirror of TaskRole.RANGE_MIN..RANGE_MAX check used on tasks.assigned_role."""
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or not (
        TaskRole.RANGE_MIN <= v <= TaskRole.RANGE_MAX
    ):
        raise ValueError(
            f"default_assigned_role must be NULL or in range "
            f"{TaskRole.RANGE_MIN}..{TaskRole.RANGE_MAX}, got {v!r}"
        )
    return int(v)


# AC outline cap mirrors tasks.acceptance_criteria max_length=50 (Kanban #1115).
_AcOutlineEntry = Annotated[str, Field(min_length=1, max_length=1_000)]


class HandoffTemplateCreate(BaseModel):
    """Request body for POST /api/handoff-templates."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2_000)
    title_pattern: str = Field(min_length=1, max_length=512)
    task_kind: _TaskKindLiteral
    task_type: _TaskTypeLiteral
    default_priority: _PriorityLiteral = 3
    default_assigned_role: int | None = None
    ac_outline: list[_AcOutlineEntry] = Field(default_factory=list, max_length=50)
    carry_context_to_comment: bool = False
    project_id: int | None = Field(default=None, ge=1)

    _check_title_pattern = field_validator("title_pattern")(_validate_title_pattern)
    _check_role = field_validator("default_assigned_role")(_validate_role_range)


class HandoffTemplateUpdate(BaseModel):
    """Request body for PATCH /api/handoff-templates/{id} — all fields optional.

    Soft-delete `status` is intentionally absent — DELETE /api/handoff-templates/{id}
    is the public soft-delete path (parity with tasks / projects).
    `project_id` is intentionally NOT PATCH-able — re-scoping a template
    between projects would surprise consumers; the operator soft-deletes and
    re-creates instead.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2_000)
    title_pattern: str | None = Field(default=None, min_length=1, max_length=512)
    task_kind: _TaskKindLiteral | None = None
    task_type: _TaskTypeLiteral | None = None
    default_priority: _PriorityLiteral | None = None
    default_assigned_role: int | None = None
    ac_outline: list[_AcOutlineEntry] | None = Field(default=None, max_length=50)
    carry_context_to_comment: bool | None = None

    _check_title_pattern = field_validator("title_pattern")(
        lambda v: v if v is None else _validate_title_pattern(v)
    )
    _check_role = field_validator("default_assigned_role")(_validate_role_range)


class HandoffTemplateRead(BaseModel):
    """Full handoff template row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    title_pattern: str
    task_kind: _TaskKindLiteral
    task_type: _TaskTypeLiteral
    default_priority: _PriorityLiteral
    default_assigned_role: int | None
    ac_outline: list[str]
    carry_context_to_comment: bool
    project_id: int | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Lockstep guards — mirror schemas/task.py pattern. Catch drift between the
# locally-declared Literals and constants.py at import time.
# ---------------------------------------------------------------------------

if set(_TaskKindLiteral.__args__) != set(TaskKind.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"_TaskKindLiteral {_TaskKindLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TaskKind.ALL {TaskKind.ALL!r}"
    )

if set(_TaskTypeLiteral.__args__) != set(TaskType.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"_TaskTypeLiteral {_TaskTypeLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TaskType.ALL {TaskType.ALL!r}"
    )
