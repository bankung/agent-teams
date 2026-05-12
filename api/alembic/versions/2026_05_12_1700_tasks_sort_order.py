"""tasks.sort_order — within-lane manual ordering support (Kanban #772)

Revision ID: 0018_tasks_sort_order
Revises: 0017_tasks_blocked_by
Create Date: 2026-05-12 17:00 UTC

Adds a `sort_order DOUBLE PRECISION NULL` column on `tasks` so the FE can
drag-drop reorder cards within a lane (process_status bucket). Paired with
the upcoming dnd-kit sortable wire-up (Kanban #772, frontend slice).

Locked design (decision 2026-05-12, Kanban #772):

- `sort_order DOUBLE PRECISION NULL`. NULL = "use created_at fallback for
  ordering" — a lane that has never been reordered keeps the natural
  newest-first / oldest-first order without paying a per-row write cost.
- **No CHECK.** Sparse-float lexicographic ordering is constraint-free at
  the DB level. Collisions, degenerate values (NaN, ±Inf, denormals) are
  app-layer concerns — the reorder endpoint never emits them, and a raw-SQL
  drift here is a no-correctness-impact ordering anomaly rather than a
  cross-row invariant break.
- **No FK.** The column is a scalar, not a reference.
- **No index this slice.** Lane-scoped sort queries already filter by
  `process_status` + `status` (existing `ix_tasks_process_status` +
  `ix_tasks_status` cover those). Adding `ix_tasks_sort_order` here would
  burn write amplification on a column that is NULL on every existing row;
  the index gains nothing until the sort_order set densifies in real use.
  Measured-first index policy — revisit only after EXPLAIN ANALYZE on a
  lane with hundreds of densified rows shows the lane-scoped + ORDER BY
  sort_order plan touching this index.
- **Ordering rule (FE + server reorder endpoint contract):**
  `ORDER BY sort_order ASC NULLS LAST, created_at ASC`. A NULL sort_order
  falls back to created_at (oldest within the NULL block first). On first
  reorder in a lane the server materializes all NULLs to floor floats
  (1.0, 2.0, 3.0, …) in the SAME transaction as the reorder write — see
  routers/tasks.py reorder endpoint.

History capture is FREE: the existing `tasks_audit_trg` (migration 0001,
initial_schema.py:189) uses `to_jsonb(OLD)` which auto-captures any new
column. No trigger change needed; `sort_order` lands in
`tasks_history.snapshot` automatically on every UPDATE / DELETE.

Downgrade is a single column drop (no index / CHECK / FK to drop).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0018_tasks_sort_order"
down_revision = "0017_tasks_blocked_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "sort_order",
            postgresql.DOUBLE_PRECISION(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "sort_order")
