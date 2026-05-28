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

Downgrade caveat (intentional NO-OP): re-adding the CHECK is NOT safe to do
blindly. By the time anyone downgrades, the live `projects` table may carry rows
whose `team` is a value that did not exist in any historical CHECK tuple (the
whole point of #1620 is that new teams are added WITHOUT a migration). Recreating
the constraint with any fixed historical tuple would fail
(`ERROR: check constraint ... is violated by some row`) on those rows, or worse,
silently exclude legitimate teams. On this single-owner dev DB the safe downgrade
is to do nothing; if a hard rollback to a CHECK-enforced schema is ever required,
the operator must first decide the exact allowed-team tuple for the CURRENT data
and recreate the constraint by hand (never raw SQL DML to mutate rows). See
shared/db-schema.md for the soft-delete + audit-trigger contract.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0051_drop_projects_team_check"
down_revision = "0050_credentials_unique_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_projects_team_valid", "projects", type_="check")


def downgrade() -> None:
    # Intentional NO-OP — re-adding the CHECK after #1620 could fail on rows
    # carrying a team value that was added without a migration. See the module
    # docstring "Downgrade caveat". A hard rollback to CHECK-enforced schema is a
    # manual, data-aware operator step, not an automatic downgrade.
    pass
