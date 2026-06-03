"""Milestone ORM model (Kanban #1868).

Per-project release-planning grouping of tasks. Mirrors migration
`0057_milestones`.

Column-naming convention (#1868), parity with `tasks`:
  - `milestone_status` (TEXT enum) is the LIFECYCLE code — planned / active /
    released / cancelled (see `constants.MilestoneStatus`).
  - `status` (SMALLINT 0/1) is the uniform soft-delete flag (`RecordStatus`).
This is the same separation `tasks` uses for `process_status` (lifecycle) vs
`status` (soft-delete). App code never issues a SQL DELETE — soft-delete only.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import MilestoneStatus, RecordStatus, in_clause, in_clause_text
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project
    from src.models.task import Task


class Milestone(Base):
    """A release-planning grouping of tasks, scoped to one Project.

    `milestone_status` (TEXT) holds the lifecycle code; `status` (0/1) is the
    soft-delete flag. `released_at` is operator-managed — stamped when the
    milestone is released (no auto-stamp this slice).
    """

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Lifecycle code — TEXT NOT NULL DEFAULT 'planned' + CHECK. Mirror of
    # migration 0057's ck_milestones_milestone_status_valid.
    milestone_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'planned'"),
        default=MilestoneStatus.PLANNED,
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Within-project manual ordering — mirror of tasks.sort_order (sparse-float,
    # NULL = created_at fallback). No CHECK / FK / index this slice.
    sort_order: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )

    # Uniform soft-delete flag (RecordStatus). Distinct from milestone_status.
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
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    project: Mapped["Project"] = relationship(
        "Project", back_populates="milestones"
    )

    # Child tasks pointing at this milestone via tasks.milestone_id. NOT a
    # cascade-delete relationship — the DB-side ON DELETE SET NULL detaches
    # tasks when a milestone is hard-deleted; the app soft-deletes the
    # milestone and NULLs children in the same transaction at the router.
    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="milestone",
        passive_deletes=True,
    )

    __table_args__ = (
        # Mirror of migration 0057's CHECKs — keeps ORM autogenerate in lockstep
        # with the live DDL.
        CheckConstraint(
            in_clause_text("milestone_status", MilestoneStatus.ALL),
            name="ck_milestones_milestone_status_valid",
        ),
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_milestones_status_valid",
        ),
        Index("ix_milestones_project_id", "project_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Milestone id={self.id} project_id={self.project_id} "
            f"milestone_status={self.milestone_status!r} status={self.status} "
            f"title={self.title!r}>"
        )
