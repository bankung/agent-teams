"""Pydantic schemas for the `tasks` table.

Integer code fields (status, priority, assigned_role) are validated against
`src.constants` ALL tuples — keeps the API in lockstep with the DB CHECK
constraints and the standards doc.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.constants import TaskPriority, TaskRole, TaskStatus

StatusCode = Annotated[int, Field(description="tasks.status — see TaskStatus.ALL")]
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

    - `field_label`: name shown in error messages (e.g. "status").
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
    status: StatusCode = TaskStatus.TODO
    priority: PriorityCode = TaskPriority.NORMAL
    assigned_role: RoleCode | None = None

    _check_status = field_validator("status")(
        _make_code_validator("status", TaskStatus.ALL, required=True)
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
    router on status transitions — clients should not set them directly. They
    are accepted here only for explicit overrides (e.g., backfill scripts).

    Missing-key vs explicit-null are different in PATCH semantics — that
    distinction is enforced at the router via `model_dump(exclude_unset=True)`.
    """

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: int | None = None
    priority: int | None = None
    assigned_role: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    _check_status = field_validator("status")(
        _make_code_validator("status", TaskStatus.ALL, required=False)
    )
    _check_priority = field_validator("priority")(
        _make_code_validator("priority", TaskPriority.ALL, required=False)
    )
    _check_role = field_validator("assigned_role")(
        _make_code_validator(
            "assigned_role", TaskRole.ALL, required=False, null_phrase="NULL or "
        )
    )


class TaskRead(BaseModel):
    """Full task row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str
    description: str | None
    status: int
    priority: int
    assigned_role: int | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
