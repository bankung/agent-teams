"""TaskTemplate ORM model (Kanban #1303).

A per-TEAM reusable Kanban-task starting point: a name/icon + a mustache-style
({{placeholder}}) description + an AC template array + default task metadata.
Mirrors migration `0060_task_templates`.

Column-naming convention, parity with `tasks` / `milestones` /
`project_resources`:
  - `status` (SMALLINT 0/1) is the uniform soft-delete flag (`RecordStatus`);
    0 = disabled/soft-deleted, 1 = active.

TEAM VALIDATION (#1620 doctrine): `team` is plain TEXT with NO DB CHECK and NO
ORM CheckConstraint. It is validated APP-SIDE against `constants.ProjectTeam.ALL`
by the router (mirror of `routers/projects.py`), exactly as `projects.team` is
since the per-team CHECK was dropped by migration 0051. The same posture applies
to the enum-bearing `default_task_type` / `default_task_kind` columns.

`updated_at` is NULLABLE and set EXPLICITLY by the router on PATCH (no DB
trigger) — NULL until first edit. This intentionally differs from
project_resources (which keeps updated_at NOT NULL DEFAULT now()) and follows the
#1303 spec.

The ONLY ORM CheckConstraint is the soft-delete `status` gate, mirroring
migration 0060 to keep ORM autogenerate in lockstep with the live DDL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants import (
    RecordStatus,
    TaskKind,
    TaskPriority,
    TaskType,
    in_clause,
)
from src.models.base import Base


class TaskTemplate(Base):
    """A per-team reusable Kanban-task starting point.

    `team` (TEXT) is the owning team — validated at the API boundary against
    `ProjectTeam.ALL` (no DB CHECK, per #1620). `description_template` +
    `acceptance_criteria_template[].text` carry mustache {{placeholder}} tokens
    rendered by `services/template_render.render_template`. `status` (0/1) is the
    uniform soft-delete flag.
    """

    __tablename__ = "task_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # App-validated against ProjectTeam.ALL — NO DB CHECK / ORM CheckConstraint
    # (#1620 doctrine; mirror of projects.team).
    team: Mapped[str] = mapped_column(Text, nullable=False)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Mustache-style body — {{placeholder}} tokens rendered at spawn time.
    description_template: Mapped[str] = mapped_column(Text, nullable=False)

    # AC template array — list of {text, ...} objects; each `text` is also
    # mustache-rendered. Element shape validated at the API layer (mirror of
    # projects.sources / project_resources.tags). server_default '[]' + Python
    # default=list so INSERT-without-explicit lands an empty list, not NULL.
    acceptance_criteria_template: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    # Default task metadata seeded onto tasks spawned from this template.
    # App-validated (TaskType / TaskPriority / TaskKind) — NO DB CHECK.
    default_task_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'feature'"),
        default=TaskType.FEATURE,
    )
    default_priority: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("2"),
        default=TaskPriority.NORMAL,
    )
    default_task_kind: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'ai'"),
        default=TaskKind.AI,
    )

    # Declared placeholder names — JSONB list of strings. Element shape
    # validated at the API layer. server_default '[]' + Python default=list.
    placeholders: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    # Uniform soft-delete flag (RecordStatus) — 0=disabled, 1=active.
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
    # NULLABLE — set explicitly by the router on PATCH (no DB trigger). NULL
    # until first edit (#1303 spec).
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # The ONLY CHECK on this table — soft-delete flag gate. Mirror of
        # migration 0060's ck_task_templates_status_valid. (team /
        # default_task_type / default_task_kind are app-validated, NO CHECK.)
        CheckConstraint(
            in_clause("status", RecordStatus.ALL),
            name="ck_task_templates_status_valid",
        ),
        # AC4: composite (team, status) index — serves the hot list query
        # `WHERE team=$1 AND status=1`. Mirror of migration 0060.
        Index("idx_task_templates_team_status", "team", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskTemplate id={self.id} team={self.team!r} "
            f"name={self.name!r} status={self.status}>"
        )
