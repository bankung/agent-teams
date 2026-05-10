"""tasks.task_kind + recurrence template columns

Revision ID: 0007_task_kind_and_recurrence
Revises: 0006_drop_active_one
Create Date: 2026-05-10 11:00 UTC

V3+ scope-lock T1 (Kanban #706). Foundation slice for the 4-feature scope-lock
2026-05-10 (recurring tasks + task_kind + drag-drop + theme). BLOCKS T2 #707
(scheduler), T3 #708 (FE badges), T4 #709 (FE drag-drop).

Up — single transaction adds:
  - tasks.task_kind VARCHAR(8) NOT NULL DEFAULT 'human'
    + CHECK ck_tasks_task_kind_valid: task_kind IN ('ai','human')
  - tasks.is_template BOOLEAN NOT NULL DEFAULT false
  - tasks.recurrence_rule VARCHAR(255) NULL  (cron string; Pydantic + croniter validate)
  - tasks.recurrence_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'  (IANA TZ; cron is TZ-sensitive)
  - tasks.next_fire_at TIMESTAMPTZ NULL  (scheduler hot-path target)
  - tasks.spawned_from_task_id BIGINT NULL FK→tasks(id) ON DELETE SET NULL
    (lineage from spawn-fire — set by T2 scheduler / not user-settable on UPDATE)
  - INDEX ix_tasks_next_fire_at_template ON (next_fire_at) WHERE is_template = TRUE
    (partial index — scheduler scans only the sparse template subset)
  - CHECK ck_tasks_template_recurrence_complete:
      is_template = false OR (recurrence_rule IS NOT NULL AND next_fire_at IS NOT NULL)
    (DB defense-in-depth — Pydantic also enforces template completeness)

Cross-table rule task_kind ↔ run_mode (HUMAN must pair with MANUAL) does NOT
live as a DB CHECK — it spans the run_mode column with no cross-row dependency
but lives at the app layer per the methodology decision in
context/teams/dev/decisions.md 2026-05-09 (cross-table = service layer). See
src/services/task_kind.py.

FK ondelete=SET NULL (NOT CASCADE): if a HARD delete ever happens to a template
(admin path), keep spawned children pointing at NULL rather than corrupted FK.
Soft-delete of a template does NOT touch this FK — children retain their
lineage pointer to the soft-deleted template (audit-friendly).

The CHECK list values are mirrored from the (forthcoming) src/constants.py
TaskKind.ALL — duplicated locally per standards/sqlalchemy/migrations.md
("Migrations don't import app code").

Existing 38 rows backfill cleanly:
  - task_kind = 'human'     (server_default fires on ADD COLUMN NOT NULL)
  - is_template = false     (server_default fires)
  - recurrence_rule = NULL  (column nullable)
  - recurrence_timezone = 'UTC'  (server_default fires)
  - next_fire_at = NULL     (column nullable)
  - spawned_from_task_id = NULL  (column nullable)

PG 16 ADD COLUMN with constant DEFAULT does not rewrite the table
(see standards/postgresql/operations.md). Backfill is metadata-only.

Down (reverse order): drop CHECK → drop partial index → drop FK → drop columns
→ drop task_kind CHECK → drop task_kind column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_task_kind_and_recurrence"
down_revision = "0006_drop_active_one"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py TaskKind.ALL — duplicated per
# standards/sqlalchemy/migrations.md "Helper duplication between app and migration".
_TASK_KIND_ALL = ("ai", "human")


def _in_clause_text(column: str, values: tuple[str, ...]) -> str:
    # Mirror of src.constants.in_clause_text — duplicated locally so the migration
    # has zero app-code imports.
    _allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    for v in values:
        if not v or any(c not in _allowed for c in v):
            raise ValueError(
                f"_in_clause_text only allows [a-z0-9_-]+ values; got {v!r}"
            )
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


def upgrade() -> None:
    # 1. task_kind — default 'human' covers existing rows without backfill.
    op.add_column(
        "tasks",
        sa.Column(
            "task_kind",
            sa.String(length=8),
            server_default="human",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_task_kind_valid",
        "tasks",
        _in_clause_text("task_kind", _TASK_KIND_ALL),
    )

    # 2. recurrence template fields. is_template + recurrence_timezone get
    #    server_defaults so existing rows backfill metadata-only. Nullable
    #    columns (recurrence_rule, next_fire_at, spawned_from_task_id) backfill
    #    to NULL.
    op.add_column(
        "tasks",
        sa.Column(
            "is_template",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("recurrence_rule", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "recurrence_timezone",
            sa.String(length=64),
            server_default="UTC",
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "next_fire_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("spawned_from_task_id", sa.BigInteger(), nullable=True),
    )

    # 3. FK lineage — SET NULL on hard delete (defense-in-depth; app never
    #    hard-deletes templates).
    op.create_foreign_key(
        "fk_tasks_spawned_from_task_id",
        "tasks",
        "tasks",
        ["spawned_from_task_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 4. Partial index — scheduler hot path. WHERE is_template=TRUE keeps the
    #    index small (most rows are non-template).
    op.create_index(
        "ix_tasks_next_fire_at_template",
        "tasks",
        ["next_fire_at"],
        postgresql_where=sa.text("is_template = TRUE"),
    )

    # 5. Template completeness CHECK — DB-level defense-in-depth. Pydantic
    #    model_validator catches the same case at 422; this CHECK fires on
    #    raw-SQL bypass / future schema drift.
    op.create_check_constraint(
        "ck_tasks_template_recurrence_complete",
        "tasks",
        "is_template = false OR (recurrence_rule IS NOT NULL AND next_fire_at IS NOT NULL)",
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_constraint(
        "ck_tasks_template_recurrence_complete", "tasks", type_="check"
    )
    op.drop_index("ix_tasks_next_fire_at_template", table_name="tasks")
    op.drop_constraint(
        "fk_tasks_spawned_from_task_id", "tasks", type_="foreignkey"
    )
    op.drop_column("tasks", "spawned_from_task_id")
    op.drop_column("tasks", "next_fire_at")
    op.drop_column("tasks", "recurrence_timezone")
    op.drop_column("tasks", "recurrence_rule")
    op.drop_column("tasks", "is_template")
    op.drop_constraint("ck_tasks_task_kind_valid", "tasks", type_="check")
    op.drop_column("tasks", "task_kind")
