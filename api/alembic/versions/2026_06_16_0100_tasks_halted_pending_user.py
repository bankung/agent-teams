"""tasks: add HALTED_PENDING_USER=8 lifecycle code + halted_at (Kanban #1839)

Revision ID: 0069_tasks_halted_pending_user
Revises: 0068_cost_forecast_threshold
Create Date: 2026-06-16 01:00 UTC

Extends `tasks.process_status` with the new lifecycle code
`HALTED_PENDING_USER=8` ('halted-pending-user') and adds a companion
`halted_at TIMESTAMPTZ NULL` column that the router stamps on the →8
transition (mirroring started_at/completed_at). Near-identical change to
migration 0022 (CANCELLED=6 + status_change_reason) — same atomic-coupling
shape so the wire contract is never half-applied across a deploy.

ORTHOGONALITY (Kanban #1839): ps=8 is a plain lifecycle code set by an
ordinary `PATCH {process_status: 8}`. It is fully decoupled from the
`halt_reason` flag (the MVP full-auto halt, #785) — ps=8 is NOT derived
from halt_reason and does not touch the resume_tasks / next_task halt_reason
logic. AC3 = "no regression to the halt_reason-flag behavior".

7 is intentionally SKIPPED/RESERVED (possible future 'rejected' status —
see routers/user_actions.py + .claude/agents/project-auditor.md). The new
valid set is `(1, 2, 3, 4, 5, 6, 8)` — NOT contiguous.

DDL:
  - Drop the existing CHECK `ck_tasks_process_status_valid` (1..6).
  - Recreate it with `process_status IN (1, 2, 3, 4, 5, 6, 8)`. Postgres has
    no `ALTER CONSTRAINT … RETARGET` for CHECK, so drop+recreate is the
    canonical idiom (same pattern as 0022_tasks_cancelled_and_reason).
  - `ADD COLUMN halted_at TIMESTAMPTZ NULL`. PG 16 treats this as a
    metadata-only op (no heap rewrite, no row backfill) because the column
    is nullable with no default literal — instant on existing rows.

Downgrade caveat: the recreated CHECK in `downgrade()` only allows
`(1..6)`. If any row carries `process_status=8` at downgrade time the
constraint creation will fail (`ERROR: check constraint ... is violated by
some row`). The operator must first PATCH those rows off 8 (e.g. back to 1
TODO or 5 DONE) via the public PATCH /api/tasks endpoint, NEVER via raw SQL
DML — the audit trigger captures the reversion, and raw DML is gated by the
`.claude/hooks/block-raw-sql-dml.ps1` PreToolUse hook. The migration
intentionally does NOT auto-mutate user data.

Wire-contract mirrors (atomic with this migration — see #1839 spawn brief):
  - api/src/constants.py        : TaskStatus.HALTED_PENDING_USER = 8 + ALL tuple
  - api/src/models/task.py      : halted_at column; CheckConstraint mirror via
                                  in_clause(TaskStatus.ALL) — auto-derives 8
  - api/src/schemas/task.py     : TaskRead/TaskSummary.halted_at read-out;
                                  TaskUpdate.halted_at parity
  - api/src/routers/tasks.py    : _STATUS_TIMESTAMP_FIELDS[8]='halted_at' stamp;
                                  next_task TODO-only filter excludes ps=8
  - api/src/routers/projects.py : counts["8"] in /api/projects/stats auto-derives
                                  from TaskStatus.ALL (no code change)
  - web/* (FE "Halted/Pending user" lane) : DEFERRED to a separate follow-up.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0069_tasks_halted_pending_user"
down_revision = "0068_cost_forecast_threshold"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py (TaskStatus.ALL). Migrations don't import
# app code — see standards/sqlalchemy/migrations.md "Helper duplication between
# app and migration". NEW adds 8 (7 intentionally skipped/reserved); OLD is the
# immediately-prior set from migration 0022 (still includes 6, omits 8).
_TASK_PROCESS_STATUS_ALL_NEW = (1, 2, 3, 4, 5, 6, 8)
_TASK_PROCESS_STATUS_ALL_OLD = (1, 2, 3, 4, 5, 6)


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
        sa.Column("halted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "halted_at")
    # CAVEAT: any row with process_status=8 will block the recreate. Operator
    # must clean up first (PATCH off 8 via API, never raw SQL DML).
    op.drop_constraint("ck_tasks_process_status_valid", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_process_status_valid",
        "tasks",
        _in_clause("process_status", _TASK_PROCESS_STATUS_ALL_OLD),
    )
