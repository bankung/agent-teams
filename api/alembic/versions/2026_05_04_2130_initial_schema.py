"""initial schema — projects, tasks, tasks_history + audit trigger

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-04 21:30 UTC

Creates the full v1 Kanban schema in one migration:
- `projects`              — project registry (one `is_active=true` enforced via partial unique index)
- `tasks`                 — Kanban tasks scoped to a project; integer-coded enums
- `tasks_history`         — audit sink, populated by PG trigger
- `tasks_audit_fn()`      — PL/pgSQL function snapshotting OLD into tasks_history
- `tasks_audit_trg`       — trigger on tasks AFTER UPDATE OR DELETE FOR EACH ROW

Integer codes (status, priority, assigned_role) are validated by CHECK constraints —
canonical values mirror src/constants.py and context/standards/general.md.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


# Keep these lists in sync with src/constants.py — duplicated here so Alembic
# migrations are self-contained (don't import application code from migrations).
_TASK_STATUS_ALL = (1, 2, 3, 4, 5)
_TASK_PRIORITY_ALL = (1, 2, 3, 4)
_TASK_ROLE_ALL = (1, 2, 3, 4, 5)


def _in_clause(column: str, values: tuple[int, ...]) -> str:
    return f"{column} IN ({', '.join(str(v) for v in values)})"


def upgrade() -> None:
    # -------- projects --------
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("paths_web", sa.Text(), nullable=False),
        sa.Column("paths_api", sa.Text(), nullable=False),
        sa.Column("paths_db", sa.Text(), nullable=False),
        sa.Column("stack_web", sa.Text(), nullable=True),
        sa.Column("stack_api", sa.Text(), nullable=True),
        sa.Column("stack_db", sa.Text(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
    )
    # Partial unique index — at most one row with is_active=true.
    op.create_index(
        "ux_projects_active_one",
        "projects",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )

    # -------- tasks --------
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
        sa.Column("assigned_role", sa.Integer(), nullable=True),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_clause("status", _TASK_STATUS_ALL),
            name="ck_tasks_status_valid",
        ),
        sa.CheckConstraint(
            _in_clause("priority", _TASK_PRIORITY_ALL),
            name="ck_tasks_priority_valid",
        ),
        sa.CheckConstraint(
            f"assigned_role IS NULL OR {_in_clause('assigned_role', _TASK_ROLE_ALL)}",
            name="ck_tasks_assigned_role_valid",
        ),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_assigned_role", "tasks", ["assigned_role"])

    # -------- tasks_history --------
    op.create_table(
        "tasks_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),  # NOT a FK by design
        sa.Column("operation", sa.CHAR(length=1), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation IN ('U', 'D')",
            name="ck_tasks_history_operation_valid",
        ),
    )
    op.create_index("ix_tasks_history_task_id", "tasks_history", ["task_id"])
    op.create_index("ix_tasks_history_changed_at", "tasks_history", ["changed_at"])

    # -------- audit function + trigger --------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tasks_audit_fn() RETURNS trigger AS $$
        DECLARE
            op_code char(1);
            row_id  bigint;
        BEGIN
            IF (TG_OP = 'UPDATE') THEN
                op_code := 'U';
                row_id  := OLD.id;
            ELSIF (TG_OP = 'DELETE') THEN
                op_code := 'D';
                row_id  := OLD.id;
            ELSE
                RETURN NULL;
            END IF;

            INSERT INTO tasks_history (task_id, operation, changed_at, snapshot)
            VALUES (row_id, op_code, now(), to_jsonb(OLD));

            IF (TG_OP = 'DELETE') THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER tasks_audit_trg
        AFTER UPDATE OR DELETE ON tasks
        FOR EACH ROW EXECUTE FUNCTION tasks_audit_fn();
        """
    )


def downgrade() -> None:
    # Drop in reverse dependency order: trigger -> function -> history -> tasks -> projects.
    op.execute("DROP TRIGGER IF EXISTS tasks_audit_trg ON tasks;")
    op.execute("DROP FUNCTION IF EXISTS tasks_audit_fn();")

    op.drop_index("ix_tasks_history_changed_at", table_name="tasks_history")
    op.drop_index("ix_tasks_history_task_id", table_name="tasks_history")
    op.drop_table("tasks_history")

    op.drop_index("ix_tasks_assigned_role", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ux_projects_active_one", table_name="projects")
    op.drop_table("projects")
