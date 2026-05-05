"""Project ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.task import Task


class Project(Base):
    """A registered project — typically maps to one Next.js + FastAPI + DB stack on disk.

    `is_active` has a partial unique index so at most one project can be active.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
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
        Index(
            "ux_projects_active_one",
            "is_active",
            unique=True,
            postgresql_where=(is_active.is_(True)),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} name={self.name!r} active={self.is_active}>"
