"""transactions partial unique on (project_id, source, source_ref) — Kanban #1325 M2

Revision ID: 0049_transaction_source_unique
Revises: 0048_credentials_vault
Create Date: 2026-05-21 03:08 UTC

Adds a partial UNIQUE index that enforces idempotent dedup of external webhook
deliveries (Stripe / PayPal retry the same event repeatedly). Without this
index, the webhook handler can race itself on retries and produce duplicate
revenue / refund rows.

The index is PARTIAL on `source_ref IS NOT NULL` because the vast majority of
ledger rows are manually entered (no external source ref) — including them in
the unique constraint would block legitimate same-day manual entries that
share the implicit NULL key. The partial form indexes only the rows that
actually carry external identifiers.

Column shape verification (no new columns this slice):
  - `source` TEXT NULL — already present (added in 0032_transactions).
  - `source_ref` TEXT NULL — already present (added in 0032_transactions).

Downgrade drops the index only; no data touched.

PG 16 — CREATE INDEX without CONCURRENTLY is fine on a small live table
(transactions row count is in the low hundreds; the brief lock is acceptable).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0049_transaction_source_unique"
down_revision = "0048_credentials_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial unique — only enforces uniqueness when source_ref is set, so the
    # bulk of manually-entered (source_ref IS NULL) rows are not constrained.
    # The (project_id, source, source_ref) tuple is the canonical idempotency
    # key for external integrations (Stripe event.id, PayPal event.id, etc).
    op.create_index(
        "ux_transactions_project_source_ref",
        "transactions",
        ["project_id", "source", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_transactions_project_source_ref",
        table_name="transactions",
    )
