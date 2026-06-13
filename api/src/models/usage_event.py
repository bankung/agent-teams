"""UsageEvent ORM model (Kanban #2354).

An APPEND-ONLY token-usage ledger row. The Mode-A ingest endpoint
(`POST /api/usage/events`) writes one row per Claude Code turn / subagent
invocation; `cost_usd` is computed SERVER-SIDE from the token totals via
`services/cost_tracker`. Mirrors migration `0067_usage_events`.

APPEND-ONLY: there is NO edit path and NO delete path in the API. The table has
NO `updated_at` and NO soft-delete column — a row, once written, never changes.
The only removals are the FK rules: ON DELETE CASCADE when the parent project is
hard-deleted, ON DELETE SET NULL when the parent task is hard-deleted (a
token-usage fact outlives its task).

`occurred_at` is the event's REAL time (client-supplied, for cross-day/month
bucketing); `created_at` is the persist time. `dedup_key` is the idempotency key
— scoped to (project_id, dedup_key). A NULL dedup_key is always insertable;
a non-NULL repeat within the SAME project collapses to the existing row at the
endpoint layer. The same key string used across different projects inserts cleanly
as a distinct pair — no cross-project collision or enumeration oracle.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class UsageEvent(Base):
    """One append-only token-usage event (Kanban #2354).

    `provider` + `model` resolve to a `cost_tracker` price-card key; `cost_usd`
    is the server-computed USD cost (4dp). An unknown model still stores the row
    (tokens preserved, `cost_usd` = 0) — partial signal beats no signal.
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # The event's REAL time — client-supplied for cross-day/month bucketing,
    # defaults to now() when omitted. Distinct from created_at (persist time).
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ON DELETE CASCADE — the ledger row dies with its project.
    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ON DELETE SET NULL — a token-usage fact outlives its task row.
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The Claude Code session uuid STRING — NOT a FK onto `sessions`.
    session_ext_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Subagent name; NULL = Lead/main.
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'anthropic'"),
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)

    input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cache_read_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cache_creation_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    # Server-computed USD cost (4dp, same scale as session_runs.total_cost_usd).
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        server_default=text("0"),
    )

    is_estimate: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'mode_a'"),
    )

    # Idempotency key — NULL always insertable; non-NULL repeat within the SAME
    # project collapses to the existing row (the endpoint catches the unique-
    # violation / SELECTs first). Per-project scope means the same key string
    # used in a different project inserts cleanly (no cross-project collision).
    dedup_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Per-project idempotency guard — composite (project_id, dedup_key)
        # mirrors migration 0067's UNIQUE constraint (review fix M1/W1 2026-06-13).
        UniqueConstraint(
            "project_id", "dedup_key", name="uq_usage_events_project_dedup_key"
        ),
        Index("ix_usage_events_occurred_at", "occurred_at"),
        Index("ix_usage_events_project_id", "project_id"),
        Index("ix_usage_events_task_id", "task_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UsageEvent id={self.id} project_id={self.project_id} "
            f"model={self.model!r} cost_usd={self.cost_usd}>"
        )
