"""ProjectResource ORM model (Kanban #1302).

A file- or URL-attachment scoped to one Project, optionally pinned to one Task.
Rows are TAG-BEARING. Mirrors migration `0059_project_resources`.

Column-naming convention, parity with `tasks` / `milestones`:
  - `kind` (TEXT enum) is the DISCRIMINATOR — file / link (see
    `constants.ResourceKind`).
  - `status` (SMALLINT 0/1) is the uniform soft-delete flag (`RecordStatus`).

SCHEMA-ONLY this slice (#1302 / X.1) — no service / router queries this table
yet (the upload endpoint is #1309 / X.2). The `back_populates` relationships are
declared for parity with the rest of the model layer; the live API does not
traverse them until X.2 ships.

FK posture (AC5):
  - `project_id` ON DELETE CASCADE — a resource dies with its project.
  - `task_id` ON DELETE SET NULL — a resource SURVIVES task deletion (it just
    detaches / unpins). Mirrors `tasks.blocked_by` / `tasks.milestone_id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import RecordStatus, ResourceKind, in_clause, in_clause_text
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project
    from src.models.task import Task


class ProjectResource(Base):
    """A file- or URL-attachment scoped to one Project (optionally pinned to a Task).

    `kind` (TEXT) holds the discriminator (file / link); `status` (0/1) is the
    soft-delete flag. Per-kind required fields ('file' needs `filename`, 'link'
    needs `url`) are enforced both at the DB level (CHECK
    `ck_project_resources_kind_fields`) and at the API boundary (Pydantic
    ResourceCreate model_validator). `tags` is a JSONB list of strings —
    element-shape validation lives at the API layer (mirrors
    `projects.sources` / `projects.required_binaries`).
    """

    __tablename__ = "project_resources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # AC5: ON DELETE CASCADE — resource dies with its project.
    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # AC5: ON DELETE SET NULL — resource SURVIVES task deletion (detaches).
    # Mirrors tasks.blocked_by / tasks.milestone_id posture.
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Discriminator — TEXT NOT NULL + CHECK. Mirror of migration 0059's
    # ck_project_resources_kind_valid.
    kind: Mapped[str] = mapped_column(Text, nullable=False)

    # Per-kind payload. 'file' rows require `filename`; 'link' rows require
    # `url` (DB CHECK ck_project_resources_kind_fields + Pydantic validator).
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # File metadata — populated by the upload endpoint (#1309). NULL for links.
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    label: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tag-bearing (#1302) — JSONB list of strings. Mirror of projects.sources /
    # projects.required_binaries (element-shape validated at the API layer; no
    # DB CHECK on shape). server_default '[]' so omitted rows read an empty
    # list; Python-side default=list keeps INSERT-without-explicit a list.
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    # Uniform soft-delete flag (RecordStatus). Distinct from `kind`.
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

    project: Mapped["Project"] = relationship(
        "Project", back_populates="resources"
    )
    # Pinned task (optional). passive_deletes=True lets the DB-side ON DELETE SET
    # NULL detach this resource when the task is hard-deleted, rather than
    # SQLAlchemy trying to manage it.
    task: Mapped["Task | None"] = relationship(
        "Task", back_populates="resources", passive_deletes=True
    )

    __table_args__ = (
        # Mirror of migration 0059's CHECKs — keeps ORM autogenerate in lockstep
        # with the live DDL.
        CheckConstraint(
            in_clause_text("kind", ResourceKind.ALL),
            name="ck_project_resources_kind_valid",
        ),
        # AC3: per-kind required fields. Mirror of migration 0059's
        # ck_project_resources_kind_fields.
        CheckConstraint(
            "(kind = 'file' AND filename IS NOT NULL) "
            "OR (kind = 'link' AND url IS NOT NULL)",
            name="ck_project_resources_kind_fields",
        ),
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_project_resources_status_valid",
        ),
        # AC4: project_id index — list-by-project hot query.
        Index("ix_project_resources_project_id", "project_id"),
        # AC4: task_id PARTIAL index — sparse reverse-lookup. Mirror of migration
        # 0059's postgresql_where predicate.
        Index(
            "ix_project_resources_task_id",
            "task_id",
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProjectResource id={self.id} project_id={self.project_id} "
            f"task_id={self.task_id} kind={self.kind!r} status={self.status}>"
        )
