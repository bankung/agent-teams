"""tasks: flip task_kind column default from 'human' to 'ai' (Kanban #858)

Revision ID: 0023_tasks_task_kind_default_ai
Revises: 0022_tasks_cancelled_and_reason
Create Date: 2026-05-13 14:58 UTC

Rationale: most tasks in this codebase are agent-driven (task_kind='ai').
'human' is reserved for tasks that REQUIRE a person to answer or decide
(interaction_kind IN ('question','decision')). The router enforces the
coercion `interaction_kind question/decision => task_kind=human` at the
API boundary (services/task_kind.py); this migration aligns the column's
INSERT-time default with the new mental model so omitting task_kind on
a POST yields the right value for the dominant case.

DDL:
  - `ALTER COLUMN task_kind SET DEFAULT 'ai'` (was 'human').

Non-destructive: `server_default` changes ONLY affect new INSERTs that
omit the column. Existing rows keep their current `task_kind` value.
Verified pre/post via `SELECT task_kind, COUNT(*) FROM tasks GROUP BY
task_kind` — count distribution invariant across upgrade/downgrade.

Downgrade: reverts the column default to 'human' (the value pinned by
migration 0007). No row backfill in either direction.

Wire-contract mirrors (atomic with this migration — see #858 spawn brief):
  - api/src/models/task.py     : Task.task_kind server_default text("'ai'")
  - api/src/schemas/task.py    : TaskCreate.task_kind default = TaskKind.AI
  - api/src/routers/tasks.py   : router coercion via services/task_kind
  - api/src/services/task_kind.py : coerce_task_kind_for_interaction(...)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0023_tasks_task_kind_default_ai"
down_revision = "0022_tasks_cancelled_and_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tasks",
        "task_kind",
        existing_type=sa.String(length=8),
        existing_nullable=False,
        server_default=sa.text("'ai'"),
    )


def downgrade() -> None:
    op.alter_column(
        "tasks",
        "task_kind",
        existing_type=sa.String(length=8),
        existing_nullable=False,
        server_default=sa.text("'human'"),
    )
