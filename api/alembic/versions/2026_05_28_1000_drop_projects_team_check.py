"""Drop ck_projects_team_valid CHECK constraint (Kanban #1620)

Revision ID: 0051_drop_projects_team_check
Revises: 0050_credentials_unique_active
Create Date: 2026-05-28 10:00 UTC

The CHECK constraint `ck_projects_team_valid` was the thing forcing a migration
every time a new team was added: the team enum lived in FOUR places that had to
move atomically (constants.py, the ORM CheckConstraint, the TeamCode Literal, and
this DB CHECK). #1620 collapses the enum to a SINGLE source of truth
(`src/constants.ProjectTeam.ALL` + `TEAM_ROSTERS`) and moves valid-value
enforcement to the API boundary:

  * `routers/projects.py` create_project / update_project reject
    `team not in ProjectTeam.ALL` with HTTP 422 (was: an unknown team fell
    through to the IntegrityError handler and returned a WRONG 409
    name-conflict message).
  * `schemas/project.py::TeamCode = Literal[*ProjectTeam.ALL]` 422s at the
    Pydantic request boundary.

After this migration, adding a team needs only a constants.py edit (+ the .md
files) — NO migration, no ORM CheckConstraint, no FE/ps1/scaffold edits.

`projects.team` stays NOT NULL DEFAULT 'dev' (unchanged) — only the value-set
CHECK is removed.

DDL: drop the constraint. Postgres `DROP CONSTRAINT ... IF EXISTS` is not used —
the constraint is known to exist (created by 0001/0038/.../0044). A plain
DROP CONSTRAINT cannot fail on row data (it only removes a rule), so this is a
zero-risk, reversible-by-recreate change on the single-owner dev/dogfood DB.

Downgrade: recreates `ck_projects_team_valid` with the 7-team set that was
current as of this revision (the set that 0044 established). This keeps the
downgrade chain self-consistent: older team migrations (0044 and earlier) expect
the constraint to be present in the DB when their `downgrade()` runs its
`op.drop_constraint` call. Without this recreate, 0044's downgrade would fail
with `ERROR: constraint "ck_projects_team_valid" of relation "projects" does not
exist`.

The same caveat as every team migration applies: if any rows carry a `team` value
outside this 7-set (e.g. a team added post-#1620 without a migration), the
recreate will fail (`ERROR: check constraint ... is violated by some row`). The
operator must re-team or soft-delete those rows via the API first — never raw SQL
DML. See shared/db-schema.md for the soft-delete + audit-trigger contract.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0051_drop_projects_team_check"
down_revision = "0050_credentials_unique_active"
branch_labels = None
depends_on = None


# The 7-team set that was current when 0051 ran. Migrations must NOT import app
# code — see standards/sqlalchemy/migrations.md "Helper duplication between app
# and migration". Kept in sync with src/constants.py ProjectTeam.ALL at the time
# of this revision.
_PROJECT_TEAM_ALL = (
    "dev",
    "novel",
    "general",
    "content",
    "seo",
    "data-analytics",
    "sem",
)


def _in_clause_text(column: str, values: tuple[str, ...]) -> str:
    # Mirror of src.constants.in_clause_text — duplicated locally so the
    # migration has zero app-code imports.
    _allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    for v in values:
        if not v or any(c not in _allowed for c in v):
            raise ValueError(
                f"_in_clause_text only allows [a-z0-9_-]+ values; got {v!r}"
            )
    quoted = ", ".join("'" + v + "'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("ck_projects_team_valid", "projects", type_="check")


def downgrade() -> None:
    # Recreate the CHECK with the then-current 7-team set so the downgrade chain
    # stays self-consistent. Older migrations (0044 and below) call
    # op.drop_constraint("ck_projects_team_valid", ...) in their own downgrade()
    # and will fail if the constraint is absent. The constraint is guaranteed
    # ABSENT here (0051.upgrade() dropped it), so a bare create is correct.
    # CAVEAT: if any rows carry a team value outside this 7-set, the recreate
    # will fail. Operator must re-team/soft-delete those rows via API first.
    op.create_check_constraint(
        "ck_projects_team_valid",
        "projects",
        _in_clause_text("team", _PROJECT_TEAM_ALL),
    )
