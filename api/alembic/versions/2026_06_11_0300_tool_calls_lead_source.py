"""tool_calls: source/kind/summary + relax engine-only NOT NULLs —
Mode A Lead report-back into the #980 activity rail (Kanban #2320)

Revision ID: 0066_tool_calls_lead_source
Revises: 0065_effort_mode
Create Date: 2026-06-11 03:00 UTC

Design lock (decisions.md 2026-06-11 #2320): reuse the #980 tool_calls table
as the single activity surface for BOTH engine tool invocations and Lead
report-back checkpoints. No new feed/table/endpoint family.

ADDED columns:
  * source  TEXT NOT NULL DEFAULT 'engine'  — discriminator {engine,lead}.
      Backfill-free: existing rows read 'engine' via the server_default; the
      DEFAULT also keeps the engine POST path (which never sends source) writing
      'engine' with no code change. Enum gated by Pydantic Literal only — NO DB
      CHECK (#980 posture: the audit log must never 23514).
  * kind    TEXT NULL  — lead-row taxonomy {spawn,tool_result,ac_verified,
      commit,status_change,blocked,tool_gap,skill_gap,note}; REQUIRED for lead
      rows via Pydantic, NULL on engine rows.
  * summary TEXT NULL  — lead-row human-readable evidence (1..2000, #2136
      sanitize); NULL on engine rows.

RELAXED to nullable (engine-only columns — the NOT-NULL engine wire contract
moves to the Pydantic layer via ToolCallCreate, which keeps them required):
  tier, input_json, duration_ms, permission_decision. Lead rows leave these
  NULL; engine rows continue to fill them (Pydantic-required, never NULL).

PG 16 metadata-only ADD COLUMN (source has a DEFAULT but PG 16 stores it as a
fast metadata default — no heap rewrite). The ALTER ... DROP NOT NULL ops are
catalog-only (no table scan).

Downgrade: re-tighten the 4 NOT NULLs and drop the 3 columns. Lead rows carry
NULL in the engine-only columns, so they CANNOT satisfy the re-tightened
constraint — we DELETE source='lead' rows first (chosen over a defensive
backfill: lead rows have no meaningful engine values to invent, and the feature
is opt-in append-only, so discarding them on downgrade is the honest revert).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0066_tool_calls_lead_source"
down_revision = "0065_effort_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD the three new columns. source NOT NULL with a server_default so
    # existing rows + the unchanged engine POST path both read 'engine'.
    op.add_column(
        "tool_calls",
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default="engine",
        ),
    )
    op.add_column("tool_calls", sa.Column("kind", sa.Text(), nullable=True))
    op.add_column("tool_calls", sa.Column("summary", sa.Text(), nullable=True))

    # RELAX the engine-only columns to nullable — lead rows leave them NULL.
    # The engine wire contract stays NOT-NULL-equivalent via ToolCallCreate.
    op.alter_column("tool_calls", "tier", existing_type=sa.Text(), nullable=True)
    op.alter_column(
        "tool_calls",
        "input_json",
        existing_type=sa.dialects.postgresql.JSONB(),
        nullable=True,
    )
    op.alter_column(
        "tool_calls", "duration_ms", existing_type=sa.Integer(), nullable=True
    )
    op.alter_column(
        "tool_calls",
        "permission_decision",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Lead rows have NULL in the engine-only columns and would violate the
    # re-tightened NOT NULLs; discard them before re-tightening (documented
    # choice — opt-in append-only feature, nothing meaningful to backfill).
    op.execute("DELETE FROM tool_calls WHERE source = 'lead'")

    op.alter_column(
        "tool_calls",
        "permission_decision",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "tool_calls", "duration_ms", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column(
        "tool_calls",
        "input_json",
        existing_type=sa.dialects.postgresql.JSONB(),
        nullable=False,
    )
    op.alter_column("tool_calls", "tier", existing_type=sa.Text(), nullable=False)

    op.drop_column("tool_calls", "summary")
    op.drop_column("tool_calls", "kind")
    op.drop_column("tool_calls", "source")
