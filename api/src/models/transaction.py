"""Transaction ORM model (Kanban #953 — financial separation).

A `transactions` row is one ledger entry scoped to a project. Together with
the four `projects.*` financial columns (tax_jurisdiction, legal_entity,
fiscal_year_start, currency_default — see project.py) this gives each
Kanban project an isolated accounting unit.

`amount_minor` is stored as BIGINT minor units (cents, satang). NEVER use
FLOAT for money — half-even rounding bugs are silent and catastrophic in
ledgers. The wire surface converts minor → major (Decimal) for the P&L
summary view; the raw column stays integer.

`kind` is the gated vocabulary (CHECK ck_transactions_kind_valid):
  - revenue   : money in from customers / sales
  - cost      : direct cost-of-goods-sold (LLM API spend lands here)
  - expense   : operating expense (hosting, tooling)
  - refund    : reverses prior revenue
  - transfer  : neutral bookkeeping move between accounts (net=0 in P&L)

`category` is free-form tagging on top of `kind` (e.g. `llm_anthropic`,
`stripe_sale`, `hosting`). No CHECK — categories evolve faster than
migrations.

`task_id` is OPTIONAL — the #944 cost hook auto-fills it for LLM-cost
auto-inserts, but manual revenue/expense rows leave it NULL. FK ON DELETE
SET NULL so hard-deleting a task (rare; usually soft-delete via status=0)
leaves the ledger intact.

No audit trigger this slice — same precedent as `sessions` / `session_runs`
(per db-schema.md). Add if accountant flow needs change history.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.project import Project
    from src.models.task import Task


# Vocabulary for transactions.kind. Mirrors the CHECK in migration 0032.
# Kept as a module constant so the Pydantic Literal can stay in lockstep.
TRANSACTION_KINDS: tuple[str, ...] = (
    "revenue",
    "cost",
    "expense",
    "refund",
    "transfer",
)


class Transaction(Base):
    """A ledger entry scoped to a project (Kanban #953)."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # BIGINT minor units (cents, satang). NEVER use FLOAT for money.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ISO 4217 alpha-3 code. CHAR(3) for the column shape; the value is
    # uppercase by convention but no DB CHECK — accountant flows occasionally
    # carry historical mixed-case data and we don't want a migration block on
    # case alone.
    currency: Mapped[str] = mapped_column(
        CHAR(3),
        nullable=False,
        server_default=text("'USD'"),
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped["Project"] = relationship("Project", back_populates="transactions")
    task: Mapped["Task | None"] = relationship("Task", lazy="select")

    __table_args__ = (
        # Mirror of migration 0032's ck_transactions_kind_valid — keeps ORM
        # autogen in lockstep with the live DDL.
        CheckConstraint(
            "kind IN ('revenue', 'cost', 'expense', 'refund', 'transfer')",
            name="ck_transactions_kind_valid",
        ),
        # Covers per-project list (default sort) + period-range filters used
        # by the P&L summary endpoint. occurred_at DESC matches the default
        # GET /api/transactions response order.
        Index(
            "ix_transactions_project_occurred",
            "project_id",
            text("occurred_at DESC"),
        ),
        # Partial — supports the auto-insert hook's idempotency check + the
        # "all txns for this task" reverse lookup. Sparse because most txns
        # are manual (no task_id).
        Index(
            "ix_transactions_task_id",
            "task_id",
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Transaction id={self.id} project_id={self.project_id} "
            f"kind={self.kind!r} amount_minor={self.amount_minor} "
            f"currency={self.currency!r}>"
        )
