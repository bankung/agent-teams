"""Drop ck_handoff_templates_default_assigned_role_range CHECK constraint (Kanban #2819)

Revision ID: 0077_drop_handoff_role_check
Revises: 0076_telegram_chat_state
Create Date: 2026-07-11 10:00 UTC

`handoff_templates.default_assigned_role` carried its own DB CHECK hardcoded
to <= 50 (migration 0045_handoff_templates). #2812 bumped `TaskRole.RANGE_MAX`
50 -> 60 (social team codes 51-57) so the app-side Pydantic validator
(`schemas/handoff_template.py::_validate_role_range`, which mirrors
`TaskRole.RANGE_MIN/RANGE_MAX` dynamically) now accepts 1..60 — but this DB
CHECK still hard-caps at 50, so a role in 51..60 that passes Pydantic then
raises an IntegrityError at the DB layer (surfaced by the router's generic
IntegrityError handler as an HTTP 400 "violates a database constraint").

This mirrors the #1620 doctrine already applied to `ck_projects_team_valid`
(migration 0051_drop_projects_team_check) and to `tasks.assigned_role` (DB
CHECK dropped by migration 0002 — see constants.TaskRole docstring): the
Pydantic validator is the single source of truth for the valid-value range,
so a static DB CHECK that duplicates it just re-diverges on every RANGE_MAX
bump. Dropping it here removes that recurring migration tax for
`handoff_templates` too.

DDL: drop the constraint. A plain DROP CONSTRAINT cannot fail on row data (it
only removes a rule), so this is a zero-risk, reversible-by-recreate change.

Downgrade: recreates the constraint with the original <= 50 bound (the bound
in place when 0045 created it), so the downgrade chain stays self-consistent
with 0045's own assumptions.

BUILD-ONLY (per the #2819 spawn brief): NOT applied by this spawn. The Lead
gates `alembic upgrade` behind a review pass of this file.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0077_drop_handoff_role_check"
down_revision = "0076_telegram_chat_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_handoff_templates_default_assigned_role_range",
        "handoff_templates",
        type_="check",
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_handoff_templates_default_assigned_role_range",
        "handoff_templates",
        "default_assigned_role IS NULL OR "
        "(default_assigned_role >= 1 AND default_assigned_role <= 50)",
    )
