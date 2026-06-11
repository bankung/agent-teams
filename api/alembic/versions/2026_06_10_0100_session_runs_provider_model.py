"""session_runs: add provider + model columns for cost metering (Kanban #2135)

Revision ID: 0063_session_runs_provider_model
Revises: 0062_task_comments
Create Date: 2026-06-10 01:00 UTC

The worker PATCH already sends provider/model in the body; the router used
them for compute_cost but dropped them (no columns to persist). This migration
adds the columns so provider/model are recorded alongside tokens + cost for
the /api/usage/daily rollup (Kanban #2135, provider cost rollup).

Both columns are nullable:
  - NULL provider → 'unknown' on the usage rollup (correct for legacy runs).
  - VARCHAR lengths: provider=32 (e.g. 'google', 'anthropic'), model=128
    (accommodates names like 'gemma4:e4b-it-qat', 'claude-haiku-4-5-20251001').

PG 16 metadata-only ADD COLUMN (nullable, no server_default) — no heap rewrite.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0063_session_runs_provider_model"
down_revision = "0062_task_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_runs",
        sa.Column("provider", sa.String(32), nullable=True),
    )
    op.add_column(
        "session_runs",
        sa.Column("model", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_runs", "model")
    op.drop_column("session_runs", "provider")
