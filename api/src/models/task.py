"""Task and TaskHistory ORM models.

`Task` mirrors the Kanban schema described in `context/standards/general.md`:
process_status, priority are INTEGER columns with CHECK constraints — canonical
codes live in `src.constants` (TaskStatus, TaskPriority). `assigned_role` no
longer carries a DB CHECK — application code validates against the active
project's lead roster (codes 1..5 for dev, 11..12 for novel, etc.).

`TaskHistory` is an audit-only sink populated by a PG trigger on the `tasks` table
(AFTER UPDATE OR DELETE). `task_id` is intentionally NOT a FK — when a task row
is deleted we still want the historical record to live on.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import (
    RecordStatus,
    TaskHistoryOperation,
    TaskPriority,
    TaskStatus,
    in_clause,
)
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project


class Task(Base):
    """A Kanban task scoped to a Project.

    `process_status` (1..5) holds the lifecycle code (TODO/IN_PROGRESS/REVIEW/
    BLOCKED/DONE — see TaskStatus). `status` (0/1) is the uniform soft-delete
    flag (RecordStatus). `assigned_role` is an integer with no DB CHECK — the
    app validates per active project's lead roster.

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
    parent: Mapped["Task | None"] = relationship(
        "Task",
        remote_side="Task.id",
        back_populates="subtasks",
        lazy="select",
    )
    subtasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="parent",
        lazy="select",
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
        # No ck_tasks_assigned_role_valid — app-layer validates per project lead's
        # roster (dev=1..5, novel=11..12, etc.). DB CHECK was dropped 2026-05-08.
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_tasks_status_valid",
        ),
        # No-self-parent backstop (Kanban #238). The app rejects re-parenting via
        # PATCH 422 entirely; this CHECK catches raw-SQL drift.
        CheckConstraint(
            "parent_task_id IS NULL OR parent_task_id <> id",
            name="ck_tasks_parent_task_id_not_self",
        ),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_process_status", "process_status"),
        Index("ix_tasks_assigned_role", "assigned_role"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
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
