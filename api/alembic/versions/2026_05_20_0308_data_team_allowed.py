"""projects.team add 'data-analytics' (Kanban #1271 AC7)

Revision ID: 0043_projects_team_data
Revises: 0042_projects_team_seo
Create Date: 2026-05-20 03:08 UTC

Adds `'data-analytics'` to the `ck_projects_team_valid` CHECK constraint so
projects can opt into the data-analytics-team playbook (BI / SQL / dashboards
/ analytics-platform integration). The team enum is a wire-contract that must
land atomically across BE (constants.py, ORM CheckConstraint, scaffolds, the
TeamCode Literal guard in schemas/project.py) and FE (web/lib/constants.ts) —
splitting risks a window where one side knows about the new value but the
other rejects it.

This migration chains atop 0042 (seo). Operator applies 0042 + 0043 together
with `MIGRATION_TARGET=live alembic upgrade head`; the constants.py +
schemas/project.py Literal extension for BOTH 'seo' AND 'data-analytics' must
land in the same commit as the apply.

DDL: drop the existing CHECK and recreate it with the extended value tuple.
Postgres has no `ALTER CONSTRAINT … RENAME / RETARGET` for CHECK — drop +
recreate is the canonical idiom and is what 0038 / 0042 used when they last
touched this constraint.

Downgrade caveat: the recreated CHECK in `downgrade()` only allows the 0042
set `('dev','novel','general','content','seo')`. If any rows with
`team='data-analytics'` exist at downgrade time the constraint creation will
fail (`ERROR: check constraint ... is violated by some row`). The operator
must either soft-delete or re-team those rows first; the migration
intentionally does NOT auto-mutate user data. See shared/db-schema.md for
the soft-delete + audit-trigger contract.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0043_projects_team_data"
down_revision = "0042_projects_team_seo"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py (ProjectTeam.ALL). Migrations don't import
# app code — see standards/sqlalchemy/migrations.md "Helper duplication between
# app and migration".
_PROJECT_TEAM_ALL_NEW = ("dev", "novel", "general", "content", "seo", "data-analytics")
_PROJECT_TEAM_ALL_OLD = ("dev", "novel", "general", "content", "seo")


def _in_clause_text(column: str, values: tuple[str, ...]) -> str:
    # Mirror of src.constants.in_clause_text — duplicated locally so the
    # migration has zero app-code imports.
    _allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    for v in values:
        if not v or any(c not in _allowed for c in v):
            raise ValueError(
                f"_in_clause_text only allows [a-z0-9_-]+ values; got {v!r}"
            )
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_projects_team_valid", "projects", type_="check")
    op.create_check_constraint(
        "ck_projects_team_valid",
        "projects",
        _in_clause_text("team", _PROJECT_TEAM_ALL_NEW),
    )


def downgrade() -> None:
    # CAVEAT: any row with team='data-analytics' will block the recreate.
    # Operator must clean up first (soft-delete or re-team via API, never
    # raw SQL DML).
    op.drop_constraint("ck_projects_team_valid", "projects", type_="check")
    op.create_check_constraint(
        "ck_projects_team_valid",
        "projects",
        _in_clause_text("team", _PROJECT_TEAM_ALL_OLD),
    )
