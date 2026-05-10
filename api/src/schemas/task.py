"""Pydantic schemas for the `tasks` table.

Integer code fields (process_status, priority, assigned_role) are validated against
`src.constants` ALL tuples — keeps the API in lockstep with the DB CHECK constraints
and the standards doc.

Note: `process_status` is the 1..5 lifecycle code (renamed from `status` by the
2026-05-08 soft-delete migration). The bare `status` name is now reserved for the
uniform 0/1 soft-delete flag — and is intentionally NOT exposed in any public
schema; clients call `DELETE /api/tasks/{id}` to soft-delete.

`assigned_role` is no longer guarded by a DB CHECK — app-layer validation against
the active project's team roster is the only constraint. The Pydantic validator
still rejects values outside the dev roster (1..5) for now; widening to per-team
roster logic is a Phase 3 follow-up (frontend will pick from a roster picker).

V3+ T1 (Kanban #706, 2026-05-10): added `task_kind` + recurrence template
fields. Cross-table validators (cron syntax, IANA TZ, template completeness)
fire at the schema layer; the kind/run_mode constraint is in
`src/services/task_kind.py` (cross-table → service layer).
"""

from __future__ import annotations

import zoneinfo
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.constants import TaskKind, TaskPriority, TaskRole, TaskRunMode, TaskStatus

# Wire-level enum for tasks.run_mode. Stays in lockstep with TaskRunMode.ALL via
# the import-time guard at the bottom of this module — same pattern as
# schemas/project.py (TeamCode <-> ProjectTeam.ALL).
TaskRunModeLiteral = Literal["manual", "auto_pickup", "auto_headless"]

# V3+ T1 (Kanban #706): wire-level enum for tasks.task_kind. Stays in lockstep
# with TaskKind.ALL via the import-time guard at the bottom of this module.
TaskKindLiteral = Literal["ai", "human"]

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


def _validate_cron_rule(v: str | None) -> str | None:
    """Validate that v parses as a cron string. None is allowed (only required
    when is_template=true — enforced by the model_validator below)."""
    if v is None:
        return None
    if not croniter.is_valid(v):
        raise ValueError(f"recurrence_rule is not a valid cron expression: {v!r}")
    return v


def _validate_timezone(v: str | None) -> str | None:
    """Validate that v is a known IANA timezone. None is allowed (the column is
    NOT NULL with DEFAULT 'UTC' — Pydantic only sees user-supplied values)."""
    if v is None:
        return None
    if v not in zoneinfo.available_timezones():
        raise ValueError(f"recurrence_timezone is not a valid IANA timezone: {v!r}")
    return v


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
    # Step 2 (Kanban #481/#483) — Kanban-driven AI execution mode. Default
    # 'manual' matches the DB DEFAULT; cross-table consent check (auto_headless)
    # lives in src/services/run_mode.py and fires in router POST/PATCH.
    run_mode: TaskRunModeLiteral = TaskRunMode.MANUAL
    # V3+ T1 (Kanban #706) — task_kind discriminates AI vs human work. Default
    # 'human' matches the DB DEFAULT; cross-table validator (HUMAN ↔ MANUAL)
    # lives in src/services/task_kind.py.
    task_kind: TaskKindLiteral = TaskKind.HUMAN
    # V3+ T1 (Kanban #706) — recurrence template fields. is_template=true
    # requires both recurrence_rule + next_fire_at (model_validator below).
    is_template: bool = False
    recurrence_rule: str | None = Field(default=None, max_length=255)
    recurrence_timezone: str = Field(default="UTC", max_length=64)
    next_fire_at: datetime | None = None
    # System-managed lineage pointer — set by the T2 scheduler when it spawns
    # a child from a template. ACCEPTED on POST (so the scheduler can use the
    # public endpoint for audit-trail consistency); REJECTED on PATCH (V1
    # forbids re-parenting lineage). Optional + ge=1 so regular user POSTs
    # default to None.
    spawned_from_task_id: int | None = Field(default=None, ge=1)

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
    _check_recurrence_rule = field_validator("recurrence_rule")(_validate_cron_rule)
    _check_recurrence_timezone = field_validator("recurrence_timezone")(
        _validate_timezone
    )

    @model_validator(mode="after")
    def _check_template_completeness(self) -> "TaskCreate":
        """A template (is_template=true) MUST carry both a cron rule and a
        next_fire_at. DB CHECK ck_tasks_template_recurrence_complete enforces
        the same invariant — this validator gives the friendly 422 ahead of the
        IntegrityError 400 fallback."""
        if self.is_template and (
            self.recurrence_rule is None or self.next_fire_at is None
        ):
            raise ValueError(
                "is_template=true requires recurrence_rule and next_fire_at"
            )
        return self


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
    # Step 2 (Kanban #481/#483). PATCH-able — unlike parent_task_id, run_mode
    # CAN be modified after creation (e.g., flipping a task from manual to
    # auto_pickup once the queue runner ships). Cross-table consent check fires
    # on the resolved final value in the router.
    run_mode: TaskRunModeLiteral | None = None
    # V3+ T1 (Kanban #706). PATCH-able — task_kind can flip post-creation
    # (e.g., reclassifying ai → human). Cross-table validator (HUMAN ↔ MANUAL)
    # fires on the resolved final values in the router.
    task_kind: TaskKindLiteral | None = None
    # V3+ T1 (Kanban #706). Recurrence template fields PATCH-able for now —
    # T2 scheduler may need to advance next_fire_at programmatically. Cron +
    # TZ field validators reuse the TaskCreate ones.
    is_template: bool | None = None
    recurrence_rule: str | None = Field(default=None, max_length=255)
    recurrence_timezone: str | None = Field(default=None, max_length=64)
    next_fire_at: datetime | None = None
    # spawned_from_task_id is NOT modifiable post-creation — V1 forbids
    # re-parenting lineage (mirror of parent_task_id rejection). The field is
    # declared so we can REJECT it explicitly; explicit-null is treated
    # identically to a non-null value.
    spawned_from_task_id: int | None = Field(default=None, ge=1)

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
    _check_recurrence_rule = field_validator("recurrence_rule")(_validate_cron_rule)
    _check_recurrence_timezone = field_validator("recurrence_timezone")(
        _validate_timezone
    )

    @model_validator(mode="after")
    def _reject_parent_task_id(self) -> "TaskUpdate":
        if "parent_task_id" in self.model_fields_set:
            raise ValueError(
                "parent_task_id cannot be modified — re-parenting is not supported in V1"
            )
        return self

    @model_validator(mode="after")
    def _reject_spawned_from_task_id(self) -> "TaskUpdate":
        """V3+ T1 (Kanban #706): spawned_from_task_id is a system-managed
        lineage pointer — settable by the T2 scheduler on POST, NEVER editable
        post-creation. Mirror of parent_task_id rejection."""
        if "spawned_from_task_id" in self.model_fields_set:
            raise ValueError(
                "spawned_from_task_id cannot be modified — re-parenting lineage "
                "is not supported in V1"
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
    run_mode: TaskRunModeLiteral
    # V3+ T1 (Kanban #706) — new fields added 2026-05-10. Migration 0007's
    # server_defaults backfill existing rows: task_kind='human', is_template=false,
    # recurrence_timezone='UTC'; nullable fields default to None.
    task_kind: TaskKindLiteral
    is_template: bool
    recurrence_rule: str | None
    recurrence_timezone: str
    next_fire_at: datetime | None
    spawned_from_task_id: int | None


# Sanity: the Literal stays in lockstep with src.constants.TaskRunMode.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
# Mirrors the TeamCode <-> ProjectTeam.ALL guard in schemas/project.py.
if set(TaskRunModeLiteral.__args__) != set(TaskRunMode.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"TaskRunModeLiteral {TaskRunModeLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TaskRunMode.ALL {TaskRunMode.ALL!r}"
    )

# V3+ T1 (Kanban #706) — same lockstep guard for TaskKindLiteral.
if set(TaskKindLiteral.__args__) != set(TaskKind.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"TaskKindLiteral {TaskKindLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TaskKind.ALL {TaskKind.ALL!r}"
    )
