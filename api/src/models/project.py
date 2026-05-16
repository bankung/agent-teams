"""Project ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.constants import ProjectTeam, RecordStatus, in_clause, in_clause_text
from src.models.base import Base

if TYPE_CHECKING:
    from src.models.task import Task


class Project(Base):
    """A registered project — typically maps to one Next.js + FastAPI + DB stack on disk.

    `is_active` is a free boolean — multiple rows may carry `is_active=true`
    simultaneously. Each Claude Code session binds to a project by name at
    bootstrap (Kanban #694, session-scoped active); the legacy "single active
    project" invariant + its partial unique index `ux_projects_active_one`
    were dropped by `0006_drop_active_one`.

    Soft-delete: `status=1` active, `status=0` deleted; uniqueness on `name` is
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

    team: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=ProjectTeam.DEV,
        default=ProjectTeam.DEV,
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

    # Step 2 (Kanban #481/#483): per-project consent gate for Mode B (auto_headless).
    # NULL = no consent yet. The first POST /api/projects/{id}/grant-consent stamps
    # this; re-grant is idempotent (no re-stamp). Cross-table rule lives in
    # src/services/run_mode.py — see decisions.md 2026-05-09.
    auto_run_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Kanban #777: project-root metadata. Orthogonal to paths_web/api/db (which
    # are per-lane sub-paths); working_path is the single project root.
    working_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    working_repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_overrides: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default="{}",
        default=dict,
    )

    # Kanban #778 (2026-05-13): per-project curated source list. Element shape
    # ({url, label?, kind?}) validated at the API boundary by Pydantic SourceEntry;
    # NO DB CHECK on element shape (mirrors `config` / `agent_overrides` /
    # `tasks.acceptance_criteria` precedent). DB CHECK `ck_projects_sources_length`
    # caps array length <= 20 as defense-in-depth — Pydantic `max_length=20` is the
    # first wall. nullable=True with server_default '[]'::jsonb: pre-existing rows
    # read `[]` via the default (PG 16 metadata-only ADD COLUMN); ORM dict-default
    # `list` keeps Python-side INSERT a list rather than None when omitted.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    # Kanban #951 (2026-05-16): per-project budget caps for the headless-engine
    # pickup gate. All three NULL = UNLIMITED (pre-#951 default behavior).
    # NUMERIC(10,2) — user-typed dollars, 2 places. Pydantic ProjectUpdate
    # validates `Decimal >= 0` at the boundary; DB CHECK
    # `ck_projects_budget_caps_nonneg` catches raw-SQL drift. The
    # budget_enforcer service short-circuits on all-NULL → no warn / no halt.
    # See migration 0026 for "reset" semantics (free via on-demand
    # `compute_spend(since=midnight)`; no scheduled job).
    budget_daily_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    budget_monthly_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    budget_total_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    # Kanban #979 (2026-05-16): per-project specialist-tool permission gate
    # config. Drives `langgraph/tools/permission_gate.check_permission()`.
    # Locked default (Q2 Option B, design lock #949) lives in migration
    # 0027 as the column's PG-level server_default + a backfill UPDATE so
    # every existing row also reads the dict, never NULL. NULL semantics
    # at the gate = "kill switch on / reject everything" (defensive — same
    # outcome as `tools_enabled=false`). API boundary validates element
    # shape via `ToolsConfig` Pydantic model; NO DB CHECK on shape
    # (mirrors `config` / `agent_overrides` / `sources` / acceptance_criteria
    # precedent — JSONB element-shape validation lives at the API layer).
    # No Python-side `default=` here: POST /api/projects intentionally OMITS
    # the column from INSERT so the PG server_default fires (parity with
    # `agent_overrides` "omit-when-None" pattern in the router).
    tools_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Kanban #989 (2026-05-16): per-project HITL timeout. NULL = pause
    # indefinitely (pre-#989 behavior — preserved as default). When set, the
    # on-demand gate inside GET /api/tasks/next-autorun stamps
    # `halt_reason='hitl_timeout'` on any BLOCKED HITL task
    # (halt_reason IN 'question'/'decision') whose updated_at is older than
    # the threshold. Halt-only; never auto-cancels. Pydantic ProjectUpdate
    # enforces `ge=1` (422 boundary); DB CHECK `ck_projects_hitl_timeout_positive`
    # catches raw-SQL drift. See migration 0029 for the locked design
    # rationale (Q2 → A: on-demand enforcement, not APScheduler).
    hitl_timeout_hours: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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
        # ProjectTeam.ALL is read at class-definition time — don't mutate it post-import.
        CheckConstraint(
            in_clause_text("team", ProjectTeam.ALL),
            name="ck_projects_team_valid",
        ),
        # Partial unique on name — only one ACTIVE row per name; soft-deleted rows
        # don't occupy the unique slot, so the name can be reused.
        Index(
            "ux_projects_name_active",
            "name",
            unique=True,
            postgresql_where=(status == 1),
        ),
        # NOTE: the partial unique on is_active (`ux_projects_active_one`) was
        # dropped by `0006_drop_active_one` — Phase 2 of the session-scoped
        # active project shift (Kanban #694). Multiple rows may now legitimately
        # have `is_active=true` because each Claude Code session binds to a
        # project by name independently.
        Index("ix_projects_status", "status"),
        # Kanban #951 — budget caps must be >= 0 (NULL = unlimited). Mirror of
        # migration 0026's named CHECK so ORM autogen stays in lockstep.
        CheckConstraint(
            "(budget_daily_usd IS NULL OR budget_daily_usd >= 0) AND "
            "(budget_monthly_usd IS NULL OR budget_monthly_usd >= 0) AND "
            "(budget_total_usd IS NULL OR budget_total_usd >= 0)",
            name="ck_projects_budget_caps_nonneg",
        ),
        # Kanban #989 — HITL timeout must be >= 1 hour when set (NULL =
        # indefinite pause, current behavior). Mirror of migration 0029's
        # named CHECK so ORM autogen stays in lockstep.
        CheckConstraint(
            "hitl_timeout_hours IS NULL OR hitl_timeout_hours >= 1",
            name="ck_projects_hitl_timeout_positive",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Project id={self.id} name={self.name!r} "
            f"active={self.is_active} status={self.status} team={self.team!r}>"
        )
