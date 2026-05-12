"""tasks.halt_reason — MVP halt mechanism for full-auto Lead (Kanban #785)

Revision ID: 0013_tasks_halt_reason
Revises: 0012_projects_path_repo_ovr
Create Date: 2026-05-12 12:00 UTC

Adds one optional column to the `tasks` table to capture the in-flight halt
signal used by full-auto Lead sessions:

- `halt_reason` (TEXT, NULL) — free-form text. When Lead hits a judgment call
  it cannot resolve mid-flow (per the #787 decision matrix), it PATCHes this
  column to a short string describing the blocker (e.g.,
  "Option A/B decision needed"). The user unhalts by PATCH-ing the column
  back to NULL.

Semantics:
- `NULL`           = task is not halted; runs normally.
- non-null string  = task is halted; auto-pickup query (defined in Kanban
                     #786) MUST skip rows where `halt_reason IS NOT NULL`
                     regardless of `process_status`.

Deliberately NOT introduced this slice:
- No new `process_status` enum value. Process_status stays whatever it was
  when the halt landed (typically 3 = in_progress) — halt is orthogonal to
  the lifecycle code, same pattern as `is_pending` (Kanban #750).
- No CHECK constraint. Free-form text is the MVP; structured halt codes
  would land in a follow-up slice once the #787 matrix stabilizes.
- No index. Auto-pickup query filters `halt_reason IS NULL` which combines
  with the existing project_id + process_status predicate — measured first,
  add an index later only if a real plan regresses.

PG 16 treats a nullable column add with no default literal as metadata-only —
no heap rewrite, no row backfill, instant on the existing rows.

Down: drop the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0013_tasks_halt_reason"
down_revision = "0012_projects_path_repo_ovr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("halt_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "halt_reason")
