"""tasks.blocked_by — single-blocker dependency support (Kanban #771)

Revision ID: 0017_tasks_blocked_by
Revises: 0016_row_changed_triggers
Create Date: 2026-05-12 16:00 UTC

Adds a self-referential FK on `tasks` so a task can declare *one* other task
as its blocker (single-blocker, M:1). Pairs with the upcoming sortable lane
work (Kanban #772) — a TODO/in_progress card with a blocker chip is rendered
non-draggable in the FE.

Locked design (decision 2026-05-12, Kanban #771):

- `blocked_by BIGINT NULL REFERENCES tasks(id) ON DELETE SET NULL`
  SET NULL (NOT CASCADE) — hard-deleting a blocker MUST NOT cascade into the
  blocked task; we want the blocked task to survive with `blocked_by=NULL`.
  Mirrors `spawned_from_task_id` (migration 0007), NOT `parent_task_id`
  (migration 0003 used CASCADE because subtasks live-and-die with the parent).
- CHECK `ck_tasks_blocked_by_not_self` ensures `blocked_by IS NULL OR
  blocked_by <> id` — DB-level backstop. The app rejects self-blocker via
  PATCH 422; this CHECK catches raw-SQL drift.
- INDEX `ix_tasks_blocked_by` on the FK column — supports the reverse-lookup
  endpoint `GET /api/tasks/{id}/blocks` (rows that point AT a given blocker).
- Same-project enforcement is app-layer only (router POST/PATCH validates
  blocker.project_id == row.project_id) — no trigger.
- Cycle prevention is app-layer (router PATCH walks the chain up to depth=10
  and rejects with 422 on cycle detection). Direct cycle on POST is
  structurally impossible (the new row has no id yet) — no POST cycle check.

History capture is FREE: the existing `tasks_audit_trg` (migration 0001,
initial_schema.py:189) uses `to_jsonb(OLD)` which auto-captures any new
column. No trigger change needed; `blocked_by` lands in
`tasks_history.snapshot` automatically on every UPDATE / DELETE.

Downgrade reverses cleanly: drop index → drop CHECK (raw SQL with
`IF EXISTS` per the migrations.md idempotent-rollback convention) → drop FK
→ drop column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0017_tasks_blocked_by"
down_revision = "0016_row_changed_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("blocked_by", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tasks_blocked_by",
        "tasks",
        "tasks",
        ["blocked_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_tasks_blocked_by_not_self",
        "tasks",
        "blocked_by IS NULL OR blocked_by <> id",
    )
    op.create_index(
        "ix_tasks_blocked_by",
        "tasks",
        ["blocked_by"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_blocked_by", table_name="tasks")
    # drop_constraint(..., type_="check") doesn't take an IF EXISTS flag —
    # match the migrations.md idempotent-rollback convention with raw SQL.
    op.execute(
        "ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_blocked_by_not_self;"
    )
    op.drop_constraint("fk_tasks_blocked_by", "tasks", type_="foreignkey")
    op.drop_column("tasks", "blocked_by")
