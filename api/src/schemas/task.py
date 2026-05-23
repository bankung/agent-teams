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
from decimal import Decimal
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
from src.schemas.notification import NotificationTarget

# Wire enum for tasks.run_mode; lockstep guard at module bottom
TaskRunModeLiteral = Literal["manual", "auto_pickup", "auto_headless"]

# Wire enum for tasks.task_kind (#706); lockstep guard at module bottom
TaskKindLiteral = Literal["ai", "human"]

# Wire enum for tasks.task_type — 'bug'/'feature'/'chore'/'docs'/'refactor'
# (#803) + 'audit' (#1211 GOV3); lockstep guard at module bottom.
TaskTypeLiteral = Literal["bug", "feature", "chore", "docs", "refactor", "audit"]

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

    # Kanban #1115 (2026-05-17, L18 prevention) — payload-size cap. Hammer-test
    # FINDING #10 showed the API accepted a 10MB description + 10000 AC items
    # with no size guard at any layer. 1000-char cap on text matches the
    # narrative-fit ceiling and gives plenty of room for verification notes.
    text: str = Field(min_length=1, max_length=1_000)
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


# ---------------------------------------------------------------------------
# Kanban #1007 — OptionItem: must precede QuestionPayload (used in options field)
# ---------------------------------------------------------------------------


class OptionItem(BaseModel):
    """A single selectable option in a `DecisionPayload` (Kanban #1007, AC1).

    `id` is the machine-stable identifier the `/decide` endpoint validates
    against. `label` is the human-readable display text. `description` and
    `hints` are optional advisory context shown on the UI option card (AC3,
    out of scope for this slice — reserved for dev-sr-frontend follow-up).

    `extra='forbid'` rejects unknown keys at 422 (parity with AcceptanceCriterion).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = None
    hints: list[str] | None = None


class QuestionPayload(BaseModel):
    """Payload for `interaction_kind IN ('question', 'decision')` tasks
    (Kanban #830).

    `question` is required (min_length=1). `options` accepts either the
    legacy `list[str]` shape (question tasks) OR the structured
    `list[OptionItem]` shape (decision tasks — Kanban #1007 AC1). Both
    forms coexist because `question_payload` is a single JSONB column and
    backward compat requires the old string-option shape to remain valid.
    `answer_history` accumulates answers over time; append-only logic
    (Kanban #832) is NOT in this slice — PATCH semantics are full-replace
    (same as `acceptance_criteria`).

    Kanban #1211 (2026-05-19): `extra='allow'` (was 'forbid') so GOV3 audit-
    flag bookkeeping keys (is_audit_flag / breach_streak_days /
    audit_history / latest_audit / latest_audit_summary / resolved_action /
    resolved_at / resolved_by / etc.) pass through both POST validation AND
    TaskRead response validation without requiring a per-feature schema
    field. Future flag-type families (e.g. GOV4 review prompts, GOV5 tuning
    proposals) ride the same pattern. Each writer (services/audit_flag.py,
    services/pause_switch.py) shapes its own extras; element-shape rigor
    lives at the writer.
    """

    model_config = ConfigDict(extra="allow")

    question: str = Field(min_length=1)
    # Kanban #1007 (AC1 / AC6): options accepts string items (legacy question
    # tasks) OR OptionItem-shaped dicts (decision tasks). The discriminator
    # model_validator in TaskCreate / TaskUpdate enforces the stricter
    # DecisionPayload contract when interaction_kind='decision'.
    options: list[str | OptionItem] | None = None
    answer_history: list[AnswerHistoryEntry] = Field(default_factory=list)


class DecisionPayload(BaseModel):
    """Formal payload shape for `interaction_kind='decision'` tasks (Kanban #1007, AC1).

    Extends the free-form `QuestionPayload` concept with typed option items
    so that the `/decide` endpoint can validate `chosen_id` against the
    canonical set. Fields:

    - `options`      — required; at least one item; each item has `id`/`label`
                       and optional `description`/`hints`.
    - `chosen_id`    — populated by `POST /api/tasks/{id}/decide`; null until decided.
    - `rationale`    — free-form justification captured by `/decide`; null until decided.
    - `chosen_at`    — UTC datetime when the decision was recorded; null until decided.
    - `chosen_by`    — who submitted the decision (defaults to 'user'); null until decided.

    `answer_history` is inherited from QuestionPayload usage but not declared
    here — the JSONB column is free-form; the Pydantic validator enforces the
    minimum contract for decision-specific keys.

    `extra='allow'` matches QuestionPayload — other keys (answer_history,
    #1211 audit flag extras) pass through unchanged.

    AC6 backward compat: this class is used ONLY when `interaction_kind='decision'`
    is explicitly set. Existing tasks with other `question_payload` shapes are
    validated only by QuestionPayload (free-form).
    """

    model_config = ConfigDict(extra="allow")

    options: list[OptionItem] = Field(min_length=1)
    chosen_id: str | None = None
    rationale: str | None = None
    chosen_at: datetime | None = None
    chosen_by: str | None = None


class DecisionRequest(BaseModel):
    """Request body for `POST /api/tasks/{id}/decide` (Kanban #1007, AC4).

    The caller supplies `chosen_id` (required — must match one of
    `question_payload.options[].id`) and an optional `rationale`. The
    endpoint merges these into `question_payload` together with
    server-stamped `chosen_at` and `chosen_by`.

    `extra='forbid'` rejects unknown keys at 422.
    """

    model_config = ConfigDict(extra="forbid")

    chosen_id: str = Field(min_length=1, max_length=128)
    rationale: str | None = None
    chosen_by: str = "user"


class HitlResolveRequest(BaseModel):
    """Request body for `POST /api/tasks/{id}/decide` HITL phone-tap path
    (Kanban #1452 — sub-feature 4 of #1192).

    Distinct from `DecisionRequest` (Kanban #1007): the phone-push flow
    records the operator's resolution into `resume_context` + clears
    `is_pending` so Lead can resume the in-flight (ps=2) task — it does
    NOT flip the task to DONE. The two contracts coexist on the SAME
    endpoint because the wire URL is locked; the router discriminates by
    body shape (the presence of `action` vs `chosen_id`).

    Field contract:
      - `action`: required; one of `approve` / `reject` / `custom`.
      - `selected_option`: required when `action in {approve, reject}`;
        must match `id` of one entry in `question_payload.options` (the
        endpoint supports both the legacy `list[str]` shape — option id
        is the string itself — AND the new `list[OptionItem]` shape — id
        is the `id` field).
      - `custom_text`: required + non-empty when `action='custom'`;
        ignored otherwise.

    `extra='forbid'` rejects unknown keys at 422.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "custom"]
    selected_option: str | None = Field(default=None, min_length=1, max_length=128)
    custom_text: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _check_action_payload_pair(self) -> "HitlResolveRequest":
        """Enforce the action ↔ payload field pairing.

        approve / reject → require `selected_option` (custom_text ignored).
        custom            → require non-empty `custom_text` (selected_option ignored).
        """
        if self.action in ("approve", "reject"):
            if self.selected_option is None:
                raise ValueError(
                    f"action='{self.action}' requires selected_option"
                )
        elif self.action == "custom":
            if not self.custom_text or not self.custom_text.strip():
                raise ValueError(
                    "action='custom' requires non-empty custom_text"
                )
        return self


class HitlResolveResponse(BaseModel):
    """Response body for `POST /api/tasks/{id}/decide` HITL phone-tap path
    (Kanban #1452).

    Distinct from the legacy `TaskRead` response of the #1007 `/decide` flow.
    The phone caller only needs to know: which task was resolved, what
    process_status it's currently in (so the FE can show a confirmation
    state), the resolution record (resume_context), and a timestamp.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int
    process_status: int
    resume_context: dict[str, Any] | None
    decided_at: datetime


class DecisionListItem(BaseModel):
    """One row in `GET /api/decisions` response (Kanban #1007, AC5).

    Flattens the relevant decision fields from `question_payload` alongside
    the task-level fields the caller needs to surface past decisions. Ordered
    by `chosen_at DESC`.
    """

    model_config = ConfigDict(from_attributes=False)

    task_id: int
    title: str
    options: list[OptionItem]
    chosen_id: str | None
    rationale: str | None
    chosen_at: datetime | None
    chosen_by: str | None


class TaskCreate(BaseModel):
    """Request body for POST /api/tasks."""

    project_id: int
    # Kanban #1115 (2026-05-17, L18 prevention) — payload-size caps. See class
    # AcceptanceCriterion comment for the hammer-test FINDING #10 context.
    # Live-DB spot-check 2026-05-17 (288 rows): max title len = 121, max desc
    # len = 9704 — both under the new caps, safe to apply retroactively.
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
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
    # Kanban #1125 (2026-05-17, L21 prevention) — per-template cap on
    # concurrently-active children. Only meaningful when is_template=true
    # (the scheduler ignores it on non-template rows). NULL = use env default
    # MAX_ACTIVE_CHILDREN_DEFAULT (currently 100). ge=1 mirrors the DB CHECK
    # ck_tasks_max_active_children_positive; le=10000 is a sanity ceiling
    # (a template that wants more than 10k concurrent children probably
    # shouldn't be a recurrence template at all — file separate tasks).
    max_active_children: int | None = Field(default=None, ge=1, le=10_000)
    # Kanban #1122 (2026-05-17, L15 prevention) — per-template auto-headless
    # confirmation timestamp. Only meaningful when is_template=true AND
    # run_mode='auto_headless'. Cross-column rule enforced by
    # _validate_template_auto_headless_confirmed below: a POST that sets
    # BOTH is_template=true AND run_mode='auto_headless' requires this field
    # to be non-null. The standard create flow is:
    #   1. POST /api/tasks with run_mode='auto_pickup' (or any non-headless),
    #   2. POST /api/tasks/{id}/confirm-template-auto-run (stamps timestamp),
    #   3. PATCH /api/tasks/{id} run_mode='auto_headless' (now passes the rule).
    # The endpoint is the only normal writer; this field is accepted on POST
    # only to support test fixtures and replay/import workflows that already
    # carry a confirmed timestamp.
    template_auto_run_confirmed_at: datetime | None = None
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
    # Kanban #1115 (L18, 2026-05-17) — initial cap of 2000.
    # Kanban #1123 (L16, 2026-05-17) — TIGHTENED to 1000. The agent-context
    # sanitizer (services/agent_context_sanitizer.py) caps at 500 on the way
    # IN to LLM prompts; the wider DB cap leaves room for operator audit
    # context (verbose halt rationale recorded for UI display) while still
    # bounding attacker-controlled fluff at the API boundary.
    halt_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    # Kanban #854 (2026-05-13): free-form rationale captured on a
    # process_status flip — most commonly when the user cancels a task
    # (process_status -> 6). Independent of the value: any PATCH may set
    # it. None / absent on POST → NULL in DB. min_length=1 rejects ""
    # at 422 (parity with halt_reason / description). Audit-trigger
    # snapshot captures the field automatically — no separate plumbing.
    # Kanban #1115 (L18, 2026-05-17) — initial cap of 2000.
    # Kanban #1123 (L16, 2026-05-17) — TIGHTENED to 1000 (parity with
    # halt_reason; same agent-context sanitizer protection on the LLM side).
    status_change_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    # Kanban #797 (2026-05-12): optional structured exit-criteria array. Each
    # element validated by AcceptanceCriterion (text required, status Literal,
    # etc.). PATCH semantics for the field on TaskUpdate mirror description /
    # halt_reason: key-absent = unchanged, explicit null = clear, array =
    # replace whole array. On POST: None / absent = NULL in DB; [] = empty
    # array (legal but unusual); [...] = stored as-is.
    # Kanban #1115 (2026-05-17, L18) — max 50 AC items per task.
    acceptance_criteria: list[AcceptanceCriterion] | None = Field(default=None, max_length=50)
    # Kanban #887 (2026-05-13): append-only subagent spawn log. NOT NULL DEFAULT
    # '[]' at the DB layer — POST default matches: empty list. Each element
    # validated by SubagentModelEntry (agent required, model Literal, at datetime).
    # Full-replace PATCH semantics (Lead accumulates, then sends the whole list).
    # Kanban #1115 (2026-05-17, L18) — max 200 spawn entries per task; covers
    # heavy multi-day auto-pickup tasks while still bounding agent runaway.
    subagent_models: list[SubagentModelEntry] = Field(default_factory=list, max_length=200)
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
    # Kanban #1211 (2026-05-19): GOV3 per-spawn override hatch. Default false
    # — the typical POST path. Operators set true (with a reason >=10 chars)
    # to bypass the 423 gate when the parent project is paused. The router
    # writes a `projects_audit` row with action='pause_override' when this
    # combination lands on a paused project so override usage stays
    # auditable (D6 + GOV5 threshold-tuning signal). The pair is validated
    # together by `_check_allow_during_pause_needs_reason` below — true
    # without a reason fails 422 before reaching the DB CHECK.
    allow_during_pause: bool = False
    allow_during_pause_reason: str | None = Field(
        default=None, min_length=10, max_length=1_000
    )
    # Kanban #1224 (2026-05-19): per-task notification-targets override. None
    # (the default) = inherit the project-level default at delivery time. An
    # explicit list REPLACES the project default for this task's notifications
    # only (no merge). Element shape validated by NotificationTarget; max
    # length 20 (parity with the project-level cap). See
    # `src/services/notification_router.py` for resolution priority.
    notification_targets: list[NotificationTarget] | None = Field(
        default=None, max_length=20
    )

    # Kanban #1194 (2026-05-19): per-task pre-spawn cost estimate consumed by
    # the budget_gate.check_budget projection at POST time. Optional — None
    # means "no estimate", which still routes through the gate (the existing
    # daily spend may already be over without any new burn). Distinct from
    # the server-computed `estimated_cost_usd` column on the row (which is
    # set on done-flip by task_cost_estimator) — this is the caller's
    # PROPOSAL, used only for the spawn-time decision. Persisted onto the
    # row's estimated_cost_usd column so a later reconcile reflects the
    # caller's claim; the heuristic at done-flip may overwrite it.
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0)

    # Kanban #1194 AC4 (2026-05-19): emergency override hatch for the
    # spawn-time budget gate. Both fields must be supplied together (model
    # validator below); when present + valid, a 429 is converted to 201 and
    # the override is recorded in the task description's audit footer. The
    # operator value 'operator' is the typical signal — free-form to allow
    # other authorized-by labels in the future (CI bots, scheduled cron
    # jobs, etc.) without a schema bump.
    budget_override_authorized_by: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    budget_override_reason: str | None = Field(
        default=None, min_length=10, max_length=1_000
    )
    # Kanban #1006 (2026-05-20): optional starter template reference.
    # When set, the router looks up the named template via
    # src.services.action_templates and applies pre-fill semantics for
    # task_kind, task_type, priority, and acceptance_criteria.  Unknown
    # name → 422.  Pre-fill is override-safe: an explicit caller value
    # takes precedence over the template default.
    action_template_id: str | None = Field(default=None, min_length=1, max_length=64)
    # Kanban #1004 (2026-05-20): optional auto-handoff template pointer.
    # When non-null, a later PATCH that transitions process_status to DONE
    # triggers the spawn hook in services/handoff_spawn.py — a child task
    # derived from the named template lands in the same transaction.
    # Existence + project-scope validated in the router (needs DB lookup).
    # Loop guard (AC6): the CHILD's handoff_template_id is set to NULL by
    # the spawn service so the chain terminates after one level.
    handoff_template_id: int | None = Field(default=None, ge=1)

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

    @model_validator(mode="after")
    def _check_allow_during_pause_needs_reason(self) -> "TaskCreate":
        """Kanban #1211 (GOV3 D6): `allow_during_pause=true` REQUIRES
        `allow_during_pause_reason` set (>=10 chars enforced by the field
        Field(min_length=10)). Fires a friendly 422 ahead of the DB CHECK
        `ck_tasks_pause_reason_length` 400 fallback. allow=false with a
        reason set is silently allowed (defensive — operator can pre-fill
        the reason then flip allow on a follow-up PATCH; the DB CHECK
        treats this as legal too)."""
        if self.allow_during_pause and self.allow_during_pause_reason is None:
            raise ValueError(
                "allow_during_pause=true requires allow_during_pause_reason "
                "(>=10 chars) — operator-supplied rationale for bypassing "
                "the project pause gate; captured into projects_audit."
            )
        return self

    @model_validator(mode="after")
    def _check_budget_override_pair(self) -> "TaskCreate":
        """Kanban #1194 AC4: the spawn-time budget gate's emergency override
        requires BOTH `budget_override_authorized_by` AND `budget_override_reason`
        to be present together. Asymmetric supply fails 422 before reaching the
        router — keeps the audit semantics intact (reason without authorizer
        is signal-less; authorizer without reason has no explanation to log).
        """
        if (self.budget_override_authorized_by is None) != (
            self.budget_override_reason is None
        ):
            raise ValueError(
                "budget_override_authorized_by and budget_override_reason "
                "must be supplied together (both or neither)"
            )
        return self

    @model_validator(mode="after")
    def _validate_template_auto_headless_confirmed(self) -> "TaskCreate":
        """Kanban #1122 (L15 prevention): a POST that sets BOTH
        is_template=true AND run_mode='auto_headless' MUST also carry a
        non-null template_auto_run_confirmed_at. Without it the scheduler
        would refuse to spawn children anyway (recurrence.fire_template gate)
        — surfacing the 422 here is the friendlier outcome than a silently-
        idle template the operator notices three days later.

        Detail message source-text-locked: pinned by the test in
        api/tests/test_template_auto_run_confirm.py. Includes the endpoint
        name so the operator can act on the 422 without consulting docs.
        """
        if self.is_template and self.run_mode == TaskRunMode.AUTO_HEADLESS:
            if self.template_auto_run_confirmed_at is None:
                raise ValueError(
                    "is_template=true AND run_mode='auto_headless' requires "
                    "template_auto_run_confirmed_at to be set (per-template "
                    "confirmation, Kanban #1122 L15). POST "
                    "/api/tasks/{id}/confirm-template-auto-run first."
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

    # Kanban #1115 (2026-05-17, L18 prevention) — payload-size caps. Parity
    # with TaskCreate. See AcceptanceCriterion comment for hammer-test #10 context.
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
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
    # Kanban #1125 (2026-05-17, L21 prevention) — PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear (NULL — falls back to env
    #                       MAX_ACTIVE_CHILDREN_DEFAULT at next fire)
    #   - non-null int    → set / change the cap. Common use: bump the cap
    #                       on a halted template (process_status=BLOCKED,
    #                       halt_reason='max_active_children_reached') so
    #                       the next tick can resume spawning. Operator
    #                       must ALSO clear halt_reason + flip ps back to
    #                       TODO in the same PATCH (or a follow-up) — the
    #                       cap alone doesn't un-halt the template.
    max_active_children: int | None = Field(default=None, ge=1, le=10_000)
    # Kanban #1122 (2026-05-17, L15 prevention) — per-template auto-headless
    # confirmation timestamp. PATCH-able only as a server-managed write path
    # (the dedicated POST /api/tasks/{id}/confirm-template-auto-run is the
    # normal writer; this field is exposed on TaskUpdate solely for backfill /
    # explicit-null clear flows). Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear (back to un-confirmed)
    #   - non-null dt     → stamp (rare on PATCH; the endpoint is preferred)
    # Cross-column rule (run_mode='auto_headless' + is_template requires
    # non-null) is enforced router-side on RESOLVED final values, NOT here
    # at the validator (mirrors the resolved-final pattern used for
    # is_template+scheduled_at XOR — a single-field PATCH cannot see the
    # existing row's other column from the schema layer).
    template_auto_run_confirmed_at: datetime | None = None
    # Kanban #1121 (2026-05-17, L14 prevention) — PATCH-able for the explicit
    # human-review-cleared flow. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit False  → reviewer ACKed; clears the flag so subsequent
    #                       PATCHes can flip run_mode to auto_headless.
    #   - explicit True   → operator manually flagging a task they want gated
    #                       (rare; the scanner does this automatically).
    # No `_reject_explicit_null` validator — the column is NOT NULL with DEFAULT
    # FALSE, so `None` is semantically meaningless here; Pydantic's
    # `Optional[bool]` accepts it, the router treats it as a no-op via
    # `exclude_unset=True` if absent, and explicit-null PATCH would hit the
    # NOT NULL constraint and 400 fallback (acceptable: callers should send
    # false explicitly). The scanner ALSO runs on every PATCH — if the PATCH
    # rewrites a flagged field to a clean value, the scanner does NOT
    # auto-clear the flag (one-way sticky); the caller must include
    # `requires_human_review=false` in the same PATCH if they want the
    # flag dropped.
    requires_human_review: bool | None = None
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
    # Kanban #1115 (L18, 2026-05-17) — initial cap of 2000.
    # Kanban #1123 (L16, 2026-05-17) — TIGHTENED to 1000 (parity with
    # TaskCreate; sanitizer caps at 500 on the LLM side, DB allows 1000
    # for operator audit context).
    halt_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    # Kanban #854 (2026-05-13): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear the reason (null IS meaningful)
    #   - empty string "" → 422 via min_length=1
    #   - non-empty       → set / overwrite the reason
    # No _reject_explicit_null validator — parity with halt_reason / description.
    # Most common use: paired with `{"process_status": 6}` on a cancel PATCH.
    # Kanban #1115 (L18, 2026-05-17) — initial cap of 2000.
    # Kanban #1123 (L16, 2026-05-17) — TIGHTENED to 1000 (parity with
    # TaskCreate halt_reason).
    status_change_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
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
    # Kanban #1115 (2026-05-17, L18) — max 50 AC items per task (parity with TaskCreate).
    acceptance_criteria: list[AcceptanceCriterion] | None = Field(default=None, max_length=50)
    # Kanban #887 (2026-05-13): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit list   → REPLACE the whole array (full-replace; Lead
    #                       accumulates, then sends the whole list each PATCH)
    # NOT nullable: the DB column is NOT NULL DEFAULT '[]'. Explicit null on
    # PATCH is NOT meaningful (cannot clear to NULL — the column has no null
    # state). Omit the key to leave unchanged. Each element validated by
    # SubagentModelEntry (agent required, model Literal, at datetime).
    # Kanban #1115 (2026-05-17, L18) — max 200 spawn entries per task (parity with TaskCreate).
    subagent_models: list[SubagentModelEntry] | None = Field(default=None, max_length=200)
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
    # Kanban #952 (2026-05-16): in-graph auditor outputs. PATCH-able by the
    # langgraph worker on finalize. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit dict   → replace whole report (single-column-latest model)
    #   - explicit null   → clear the report (null IS meaningful — column is
    #                       nullable JSONB)
    # No element-shape validation this slice — the engine is the only writer.
    audit_report: dict[str, Any] | None = None
    # Kanban #952: retry counter. Non-negative; CHECK ck_tasks_audit_retry_count_nonneg
    # at the DB layer catches drift.
    audit_retry_count: int | None = Field(default=None, ge=0)
    # Kanban #960 (2026-05-17): Health monitor sweep output. PATCH-able by the
    # Health monitor service (the only writer in normal operation). Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit dict   → replace whole alert (single-object latest-only)
    #   - explicit null   → clear the alert (null IS meaningful — column is
    #                       nullable JSONB; used when a detector clears)
    # No element-shape validation this slice — the service is the only writer
    # and shapes the dict server-side.
    health_alert: dict[str, Any] | None = None
    # Kanban #1211 (2026-05-19): GOV3 per-spawn override hatch. PATCH-able for
    # the rare flow where the operator changes their mind mid-task lifecycle
    # (e.g. flip true → false after the parent project is unpaused to clean
    # up the audit-signal). Mirrors TaskCreate's semantics + validator. The
    # router does NOT write a `projects_audit` row on PATCH-flip — only the
    # initial POST against a paused project triggers the audit row (the
    # bypass-at-creation IS the auditable event).
    allow_during_pause: bool | None = None
    allow_during_pause_reason: str | None = Field(
        default=None, min_length=10, max_length=1_000
    )
    # Kanban #1224 (2026-05-19): PATCH-able per-task notification-targets
    # override. Semantics — key-absent leaves unchanged (exclude_unset);
    # explicit list REPLACES the prior value (no deep merge); explicit `null`
    # CLEARS to NULL (= inherit project default again). Element shape
    # validated by NotificationTarget.
    notification_targets: list[NotificationTarget] | None = Field(
        default=None, max_length=20
    )
    # Kanban #1004 (2026-05-20): PATCH-able. Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit null   → clear (NULL — disables auto-handoff on next DONE-flip)
    #   - non-null int    → point at a different template (router validates
    #                       existence + project scope; same project as the
    #                       task, OR global template (project_id IS NULL))
    # Same posture as `blocked_by` — re-pointing IS supported in V1.
    handoff_template_id: int | None = Field(default=None, ge=1)

    # Kanban #1011 (2026-05-20): per-task nudge on/off toggle. PATCH-able.
    # Semantics:
    #   - key absent      → leave unchanged (exclude_unset=True in router)
    #   - explicit true   → silence nudges for this task
    #   - explicit false  → re-enable nudges (if previously silenced)
    # No explicit-null validator — the column is NOT NULL DEFAULT false;
    # explicit null on PATCH would hit the NOT NULL constraint and 400 (same
    # posture as is_pending, requires_human_review).
    nudge_disabled: bool | None = None

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
    # Kanban #1125 (2026-05-17) — L21 prevention per-template cap on
    # concurrently-active children. Backfilled to NULL on existing rows by
    # migration 0035's nullable=true. NULL = use env default at fire-time.
    max_active_children: int | None = None
    # Kanban #1122 (2026-05-17) — L15 prevention per-template auto-headless
    # confirmation timestamp. Backfilled to NULL on existing rows by
    # migration 0036's nullable=true. NULL = un-confirmed (the scheduler
    # refuses to spawn children if run_mode='auto_headless'). Stamped via
    # POST /api/tasks/{id}/confirm-template-auto-run.
    template_auto_run_confirmed_at: datetime | None = None
    # Kanban #1121 (2026-05-17) — L14 prevention content-moderation tag. Set
    # to TRUE by routers/tasks.py via the scanner in services/content_moderation.py
    # when destructive intent matches in any author-supplied field. Backfilled
    # to FALSE on existing rows by migration 0037's server_default. The auto-
    # headless gate in routers/tasks.py refuses run_mode=auto_headless PATCHes
    # while this is TRUE. Reviewer clears via PATCH requires_human_review=false.
    requires_human_review: bool = False
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
    # Kanban #944 (2026-05-16) — per-task LLM-cost estimate captured on
    # done-flip (process_status: <5 → 5). NULL until first close; idempotent
    # re-flip preserves the first-close values. Read-only — TaskCreate /
    # TaskUpdate do NOT accept these (server-computed only).
    # Backfilled to NULL on existing rows by migration 0025's nullable=true.
    estimated_input_tokens: int | None
    estimated_output_tokens: int | None
    estimated_cost_usd: Decimal | None
    # Kanban #952 (2026-05-16) — in-graph auditor node outputs (Auditor agent).
    # `audit_report` is a JSONB blob holding the LATEST audit's structured
    # outcome (verdict / severity / evidence / action_taken / etc.). Element
    # shape is locked by the engine (langgraph/nodes.py::auditor_node);
    # exposed raw here. `audit_retry_count` is the count of AUTO-RESOLVE
    # retries the auditor has applied — cap (=3) lives in the engine.
    # Backfilled to NULL / 0 on existing rows by migration 0030.
    audit_report: dict[str, Any] | None = None
    audit_retry_count: int = 0
    # Kanban #960 (2026-05-17): periodic Health monitor sweep output. Single-
    # object latest-only JSONB. None = no current alert. Backfilled to NULL on
    # existing rows by migration 0031.
    health_alert: dict[str, Any] | None = None
    # Kanban #1211 (2026-05-19) — GOV3 per-spawn override hatch. Backfilled to
    # FALSE / NULL on existing rows by migration 0040's server_default /
    # nullable. The pair represents whether the task was filed against a
    # paused project via the bypass route; surfaces on every read so the FE
    # can render a "bypassed pause" badge / surface the operator rationale.
    allow_during_pause: bool = False
    allow_during_pause_reason: str | None = None
    # Kanban #1224 (2026-05-19) — per-task notification-targets override.
    # NULL = inherit project default. Value-tolerant on read (list of dicts)
    # for legacy / hand-edited resilience — mirrors the project-level
    # ProjectRead.notification_targets shape.
    notification_targets: list[dict[str, Any]] | None = None
    # Kanban #1004 (2026-05-20) — auto-handoff template pointer. Backfilled
    # to NULL on existing rows by migration 0045's nullable=true. NULL = no
    # auto-handoff configured; non-null = on the next DONE-flip the router
    # spawns a child via services/handoff_spawn.py (the CHILD's value is
    # always NULL — loop guard).
    handoff_template_id: int | None = None

    # Kanban #1011 (2026-05-20) — HITL aging nudge dedup + per-task toggle.
    # `last_nudge_at`: the timestamp of the last nudge fired for this task.
    #   NULL = never nudged. The cron sets it after every fire (regardless of
    #   delivery outcome). Backfilled to NULL on existing rows by migration 0047.
    # `nudge_disabled`: per-task nudge on/off toggle. Default false (nudges
    #   enabled per the project threshold). Operator sets true to silence a
    #   specific task. Backfilled to false on existing rows by migration 0047's
    #   NOT NULL DEFAULT false.
    last_nudge_at: datetime | None = None
    nudge_disabled: bool = False


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


class SnoozeRequest(BaseModel):
    """Request body for POST /api/tasks/{id}/snooze (Kanban #1011, AC5).

    `hours` sets how long until the next eligible nudge.  Default 4h.
    Validation: 1..168 (1 hour to 1 week max).

    The snooze math: `last_nudge_at = now() + (<hours> - 24) * interval`
    so the next eligible time (last_nudge_at + 24h) equals exactly
    `now() + <hours>`.  Example: hours=4 → last_nudge_at = now()-20h, next
    eligible = now()+4h.

    `extra='forbid'` rejects unknown keys at 422 (parity with other action
    request bodies in this module).
    """

    model_config = ConfigDict(extra="forbid")

    hours: int = Field(default=4, ge=1, le=168)


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
