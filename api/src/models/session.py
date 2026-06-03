"""Session ORM models — sessions / session_runs / session_compacts.

CTX-1 (Kanban #716): foundation slice for context-management hybrid storage.
DB rows hold metadata + queryability; markdown content lives on disk under
`<repo_root>/_sessions/<id>/`.

NO audit trigger on these tables (unlike `tasks`). Sessions self-audit via
`session_compacts` rows + per-compact archive files.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project
    from src.models.task import Task


class Session(Base):
    """One row per Lead bootstrap (or master-agent process) for a project.

    Per project x per Claude Code instance — multiple `status='active'` rows
    per project_id are allowed (multi-instance support; one terminal binds
    to one project, terminals run in parallel). The partial index
    `ix_sessions_project_id_active` is a hot-path scan accelerator, NOT a
    uniqueness gate.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    process_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 'active' | 'compacting' | 'closed' — CHECK constrained.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="active",
    )

    # Soft token budget (per-run); NULL = no budget. Pre-flight count emits a
    # `budget_warning=true` on session_runs; never blocks.
    token_budget_per_run: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Compact ceilings — when summed `total_context_chars` (or token estimate)
    # exceeds these, CTX-4 triggers a compact pass.
    compacted_history_ceiling_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("13000"),
    )
    recent_activity_ceiling_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("15000"),
    )
    # CTX-1 audit follow-up (Kanban #722, migration 0009): doc 4-bucket
    # breakdown's two remaining ceilings. CTX-3 token counter reads these.
    card_detail_ceiling_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("6000"),
    )
    output_budget_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("4000"),
    )

    # Filesystem root — set by router post-INSERT (`_sessions/<id>/`).
    session_root_path: Mapped[str] = mapped_column(Text, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    # One-way reference to the parent project — no reverse relationship on
    # Project (sessions don't fan out from project listings).
    project: Mapped["Project"] = relationship("Project")

    runs: Mapped[list["SessionRun"]] = relationship(
        "SessionRun",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    compacts: Mapped[list["SessionCompact"]] = relationship(
        "SessionCompact",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','compacting','closed')",
            name="ck_sessions_status_valid",
        ),
        Index("ix_sessions_project_id", "project_id"),
        Index(
            "ix_sessions_project_id_active",
            "project_id",
            postgresql_where=text("status = 'active'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Session id={self.id} project_id={self.project_id} "
            f"status={self.status!r} root={self.session_root_path!r}>"
        )


class SessionRun(Base):
    """One row per task fire / manual run within a session.

    Cost / token totals roll up here (CTX-3 wires the real values; CTX-1
    accepts user-supplied totals on PATCH for now). FK to `tasks` is
    `ON DELETE SET NULL` — preserves the run audit row when a task is
    later hard-deleted.
    """

    __tablename__ = "session_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 'running' | 'done' | 'error' | 'timeout' — CHECK constrained.
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="running",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    total_input_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    total_output_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    # G2 (#1689): real prompt-cache token counts from Anthropic usage objects.
    # cache_read_input_tokens   — served from cache (billed at 0.10x input rate).
    # cache_creation_input_tokens — written to cache (billed at 1.25x input rate).
    # Persisted so cost_usd reflects actual cache-hit/miss spend and the UI can
    # display cache efficiency per run. Both default 0 (= "no cache observed").
    cache_read_input_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    cache_creation_input_tokens: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    total_context_chars: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        server_default=text("0"),
    )
    budget_warning: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    # `_sessions/<sid>/cards/<task_id>.md` — set by router post-INSERT when
    # task_id is given.
    card_log_path: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    session: Mapped["Session"] = relationship("Session", back_populates="runs")
    task: Mapped["Task | None"] = relationship("Task")

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','done','error','timeout')",
            name="ck_session_runs_status_valid",
        ),
        Index("ix_session_runs_session_id", "session_id"),
        Index("ix_session_runs_task_id", "task_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SessionRun id={self.id} session_id={self.session_id} "
            f"task_id={self.task_id} status={self.status!r}>"
        )


class SessionCompact(Base):
    """One row per compact event within a session.

    CTX-4 wires the runner; CTX-1 only ships the schema + read endpoints.
    """

    __tablename__ = "session_compacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'size' | 'manual' | 'run_count' — CHECK constrained.
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # `_sessions/<sid>/archive/compact_NNN.md` — pre-compact snapshot path.
    archive_path: Mapped[str] = mapped_column(Text, nullable=False)
    before_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    after_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compact_model: Mapped[str] = mapped_column(String(64), nullable=False)
    compact_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        server_default=text("0"),
    )
    compacted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped["Session"] = relationship("Session", back_populates="compacts")

    __table_args__ = (
        CheckConstraint(
            "trigger_kind IN ('size','manual','run_count')",
            name="ck_session_compacts_trigger_valid",
        ),
        Index("ix_session_compacts_session_id", "session_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SessionCompact id={self.id} session_id={self.session_id} "
            f"trigger={self.trigger_kind!r} model={self.compact_model!r}>"
        )
