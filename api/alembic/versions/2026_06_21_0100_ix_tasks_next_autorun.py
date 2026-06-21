"""tasks: ix_tasks_next_autorun covering partial index (Kanban #2505)

Revision ID: 0071_ix_tasks_next_autorun
Revises: 0070_proj_auto_decision_policy
Create Date: 2026-06-21 01:00 UTC

Adds a covering partial index on the next-autorun hot path.  The query
(`WHERE project_id=? AND process_status=1 AND status=1 AND run_mode IN (...)
ORDER BY priority DESC, sort_order ASC`) currently seqscans all 1,696 tasks;
the partial predicate (status=1 AND process_status=1) keeps the index sparse
(~175 rows today).  The INCLUDE list avoids a heap fetch for the columns the
caller materialises after the index scan.

CREATE INDEX CONCURRENTLY cannot run inside a transaction, so the op is
wrapped in autocommit_block() (Alembic's non-transactional escape hatch).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0071_ix_tasks_next_autorun"
down_revision = "0070_proj_auto_decision_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_tasks_next_autorun",
            "tasks",
            ["project_id", "process_status", "status", "run_mode"],
            postgresql_concurrently=True,
            postgresql_include=[
                "priority",
                "sort_order",
                "created_at",
                "halt_reason",
                "blocked_by",
                "scheduled_at",
            ],
            postgresql_where=sa.text("status = 1 AND process_status = 1"),
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_tasks_next_autorun",
            table_name="tasks",
            postgresql_concurrently=True,
            if_exists=True,
        )
