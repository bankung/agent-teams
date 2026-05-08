"""Pydantic schemas for the `tasks` table.

Integer code fields (process_status, priority, assigned_role) are validated against
`src.constants` ALL tuples — keeps the API in lockstep with the DB CHECK constraints
and the standards doc.

Note: `process_status` is the 1..5 lifecycle code (renamed from `status` by the
2026-05-08 soft-delete migration). The bare `status` name is now reserved for the
uniform 0/1 soft-delete flag — and is intentionally NOT exposed in any public
schema; clients call `DELETE /api/tasks/{id}` to soft-delete.

`assigned_role` is no longer guarded by a DB CHECK — app-layer validation against
the active project's lead roster is the only constraint. The Pydantic validator
still rejects values outside the dev roster (1..5) for now; widening to per-lead
roster logic is a Phase 3 follow-up (frontend will pick from a roster picker).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.constants import TaskPriority, TaskRole, TaskStatus

ProcessStatusCode = Annotated[
    int, Field(description="tasks.process_status — see TaskStatus.ALL")
]
PriorityCode = Annotated[int, Field(description="tasks.priority — see TaskPriority.ALL")]
RoleCode = Annotated[int, Field(description="tasks.assigned_role — see TaskRole.ALL")]


def _make_code_validator(
    field_label: str,
    allowed: tuple[int, ...],
    *,
    required: bool,
    null_phrase: str = "",
) -> Callable[[Any], int | None]:
    """Build a validator closure for an integer-code field.

    - `field_label`: name shown in error messages (e.g. "process_status").
    - `allowed`: the canonical ALL tuple from src.constants.
    - `required=True` → raise on None ("<label> is required"); used by TaskCreate.
    - `required=False` → return None on None; used by TaskUpdate (and for the
      nullable `assigned_role` column on TaskCreate).
    - `null_phrase`: when set (e.g. "NULL or "), prefixes the "must be one of"
      error so callers see "must be NULL or one of (...)" — preserves the
      existing assigned_role message.
    """
    error_prefix = f"{field_label} must be {null_phrase}one of {allowed}"

    def _validate(v: Any) -> int | None:
        if v is None:
            if required:
                raise ValueError(f"{field_label} is required")
            return None
        if v not in allowed:
            raise ValueError(f"{error_prefix}, got {v!r}")
        return int(v)

    return _validate


class TaskCreate(BaseModel):
    """Request body for POST /api/tasks."""

    project_id: int
    title: str = Field(min_length=1)
    description: str | None = None
    process_status: ProcessStatusCode = TaskStatus.TODO
    priority: PriorityCode = TaskPriority.NORMAL
    assigned_role: RoleCode | None = None
    # Optional parent for subtask creation (Kanban #238). None = top-level task.
    # Same-project + parent-exists checks happen in the router (need DB lookup).
    parent_task_id: int | None = Field(default=None, ge=1)

    _check_process_status = field_validator("process_status")(
        _make_code_validator("process_status", TaskStatus.ALL, required=True)
    )
    _check_priority = field_validator("priority")(
        _make_code_validator("priority", TaskPriority.ALL, required=True)
    )
    _check_role = field_validator("assigned_role")(
        _make_code_validator(
            "assigned_role", TaskRole.ALL, required=False, null_phrase="NULL or "
        )
    )


class TaskUpdate(BaseModel):
    """Request body for PATCH /api/tasks/{id} — all fields optional.

    Note: lifecycle timestamps (started_at, completed_at) are managed by the
    router on process_status transitions — clients should not set them directly.
    They are accepted here only for explicit overrides (e.g., backfill scripts).

    Soft-delete `status` is intentionally absent — DELETE /api/tasks/{id} is the
    public soft-delete path. If a client sends `{"status": 0}` in a PATCH body,
    Pydantic silently ignores the unknown field (default model_config behavior).

    Missing-key vs explicit-null are different in PATCH semantics — that
    distinction is enforced at the router via `model_dump(exclude_unset=True)`.
    """

    # Text-lock the silent-ignore behavior so a future Pydantic default change
    # can't flip it. `status` and any other unknown key drop on the floor.
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    process_status: int | None = None
    priority: int | None = None
    assigned_role: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Re-parenting is NOT allowed in V1 (Kanban #238 lock 2026-05-08).
    # The field is declared so we can REJECT it explicitly — `extra="ignore"` on
    # this schema would silently drop unknown keys, which is wrong for this one.
    # `model_fields_set` distinguishes "not provided" from "provided as None":
    # the validator only raises if the caller actually included the key.
    parent_task_id: int | None = Field(default=None, ge=1)

    _check_process_status = field_validator("process_status")(
        _make_code_validator("process_status", TaskStatus.ALL, required=False)
    )
    _check_priority = field_validator("priority")(
        _make_code_validator("priority", TaskPriority.ALL, required=False)
    )
    _check_role = field_validator("assigned_role")(
        _make_code_validator(
            "assigned_role", TaskRole.ALL, required=False, null_phrase="NULL or "
        )
    )

    @model_validator(mode="after")
    def _reject_parent_task_id(self) -> "TaskUpdate":
        if "parent_task_id" in self.model_fields_set:
            raise ValueError(
                "parent_task_id cannot be modified — re-parenting is not supported in V1"
            )
        return self


class TaskRead(BaseModel):
    """Full task row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    parent_task_id: int | None
    title: str
    description: str | None
    process_status: int
    priority: int
    assigned_role: int | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
