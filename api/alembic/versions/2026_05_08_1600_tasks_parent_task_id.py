"""tasks.parent_task_id — subtask hierarchy support

Revision ID: 0003_tasks_parent_task_id
Revises: 0002_soft_delete_and_lead
Create Date: 2026-05-08 16:00 UTC

Adds a self-referential FK to `tasks` so a task can be a subtask of another
task (umbrella task pattern). Locked design (decision 2026-05-08, Kanban #238):

- `parent_task_id BIGINT NULL REFERENCES tasks(id) ON DELETE CASCADE`
  CASCADE is defense-in-depth — application code never hard-deletes; soft-delete
  with active children is blocked at 409 by the API. CASCADE only matters if a
  raw-SQL DELETE bypasses the app layer.
- CHECK `parent_task_id IS NULL OR parent_task_id <> id` — no self-parent at
  the DB level (backstop; the app rejects re-parenting via PATCH 422).
- INDEX `ix_tasks_parent_task_id` on the FK column for child-lookup queries.
- Same-project enforcement is app-layer only (POST validates parent.project_id
  == payload.project_id) — no trigger.

Downgrade reverses cleanly: drop index, drop CHECK, drop FK, drop column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_tasks_parent_task_id"
down_revision = "0002_soft_delete_and_lead"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("parent_task_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tasks_parent_task_id",
        "tasks",
        "tasks",
        ["parent_task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_tasks_parent_task_id_not_self",
        "tasks",
        "parent_task_id IS NULL OR parent_task_id <> id",
    )
    op.create_index(
        "ix_tasks_parent_task_id",
        "tasks",
        ["parent_task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    # drop_constraint(..., type_="check") doesn't take an IF EXISTS flag —
    # match the migrations.md idempotent-rollback convention with raw SQL.
    op.execute(
        "ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_parent_task_id_not_self;"
    )
    op.drop_constraint("fk_tasks_parent_task_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "parent_task_id")
