"""ix_task_gates_answered: partial index on task_gates (task_id) WHERE status='answered'

Revision ID: 0073_ix_task_gates_answered
Revises: 0072_task_gates
Create Date: 2026-06-24 02:00 UTC

Adds a partial index to accelerate the `_answered_gate_exists` EXISTS subquery
used by the next-autorun picker's `gate_resume_stmt` in
`api/src/routers/tasks.py` (~L854-881). Under runner load the picker evaluates
this subquery for every candidate task — the partial index keeps the scan sparse
(only 'answered' gates) and avoids a full sequential scan as gate volume grows.

Mirrors the posture of `ix_task_gates_open` (same migration parent) — both are
partial indexes on `(task_id)` scoped to a single status value.

Wire-contract: model `__table_args__` updated in the same spawn (migration-vs-ORM
timing rule — context/standards/ sqlalchemy/migrations.md).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0073_ix_task_gates_answered"
down_revision = "0072_task_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial index on the answered subset — the `_answered_gate_exists` EXISTS
    # subquery in the next-autorun picker's gate_resume_stmt scans this path
    # under runner load. Mirrors ix_task_gates_open (status='open') in form.
    op.create_index(
        "ix_task_gates_answered",
        "task_gates",
        ["task_id"],
        postgresql_where=sa.text("status = 'answered'"),
    )


def downgrade() -> None:
    op.drop_index("ix_task_gates_answered", table_name="task_gates")
