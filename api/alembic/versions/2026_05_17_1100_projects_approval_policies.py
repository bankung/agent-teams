"""projects: per-project HITL approval policies (Kanban #957 Phase 1)

Revision ID: 0033_projects_approval_policies
Revises: 0032_transactions_ledger
Create Date: 2026-05-17 11:00 UTC

Adds `projects.approval_policies JSONB NULL` — per-project rules that match
against pending HITL prompts (langgraph `request_user_input` payloads) and
decide one of three actions:

  - auto_approve  → worker resumes with default answer (no operator)
  - auto_deny     → worker halts with halt_reason='operator_rejected'
  - (no match)    → REQUIRE_ATTENTION (current HITL pause behavior)

Mirrors the per-project JSONB-knob precedent (`agent_overrides`, `tools_config`,
`health_thresholds`): nullable, no DB CHECK on shape (service-layer validates
at the API boundary). NULL = no policies = every HITL prompt requires
attention (preserves pre-#957 behavior for every existing project).

JSONB element shape (validated by the API layer, not the DB):

    {
      "rules": [
        {
          "name": "auto-approve small llm spend",
          "match": {
            "text_contains": "spend",
            "amount_usd_lt": 5.0
          },
          "action": "auto_approve",
          "default_answer": "accept"
        }
      ]
    }

Rules evaluated in order; first match wins. Match predicates ANDed within a
rule; rules ORed across the list. Phase 1 minimum predicates:
text_contains / text_contains_all / text_contains_any / amount_usd_lt /
amount_usd_gt / options_include. Default action on no match:
REQUIRE_ATTENTION.

Audit trail (Phase 1 minimal): the matched rule's name + action lands in
`task.status_change_reason` on the worker's PATCH; the existing
`tasks_history` audit trigger captures the row state. No new audit column
this slice. Richer per-policy audit log deferred.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0033_projects_approval_policies"
down_revision = "0032_transactions_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("approval_policies", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "approval_policies")
