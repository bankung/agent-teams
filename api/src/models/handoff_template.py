"""HandoffTemplate ORM model — auto-handoff recipe (Kanban #1004).

Operator-CRUD-able recipe for spawning a follow-up task when a parent task
flips to DONE. Mirrors migration `0045_handoff_templates`.

Loop-guard discipline lives in `services/handoff_spawn.py` — the child row's
`handoff_template_id` is explicitly set to NULL by the spawn service so the
chain terminates after one level (AC6).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants import RecordStatus, TaskKind, TaskType, in_clause_text
from src.models.base import Base

if TYPE_CHECKING:
    pass


class HandoffTemplate(Base):
    """A reusable recipe for auto-handoff child tasks.

    `project_id` NULL means the template is global (cross-project).
    Non-null scopes it to a single project. Uniqueness of `name` is enforced
    per-project via a partial unique index on (name, COALESCE(project_id, 0))
    WHERE status=1 — see migration 0045.
    """

    __tablename__ = "handoff_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Python `str.format(parent_title=...)` template. Validated at the API
    # boundary to confirm `{parent_title}` is referenced; runtime KeyError /
    # IndexError on a malformed pattern surfaces as 422 in the PATCH spawn
    # hook (services/handoff_spawn.py).
    title_pattern: Mapped[str] = mapped_column(String(512), nullable=False)

    task_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False)

    default_priority: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default="3",
        default=3,
    )
    default_assigned_role: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # list[str] outline → each entry becomes a {text, status="pending"}
    # AcceptanceCriterion on the spawned child's acceptance_criteria.
    ac_outline: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    carry_context_to_comment: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )

    project_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
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

    __table_args__ = (
        CheckConstraint(
            "status IN (0, 1)", name="ck_handoff_templates_status_valid"
        ),
        # Mirrors of migration 0045's CHECKs — keep ORM autogenerate in lockstep
        # with the live DDL.
        CheckConstraint(
            in_clause_text("task_kind", TaskKind.ALL),
            name="ck_handoff_templates_task_kind_valid",
        ),
        CheckConstraint(
            in_clause_text("task_type", TaskType.ALL),
            name="ck_handoff_templates_task_type_valid",
        ),
        CheckConstraint(
            "default_priority IN (1, 2, 3, 4)",
            name="ck_handoff_templates_default_priority_valid",
        ),
        CheckConstraint(
            "default_assigned_role IS NULL OR "
            "(default_assigned_role >= 1 AND default_assigned_role <= 50)",
            name="ck_handoff_templates_default_assigned_role_range",
        ),
        Index("ix_handoff_templates_status", "status"),
        Index("ix_handoff_templates_project_id", "project_id"),
        # Partial unique index — declared via Index for ORM autogenerate
        # parity. The migration uses the same predicate.
        Index(
            "ux_handoff_templates_name_project",
            "name",
            text("COALESCE(project_id, 0)"),
            unique=True,
            postgresql_where=text("status = 1"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<HandoffTemplate id={self.id} name={self.name!r} "
            f"project_id={self.project_id} status={self.status}>"
        )
