"""tasks.model_override — per-task model-tier override (Kanban #1677)

Revision ID: 0056_task_model_override
Revises: 0055_required_binaries
Create Date: 2026-06-02 02:00 UTC

Adds ONE nullable TEXT column `tasks.model_override` storing an optional
model-tier override for a single task — one of `'haiku'`, `'sonnet'`, `'opus'`,
or NULL. NULL = "inherit" (no per-task override), which is today's behavior
byte-for-byte.

PRECEDENCE (resolution order, documented; enforcement is an orchestrator
convention, NOT code in this slice):
    task.model_override  >  project.agent_overrides  >  role default
The Lead/orchestrator reads `task.model_override` off TaskRead, resolves the
effective tier, and records the resolved tier in the EXISTING
`tasks.subagent_models` spawn log. This migration only STORES the override; no
runtime that consumes it changes here.

Column shape (validated at the API boundary by a Pydantic Literal, NOT a DB
CHECK — mirrors the run_mode / task_kind / task_type "wire-enum Literal at the
schema layer, no DB CHECK on the tier value" precedent for the model-tier set;
note those columns DO carry a CHECK, but they are NOT NULL with a DB DEFAULT.
model_override is nullable-with-no-default like halt_reason, so we follow the
halt_reason/status_change_reason posture: nullable TEXT, value-gated by Pydantic
422 at the boundary):
    'haiku' | 'sonnet' | 'opus' | NULL
  NULL = inherit (no override). Nullable, NO server_default — explicit NULL is
  the meaningful "inherit" sentinel (parity with halt_reason / status_change_reason).

PG 16 — nullable ADD COLUMN with no DEFAULT is metadata-only (no heap rewrite,
no backfill). Existing ~1060 task rows unaffected.

Downgrade caveat:
- Dropping `model_override` discards any per-task tier overrides silently.
  Recovery is a re-PATCH of the affected tasks (operator-CRUD metadata).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0056_task_model_override"
down_revision = "0055_required_binaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no server_default — explicit NULL = "inherit" (no per-task
    # override). Value set ('haiku'|'sonnet'|'opus') is gated by the Pydantic
    # Literal at the API boundary (422), mirroring the halt_reason posture of a
    # nullable TEXT with no DB DEFAULT.
    op.add_column(
        "tasks",
        sa.Column(
            "model_override",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "model_override")
