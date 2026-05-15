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
accepts NULL or any int in the team-range partition `1..20` (Kanban #926,
2026-05-15): 1..10 = dev team, 11..20 = novel team, 21+ reserved. Per-team
roster strictness (e.g. "code 13 invalid on a dev-team project") is a future
follow-up; today both teams share one numeric range.

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

from src.constants import (
    TaskInteractionKind,
    TaskKind,
    TaskPriority,
    TaskRole,
    TaskRunMode,
    TaskStatus,
    TaskType,
)

# Wire enum for tasks.run_mode; lockstep guard at module bottom
TaskRunModeLiteral = Literal["manual", "auto_pickup", "auto_headless"]

# Wire enum for tasks.task_kind (#706); lockstep guard at module bottom
TaskKindLiteral = Literal["ai", "human"]

# Wire enum for tasks.task_type (#803); lockstep guard at module bottom
TaskTypeLiteral = Literal["bug", "feature", "chore", "docs", "refactor"]

# Wire enum for tasks.interaction_kind (#830); lockstep guard at module bottom
InteractionKindLiteral = Literal["work", "question", "decision"]

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
    `required=True` raises on None; `required=False` returns None.
    `null_phrase` (e.g. "NULL or ") prefixes the "must be one of" error.
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


def _make_role_range_validator(
    field_label: str,
    range_min: int,
    range_max: int,
) -> Callable[[Any], int | None]:
    """Build a range-based validator for `tasks.assigned_role` (Kanban #926).

    NULL is always allowed (column is nullable; PATCH semantics also rely on
    None = no-touch). Non-null values must be integers in [range_min,
    range_max] inclusive — the range partition lives in `TaskRole`'s docstring
    (1..10 = dev, 11..20 = novel, etc.). Membership in `TaskRole.ALL` is
    NOT checked here: unnamed codes inside an existing range are reserved
    for the owning team to claim later without requiring a schema bump.

    The error string is part of the wire contract — pinned by test_validators.
    """
    error_msg_template = (
        f"{field_label} must be NULL or in range {range_min}..{range_max}"
    )

    def _validate(v: Any) -> int | None:
        if v is None:
            return None
        if not isinstance(v, int) or isinstance(v, bool) or not (range_min <= v <= range_max):
            raise ValueError(f"{error_msg_template}, got {v!r}")
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


class AcceptanceCriterion(BaseModel):
    """One row in `tasks.acceptance_criteria` (Kanban #797).

    Locked design 2026-05-12: structured JSONB array element with five fields.
    `text` is required (free-form, but min_length=1 — empty strings would be
    invisible-but-counted false positives at done-time). `status` defaults to
    `"pending"` so a freshly-filed criterion is opt-in to verification. The
    rest are optional metadata set when an agent / human verifies the item.

    Element shape is enforced HERE at the API boundary — the DB column is
    plain JSONB with no CHECK (same precedent as projects.paths / .stack /
    .config). Unknown keys are rejected via Pydantic's default model_config so
    a typoed field surfaces at 422 rather than silently landing in storage.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    status: Literal["pending", "passed", "failed", "na"] = "pending"
    verified_by: str | None = None
    verified_at: datetime | None = None
    notes: str | None = None


class SubagentModelEntry(BaseModel):
    """One entry in `tasks.subagent_models` (Kanban #887).

    Locked design 2026-05-13: append-only audit log of subagent spawns per task.
    `agent` is required (free-form name from agent frontmatter, min_length=1).
    `model` is constrained to the three Claude tiers so the log stays
    queryable by tier without free-form string matching.
    `at` is the UTC ISO-8601 spawn timestamp.

    `extra='forbid'` rejects unknown keys at 422 (parity with AcceptanceCriterion).
    PATCH semantics: full-replace (Lead accumulates, then sends the whole list).
    """

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1)
    model: Literal["opus", "sonnet", "haiku"]
    at: datetime


class AnswerHistoryEntry(BaseModel):
    """One entry in `QuestionPayload.answer_history` (Kanban #830).

    `value` and `answered_by` are required (free-form, min_length=1).
    `answered_at` is nullable — the Lead may record an answer before
    the timestamp is available. `is_valid` defaults True; set False
    to soft-invalidate a superseded answer. `invalidated_reason` is
    the human-readable note for why the answer was superseded.

    `extra='forbid'` rejects unknown keys at 422 (parity with
    AcceptanceCriterion).
    """

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)
    answered_at: datetime | None = None
    is_valid: bool = True
    invalidated_reason: str | None = None


class QuestionPayload(BaseModel):
    """Payload for `interaction_kind IN ('question', 'decision')` tasks
    (Kanban #830).

    `question` is required (min_length=1). `options` is an optional list
    of choice strings (used for 'decision' tasks — Option A / B / …).
    `answer_history` accumulates answers over time; append-only logic
    (Kanban #832) is NOT in this slice — PATCH semantics are full-replace
    (same as `acceptance_criteria`).

    `extra='forbid'` rejects unknown keys at 422.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    options: list[str] | None = None
    answer_history: list[AnswerHistoryEntry] = Field(default_factory=list)


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
    # 'ai' matches the DB DEFAULT (Kanban #858 — flipped from 'human' on
    # 2026-05-13). The router coerces task_kind='human' server-side when
    # interaction_kind IN ('question','decision') regardless of caller input,
    # so the schema default never lies about a question/decision body's
    # final stored value. Cross-table validator (HUMAN ↔ MANUAL) lives in
    # src/services/task_kind.py.
    task_kind: TaskKindLiteral = TaskKind.AI
    # Kanban #803 (2026-05-12) — task_type classifies the work. Default
    # 'feature' matches the DB DEFAULT. No cross-table validator — purely
    # classification metadata.
    task_type: TaskTypeLiteral = TaskType.FEATURE
    # V3+ T1 (Kanban #706) — recurrence template fields. is_template=true
    # requires both recurrence_rule + next_fire_at (model_validator below).
    is_template: bool = False
    recurrence_rule: str | None = Field(default=None, max_length=255)
    recurrence_timezone: str = Field(default="UTC", max_length=64)
    next_fire_at: datetime | None = None
    # V3+ T1 audit follow-up (Kanban #723) — one-shot scheduling. Mutually
    # exclusive with is_template=true (model_validator below + DB CHECK).
    scheduled_at: datetime | None = None
    # Kanban #750 (2026-05-11): "in-flight and stuck" flag — orthogonal to
    # process_status. Cross-state rule (is_pending=true REQUIRES
    # process_status=2) enforced in src/services/is_pending.py at POST + PATCH.
    is_pending: bool = False
    # System-managed lineage pointer — set by the T2 scheduler when it spawns
    # a child from a template. ACCEPTED on POST (so the scheduler can use the
    # public endpoint for audit-trail consistency); REJECTED on PATCH (V1
    # forbids re-parenting lineage). Optional + ge=1 so regular user POSTs
    # default to None.
    spawned_from_task_id: int | None = Field(default=None, ge=1)
    # Kanban #771 (2026-05-12): single-blocker dependency. None = unblocked;
    # non-null = points at the task that blocks this one. Same-project +
    # existence + not-self checks happen in the router (need DB lookup).
    # Direct cycle is structurally impossible on POST (new row has no id yet);
    # PATCH walks the chain for transitive cycle detection.
    blocked_by: int | None = Field(default=None, ge=1)
    # Kanban #772 (2026-05-12): within-lane manual sort key. Sparse-float
    # lexicographic ordering — NULL = "use created_at fallback for ordering"
    # for the lane. Optional on POST (most rows land NULL and only acquire
    # a value via POST /api/tasks/{id}/reorder or a direct PATCH). No range
    # validation: the sparse-float scheme is unbounded by design.
    sort_order: float | None = Field(default=None)
    # Kanban #785 (MVP-2): in-flight halt flag for full-auto Lead sessions.
    # Non-empty string = task is halted (auto-pickup query skips these);
    # None / absent = task runs normally. Rare-but-legal on POST (e.g., user
    # files a task that's pending external input). min_length=1 rejects "" at
    # 422; explicit null = unhalt (PATCH semantics, no _reject_explicit_null
    # validator). Parity with `description`, `working_path`, etc.
    halt_reason: str | None = Field(default=None, min_length=1)
    # Kanban #854 (2026-05-13): free-form rationale captured on a
    # process_status flip — most commonly when the user cancels a task
    # (process_status -> 6). Independent of the value: any PATCH may set
    # it. None / absent on POST → NULL in DB. min_length=1 rejects ""
    # at 422 (parity with halt_reason / description). Audit-trigger
    # snapshot captures the field automatically — no separate plumbing.
    status_change_reason: str | None = Field(default=None, min_length=1)
    # Kanban #797 (2026-05-12): optional structured exit-criteria array. Each
    # element validated by AcceptanceCriterion (text required, status Literal,
    # etc.). PATCH semantics for the field on TaskUpdate mirror description /
    # halt_reason: key-absent = unchanged, explicit null = clear, array =
    # replace whole array. On POST: None / absent = NULL in DB; [] = empty
    # array (legal but unusual); [...] = stored as-is.
    acceptance_criteria: list[AcceptanceCriterion] | None = None
    # Kanban #887 (2026-05-13): append-only subagent spawn log. NOT NULL DEFAULT
    # '[]' at the DB layer — POST default matches: empty list. Each element
    # validated by SubagentModelEntry (agent required, model Literal, at datetime).
    # Full-replace PATCH semantics (Lead accumulates, then sends the whole list).
    subagent_models: list[SubagentModelEntry] = Field(default_factory=list)
    # Kanban #830 (2026-05-12): interaction_kind discriminates agent-executed work
    # from user-interaction gate tasks created by the auto-run loop when ambiguity
    # is detected mid-task. 'work' is the default; 'question'/'decision' require
    # question_payload to be provided.
    interaction_kind: InteractionKindLiteral = TaskInteractionKind.WORK
    # Required when interaction_kind IN ('question','decision') — model_validator below.
    # PATCH semantics: full-replace (same as acceptance_criteria). Append-only logic
    # for answer_history lands in Kanban #832.
    question_payload: QuestionPayload | None = None
    # Free-form partial-work state stored by Lead when auto-run halts mid-task.
    # Used by re-spawn brief on resume. No shape constraint.
    resume_context: dict[str, Any] | None = None

    _check_process_status = field_validator("process_status")(
        _make_code_validator("process_status", TaskStatus.ALL, required=True)
    )
    _check_priority = field_validator("priority")(
        _make_code_validator("priority", TaskPriority.ALL, required=True)
    )
    # Kanban #926 (2026-05-15): widened from membership-in-(1..5) to range
    # 1..20 to admit novel team codes (11..20). DB CHECK was already dropped
    # 2026-05-08 → app-layer is the only gate; widening here is sufficient.
    _check_role = field_validator("assigned_role")(
        _make_role_range_validator(
            "assigned_role", TaskRole.RANGE_MIN, TaskRole.RANGE_MAX
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

    @model_validator(mode="after")
    def _check_scheduled_xor_template(self) -> "TaskCreate":
        """Kanban #723: scheduled_at (one-shot) and is_template=true are
        mutually exclusive. DB CHECK ck_tasks_scheduled_xor_template enforces
        the same invariant — this validator gives the friendly 422 ahead of
        the IntegrityError 400 fallback. Detail message MUST mention BOTH
        scheduled_at AND is_template (testable wire contract)."""
        if self.is_template and self.scheduled_at is not None:
            raise ValueError(
                "scheduled_at is incompatible with is_template=true "
                "(use recurrence_rule for templates)"
            )
        return self

    @model_validator(mode="after")
    def _check_question_payload_required(self) -> "TaskCreate":
        if self.interaction_kind in (TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION):
            if self.question_payload is None:
                raise ValueError(
                    "question_payload is required when interaction_kind is "
                    f"'question' or 'decision'"
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
    # Kanban #803 (2026-05-12). PATCH-able — task_type can be reclassified
    # post-creation (e.g., a "feature" being downgraded to "chore"). No
    # cross-table rule; Literal alone is the constraint.
    task_type: TaskTypeLiteral | None = None
    # V3+ T1 (Kanban #706). Recurrence template fields PATCH-able for now —
    # T2 scheduler may need to advance next_fire_at programmatically. Cron +
    # TZ field validators reuse the TaskCreate ones.
    is_template: bool | None = None
    recurrence_rule: str | None = Field(default=None, max_length=255)
    recurrence_timezone: str | None = Field(default=None, max_length=64)
    next_fire_at: datetime | None = None
    # V3+ T1 audit follow-up (Kanban #723) — PATCH-able. Set null to un-schedule;
    # set a new datetime to reschedule. Resolved-final XOR (is_template AND
    # scheduled_at) is enforced router-side because the validator alone can't
    # see the existing row's state on a one-field PATCH.
    scheduled_at: datetime | None = None
    # Kanban #750 (2026-05-11): PATCH-able. Explicit value (true / false) is
    # the user signal; absence (key not in payload) means don't touch.
    # Resolved-final cross-state check in routers/tasks.py pairs the resolved
    # is_pending with the resolved process_status.
    is_pending: bool | None = None
    # spawned_from_task_id is NOT modifiable post-creation — V1 forbids
    # re-parenting lineage (mirror of parent_task_id rejection). The field is
    # declared so we can REJECT it explicitly; explicit-null is treated
    # identically to a non-null value.
    spawned_from_task_id: int | None = Field(default=None, ge=1)
    # Kanban #771 (2026-05-12): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear / unblock the task (null IS meaningful —
    #                       column is nullable; lifts the blocker)
    #   - non-null int    → set / change the blocker (router validates
    #                       existence, same-project, not-self, no cycle)
    # No _reject_explicit_null validator — parity with description, halt_reason,
    # acceptance_criteria. Unlike parent_task_id / spawned_from_task_id,
    # re-blocking IS supported in V1 (whole point of the field).
    blocked_by: int | None = Field(default=None, ge=1)
    # Kanban #772 (2026-05-12): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear (NULL — falls back to created_at ordering)
    #   - non-null float  → set directly. Router runs the blocker-order
    #                       cross-row constraint after applying the value;
    #                       422 with "cannot be ordered before its blocker"
    #                       template on violation.
    # The POST /api/tasks/{id}/reorder endpoint is the user-facing API;
    # direct PATCH of sort_order is the escape hatch for "I know what value
    # I want" cases (smoke tests, bulk admin).
    sort_order: float | None = Field(default=None)
    # Kanban #785 (MVP-2): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear / unhalt the task (null IS meaningful)
    #   - empty string "" → 422 via min_length=1
    #   - non-empty       → set halt reason
    # No _reject_explicit_null validator — parity with `description`,
    # `working_path`, etc.
    halt_reason: str | None = Field(default=None, min_length=1)
    # Kanban #854 (2026-05-13): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear the reason (null IS meaningful)
    #   - empty string "" → 422 via min_length=1
    #   - non-empty       → set / overwrite the reason
    # No _reject_explicit_null validator — parity with halt_reason / description.
    # Most common use: paired with `{"process_status": 6}` on a cancel PATCH.
    status_change_reason: str | None = Field(default=None, min_length=1)
    # Kanban #797 (2026-05-12): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear the array (null IS meaningful — column is
    #                       nullable JSONB)
    #   - explicit array  → REPLACE the whole array (no element-merge; clients
    #                       must re-send the full list each PATCH). Atomic
    #                       single-item PATCH is intentionally NOT supported
    #                       (KISS — full array replace only).
    # Each element validated by AcceptanceCriterion (text required, status
    # Literal). No _reject_explicit_null validator — parity with description
    # and halt_reason.
    acceptance_criteria: list[AcceptanceCriterion] | None = None
    # Kanban #887 (2026-05-13): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit list   → REPLACE the whole array (full-replace; Lead
    #                       accumulates, then sends the whole list each PATCH)
    # NOT nullable: the DB column is NOT NULL DEFAULT '[]'. Explicit null on
    # PATCH is NOT meaningful (cannot clear to NULL — the column has no null
    # state). Omit the key to leave unchanged. Each element validated by
    # SubagentModelEntry (agent required, model Literal, at datetime).
    subagent_models: list[SubagentModelEntry] | None = None
    interaction_kind: InteractionKindLiteral | None = None
    question_payload: QuestionPayload | None = None
    resume_context: dict[str, Any] | None = None
    # Kanban #832: answer append for question/decision tasks.
    # When set, the router appends this entry (with is_valid=True + answered_at=now())
    # to the existing question_payload.answer_history. Does NOT replace the whole
    # question_payload. Only valid when interaction_kind IN ('question','decision').
    # None / absent = no append (standard PATCH semantics).
    new_answer: str | None = Field(default=None, min_length=1)
    # Kanban #832: who is submitting the answer. Defaults to 'user'.
    # Only used when new_answer is set.
    new_answer_by: str | None = Field(default=None, min_length=1)
    # Kanban #832: invalidate the last valid answer in answer_history.
    # When True, finds the last entry with is_valid=True and flips it to False
    # + sets invalidated_reason from invalidated_reason field below.
    # Task does NOT auto-flip to done — it remains a blocker.
    invalidate_last_answer: bool | None = None
    # Reason for invalidation — used when invalidate_last_answer=True.
    invalidated_reason: str | None = Field(default=None, min_length=1)

    _check_process_status = field_validator("process_status")(
        _make_code_validator("process_status", TaskStatus.ALL, required=False)
    )
    _check_priority = field_validator("priority")(
        _make_code_validator("priority", TaskPriority.ALL, required=False)
    )
    # Kanban #926 (2026-05-15): same range-validator as TaskCreate — see comment there.
    _check_role = field_validator("assigned_role")(
        _make_role_range_validator(
            "assigned_role", TaskRole.RANGE_MIN, TaskRole.RANGE_MAX
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

    @model_validator(mode="after")
    def _check_scheduled_xor_template_in_payload(self) -> "TaskUpdate":
        """Kanban #723: catch the both-fields-set-in-the-same-PATCH case at
        422. The resolved-final XOR (where the patch interacts with the
        existing row's state) is enforced in the router because the validator
        can't see the existing row on a one-field PATCH. Detail mentions BOTH
        scheduled_at AND is_template (testable wire contract)."""
        if self.is_template is True and self.scheduled_at is not None:
            raise ValueError(
                "scheduled_at is incompatible with is_template=true "
                "(use recurrence_rule for templates)"
            )
        return self

    @model_validator(mode="after")
    def _reject_explicit_null_recurrence_timezone(self) -> "TaskUpdate":
        """Kanban #714 MIN-3 (2026-05-11): the DB column is NOT NULL with
        DEFAULT 'UTC'. A PATCH body of `{"recurrence_timezone": null}` would
        otherwise reach the DB and surface as an IntegrityError 400. Reject
        the explicit-null at 422 with a clear actionable detail.

        Missing key (Field default = None, absent from `model_fields_set`) →
        skip; preserves PATCH "no key = no touch" semantics.

        Detail string is source-text-locked by the test pin — wire contract.
        """
        if (
            "recurrence_timezone" in self.model_fields_set
            and self.recurrence_timezone is None
        ):
            raise ValueError(
                "recurrence_timezone cannot be explicitly null — omit the key "
                "to leave the existing value, or send a valid IANA TZ string"
            )
        return self

    @model_validator(mode="after")
    def _reject_explicit_null_subagent_models(self) -> "TaskUpdate":
        if (
            "subagent_models" in self.model_fields_set
            and self.subagent_models is None
        ):
            raise ValueError(
                "subagent_models cannot be explicitly null — omit the key "
                "to leave the existing value, or send [] to clear"
            )
        return self

    @model_validator(mode="after")
    def _check_template_completeness(self) -> "TaskUpdate":
        """Kanban #714 MIN-1 (2026-05-11): mirror of TaskCreate's
        `_check_template_completeness`. Flipping `is_template=true` via PATCH
        without supplying BOTH `recurrence_rule` and `next_fire_at` would
        otherwise fall through to the DB CHECK
        `ck_tasks_template_recurrence_complete` 400. This validator fires the
        friendly 422 first.

        PATCH semantics: the validator can only see what's in the payload —
        not the existing row's values. So we fire only when:
          - `is_template=True` is in `model_fields_set` (explicit True), AND
          - EITHER `recurrence_rule` resolves to None (explicit-null or absent
            with the default), OR `next_fire_at` resolves to None.
        Bundled body with all three present and non-null → 200 (positive).
        PATCH of `{is_template: false}` alone → 200 (un-template flow).
        Absence of `is_template` from the payload → skip entirely.

        Detail message is byte-for-byte verbatim with TaskCreate so the wire
        contract is one source-text-locked string for both create + patch.
        """
        if "is_template" not in self.model_fields_set:
            return self
        if self.is_template is not True:
            # is_template=False (un-template flow) or explicit null → no check.
            return self
        if self.recurrence_rule is None or self.next_fire_at is None:
            raise ValueError(
                "is_template=true requires recurrence_rule and next_fire_at"
            )
        return self

    @model_validator(mode="after")
    def _check_question_payload_required(self) -> "TaskUpdate":
        if (
            "interaction_kind" in self.model_fields_set
            and self.interaction_kind in (TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION)
            and "question_payload" not in self.model_fields_set
            # Only fire when interaction_kind changes to question/decision AND
            # question_payload is not being supplied in the same PATCH.
            # The resolved-final check in the router handles cross-state PATCH
            # (e.g. PATCH interaction_kind='question' when question_payload already
            # exists in the DB).
        ):
            raise ValueError(
                "question_payload is required when interaction_kind is "
                "'question' or 'decision'"
            )
        return self

    @model_validator(mode="after")
    def _check_invalidate_needs_reason(self) -> "TaskUpdate":
        if (
            "invalidate_last_answer" in self.model_fields_set
            and self.invalidate_last_answer is True
            and self.invalidated_reason is None
        ):
            raise ValueError(
                "invalidated_reason is required when invalidate_last_answer=True"
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
    # Kanban #803 (2026-05-12) — backfilled to 'feature' on existing rows by
    # migration 0015's server_default.
    task_type: TaskTypeLiteral
    is_template: bool
    recurrence_rule: str | None
    recurrence_timezone: str
    next_fire_at: datetime | None
    spawned_from_task_id: int | None
    # Kanban #771 (2026-05-12) — single-blocker dependency. Backfilled to NULL
    # on existing rows by migration 0017's nullable=true. NULL = unblocked.
    blocked_by: int | None
    # Kanban #772 (2026-05-12) — within-lane manual sort key (sparse-float).
    # Backfilled to NULL on existing rows by migration 0018's nullable=true.
    # NULL = "use created_at fallback for ordering" — first reorder in the
    # lane materializes NULLs to floor floats. ORDER BY sort_order ASC
    # NULLS LAST, created_at ASC is the canonical lane-sort rule.
    sort_order: float | None
    # V3+ T1 audit follow-up (Kanban #723) — backfilled to NULL on existing rows.
    scheduled_at: datetime | None
    # Kanban #750 (2026-05-11) — backfilled to FALSE on existing rows by
    # migration 0011's server_default. Cross-state validator at
    # services/is_pending.py couples is_pending=true with process_status=2.
    is_pending: bool
    # Kanban #785 (MVP-2) — backfilled to NULL on existing rows by migration
    # 0013's nullable=true. Free-form string set by Lead at halt time per the
    # #787 decision matrix; NULL = task runs normally.
    halt_reason: str | None
    # Kanban #854 (2026-05-13) — free-form rationale captured on a process_status
    # flip (most commonly cancellation, ps=6). Backfilled to NULL on existing
    # rows by migration 0022's nullable=true. Audit-trigger snapshot includes it.
    status_change_reason: str | None
    # Kanban #797 (2026-05-12) — structured exit-criteria. Backfilled to NULL
    # on existing rows by migration 0014's nullable=true. AcceptanceCriterion
    # validates element shape on the way IN (TaskCreate / TaskUpdate); on the
    # way OUT we expose the stored shape — Pydantic re-validates each element
    # so a hand-edited corrupt row would 500 here rather than silently leak.
    acceptance_criteria: list[AcceptanceCriterion] | None
    # Kanban #887 (2026-05-13) — append-only subagent spawn log. Backfilled to
    # '[]' on existing rows by migration 0023's server_default. SubagentModelEntry
    # validates element shape on the way IN; on the way OUT we expose the stored
    # shape — Pydantic re-validates so a corrupt row would 500 rather than leak.
    # NOT NULL in the DB — always a list on the wire, never null.
    subagent_models: list[SubagentModelEntry]
    # Kanban #830 (2026-05-12) — backfilled to 'work' on existing rows by migration 0019.
    interaction_kind: InteractionKindLiteral
    # Kanban #830 — nullable JSONB. question_payload element shape validated by
    # QuestionPayload / AnswerHistoryEntry on the way IN. On the way OUT we expose
    # the stored shape. None = no question data; object = the structured payload.
    question_payload: QuestionPayload | None
    # Kanban #830 — free-form JSONB. Any | None at read time (no shape constraint).
    resume_context: dict[str, Any] | None


class NextAutorunResponse(BaseModel):
    """Response for GET /api/tasks/next-autorun (Kanban #833).

    Tells the headless auto-run loop what to do next:
    - next_task: the next work task to execute (if any)
    - resume_tasks: HALTED tasks whose blocker is now DONE (ready to re-run)
    - pending_questions: question/decision tasks awaiting user answer
    - blocked_count: total tasks currently blocked (any blocker not DONE)
    """

    next_task: TaskRead | None
    resume_tasks: list[TaskRead]
    pending_questions: list[TaskRead]
    blocked_count: int


class TaskReorder(BaseModel):
    """Request body for POST /api/tasks/{task_id}/reorder (Kanban #772).

    Anchor-based reorder spec. At LEAST one of `before_id` / `after_id` must
    be provided; both together pins the moved task between two anchors. The
    moved task, before_id, and after_id MUST all share the same
    `process_status` (same-lane invariant — enforced server-side).

    Semantics:
      - `before_id`: the task that should appear immediately AFTER the moved
        task post-reorder. The moved task's new `sort_order` lands JUST
        BELOW (smaller than) `before_id.sort_order`.
      - `after_id`: the task that should appear immediately BEFORE the moved
        task post-reorder. The moved task's new `sort_order` lands JUST
        ABOVE (larger than) `after_id.sort_order`.
      - Both → server averages: `new = (after.sort_order + before.sort_order) / 2`.
        Server does NOT validate they are currently adjacent — trust client.

    `extra='forbid'` rejects unknown keys at 422 (parity with TaskCreate).
    """

    model_config = ConfigDict(extra="forbid")

    before_id: int | None = Field(default=None, ge=1)
    after_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_one_anchor(self) -> "TaskReorder":
        if self.before_id is None and self.after_id is None:
            raise ValueError(
                "reorder requires at least one of before_id or after_id"
            )
        if (
            self.before_id is not None
            and self.before_id == self.after_id
        ):
            raise ValueError(
                "before_id and after_id cannot reference the same task"
            )
        return self


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

# Kanban #803 (2026-05-12) — same lockstep guard for TaskTypeLiteral.
if set(TaskTypeLiteral.__args__) != set(TaskType.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"TaskTypeLiteral {TaskTypeLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TaskType.ALL {TaskType.ALL!r}"
    )

# Kanban #830 (2026-05-12) — InteractionKindLiteral lockstep with TaskInteractionKind.ALL.
if set(InteractionKindLiteral.__args__) != set(TaskInteractionKind.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"InteractionKindLiteral {InteractionKindLiteral.__args__!r} drifted from "  # type: ignore[attr-defined]
        f"TaskInteractionKind.ALL {TaskInteractionKind.ALL!r}"
    )
