"""Task and TaskHistory ORM models.

`Task` mirrors the Kanban schema described in `context/standards/general.md`:
status, priority, and assigned_role are INTEGER columns with CHECK constraints —
canonical codes live in `src.constants` (TaskStatus, TaskPriority, TaskRole).

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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import (
    TaskHistoryOperation,
    TaskPriority,
    TaskRole,
    TaskStatus,
    in_clause,
)
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project


class Task(Base):
    """A Kanban task scoped to a Project.

    Status / priority / assigned_role use stable integer codes (see src.constants).
    Lifecycle timestamps `started_at` / `completed_at` are managed by the API layer
    on status transitions (PATCH /api/tasks/{id}).
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[int] = mapped_column(
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

    __table_args__ = (
        CheckConstraint(
            in_clause("status", TaskStatus.ALL),
            name="ck_tasks_status_valid",
        ),
        CheckConstraint(
            in_clause("priority", TaskPriority.ALL),
            name="ck_tasks_priority_valid",
        ),
        CheckConstraint(
            f"assigned_role IS NULL OR {in_clause('assigned_role', TaskRole.ALL)}",
            name="ck_tasks_assigned_role_valid",
        ),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_assigned_role", "assigned_role"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Task id={self.id} project_id={self.project_id} "
            f"status={self.status} title={self.title!r}>"
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
