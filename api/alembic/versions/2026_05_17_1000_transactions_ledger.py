"""transactions ledger + per-project financial separation (Kanban #953)

Revision ID: 0032_transactions_ledger
Revises: 0031_health_monitor
Create Date: 2026-05-17 10:00 UTC

Per-project accounting isolation. Each project becomes an isolated accounting
unit with its own ledger (revenue / cost / expense / refund / transfer) and
P&L surface. Builds on #944 (`tasks.estimated_cost_usd`) — task close
auto-inserts a `cost` transaction so the ledger stays complete without
manual reconciliation.

Two surfaces in one slice:

1. `transactions` table — the ledger. Per-project; FK ON DELETE CASCADE
   so project soft-delete + future hard-delete leaves no orphans. Amounts
   stored as `amount_minor BIGINT` (cents/satang) — no FLOAT for money.
   `kind` CHECK gates the 5 vocabulary values; `category` is free-form
   tagging (e.g. `llm_anthropic`, `stripe_sale`, `hosting`). Optional
   `task_id` FK ON DELETE SET NULL ties auto-inserted cost entries to
   their source task without blocking task delete.

2. `projects` financial-separation columns:
   - `tax_jurisdiction` TEXT NULL — free-form tax region code.
   - `legal_entity` TEXT NULL — owning entity for accountant hand-off.
   - `fiscal_year_start` INTEGER DEFAULT 1 — month-of-year (1..12).
   - `currency_default` CHAR(3) DEFAULT 'USD' — default txn currency.
   `fiscal_year_start` carries a CHECK 1..12 (defense-in-depth against
   raw-SQL drift; Pydantic ProjectUpdate is the first wall at 422).

Indexes:
  - `ix_transactions_project_occurred` on (project_id, occurred_at DESC) —
    covers the per-project ledger list (default sort) + period-range filters
    used by the P&L summary.
  - `ix_transactions_task_id` partial on (task_id) WHERE task_id IS NOT NULL
    — supports the reverse-lookup "all txns for this task" query plus the
    idempotency check used by the auto-insert hook in tasks.py PATCH.

NO audit trigger on `transactions` for V1 — same precedent as `sessions` /
`session_runs` (per db-schema.md). Add later if the accountant flow needs
change history.

Wire-contract mirrors (atomic with this migration — see #953 spawn brief):
  - api/src/models/transaction.py        : Transaction ORM
  - api/src/models/project.py            : 4 financial columns
  - api/src/schemas/transaction.py       : Create/Update/Read
  - api/src/schemas/project.py           : ProjectRead/ProjectUpdate extensions
  - api/src/schemas/pl.py                : PLSummary
  - api/src/routers/transactions.py      : GET/POST/PATCH /api/transactions
  - api/src/routers/pl.py                : GET /api/projects/{id}/pl + /export
  - api/src/services/pl_calculator.py    : pure period-bucketing service
  - api/src/routers/tasks.py             : #944 cost-write hook now also
                                           inserts a transactions row
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0032_transactions_ledger"
down_revision = "0031_health_monitor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- projects financial-separation columns -------------------------------
    op.add_column(
        "projects",
        sa.Column("tax_jurisdiction", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("legal_entity", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "fiscal_year_start",
            sa.Integer(),
            nullable=True,
            server_default="1",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "currency_default",
            sa.CHAR(3),
            nullable=True,
            server_default="USD",
        ),
    )
    # Defense-in-depth: fiscal_year_start must be 1..12 (NULL allowed for
    # legacy rows pre-default; new INSERTs land DEFAULT 1). Mirror of
    # ProjectUpdate's Pydantic ge=1, le=12 validator.
    op.create_check_constraint(
        "ck_projects_fiscal_year_start_valid",
        "projects",
        "fiscal_year_start IS NULL OR (fiscal_year_start >= 1 AND fiscal_year_start <= 12)",
    )

    # ---- transactions table --------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency",
            sa.CHAR(3),
            nullable=False,
            server_default="USD",
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('revenue', 'cost', 'expense', 'refund', 'transfer')",
            name="ck_transactions_kind_valid",
        ),
    )
    op.create_index(
        "ix_transactions_project_occurred",
        "transactions",
        ["project_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_transactions_task_id",
        "transactions",
        ["task_id"],
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_task_id", table_name="transactions")
    op.drop_index("ix_transactions_project_occurred", table_name="transactions")
    op.drop_table("transactions")
    op.drop_constraint(
        "ck_projects_fiscal_year_start_valid", "projects", type_="check"
    )
    op.drop_column("projects", "currency_default")
    op.drop_column("projects", "fiscal_year_start")
    op.drop_column("projects", "legal_entity")
    op.drop_column("projects", "tax_jurisdiction")
