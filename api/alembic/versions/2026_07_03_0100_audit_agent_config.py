"""audit_agent_config: extend ck_projects_audit_action_valid with 'agent_config' (Kanban #2768)

Revision ID: 0075_audit_agent_config
Revises: 0074_ix_subagent_models_gin
Create Date: 2026-07-03 01:00 UTC

Reuses `projects_audit` (Kanban #1209/#1211's kill/revive/pause ledger) rather
than a new table — the per-agent-overrides change delta rides the existing
`drain_summary` JSONB column (a generic payload column despite its
kill-flavored name). Adds a 6th action value, `agent_config`, written by
PATCH /api/projects/{id}/agent-overrides whenever the request actually
changes an agent's enabled/tier/notes state (no-op PATCHes write no row).

Same DROP + ADD pattern as migration 0040 (CHECK constraints are immutable
in PG). No new columns, no data migration — existing kill/revive/pause/
unpause/pause_override rows are untouched.

Downgrade caveat (mirrors 0040's downgrade docstring): reversing the CHECK
to the 5-action set will FAIL at constraint-validation time if any row
currently carries action='agent_config' — the operator must archive or
delete those rows first (never via raw SQL DML from an agent; a human runs
psql for that per db-schema.md's "Hard DELETE is reserved for manual psql
cleanup" carve-out).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0075_audit_agent_config"
down_revision = "0074_ix_subagent_models_gin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CHECK constraints are immutable in PG — DROP + ADD is the only path
    # (mirrors migration 0040's EXTEND of this same constraint).
    op.drop_constraint(
        "ck_projects_audit_action_valid", "projects_audit", type_="check"
    )
    op.create_check_constraint(
        "ck_projects_audit_action_valid",
        "projects_audit",
        "action IN ('kill', 'revive', 'pause', 'unpause', 'pause_override', "
        "'agent_config')",
    )


def downgrade() -> None:
    # Reverts to the 5-action set. FAILS at constraint-validation time if any
    # row currently carries action='agent_config' — operator archives/deletes
    # those rows first (manual psql; never raw SQL DML from an agent).
    op.drop_constraint(
        "ck_projects_audit_action_valid", "projects_audit", type_="check"
    )
    op.create_check_constraint(
        "ck_projects_audit_action_valid",
        "projects_audit",
        "action IN ('kill', 'revive', 'pause', 'unpause', 'pause_override')",
    )
