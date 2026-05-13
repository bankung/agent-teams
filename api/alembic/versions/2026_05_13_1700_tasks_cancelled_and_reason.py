"""tasks: add CANCELLED=6 + status_change_reason (Kanban #854)

Revision ID: 0022_tasks_cancelled_and_reason
Revises: 0021_projects_team_general
Create Date: 2026-05-13 17:00 UTC

Extends `tasks.process_status` to include the new lifecycle code
`CANCELLED=6` and adds a companion `status_change_reason TEXT NULL` column
so cancellation flips can carry the user-facing rationale (captured into
the audit-trigger snapshot for free). This is an atomic-coupling slice
(same precedent as #844): the BE+FE enum mirror lands in one landing so
the wire contract is never half-applied across a deploy.

DDL:
  - Drop the existing CHECK `ck_tasks_process_status_valid` (1..5).
  - Recreate it with `process_status IN (1, 2, 3, 4, 5, 6)`. Postgres has
    no `ALTER CONSTRAINT … RETARGET` for CHECK, so drop+recreate is the
    canonical idiom (same pattern as 0021_projects_team_general).
  - `ADD COLUMN status_change_reason TEXT NULL`. PG 16 treats this as a
    metadata-only op (no heap rewrite, no row backfill) because the
    column is nullable with no default literal — instant on existing rows.

Downgrade caveat: the recreated CHECK in `downgrade()` only allows
`(1..5)`. If any row carries `process_status=6` at downgrade time the
constraint creation will fail (`ERROR: check constraint ... is violated
by some row`). The operator must first PATCH those rows back to 1 (TODO)
or 5 (DONE) via the public PATCH /api/tasks endpoint, NEVER via raw SQL
DML — the audit trigger captures the reversion, and raw DML is gated by
the `.claude/hooks/block-raw-sql-dml.ps1` PreToolUse hook. The migration
intentionally does NOT auto-mutate user data.

Wire-contract mirrors (atomic with this migration — see #854 spawn brief):
  - api/src/constants.py        : TaskStatus.CANCELLED = 6 + ALL tuple
  - api/src/models/task.py      : CheckConstraint mirror via in_clause(...)
  - api/src/schemas/task.py     : TaskRead/TaskUpdate.status_change_reason
  - api/src/routers/tasks.py    : ?include_cancelled=true list filter
  - api/src/routers/projects.py : counts["6"] in /api/projects/stats; cancelled rows
                                  excluded from last_activity_at
  - web/lib/constants.ts        : TaskStatus.CANCELLED = 6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0022_tasks_cancelled_and_reason"
down_revision = "0021_projects_team_general"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py (TaskStatus.ALL). Migrations don't import
# app code — see standards/sqlalchemy/migrations.md "Helper duplication between
# app and migration".
_TASK_PROCESS_STATUS_ALL_NEW = (1, 2, 3, 4, 5, 6)
_TASK_PROCESS_STATUS_ALL_OLD = (1, 2, 3, 4, 5)


def _in_clause(column: str, values: tuple[int, ...]) -> str:
    """Mirror of src.constants.in_clause — duplicated locally so the migration
    has zero app-code imports."""
    return f"{column} IN ({', '.join(str(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_tasks_process_status_valid", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_process_status_valid",
        "tasks",
        _in_clause("process_status", _TASK_PROCESS_STATUS_ALL_NEW),
    )
    op.add_column(
        "tasks",
        sa.Column("status_change_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "status_change_reason")
    # CAVEAT: any row with process_status=6 will block the recreate. Operator
    # must clean up first (PATCH to 1 or 5 via API, never raw SQL DML).
    op.drop_constraint("ck_tasks_process_status_valid", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_process_status_valid",
        "tasks",
        _in_clause("process_status", _TASK_PROCESS_STATUS_ALL_OLD),
    )
