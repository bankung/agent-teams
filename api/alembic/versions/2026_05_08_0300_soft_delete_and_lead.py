"""soft-delete columns + multi-domain lead — bundle migration

Revision ID: 0002_soft_delete_and_lead
Revises: 0001_initial
Create Date: 2026-05-08 03:00 UTC

Bundles three coupled schema changes that have to land atomically (the app
layer rename + lead column + assigned_role CHECK drop all flip on the same
deploy):

1. Soft-delete (decision 2026-05-05)
   - Rename `tasks.status` -> `tasks.process_status` (1..5 lifecycle code; freeing
     `status` for the uniform 0/1 soft-delete name across every business table).
   - Drop ck_tasks_status_valid (1..5 CHECK on the renamed column); recreate as
     ck_tasks_process_status_valid against process_status.
   - Add SMALLINT NOT NULL DEFAULT 1 `status` column to projects + tasks with named
     CHECK ck_<table>_status_valid (status IN (0, 1)). Existing rows backfill
     status=1 via DEFAULT (no manual UPDATE needed).
   - Index `ix_<table>_status` on each new soft-delete column.
   - Drop the unbounded UNIQUE on projects.name; replace with partial unique
     `ux_projects_name_active` ON projects(name) WHERE status=1 — soft-deleted
     rows free the name for re-use.
   - Drop+recreate `ux_projects_active_one` partial unique with the additional
     `AND status=1` predicate so a soft-deleted "active" row doesn't block a
     new active project.
   - Rename existing `ix_tasks_status` (1..5 lifecycle index) -> `ix_tasks_process_status`
     BEFORE creating the new soft-delete `ix_tasks_status` so names don't collide.
   - Audit trigger needs no changes — `to_jsonb(OLD)` snapshots the renamed column
     automatically; future trigger snapshots include the new `status` field.

2. Multi-domain lead (decision 2026-05-07)
   - Add TEXT NOT NULL DEFAULT 'dev' `lead` column to projects with named CHECK
     ck_projects_lead_valid (lead IN ('dev', 'novel')). Existing agent-teams row
     backfills lead='dev' via DEFAULT.

3. Drop tasks.assigned_role CHECK (decision 2026-05-07)
   - DROP ck_tasks_assigned_role_valid. App-layer validates assigned_role per
     active project's lead roster (codes 1..5 for dev, 11..12 for novel, etc.).
     Codes are still INTEGER NULL on the DB; only the CHECK is gone.

Downgrade reverses cleanly: drop new columns/indexes/checks, restore the
unbounded UNIQUE on projects.name, restore ux_projects_active_one predicate,
restore the assigned_role CHECK, rename process_status -> status. Uses
DROP ... IF EXISTS where the standard requires it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_soft_delete_and_lead"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py (TaskStatus.ALL, TaskRole.ALL, RecordStatus.ALL,
# ProjectLead.ALL). Migrations don't import app code — see standards/sqlalchemy/migrations.md.
_TASK_STATUS_ALL = (1, 2, 3, 4, 5)
_TASK_ROLE_ALL = (1, 2, 3, 4, 5)
_RECORD_STATUS_ALL = (0, 1)
_PROJECT_LEAD_ALL = ("dev", "novel")


def _in_clause(column: str, values: tuple[int, ...]) -> str:
    return f"{column} IN ({', '.join(str(v) for v in values)})"


def _in_clause_text(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. tasks.status -> tasks.process_status (and CHECK rename)
    # -------------------------------------------------------------------------
    # The 1..5 CHECK is named ck_tasks_status_valid in 0001_initial; the column
    # rename does NOT auto-rename the constraint, so we drop+recreate.
    op.drop_constraint("ck_tasks_status_valid", "tasks", type_="check")
    op.alter_column("tasks", "status", new_column_name="process_status")
    op.create_check_constraint(
        "ck_tasks_process_status_valid",
        "tasks",
        _in_clause("process_status", _TASK_STATUS_ALL),
    )

    # Rename the existing index BEFORE creating the new soft-delete `ix_tasks_status`
    # so names don't collide mid-migration.
    op.execute("ALTER INDEX ix_tasks_status RENAME TO ix_tasks_process_status;")

    # -------------------------------------------------------------------------
    # 2. Drop ck_tasks_assigned_role_valid (app-layer validation per lead roster)
    # -------------------------------------------------------------------------
    op.drop_constraint("ck_tasks_assigned_role_valid", "tasks", type_="check")

    # -------------------------------------------------------------------------
    # 3. Soft-delete `status` on projects + tasks
    # -------------------------------------------------------------------------
    # Existing rows backfill status=1 via the DEFAULT clause — no UPDATE needed.
    op.add_column(
        "projects",
        sa.Column(
            "status",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_projects_status_valid",
        "projects",
        _in_clause("status", _RECORD_STATUS_ALL),
    )
    op.create_index("ix_projects_status", "projects", ["status"])

    op.add_column(
        "tasks",
        sa.Column(
            "status",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_tasks_status_valid",
        "tasks",
        _in_clause("status", _RECORD_STATUS_ALL),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])

    # -------------------------------------------------------------------------
    # 4. projects.name uniqueness: drop unbounded UNIQUE -> partial unique on status=1
    # -------------------------------------------------------------------------
    # The 0001_initial migration declared name UNIQUE inline on the column, which
    # PostgreSQL implements as `projects_name_key`. Drop it then create the
    # partial unique index gated on status=1.
    op.drop_constraint("projects_name_key", "projects", type_="unique")
    op.create_index(
        "ux_projects_name_active",
        "projects",
        ["name"],
        unique=True,
        postgresql_where=sa.text("status = 1"),
    )

    # -------------------------------------------------------------------------
    # 5. ux_projects_active_one — tighten predicate to also require status=1
    # -------------------------------------------------------------------------
    op.drop_index("ux_projects_active_one", table_name="projects")
    op.create_index(
        "ux_projects_active_one",
        "projects",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE AND status = 1"),
    )

    # -------------------------------------------------------------------------
    # 6. projects.lead — TEXT NOT NULL DEFAULT 'dev', CHECK lead IN ('dev','novel')
    # -------------------------------------------------------------------------
    op.add_column(
        "projects",
        sa.Column(
            "lead",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'dev'"),
        ),
    )
    op.create_check_constraint(
        "ck_projects_lead_valid",
        "projects",
        _in_clause_text("lead", _PROJECT_LEAD_ALL),
    )


def downgrade() -> None:
    # Reverse order of upgrade(). Use DROP ... IF EXISTS where the rollback may
    # be re-run (idempotent recovery per standards/sqlalchemy/migrations.md).

    # 6. lead column
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_lead_valid;")
    op.drop_column("projects", "lead")

    # 5. ux_projects_active_one — restore predicate without status=1
    op.drop_index("ux_projects_active_one", table_name="projects")
    op.create_index(
        "ux_projects_active_one",
        "projects",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )

    # 4. projects.name — restore unbounded UNIQUE
    op.drop_index("ux_projects_name_active", table_name="projects")
    op.create_unique_constraint("projects_name_key", "projects", ["name"])

    # 3. soft-delete status columns + indexes + checks
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_status_valid;")
    op.drop_column("tasks", "status")

    op.drop_index("ix_projects_status", table_name="projects")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_status_valid;")
    op.drop_column("projects", "status")

    # 2. restore ck_tasks_assigned_role_valid
    op.create_check_constraint(
        "ck_tasks_assigned_role_valid",
        "tasks",
        f"assigned_role IS NULL OR {_in_clause('assigned_role', _TASK_ROLE_ALL)}",
    )

    # 1. process_status -> status (and CHECK rename back)
    op.execute("ALTER INDEX ix_tasks_process_status RENAME TO ix_tasks_status;")
    op.execute(
        "ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_process_status_valid;"
    )
    op.alter_column("tasks", "process_status", new_column_name="status")
    op.create_check_constraint(
        "ck_tasks_status_valid",
        "tasks",
        _in_clause("status", _TASK_STATUS_ALL),
    )
