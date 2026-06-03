"""session_runs: add cache_read_input_tokens + cache_creation_input_tokens (G2, Kanban #1689)

Revision ID: 0058_session_runs_cache_tokens
Revises: 0057_milestones
Create Date: 2026-06-03 02:00 UTC

Capture real prompt-cache token usage in session_runs so cost metering
reflects actual cache-hit/miss spend rather than treating all input as
non-cached. Anthropic usage objects return:
  - usage.input_tokens              → already mapped to total_input_tokens
  - usage.cache_read_input_tokens   → new column (served from cache, 0.10x rate)
  - usage.cache_creation_input_tokens → new column (written to cache, 1.25x rate)

Both columns are BigInteger NOT NULL DEFAULT 0 — mirrors the existing
total_input_tokens / total_output_tokens shape. NULL rows would require
nullable handling throughout the cost math; zero is the correct
pre-migration baseline (equivalent to "no cache tokens observed").

PG 16 metadata-only ADD COLUMN (nullable columns with DEFAULT or NOT NULL
with server_default that has no volatile function) — no heap rewrite on
existing session_runs rows.

The router's update_session_run handler already pops these fields from the
PATCH body and forwards them to compute_cost; this migration adds the columns
so the values are persisted instead of discarded after cost calculation.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0058_session_runs_cache_tokens"
down_revision = "0057_milestones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 16 metadata-only ADD COLUMN: NOT NULL with constant server_default
    # — no heap rewrite required. Existing rows read 0 on first access.
    op.add_column(
        "session_runs",
        sa.Column(
            "cache_read_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "session_runs",
        sa.Column(
            "cache_creation_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("session_runs", "cache_creation_input_tokens")
    op.drop_column("session_runs", "cache_read_input_tokens")
