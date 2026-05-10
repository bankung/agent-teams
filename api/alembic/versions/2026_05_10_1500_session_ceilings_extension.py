"""sessions ceilings extension — card_detail + output_budget (CTX-1 audit follow-up)

Revision ID: 0009_session_ceilings_extension
Revises: 0008_sessions_and_runs
Create Date: 2026-05-10 15:00 UTC

Audit follow-up to CTX-1 #716 (Kanban #722). Doc section 1.3 specifies a
4-bucket token budget breakdown:

  system prompt          ~2,000 tokens   (fixed; not a per-session knob)
  session.md (ceiling)  ~28,000 tokens
    ├── compacted history ~13,000  (CTX-1: compacted_history_ceiling_tokens)
    └── recent activity   ~15,000  (CTX-1: recent_activity_ceiling_tokens)
  card detail (current)  ~6,000 tokens   ← THIS migration adds it
  output budget          ~4,000 tokens   ← THIS migration adds it
  ──────────────────────────────────────
  total                 ~40,000 tokens per run

CTX-1 (#716) shipped only the two session.md ceilings + a single
`token_budget_per_run` (NULL = no budget; soft-warn). This migration
materialises the remaining 2 buckets so CTX-3 (#718, token counter) can
compare measured tokens against per-bucket ceilings without hardcoding.

Schema-additive only. No data migration: existing rows backfill via
`server_default` on each ADD COLUMN.

Downgrade drops both columns in reverse order.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_session_ceilings_extension"
down_revision = "0008_sessions_and_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "card_detail_ceiling_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("6000"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "output_budget_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("4000"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "output_budget_tokens")
    op.drop_column("sessions", "card_detail_ceiling_tokens")
