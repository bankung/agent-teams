"""tasks.is_pending — in-flight-and-stuck flag (Kanban #750)

Revision ID: 0011_tasks_is_pending
Revises: 0010_tasks_scheduled_at
Create Date: 2026-05-11 12:00 UTC

User clarification (2026-05-11) on Kanban #748: "pending" semantically means
"in-flight work that hit a problem and is stuck" — NOT "process_status==TODO".
The two concepts are orthogonal at the DB level; only the cross-state pair
(is_pending=true with process_status != 2) is semantically meaningless.

Schema-additive only: BOOLEAN NOT NULL DEFAULT FALSE. PG 16 treats this as a
metadata-only column add (non-null literal DEFAULT), so the 55 existing rows
backfill via DEFAULT without rewriting the heap.

No CHECK constraint this slice. Cross-state validation (is_pending=true
REQUIRES process_status=2) is APP-LAYER only for V1 — lockstep with the
task_kind/run_mode and run_mode/consent cross-table validators which both
also live in services/*.py. Adding a single-column CHECK is trivially
satisfied here (a CHECK can't reach the pair without referencing
process_status which would lock us into a single-column-mutation interlock —
not what we want); the resolved-final PATCH pattern is the right enforcement
shape. A future raw-SQL incident would motivate adding a DB CHECK; for now
the app gate is sufficient.

Down: drop the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_tasks_is_pending"
down_revision = "0010_tasks_scheduled_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "is_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "is_pending")
