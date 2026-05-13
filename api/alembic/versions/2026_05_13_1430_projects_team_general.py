"""projects.team add 'general' (Kanban #844)

Revision ID: 0021_projects_team_general
Revises: 0020_projects_sources
Create Date: 2026-05-13 14:30 UTC

Adds `'general'` to the `ck_projects_team_valid` CHECK constraint so projects
can opt into a non-domain-specific team playbook (`.claude/teams/general.md`,
drafted in the follow-up Kanban #845). The team enum is a wire-contract that
must land atomically across BE (constants.py, ORM CheckConstraint, scaffolds)
and FE (web/lib/constants.ts) — splitting risks a window where one side knows
about the new value but the other rejects it. Atomic-coupling rationale lives
in the Kanban #844 spawn brief.

DDL: drop the existing CHECK and recreate it with the extended value tuple.
Postgres has no `ALTER CONSTRAINT … RENAME / RETARGET` for CHECK — drop +
recreate is the canonical idiom and is what `0004_rename_lead_to_team` used
when it last touched this constraint.

Downgrade caveat: the recreated CHECK in `downgrade()` only allows
`('dev','novel')`. If any rows with `team='general'` exist at downgrade time
the constraint creation will fail (`ERROR: check constraint ... is violated
by some row`). The operator must either soft-delete or re-team those rows
first; the migration intentionally does NOT auto-mutate user data. See
shared/db-schema.md for the soft-delete + audit-trigger contract.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0021_projects_team_general"
down_revision = "0020_projects_sources"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py (ProjectTeam.ALL). Migrations don't import
# app code — see standards/sqlalchemy/migrations.md "Helper duplication between
# app and migration".
_PROJECT_TEAM_ALL_NEW = ("dev", "novel", "general")
_PROJECT_TEAM_ALL_OLD = ("dev", "novel")


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
    # CAVEAT: any row with team='general' will block the recreate. Operator
    # must clean up first (soft-delete or re-team via API, never raw SQL DML).
    op.drop_constraint("ck_projects_team_valid", "projects", type_="check")
    op.create_check_constraint(
        "ck_projects_team_valid",
        "projects",
        _in_clause_text("team", _PROJECT_TEAM_ALL_OLD),
    )
