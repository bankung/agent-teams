"""tasks.health_alert + projects.health_thresholds (Kanban #960 — Health monitor)

Revision ID: 0031_health_monitor
Revises: 0030_tasks_audit_report
Create Date: 2026-05-17 09:00 UTC

Adds the two storage surfaces the periodic Health monitor sweep needs:

- `tasks.health_alert JSONB NULL` — populated when a detector fires for the
  task. Element shape (single object, latest-only):
    {detector, severity, evidence, alerted_at, threshold_used}
  Audit history across sweeps is recoverable from `tasks_history` via the
  existing audit trigger — single-column JSONB stays simplest (same precedent
  as `tasks.audit_report` #952). NULL = no current alert.

- `projects.health_thresholds JSONB NULL` — per-project tuning knobs that
  override the env-driven defaults. Element shape (validated at the API
  boundary):
    {enabled, stale_hours, max_retry_cycles, token_burn_threshold_per_hour,
     burn_spike_multiplier}
  NULL = use env defaults. `enabled=false` short-circuits the sweep for
  that project entirely. No DB CHECK on element shape (mirrors `config` /
  `agent_overrides` / `sources` / `acceptance_criteria` / `tools_config`
  precedent — JSONB element-shape validation lives at the API layer).

Both additive (NULL defaults), no data backfill needed. PG 16 ADD COLUMN
with no DEFAULT is metadata-only — no table-rewrite, safe on hot tables.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0031_health_monitor"
down_revision = "0030_tasks_audit_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "health_alert",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "health_thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "health_thresholds")
    op.drop_column("tasks", "health_alert")
