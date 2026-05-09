"""tasks.run_mode + projects.auto_run_consent_at

Revision ID: 0005_run_mode_and_consent
Revises: 0004_rename_lead_to_team
Create Date: 2026-05-09 10:00 UTC

Schema seam for Step 2 of the Kanban-driven AI integration (umbrella task #481,
this subtask #482). Pure DDL — no app-code change in this revision; ORM models,
Pydantic schemas, constants and routers land in the next subtask (#481-B).

Up:
  - tasks.run_mode TEXT NOT NULL DEFAULT 'manual'
  - CHECK ck_tasks_run_mode_valid: run_mode IN ('manual','auto_pickup','auto_headless')
  - projects.auto_run_consent_at TIMESTAMPTZ NULL  (NULL = not yet consented)

Down (reverse order):
  - drop projects.auto_run_consent_at  (data lost — consent is a UI action; user re-grants)
  - drop CHECK ck_tasks_run_mode_valid
  - drop tasks.run_mode

Indexes:
  - none in this revision. Queue runner (`WHERE run_mode != 'manual' AND
    process_status = 1 ORDER BY priority DESC`) can ride the existing
    process_status/priority indexes; if profiling later shows a partial
    index `WHERE run_mode != 'manual'` is needed, add it then. YAGNI.

The CHECK list is mirrored from the (future) src/constants.py TaskRunMode
helper — duplicated locally per standards/sqlalchemy/migrations.md
("Migrations don't import app code").
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_run_mode_and_consent"
down_revision = "0004_rename_lead_to_team"
branch_labels = None
depends_on = None


# Kept in sync with the (forthcoming) src/constants.py TaskRunMode.ALL.
# Migrations don't import app code — see standards/sqlalchemy/migrations.md
# "Helper duplication between app and migration".
_TASK_RUN_MODE_ALL = ("manual", "auto_pickup", "auto_headless")


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
    # 1. tasks.run_mode — default 'manual' covers existing rows without backfill.
    #    PG 16 ADD COLUMN with constant DEFAULT does not rewrite the table
    #    (see standards/postgresql/operations.md).
    op.add_column(
        "tasks",
        sa.Column(
            "run_mode",
            sa.Text(),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
    )

    # 2. CHECK on the new column. Named explicitly so downgrade can drop it
    #    by name (PG does not auto-name CHECKs predictably).
    op.create_check_constraint(
        "ck_tasks_run_mode_valid",
        "tasks",
        _in_clause_text("run_mode", _TASK_RUN_MODE_ALL),
    )

    # 3. projects.auto_run_consent_at — NULL = no consent yet. No CHECK needed
    #    (any TIMESTAMPTZ value is meaningful) and no index (only read alongside
    #    projects.id PK lookups).
    op.add_column(
        "projects",
        sa.Column(
            "auto_run_consent_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_column("projects", "auto_run_consent_at")
    op.drop_constraint("ck_tasks_run_mode_valid", "tasks", type_="check")
    op.drop_column("tasks", "run_mode")
