"""Task and TaskHistory ORM models.

`Task` mirrors the Kanban schema described in `context/standards/general.md`:
process_status, priority are INTEGER columns with CHECK constraints — canonical
codes live in `src.constants` (TaskStatus, TaskPriority). `assigned_role` no
longer carries a DB CHECK — application code validates against the active
project's team roster (codes 1..5 for dev, 11..12 for novel, etc.).

`TaskHistory` is an audit-only sink populated by a PG trigger on the `tasks` table
(AFTER UPDATE OR DELETE). `task_id` is intentionally NOT a FK — when a task row
is deleted we still want the historical record to live on.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from decimal import Decimal as _Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import (
    RecordStatus,
    TaskHistoryOperation,
    TaskInteractionKind,
    TaskKind,
    TaskPriority,
    TaskRunMode,
    TaskStatus,
    TaskType,
    in_clause,
    in_clause_text,
)
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.milestone import Milestone
    from src.models.project import Project
    from src.models.project_resource import ProjectResource


class Task(Base):
    """A Kanban task scoped to a Project.

    `process_status` (1..5) holds the lifecycle code (TODO/IN_PROGRESS/REVIEW/
    BLOCKED/DONE — see TaskStatus). `status` (0/1) is the uniform soft-delete
    flag (RecordStatus). `assigned_role` is an integer with no DB CHECK — the
    app validates per active project's team roster.

    Lifecycle timestamps `started_at` / `completed_at` are managed by the API
    layer on process_status transitions (PATCH /api/tasks/{id}).
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Self-referential FK for subtask hierarchy (Kanban #238). Locked design
    # 2026-05-08: ON DELETE CASCADE is defense-in-depth — app never hard-deletes,
    # and soft-delete with active children is blocked at 409 by the router.
    parent_task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    process_status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=str(TaskStatus.TODO),
        default=TaskStatus.TODO,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=str(TaskPriority.NORMAL),
        default=TaskPriority.NORMAL,
    )
    assigned_role: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default="1",
        default=RecordStatus.ACTIVE,
    )

    # Kanban #750 (2026-05-11): "in-flight and stuck" flag — orthogonal to
    # process_status. The cross-state rule (is_pending=true REQUIRES
    # process_status=2/in_progress) lives in src/services/is_pending.py as an
    # app-layer validator (resolved-final pattern on PATCH). No DB CHECK this
    # slice; DB DEFAULT false backfills the 55 existing rows on migration 0011.
    is_pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # Step 2 (Kanban #481/#483): execution mode for Kanban-driven AI.
    # No Python-side default needed — DB DEFAULT 'manual' covers INSERT.
    # Pydantic Literal validation gates accepted values at the API boundary.
    # Cross-table rule (auto_headless requires project consent) lives in
    # src/services/run_mode.py — not as a DB CHECK because it spans tables.
    run_mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'manual'"),
    )

    # V3+ T1 (Kanban #706): task_kind discriminates AI vs human work.
    # DB DEFAULT 'ai' (Kanban #858 — flipped from 'human' on 2026-05-13). Most
    # tasks are agent-driven; 'human' is reserved for interaction_kind in
    # ('question','decision'), which the router coerces server-side via
    # services/task_kind.coerce_task_kind_for_interaction. The cross-table
    # rule (HUMAN must pair with MANUAL) lives in src/services/task_kind.py —
    # spans the run_mode column at the app layer.
    task_kind: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'ai'"),
    )

    # Kanban #803 (2026-05-12): task_type classifies work — bug / feature /
    # chore / docs / refactor. DB DEFAULT 'feature' covers existing rows +
    # INSERT-without-explicit. Mirror of migration 0015's CHECK predicate.
    task_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="feature",
    )

    # V3+ T1 (Kanban #706): recurrence template fields. A "template" row carries
    # is_template=true + a cron rule + a next_fire_at; the scheduler (T2) reads
    # the partial index on next_fire_at WHERE is_template=TRUE, spawns child
    # rows pointing back via spawned_from_task_id, and advances the template's
    # next_fire_at. The template itself is never modified by lifecycle PATCHes
    # — it's a recipe, not a task.
    is_template: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    recurrence_rule: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    recurrence_timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default="UTC",
    )
    next_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # V3+ T1 audit follow-up (Kanban #723): one-shot scheduling path. Mutually
    # exclusive with is_template=true — enforced by ck_tasks_scheduled_xor_template
    # + Pydantic model_validators on TaskCreate / TaskUpdate. T2 scheduler scans
    # ix_tasks_scheduled_at_pending and transitions matching rows to in_progress.
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Kanban #785 (MVP-2): in-flight halt flag for full-auto Lead sessions.
    # Non-null string = task is halted (auto-pickup query skips these);
    # NULL = task runs normally. Free-form reason text set by Lead at halt
    # time per the #787 decision matrix.
    halt_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kanban #854 (2026-05-13): free-form rationale captured on a
    # process_status flip — most commonly when the user cancels a task
    # (process_status -> 6). Independent of the value: any PATCH may set it.
    # NULL = unset. Audit-trigger snapshot captures the field automatically.
    status_change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kanban #1839: stamped once on the →process_status=8 ('halted-pending-user')
    # transition by routers/tasks.py, mirroring started_at/completed_at (stamped
    # only when currently NULL; persists; not auto-cleared; no re-stamp on re-halt).
    # Orthogonal to halt_reason (#785) — the two are decoupled. CHECK derives from
    # TaskStatus.ALL — do NOT hand-edit ck_tasks_process_status_valid.
    halted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Kanban #830 (2026-05-12): interaction_kind discriminates agent-executed tasks
    # from user-interaction gates. DB DEFAULT 'work' covers existing rows + INSERT.
    interaction_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="work",
    )
    # Kanban #830: question/decision task payload — question text, options, answer history.
    # Full-replace PATCH semantics (same as acceptance_criteria). Append logic in #832.
    question_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Kanban #830: partial-work state stored by Lead when auto-run halts mid-task.
    # Used by re-spawn brief on resume. Free-form — no shape constraint.
    resume_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Kanban #797 (2026-05-12): structured per-criterion exit-criteria tracker.
    # Optional JSONB array — each element is {text, status, verified_by,
    # verified_at, notes}; element shape validated by Pydantic
    # AcceptanceCriterion at the API boundary. NULL = unset (task filed without
    # structured criteria); [] = explicitly cleared. Soft enforce via agent
    # prompts (#798) — no DB CHECK, no done-guard this slice.
    acceptance_criteria: Mapped[list[dict] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Kanban #887 (2026-05-13): append-only subagent spawn log per task.
    # JSONB NOT NULL DEFAULT '[]'. Each element: {agent:str, model:str, at:datetime}.
    # Shape validated by SubagentModelEntry at the API boundary. Full-replace
    # PATCH semantics (Lead accumulates, then sends the whole list).
    subagent_models: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )
    # Kanban #1677 (2026-06-02): per-task model-tier override. One of
    # 'haiku'/'sonnet'/'opus' or NULL (=inherit). Precedence (orchestrator
    # convention, NOT enforced in code here): task.model_override >
    # project.agent_overrides > role default. The Lead/orchestrator reads this
    # off TaskRead, resolves the effective tier, and records the RESOLVED tier
    # in the existing subagent_models spawn log. No DB CHECK on the value —
    # the Pydantic Literal at the API boundary gates the tier set (422),
    # mirroring the halt_reason posture (nullable TEXT, no DB DEFAULT). NULL =
    # inherit; non-null = the explicit tier for every spawn on this task.
    # Migration 0056's nullable=true backfills existing rows to NULL.
    model_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kanban #2300 (2026-06-11): per-task Anthropic effort carrier. One of
    # off/low/medium/high/extra/max or NULL (= inherit). Precedence:
    # task.effort_override > project.effort_mode > off. In 'auto' project mode the
    # worker auto-resolves a level and WRITES it here at spawn for visibility.
    # 'max' is manual-only (Slice-2 UI); auto never selects it (server-side clamp
    # caps at 'extra'). Pydantic EffortOverrideLiteral gates the value (422); NO
    # DB CHECK (#1677 posture). NULL = inherit; non-null = the explicit level.
    effort_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Self-ref FK: spawned children point at the template they came from.
    # ON DELETE SET NULL — defense-in-depth; app never hard-deletes templates.
    spawned_from_task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Kanban #1125 (2026-05-17): L21 prevention — per-template cap on the
    # number of concurrently-active children spawned by recurrence.fire_template.
    # Only meaningful on is_template=true rows; non-template rows ignore it.
    # NULL = use the env default (MAX_ACTIVE_CHILDREN_DEFAULT, currently 100)
    # at fire-time. Mirror of migration 0035's CHECK predicate — defense-in-
    # depth against raw-SQL writes of zero / negative values.
    max_active_children: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # Kanban #1122 (2026-05-17): L15 prevention — per-template auto-headless
    # confirmation timestamp. Only meaningful on rows with is_template=true
    # AND run_mode='auto_headless'. NULL = not yet confirmed; the scheduler
    # refuses to spawn children from this template (fire_template returns
    # None, advance is skipped). Non-null = a human POSTed
    # /api/tasks/{id}/confirm-template-auto-run. No DB CHECK — the cross-
    # column rule is app-layer-only (mirrors run_mode='auto_headless' needs
    # project.auto_run_consent_at pattern in services/run_mode.py).
    template_auto_run_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Kanban #1121 (2026-05-17): L14 prevention — content-moderation tag.
    # Set to TRUE by `routers/tasks.py` on POST + PATCH when the scanner in
    # `src/services/content_moderation.py` matches a destructive-intent pattern
    # in any of (title, description, acceptance_criteria[*].text, halt_reason,
    # status_change_reason). The auto-headless gate refuses
    # `run_mode=auto_headless` PATCHes on flagged rows — reviewer must
    # explicitly clear via PATCH `requires_human_review=false`. NOT NULL with
    # server_default=false so existing rows backfill cleanly via migration
    # 0037. Sticky: false → true happens on every scan-match; true → false
    # ONLY via an explicit caller-supplied PATCH (the router never re-clears
    # the flag based on a scan, even if the PATCH rewrites a flagged field
    # to a clean value — clearing requires a deliberate human ack).
    requires_human_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    # Kanban #771 (2026-05-12): single-blocker dependency. NULL = unblocked;
    # non-null = points at the task that blocks this one. ON DELETE SET NULL
    # (NOT CASCADE) — hard-deleting a blocker must NOT delete the blocked task.
    # Same-project + cycle-prevention enforced app-side in routers/tasks.py;
    # ck_tasks_blocked_by_not_self in __table_args__ catches self-blocker
    # drift via raw SQL.
    blocked_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Kanban #772 (2026-05-12): within-lane manual sort key (sparse-float
    # lexicographic ordering). NULL = "use created_at fallback for ordering"
    # — a lane that's never been reordered keeps its natural created_at
    # order without paying a per-row write cost. No DB CHECK / FK / index
    # this slice (measured-first index policy — lane-scoped queries already
    # filter by process_status + status, both indexed). Ordering rule pinned
    # in the api-contracts.md GET /api/tasks section: ORDER BY sort_order
    # ASC NULLS LAST, created_at ASC. POST /api/tasks/{id}/reorder
    # materializes NULL lane-mates on first reorder. The cross-row
    # blocker-order constraint (T.sort_order >= T.blocked_by.sort_order in
    # same lane when both ps=TODO) lives app-side in routers/tasks.py.
    sort_order: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )

    # Kanban #944 (2026-05-16): per-task LLM-cost estimation captured on
    # done-flip (process_status: <5 → 5). All three NULL until first close;
    # idempotent re-flip preserves the first-close values. Heuristic shape
    # documented in migration 0025's docstring and
    # src/services/task_cost_estimator.py. Read-only on the wire (TaskRead
    # exposes; TaskCreate / TaskUpdate do NOT — server-computed only).
    estimated_input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    estimated_output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    estimated_cost_usd: Mapped[_Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )

    # Kanban #1304 (2026-06-15): per-task PRE-run cost forecast, persisted by
    # POST /api/tasks/{id}/cost-forecast. Mirror of estimated_cost_usd's shape
    # (Numeric(10,4), nullable, no CHECK). Distinct from estimated_cost_usd: that
    # is the POST-HOC (#944 done-flip) actual; this is the BEFORE-spawn forecast.
    # Storing both makes the ±30% calibration loop measurable. Read-only on the
    # wire (TaskRead exposes; TaskCreate / TaskUpdate do NOT — server-computed
    # only). Backfilled to NULL on existing rows by migration 0068's nullable=true.
    forecast_cost_usd: Mapped[_Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )

    # Kanban #952 (2026-05-16): in-graph auditor node outputs.
    # `audit_report` is the LATEST audit's structured outcome (verdict,
    # severity, evidence, action_taken, …). Audit history across retries lives
    # in `tasks_history` via the existing audit trigger — single-column JSONB
    # keeps the surface minimal. `audit_retry_count` tracks AUTO-RESOLVE
    # retries; cap (=3) enforced in the engine (AUDITOR_RETRY_CAP_DEFAULT),
    # CHECK `>= 0` is defense-in-depth against raw-SQL drift.
    audit_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    audit_retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        default=0,
    )

    # Kanban #1209 (2026-05-19): GOV1 hard kill switch — per-task frozen-in-place
    # marker. Set TRUE by `services/kill_switch.py::kill_project` for every open
    # TODO/IN_PROGRESS row in the killed project (preserved through kill, not
    # archived — D3 "ค้างไว้แบบไหน กลับมาแบบนั้น"). Cleared back to FALSE on
    # revive. Orthogonal to process_status — a frozen TODO row stays at
    # process_status=1 (the worker is blocked at a different gate: project
    # is_killed=true). No DB CHECK on cross-table coherence (revive sweeps
    # cover the inverse). NOT NULL DEFAULT false so existing 510 task rows
    # backfill cleanly via migration 0039.
    kill_frozen: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )

    # Kanban #1211 (2026-05-19): GOV3 soft-pause per-spawn override hatch.
    # When the parent project is paused (`projects.is_paused=true`), POST
    # /api/tasks is blocked with 423 — UNLESS the body carries
    # `allow_during_pause=true` AND `allow_during_pause_reason` (>=10 chars).
    # The bypass IS the audit signal: a `projects_audit` row with
    # action='pause_override' is written so operators can review the
    # override frequency / signal-quality of the threshold (D6 + GOV5
    # callout: "if used >X times/week per project, threshold is wrong").
    # DB CHECK `ck_tasks_pause_reason_length` enforces the >=10-chars
    # invariant; Pydantic TaskCreate fires the friendlier 422 first.
    allow_during_pause: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    allow_during_pause_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Kanban #960 (2026-05-17): periodic Health monitor sweep output.
    # Single-object latest-only JSONB (audit history flows via tasks_history
    # trigger — same precedent as audit_report #952). Element shape:
    # {detector, severity, evidence, alerted_at, threshold_used}. NULL = no
    # current alert. No DB CHECK on element shape (JSONB element-shape
    # validation lives at the API / service layer).
    health_alert: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Kanban #1224 (2026-05-19): per-task push-notification targets override.
    # NULL = inherit `projects.notification_targets` at delivery time. Element
    # shape validated at API boundary by Pydantic NotificationTarget. No DB
    # CHECK on element shape (mirrors notification_targets at project level
    # + the wider JSONB-element-shape-at-API-layer precedent).
    notification_targets: Mapped[list[dict] | None] = mapped_column(
        JSONB, nullable=True
    )

    # Kanban #1004 (2026-05-20): auto-handoff template pointer. When non-null,
    # a PATCH that transitions process_status to DONE triggers the spawn hook
    # in services/handoff_spawn.py — a child task derived from the named
    # template lands in the same transaction. The CHILD's handoff_template_id
    # is set to NULL by the spawn service (loop guard AC6). ON DELETE SET NULL
    # mirrors blocked_by / spawned_from_task_id posture — defense-in-depth
    # against a templates table delete that would otherwise cascade. App layer
    # soft-deletes templates; hard-delete remains a human-only path.
    handoff_template_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("handoff_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Kanban #1868 (2026-06-03): optional milestone grouping for release
    # planning. NULL = task is not assigned to a milestone. FK ON DELETE SET
    # NULL — deleting a milestone detaches its tasks (NOT cascade); the app
    # soft-deletes a milestone and NULLs children in the same transaction
    # (routers/milestones.py DELETE). Same-project rule (the referenced
    # milestone must belong to the task's project) is app-layer-only — enforced
    # in routers/tasks.py on POST + PATCH, NOT a DB CHECK (cross-table). Mirror
    # of migration 0057's nullable FK; ix_tasks_milestone_id supports the
    # `?milestone_id` task-list filter + the milestone rollup GROUP BY.
    milestone_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("milestones.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Kanban #1868 follow-up (2026-06-03): optional display/planning date for
    # the Calendar view (built later). Bare Date — no time, no TZ, no scheduler
    # coupling. Fully decoupled from `scheduled_at` (DateTime, one-shot fire)
    # and from the Gantt (milestone start_date / target_date). NULL = unset.
    # Migration 0057_milestones adds the column; PG 16 metadata-only ADD COLUMN.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Kanban #2127 (2026-06-11): queryable "blocked-on-operator" marker —
    # task-level rollup. `operator_gate` is one of the 5-enum
    # (key/commit/decision/hitl/external) or NULL (=not gated at the task level).
    # `operator_gate_note` is free-form advisory text for the specific ask.
    # Both set DIRECTLY by the Lead — NO auto-derivation, NO trigger, NO sweep
    # (explicit prohibition; the AC-level gate is the source of truth for "still
    # waiting on operator", the task-level column is a convenience rollup for
    # tasks without ACs / a direct flag). No DB CHECK on operator_gate — the
    # Pydantic OperatorGateLiteral at the API boundary gates the value set (422),
    # mirroring the #1677 model_override posture (nullable TEXT, no DB DEFAULT).
    # operator_gate_note has no length cap (advisory) and is settable
    # independently of operator_gate. Migration 0064's nullable=true backfills
    # existing rows to NULL. The "what's on me" filter
    # (GET /api/tasks?operator_gate=...) matches a task iff this column IS NOT
    # NULL [and equals the specific value] OR ≥1 AC item has gate='operator' AND
    # status='pending' [and gate_kind=<value>] — see routers/tasks.py list_tasks.
    operator_gate: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_gate_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Kanban #1011 (2026-05-20): HITL aging nudge cron dedup column.
    # The cron sets this to now() after every nudge attempt (success or
    # failure — see AC4 lockdown "ALWAYS UPDATE last_nudge_at"). The query
    # predicate "last_nudge_at IS NULL OR last_nudge_at < now() - interval
    # '24 hours'" prevents re-nudging within 24h. NULL = never nudged.
    last_nudge_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Kanban #1011 (2026-05-20): per-task nudge on/off toggle. Set true by
    # operator (PATCH /api/tasks/{id} nudge_disabled=true) to silence nudges
    # for one specific task even when the project default would otherwise fire.
    # NOT NULL DEFAULT false so existing rows backfill cleanly via migration
    # 0047 (PG 16 metadata-only ADD COLUMN).
    nudge_disabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )

    # Kanban #1240 (2026-06-07): auto-archive flag. The daily audit-archive
    # sweep (services/audit_archive.py) flips this to FALSE on COMPLETED audit
    # tasks older than AUDIT_ARCHIVE_DAYS (default 30). Orthogonal to `status`
    # (soft-delete 0/1) and `process_status` (lifecycle 1..6): an archived row
    # stays status=1 + process_status=5; is_active=false just hides it from the
    # default board/list view. GET /api/tasks default-excludes is_active=false;
    # opt-in ?include_archived=true fetches them. NOT NULL DEFAULT true backfills
    # existing rows to "visible" via migration 0061 (PG 16 metadata-only ADD
    # COLUMN — no heap rewrite). No DB CHECK (plain boolean — parity with
    # is_pending / requires_human_review / nudge_disabled).
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")

    # Kanban #1868: optional milestone grouping. lazy='select' (default) — the
    # rollup endpoint aggregates via a GROUP BY query, not this relationship.
    milestone: Mapped["Milestone | None"] = relationship(
        "Milestone", back_populates="tasks"
    )

    # Kanban #1302: resources pinned to this task (optional). NOT a
    # cascade-delete relationship — the DB-side ON DELETE SET NULL on
    # project_resources.task_id DETACHES resources when a task is hard-deleted
    # (the resource survives, unpinned). passive_deletes=True tells SQLAlchemy
    # to let the DB handle the SET NULL rather than loading + nulling itself.
    resources: Mapped[list["ProjectResource"]] = relationship(
        "ProjectResource",
        back_populates="task",
        passive_deletes=True,
    )

    # Self-referential adjacency-list (SQLAlchemy "Adjacency List" pattern).
    # `remote_side="Task.id"` disambiguates which side is the parent; without
    # it SQLAlchemy can't tell parent.id from children.parent_task_id.
    # `foreign_keys=` is required since V3+ T1 (Kanban #706) added a SECOND
    # self-FK column (spawned_from_task_id) — without an explicit selector
    # SQLAlchemy raises AmbiguousForeignKeysError.
    parent: Mapped["Task | None"] = relationship(
        "Task",
        remote_side="Task.id",
        back_populates="subtasks",
        lazy="select",
        foreign_keys=lambda: [Task.parent_task_id],
    )
    subtasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="parent",
        lazy="select",
        foreign_keys=lambda: [Task.parent_task_id],
    )

    __table_args__ = (
        CheckConstraint(
            in_clause("process_status", TaskStatus.ALL),
            name="ck_tasks_process_status_valid",
        ),
        CheckConstraint(
            in_clause("priority", TaskPriority.ALL),
            name="ck_tasks_priority_valid",
        ),
        # No ck_tasks_assigned_role_valid — app-layer validates per project team's
        # roster (dev=1..5, novel=11..12, etc.). DB CHECK was dropped 2026-05-08.
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_tasks_status_valid",
        ),
        # Mirror of migration 0005's ck_tasks_run_mode_valid — keeps ORM
        # autogenerate in lockstep with the live DDL.
        CheckConstraint(
            in_clause_text("run_mode", TaskRunMode.ALL),
            name="ck_tasks_run_mode_valid",
        ),
        # No-self-parent backstop (Kanban #238). The app rejects re-parenting via
        # PATCH 422 entirely; this CHECK catches raw-SQL drift.
        CheckConstraint(
            "parent_task_id IS NULL OR parent_task_id <> id",
            name="ck_tasks_parent_task_id_not_self",
        ),
        # No-self-blocker backstop (Kanban #771). App rejects self-blocker via
        # POST/PATCH 422; this CHECK catches raw-SQL drift. Mirror of
        # ck_tasks_parent_task_id_not_self in shape + intent.
        CheckConstraint(
            "blocked_by IS NULL OR blocked_by <> id",
            name="ck_tasks_blocked_by_not_self",
        ),
        # V3+ T1 (Kanban #706): mirror of migration 0007's CHECKs. task_kind
        # values + template completeness — DB defense-in-depth alongside the
        # Pydantic model_validator on TaskCreate.
        CheckConstraint(
            in_clause_text("task_kind", TaskKind.ALL),
            name="ck_tasks_task_kind_valid",
        ),
        # Kanban #803 (2026-05-12): mirror of migration 0015's CHECK.
        CheckConstraint(
            in_clause_text("task_type", TaskType.ALL),
            name="ck_tasks_task_type_valid",
        ),
        # Kanban #830 (2026-05-12): mirror of migration 0019's CHECK predicate.
        CheckConstraint(
            in_clause_text("interaction_kind", TaskInteractionKind.ALL),
            name="ck_tasks_interaction_kind_valid",
        ),
        CheckConstraint(
            "is_template = false OR (recurrence_rule IS NOT NULL "
            "AND next_fire_at IS NOT NULL)",
            name="ck_tasks_template_recurrence_complete",
        ),
        # V3+ T1 audit follow-up (Kanban #723): scheduled_at and is_template are
        # mutually exclusive. Mirror of migration 0010.
        CheckConstraint(
            "NOT (scheduled_at IS NOT NULL AND is_template = TRUE)",
            name="ck_tasks_scheduled_xor_template",
        ),
        # Kanban #952 (2026-05-16): retry counter is non-negative.
        # Mirror of migration 0030's CHECK — defense-in-depth.
        CheckConstraint(
            "audit_retry_count >= 0",
            name="ck_tasks_audit_retry_count_nonneg",
        ),
        # Kanban #1125 (2026-05-17): L21 prevention — max_active_children must
        # be NULL or strictly positive. Mirror of migration 0035's CHECK.
        CheckConstraint(
            "max_active_children IS NULL OR max_active_children > 0",
            name="ck_tasks_max_active_children_positive",
        ),
        # Kanban #1211 — GOV3 per-spawn override: when allow_during_pause=true
        # the reason must be present and >= 10 chars. Mirror of migration
        # 0040's CHECK; defense-in-depth against raw-SQL drift (Pydantic
        # TaskCreate also enforces; the DB CHECK catches direct INSERTs that
        # bypass the API layer).
        CheckConstraint(
            "allow_during_pause = FALSE OR "
            "(allow_during_pause_reason IS NOT NULL "
            "AND length(allow_during_pause_reason) >= 10)",
            name="ck_tasks_pause_reason_length",
        ),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_process_status", "process_status"),
        Index("ix_tasks_assigned_role", "assigned_role"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
        # Kanban #771: supports the reverse-lookup endpoint
        # GET /api/tasks/{id}/blocks (rows pointing AT a given blocker).
        Index("ix_tasks_blocked_by", "blocked_by"),
        # Kanban #1868: supports the `?milestone_id` task-list filter + the
        # milestone rollup GROUP BY. Mirror of migration 0057's index.
        Index("ix_tasks_milestone_id", "milestone_id"),
        # Partial index — scheduler hot path scans only the sparse template
        # subset. Mirror of migration 0007's postgresql_where predicate.
        Index(
            "ix_tasks_next_fire_at_template",
            "next_fire_at",
            postgresql_where=text("is_template = TRUE"),
        ),
        # V3+ T1 audit follow-up (Kanban #723): one-shot fire path. Mirror of
        # migration 0010's postgresql_where — keeps the index sparse so the
        # scheduler scan stays cheap.
        Index(
            "ix_tasks_scheduled_at_pending",
            "scheduled_at",
            postgresql_where=text(
                "scheduled_at IS NOT NULL AND process_status = 1 AND status = 1"
            ),
        ),
        # Kanban #1240: audit-archive sweep hot path — WHERE
        # task_type='audit' AND completed_at < <cutoff>. Mirror of migration
        # 0061's composite index (keeps ORM autogenerate in lockstep).
        Index(
            "ix_tasks_archive_sweep",
            "task_type",
            "completed_at",
        ),
        # Kanban #1240: tiny partial index for the rare "fetch archived rows"
        # path (?include_archived=true / archive audit). Mirror of migration
        # 0061's postgresql_where predicate so the index stays sparse.
        Index(
            "ix_tasks_active_archived",
            "is_active",
            postgresql_where=text("is_active = false"),
        ),
        # Kanban #2127: GIN index for the operator-gate AC-level filter
        # predicate. jsonb_path_ops opclass indexes the @> containment operator
        # ONLY (NOT jsonb_path_exists / @?) — the list_tasks filter uses @>
        # against the acceptance_criteria JSONB array. Mirror of migration
        # 0064's index (keeps ORM autogenerate in lockstep with the live DDL).
        Index(
            "ix_tasks_ac_gin",
            "acceptance_criteria",
            postgresql_using="gin",
            postgresql_ops={"acceptance_criteria": "jsonb_path_ops"},
        ),
        # Kanban #2505: next-autorun hot path — WHERE project_id=? AND
        # process_status=1 AND status=1 AND run_mode IN ('auto','auto_headless').
        # Partial predicate keeps the index sparse (~175 active-TODO rows today).
        # Covering INCLUDE avoids heap fetches for the priority-sort + scheduling
        # fields the caller reads. Mirror of migration 0071.
        Index(
            "ix_tasks_next_autorun",
            "project_id",
            "process_status",
            "status",
            "run_mode",
            postgresql_include=[
                "priority",
                "sort_order",
                "created_at",
                "halt_reason",
                "blocked_by",
                "scheduled_at",
            ],
            postgresql_where=text("status = 1 AND process_status = 1"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Task id={self.id} project_id={self.project_id} "
            f"process_status={self.process_status} status={self.status} "
            f"title={self.title!r}>"
        )


class TaskHistory(Base):
    """Audit trail for tasks (UPDATE/DELETE snapshots).

    Populated by the PG trigger `tasks_audit_trg` defined in the initial migration.
    Application code should NOT insert here directly — let the trigger do it so the
    history matches the actual DB state including out-of-band edits.
    """

    __tablename__ = "tasks_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Intentionally not a FK — survives task deletion.
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    operation: Mapped[str] = mapped_column(
        Text,  # CHAR(1) is enforced via CHECK; using Text keeps SQLAlchemy mapping simple.
        nullable=False,
    )

    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )

    __table_args__ = (
        # Kanban #1224 (2026-05-19): extended from `IN ('U','D')` to
        # `IN ('U','D','N')`. 'N' is the NOTIFY delivery-attempt audit row,
        # inserted directly by services/notification_router.py (the existing
        # tasks_audit_trg trigger writes 'U'/'D' only). Mirror of migration
        # 0041's CHECK predicate.
        CheckConstraint(
            "operation IN ('U', 'D', 'N')",
            name="ck_tasks_history_operation_valid",
        ),
        Index("ix_tasks_history_task_id", "task_id"),
        Index("ix_tasks_history_changed_at", "changed_at"),
    )

    # Reference the operation codes module so tooling shows the relationship.
    OPERATIONS = TaskHistoryOperation

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskHistory id={self.id} task_id={self.task_id} "
            f"op={self.operation} at={self.changed_at}>"
        )
