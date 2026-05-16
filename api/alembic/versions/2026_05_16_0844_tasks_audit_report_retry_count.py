"""tasks: audit_report + audit_retry_count (Kanban #952 — Auditor agent)

Revision ID: 0030_tasks_audit_report
Revises: 0029_projects_hitl_timeout
Create Date: 2026-05-16 08:44 UTC

Adds the storage surfaces the in-graph auditor node writes on every audit pass:

- `tasks.audit_report JSONB NULL` — the LATEST audit's structured outcome:
  `{verdict, severity, evidence, action_taken, escalation_payload, llm_skipped,
    audited_at, retry_count_at_audit}`. Element-shape validation lives in the
  API boundary (TaskRead exposes it raw; no Pydantic write-side this slice).
  Audit history across retries is recoverable from `tasks_history` via the
  existing audit trigger — single-column JSONB stays simplest. Q5=A locked.

- `tasks.audit_retry_count INTEGER NOT NULL DEFAULT 0` — number of AUTO-RESOLVE
  retries the auditor has applied to this task. Hardcoded cap
  `AUDITOR_RETRY_CAP_DEFAULT=3` lives in `langgraph/nodes.py`; per-project
  tuning column deferred (sibling task). CHECK `>= 0` is defense-in-depth
  against raw-SQL drift — mirrors `ck_tasks_estimated_input_tokens_nonneg`
  precedent (Kanban #944). Q6=A locked.

No data backfill needed — both columns are additive (NULL / 0 defaults).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0030_tasks_audit_report"
down_revision = "0029_projects_hitl_timeout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "audit_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "audit_retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_tasks_audit_retry_count_nonneg",
        "tasks",
        "audit_retry_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tasks_audit_retry_count_nonneg", "tasks", type_="check"
    )
    op.drop_column("tasks", "audit_retry_count")
    op.drop_column("tasks", "audit_report")
