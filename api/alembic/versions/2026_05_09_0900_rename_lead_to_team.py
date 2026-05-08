"""rename projects.lead -> projects.team

Revision ID: 0004_rename_lead_to_team
Revises: 0003_tasks_parent_task_id
Create Date: 2026-05-09 09:00 UTC

Phase 2.5b1 of the MD compaction + naming-clarity effort. The user (and the
architecture going forward) treats `team='dev'` / `team='novel'` as the team
that owns a project's roster of agents — eliminating the ambiguity between
the column value and the orchestrator persona "Lead" (capital-L), which stays
unchanged.

Pure DDL — no data migration. ALTER COLUMN ... RENAME preserves rows + data
+ default; we drop+recreate the named CHECK constraint because column rename
does NOT auto-rename constraints in Postgres (same situation as the prior
`tasks.status` -> `tasks.process_status` rename in 0002).

- Rename column projects.lead -> projects.team (server_default 'dev' is preserved
  by ALTER COLUMN RENAME — verified against PG 16 docs).
- Drop ck_projects_lead_valid; recreate as ck_projects_team_valid against the
  renamed column with `team IN ('dev', 'novel')`.

Downgrade reverses cleanly: rename back to `lead` and recreate the original
CHECK name.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_rename_lead_to_team"
down_revision = "0003_tasks_parent_task_id"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py (ProjectTeam.ALL). Migrations don't import
# app code — see standards/sqlalchemy/migrations.md "Helper duplication between
# app and migration".
_PROJECT_TEAM_ALL = ("dev", "novel")


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
    # 1. Drop the old CHECK first — it references the column by name and would
    #    otherwise still be named ck_projects_lead_valid after the rename.
    op.execute(
        "ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_lead_valid;"
    )

    # 2. Rename the column. server_default ('dev') is preserved by RENAME.
    op.alter_column("projects", "lead", new_column_name="team")

    # 3. Recreate the CHECK against the renamed column.
    op.create_check_constraint(
        "ck_projects_team_valid",
        "projects",
        _in_clause_text("team", _PROJECT_TEAM_ALL),
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.execute(
        "ALTER TABLE projects DROP CONSTRAINT IF EXISTS ck_projects_team_valid;"
    )
    op.alter_column("projects", "team", new_column_name="lead")
    op.create_check_constraint(
        "ck_projects_lead_valid",
        "projects",
        _in_clause_text("lead", _PROJECT_TEAM_ALL),
    )
