"""projects: per-project auto-decision policy DSL (Kanban #1840)

Revision ID: 0070_proj_auto_decision_policy
Revises: 0069_tasks_halted_pending_user
Create Date: 2026-06-16 02:00 UTC

Adds `projects.auto_decision_policy JSONB NULL` — a declarative, per-project
override for the full-auto Lead's top-5 decision matrix (the hardcoded MVP-4
rules in context/teams/dev/full-auto.md). The full-auto Lead reads this column
per project and falls back to the hardcoded matrix when it is unset.

The codified knobs (each field optional — a partial policy overrides only the
rules it names; absent fields keep the matrix default):

  - reviewer_warn          : {fold_max_loc, fold_requires_no_contract_change}
                             (WARN -> FOLD when fix <= fold_max_loc LOC AND no
                             contract/shared-doc change; else FILE FOLLOW-UP)
  - reviewer_nit           : 'defer' | 'fold'   (matrix default: always DEFER)
  - tester_standards_proposal : 'log_only' | 'halt'  (matrix: LOG only — the
                             humans-only context/standards/ invariant means
                             'log_only' is the only safe auto-action)
  - validator_ambiguity    : 'halt'             (matrix default: HALT on A/B)
  - scope_creep            : 'halt'             (matrix default: HALT)

Mirrors the per-project JSONB-knob precedent EXACTLY — most directly
`approval_policies` (migration 0033): nullable, NO server_default, NO DB CHECK
on shape (element-shape validation lives at the API boundary via the typed
`AutoDecisionPolicy` Pydantic model in schemas/project.py). NULL = no policy =
the full-auto Lead falls back to the hardcoded matrix (preserves current
behavior for every existing project).

PG 16 treats `ADD COLUMN ... JSONB NULL` (nullable, no default literal) as a
metadata-only op — no heap rewrite, instant on existing rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0070_proj_auto_decision_policy"
down_revision = "0069_tasks_halted_pending_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "auto_decision_policy",
            JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "auto_decision_policy")
