"""tasks.task_type — bug/feature/chore/docs/refactor classification (Kanban #803)

Revision ID: 0015_tasks_task_type
Revises: 0014_tasks_acceptance_criteria
Create Date: 2026-05-12 14:00 UTC

Adds one VARCHAR(16) NOT NULL column to the `tasks` table to classify work
type. Motivated by the 2026-05-12 AC-discipline audit: bug-fix tasks
(e.g. #801) and feature tasks (e.g. #792, #795) currently file in the same
shape — no structural way to tell which is which. `task_type` makes the
distinction queryable + drives report grouping later.

- `task_type` VARCHAR(16) NOT NULL DEFAULT 'feature'
  + CHECK ck_tasks_task_type_valid: task_type IN
    ('bug','feature','chore','docs','refactor')

Pattern reference (mirror exactly):
- `task_kind` column (migration 0007, Kanban #706) is the canonical "small
  enum string column" template — same DEFAULT-covers-backfill story, same
  CHECK-constraint naming pattern, same _IN_ALL local tuple kept in lockstep
  with src/constants.py per
  context/standards/sqlalchemy/migrations.md "Helper duplication between app
  and migration" (migrations don't import app code).

Existing rows backfill cleanly to `task_type='feature'` because the
server_default fires on ADD COLUMN NOT NULL. PG 16 ADD COLUMN with a constant
DEFAULT does not rewrite the table (see standards/postgresql/operations.md) —
metadata-only.

Down: drop the CHECK, drop the column (reverse order).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0015_tasks_task_type"
down_revision = "0014_tasks_acceptance_criteria"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py TaskType.ALL — duplicated per
# standards/sqlalchemy/migrations.md "Helper duplication between app and
# migration" (migrations don't import app code).
_TASK_TYPE_ALL = ("bug", "feature", "chore", "docs", "refactor")


def _in_clause_text(column: str, values: tuple[str, ...]) -> str:
    # Mirror of src.constants.in_clause_text — duplicated locally so the
    # migration has zero app-code imports.
    _allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    for v in values:
        if not v or any(c not in _allowed for c in v):
            raise ValueError(
                f"_in_clause_text only allows [a-z0-9_-]+ values; got {v!r}"
            )
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


def upgrade() -> None:
    # 1. task_type — default 'feature' covers existing rows without a backfill
    #    UPDATE statement.
    op.add_column(
        "tasks",
        sa.Column(
            "task_type",
            sa.String(length=16),
            server_default="feature",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_task_type_valid",
        "tasks",
        _in_clause_text("task_type", _TASK_TYPE_ALL),
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_task_type_valid", "tasks", type_="check")
    op.drop_column("tasks", "task_type")
