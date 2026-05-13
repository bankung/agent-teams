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

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    from src.models.project import Project


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
    # Self-ref FK: spawned children point at the template they came from.
    # ON DELETE SET NULL — defense-in-depth; app never hard-deletes templates.
    spawned_from_task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
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

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")

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
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_process_status", "process_status"),
        Index("ix_tasks_assigned_role", "assigned_role"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
        # Kanban #771: supports the reverse-lookup endpoint
        # GET /api/tasks/{id}/blocks (rows pointing AT a given blocker).
        Index("ix_tasks_blocked_by", "blocked_by"),
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
        CheckConstraint(
            "operation IN ('U', 'D')",
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
