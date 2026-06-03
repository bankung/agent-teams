"""milestones table + tasks.milestone_id (Kanban #1868)

Revision ID: 0057_milestones
Revises: 0056_task_model_override
Create Date: 2026-06-03 01:00 UTC

Per-project Milestones (Phase 1, backend only) — a first-class entity to group
tasks for release planning. A milestone belongs to one project; a task may
optionally point at one milestone in the same project.

DESIGN (locked spec, #1868):

1. `milestones` table — the grouping entity.
   - `project_id` BIGINT NOT NULL, FK ON DELETE CASCADE — deleting a project
     takes its milestones with it (mirrors `tasks` / `handoff_templates`).
   - `title` TEXT NOT NULL; `description` TEXT NULLABLE.
   - `milestone_status` TEXT NOT NULL DEFAULT 'planned' + CHECK IN
     ('planned','active','released','cancelled'). This is the LIFECYCLE column.
   - `start_date` / `target_date` DATE NULLABLE — Gantt v1 dates. App-layer
     validator enforces start_date <= target_date when both are set (no DB
     CHECK — parity with the cross-field rules kept at the API boundary).
   - `sort_order` DOUBLE PRECISION NULLABLE — within-project manual ordering.
     Mirrors `tasks.sort_order` (sparse-float, NULL = created_at fallback). No
     CHECK / FK / index this slice (same measured-first index policy as
     migration 0018 for tasks.sort_order).
   - `status` SMALLINT NOT NULL DEFAULT 1 + CHECK IN (0,1) — uniform soft-delete
     flag (RecordStatus). MIRRORS tasks exactly. NAMING (#1868): the lifecycle
     column is `milestone_status`; the soft-delete flag is `status` — the same
     separation tasks use for `process_status` (lifecycle) vs `status`
     (soft-delete).
   - `created_at` / `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now().
   - `released_at` TIMESTAMPTZ NULLABLE — stamped when the milestone is released
     (operator-managed; no auto-stamp this slice).
   - Index `ix_milestones_project_id` — list filter by project (the hot query).

2. `tasks.milestone_id` BIGINT NULLABLE — FK ON DELETE SET NULL. NULL = task is
   not assigned to any milestone. ON DELETE SET NULL (NOT CASCADE) so deleting
   a milestone (rare; milestones are soft-deleted in practice) does not
   cascade-delete its tasks — they just detach. Mirrors the `blocked_by` /
   `handoff_template_id` posture (defense-in-depth; app soft-deletes a milestone
   and NULLs children in the same transaction at the router). Index
   `ix_tasks_milestone_id` supports the `?milestone_id` task-list filter +
   the milestone rollup GROUP BY.

History capture is FREE on `tasks.milestone_id`: the existing `tasks_audit_trg`
(migration 0001) uses `to_jsonb(OLD)` which auto-captures any new column. No
trigger change needed; `milestone_id` lands in `tasks_history.snapshot`
automatically. NO audit trigger on `milestones` (mirrors `handoff_templates` /
`sessions` precedent — operator-CRUD metadata, not lifecycle-tracked work).

PG 16 — metadata-only ADD COLUMN on `tasks.milestone_id` (nullable, no DEFAULT)
— no heap rewrite on existing task rows.

Downgrade caveats:
  - Dropping `tasks.milestone_id` (+ its index) discards the FK silently; the
    feature is opt-in per task so there is no data migration.
  - Dropping `milestones` deletes any operator-configured milestones. No
    restore path; operator re-creates after downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0057_milestones"
down_revision = "0056_task_model_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- milestones table ---------------------------------------------------
    op.create_table(
        "milestones",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "milestone_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'planned'"),
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        # Within-project manual ordering — mirror of tasks.sort_order
        # (migration 0018): DOUBLE PRECISION NULLABLE, no CHECK / FK / index.
        sa.Column(
            "sort_order",
            sa.dialects.postgresql.DOUBLE_PRECISION(),
            nullable=True,
        ),
        # Soft-delete (postgresql/soft-delete.md) — uniform 0/1 column, MIRRORS
        # tasks exactly. Distinct from `milestone_status` (lifecycle) above.
        sa.Column(
            "status",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # Lifecycle enum gate — mirror of constants.MilestoneStatus.ALL.
        sa.CheckConstraint(
            "milestone_status IN ('planned', 'active', 'released', 'cancelled')",
            name="ck_milestones_milestone_status_valid",
        ),
        # Soft-delete flag gate — mirror of tasks' ck_tasks_status_valid intent.
        sa.CheckConstraint(
            "status IN (0, 1)",
            name="ck_milestones_status_valid",
        ),
    )
    op.create_index(
        "ix_milestones_project_id",
        "milestones",
        ["project_id"],
    )

    # ---- tasks.milestone_id -------------------------------------------------
    # PG 16 metadata-only ADD COLUMN — nullable, no DEFAULT, no heap rewrite.
    # FK ON DELETE SET NULL so deleting a milestone detaches its tasks rather
    # than cascade-deleting them. Mirrors blocked_by / handoff_template_id.
    op.add_column(
        "tasks",
        sa.Column(
            "milestone_id",
            sa.BigInteger(),
            sa.ForeignKey("milestones.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tasks_milestone_id",
        "tasks",
        ["milestone_id"],
    )

    # ---- tasks.due_date -----------------------------------------------------
    # Kanban #1868 follow-up: optional display/planning date for a future
    # Calendar view. Distinct from `scheduled_at` (DateTime, scheduler-linked)
    # — this is a bare Date (no time, no TZ), fully decoupled from the
    # scheduler / autorun / Gantt. PG 16 metadata-only ADD COLUMN — nullable,
    # no DEFAULT, no heap rewrite.
    op.add_column(
        "tasks",
        sa.Column("due_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "due_date")
    op.drop_index("ix_tasks_milestone_id", table_name="tasks")
    op.drop_column("tasks", "milestone_id")
    op.drop_index("ix_milestones_project_id", table_name="milestones")
    op.drop_table("milestones")
