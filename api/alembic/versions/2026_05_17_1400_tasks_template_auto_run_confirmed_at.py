"""tasks: template_auto_run_confirmed_at (Kanban #1122 — L15 prevention)

Revision ID: 0036_tasks_template_confirmed_at
Revises: 0035_tasks_max_active_children
Create Date: 2026-05-17 14:00 UTC

L15 prevention layer: per-template auto-headless confirmation. Project-level
`auto_run_consent_at` (Kanban #481/#483) is a single switch that authorizes
ALL auto_headless work in a project. But a recurring template that runs
unattended every day at 03:00 is fundamentally riskier than a one-shot
auto_headless task — it's "set it and forget it" with no human-in-the-loop
confirmation per fire.

This migration adds a per-template tier: even with project consent granted,
EACH template that wants to fire children unattended must be explicitly
confirmed by a human. Column shape:

- `tasks.template_auto_run_confirmed_at TIMESTAMPTZ NULL` — only meaningful
  on rows with `is_template=true AND run_mode='auto_headless'`. NULL = not
  yet confirmed; the scheduler refuses to spawn children from this template.
  Non-null = a human POSTed /api/tasks/{id}/confirm-template-auto-run.

No DB CHECK constraint this slice. The cross-column rule
("auto_headless AND is_template REQUIRES non-null confirm") is enforced at
the Pydantic + service layer, mirroring the existing `run_mode='auto_headless'
requires project.auto_run_consent_at` pattern in `services/run_mode.py`
(also app-layer-only, no DB CHECK — span tables / spans columns).

No data backfill needed — column is additive, NULL default applies to every
existing row. The migration plays cleanly even with pre-existing
auto_headless templates: those templates simply won't fire children after
this migration lands until an operator POSTs the confirm endpoint. That's
the intentional gate (the whole point of L15).

Sibling layers:
- L14 (#1121): content moderation flag on templates — flag risky templates
  before they spawn children.
- L18 (#1115): payload-size caps on description / acceptance_criteria /
  subagent_models — bounds per-row growth.
- L21 (#1125): per-template max_active_children cap — bounds concurrent
  children spawned by a single template.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0036_tasks_template_confirmed_at"
down_revision = "0035_tasks_max_active_children"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "template_auto_run_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "template_auto_run_confirmed_at")
