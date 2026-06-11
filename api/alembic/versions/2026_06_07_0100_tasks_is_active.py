"""tasks.is_active — auto-archive flag for old audit tasks (Kanban #1240)

Revision ID: 0061_tasks_is_active
Revises: 0060_task_templates
Create Date: 2026-06-07 01:00 UTC

Adds ONE column `tasks.is_active` BOOLEAN NOT NULL DEFAULT true, plus two
supporting indexes for the daily auto-archive sweep + the new default-query
exclusion.

WHY (Kanban #1240): a daily APScheduler sweep flips `is_active=false` on
COMPLETED audit tasks (`task_type='audit'` AND `completed_at < now() - TTL`)
so the board / list endpoints stop surfacing stale governance-audit rows.
TTL is env-configurable (AUDIT_ARCHIVE_DAYS, default 30). The sweep skips
projects with `projects.audit_enabled=false`. is_active is the
ARCHIVE state — orthogonal to `status` (soft-delete 0/1) and to
`process_status` (lifecycle 1..6). An archived row is still ACTIVE
(status=1) and still DONE (process_status=5); is_active=false just hides it
from the default board view. GET /api/tasks default-excludes is_active=false
rows; the opt-in `?include_archived=true` query param fetches them
(blast-radius guard for explicit existing callers).

NAMING NOTE: `projects.is_active` already exists (cold-archive flag on the
PROJECT row, free boolean). `tasks.is_active` is the same NAME but a
different table — there is no collision. Chosen to match the #1240 spec
verbatim.

DESIGN (locked, #1240):

1. `tasks.is_active` BOOLEAN NOT NULL server_default true.
   - NOT NULL DEFAULT true backfills every existing ~1060 task row to
     is_active=true (i.e. "visible") on apply. PG 16: adding a NOT NULL
     column with a CONSTANT default is a metadata-only catalog change — NO
     heap rewrite, NO table-lock-held scan of every row (the default is
     stored in pg_attribute.atthasmissing/attmissingval and materialized
     lazily). So this migration does NOT need a risky backfill UPDATE and
     does NOT take a long ACCESS EXCLUSIVE lock on the busy `tasks` table.
   - No DB CHECK — a plain boolean needs none (parity with is_pending /
     requires_human_review / nudge_disabled boolean flags on this table).

2. Index `ix_tasks_archive_sweep` ON (task_type, completed_at) — serves the
   sweep's WHERE `task_type='audit' AND completed_at < <cutoff>` predicate
   directly (leading equality on task_type, range scan on completed_at).
   Not partial: keeps it usable for any future task_type-scoped completed_at
   query, and the table is small enough that a full composite index is cheap.

3. Partial index `ix_tasks_active_archived` ON (is_active) WHERE
   is_active = false — supports the rare `?include_archived` /
   archive-audit queries that want ONLY archived rows, and keeps the index
   tiny (the overwhelming majority of rows are is_active=true, which the
   default-query path does NOT need an index for — it filters them IN, and
   the existing per-project + process_status indexes already narrow the set).

History capture is FREE on `tasks.is_active`: the existing `tasks_audit_trg`
(migration 0001) snapshots `to_jsonb(OLD)`, which auto-captures any new
column. The sweep's UPDATE therefore lands in `tasks_history` automatically
(operation 'U') — no trigger change needed.

Downgrade caveat: dropping `is_active` (+ its indexes) discards the archive
state silently. The feature is opt-in (every row defaults to visible), so
there is no data migration on downgrade — archived rows simply become
visible again, which is the pre-feature behavior.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0061_tasks_is_active"
down_revision = "0060_task_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 16 metadata-only ADD COLUMN: NOT NULL with a CONSTANT server_default
    # (true) is a catalog-only change — no heap rewrite, no full-table scan
    # under ACCESS EXCLUSIVE. Existing rows read `true` lazily via the stored
    # missing-value. See migration docstring for the no-risky-backfill rationale.
    op.add_column(
        "tasks",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Sweep hot path: WHERE task_type='audit' AND completed_at < <cutoff>.
    # Leading equality (task_type) + range (completed_at).
    op.create_index(
        "ix_tasks_archive_sweep",
        "tasks",
        ["task_type", "completed_at"],
    )
    # Tiny partial index for the rare "fetch archived rows" path
    # (?include_archived=true / archive audit). The default-query path
    # (is_active=true) does NOT need an index — it filters those rows IN and
    # the existing project_id / process_status indexes already narrow it.
    op.create_index(
        "ix_tasks_active_archived",
        "tasks",
        ["is_active"],
        postgresql_where=sa.text("is_active = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_active_archived", table_name="tasks")
    op.drop_index("ix_tasks_archive_sweep", table_name="tasks")
    op.drop_column("tasks", "is_active")
