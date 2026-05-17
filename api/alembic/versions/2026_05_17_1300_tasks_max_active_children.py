"""tasks: max_active_children cap (Kanban #1125 — L21 prevention)

Revision ID: 0035_tasks_max_active_children
Revises: 0034_pytest_runner_role
Create Date: 2026-05-17 13:00 UTC

L21 prevention layer for the hammer-test FINDING #13 (T-DOS-3): a recurrence
template with `* * * * *` and `next_fire_at` 7 days in the past spawns a
child every minute → 1440 children/day → 10080 children/week. Unbounded
TODO clutter, audit-trigger pollution, next-autorun queue noise.

This migration adds a per-template cap. Default cap (when column is NULL)
is supplied by `MAX_ACTIVE_CHILDREN_DEFAULT` env at fire-time in
`src/services/recurrence.py` — currently 100. Per-template explicit value
overrides the env default (e.g. a high-volume audit template can opt up
to 500; a sensitive ops template can drop to 5).

Column shape:

- `tasks.max_active_children INTEGER NULL` — only meaningful on rows with
  `is_template=true`. Non-template rows ignore it. NULL = use env default.
- CHECK `max_active_children IS NULL OR max_active_children > 0` — zero or
  negative is nonsense ("cap of 0 active children" = template that never
  fires, which is what `is_template=false` already expresses). Mirror of
  the column's Pydantic `ge=1, le=10000` boundaries at the API layer.

No data backfill needed — column is additive, NULL default applies to
every existing row (including templates).

Sibling layers:
- L18 (#1115): payload-size caps on description / acceptance_criteria /
  subagent_models — bounds per-row growth.
- L15 (sibling P2 task, not in this slice): per-template auto-headless
  confirmation prompt — separate concern from cap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0035_tasks_max_active_children"
down_revision = "0034_pytest_runner_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("max_active_children", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_tasks_max_active_children_positive",
        "tasks",
        "max_active_children IS NULL OR max_active_children > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tasks_max_active_children_positive", "tasks", type_="check"
    )
    op.drop_column("tasks", "max_active_children")
