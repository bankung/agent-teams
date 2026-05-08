"""Project ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import ProjectLead, RecordStatus, in_clause
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.task import Task


class Project(Base):
    """A registered project — typically maps to one Next.js + FastAPI + DB stack on disk.

    `is_active` has a partial unique index so at most one ACTIVE project can be active.
    Soft-delete: `status=1` active, `status=0` deleted; uniqueness on `name` is also
    partial (gated on `status=1`) so a name can be reused after a soft delete.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # `name` uniqueness is enforced via partial unique index `ux_projects_name_active`
    # (see __table_args__) — NOT via column-level UNIQUE — so soft-deleted rows free
    # the name for re-use.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    paths_web: Mapped[str] = mapped_column(Text, nullable=False)
    paths_api: Mapped[str] = mapped_column(Text, nullable=False)
    paths_db: Mapped[str] = mapped_column(Text, nullable=False)

    stack_web: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_api: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_db: Mapped[str | None] = mapped_column(Text, nullable=True)

    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
        default=dict,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        default=False,
    )

    lead: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=ProjectLead.DEV,
        default=ProjectLead.DEV,
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

    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_projects_status_valid",
        ),
        CheckConstraint(
            f"lead IN ({', '.join(repr(v) for v in ProjectLead.ALL)})",
            name="ck_projects_lead_valid",
        ),
        # Partial unique on name — only one ACTIVE row per name; soft-deleted rows
        # don't occupy the unique slot, so the name can be reused.
        Index(
            "ux_projects_name_active",
            "name",
            unique=True,
            postgresql_where=(status == 1),
        ),
        # Partial unique on is_active — at most one ACTIVE project. Predicate now
        # also gates on status=1 so a soft-deleted "active" row doesn't block a
        # new active project from being created.
        Index(
            "ux_projects_active_one",
            "is_active",
            unique=True,
            postgresql_where=(is_active.is_(True) & (status == 1)),
        ),
        Index("ix_projects_status", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Project id={self.id} name={self.name!r} "
            f"active={self.is_active} status={self.status} lead={self.lead!r}>"
        )
