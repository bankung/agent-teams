"""tasks.scheduled_at — one-shot scheduling path (T1 audit follow-up)

Revision ID: 0010_tasks_scheduled_at
Revises: 0009_session_ceilings_extension
Create Date: 2026-05-10 17:00 UTC

Audit follow-up to T1 #706 (Kanban #723). T1 covered the recurring path
(template + cron + next_fire_at). User Req 1 — "ระบุวัน+เวลาที่จะทำ task
นี้ได้ด้วย" — also requires a one-shot fire path: run once at a future
moment, NOT a recurring template.

Per the 2026-05-10 audit lock: one-shot uses a regular task
(`is_template=false`) with a new `scheduled_at` column. The T2 scheduler
(#707) scans both:
  - templates with `next_fire_at <= now()` → spawn child rows
  - tasks with `scheduled_at <= now() AND process_status=1 AND is_template=false`
    → transition to ps=2 (in_progress), stamp `started_at`, clear scheduled_at

The two paths are mutually exclusive at the row level — enforced by
`ck_tasks_scheduled_xor_template`. Mixing both is undefined behavior;
DB CHECK rejects it as a defense-in-depth backstop alongside the Pydantic
model_validator on TaskCreate / TaskUpdate.

Schema-additive: existing 53+ rows backfill `scheduled_at = NULL`. The
partial index `ix_tasks_scheduled_at_pending` is the scheduler hot-path —
filters by `scheduled_at IS NOT NULL AND process_status = 1 AND status = 1`
so the index stays sparse (only the active Todo rows with a pending fire).
NOT unique — multiple tasks can share a `scheduled_at` instant.

Down (reverse order): drop CHECK → drop partial index → drop column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_tasks_scheduled_at"
down_revision = "0009_session_ceilings_extension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "scheduled_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )

    # Hot-path partial index — scheduler scans active Todo rows whose
    # scheduled_at has arrived. Predicate keeps it sparse.
    op.create_index(
        "ix_tasks_scheduled_at_pending",
        "tasks",
        ["scheduled_at"],
        postgresql_where=sa.text(
            "scheduled_at IS NOT NULL AND process_status = 1 AND status = 1"
        ),
    )

    # Cross-column CHECK: scheduled_at and is_template are mutually
    # exclusive. Template uses recurrence_rule + next_fire_at (T1 path);
    # one-shot uses scheduled_at. DB defense-in-depth alongside Pydantic.
    op.create_check_constraint(
        "ck_tasks_scheduled_xor_template",
        "tasks",
        "NOT (scheduled_at IS NOT NULL AND is_template = TRUE)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tasks_scheduled_xor_template", "tasks", type_="check"
    )
    op.drop_index("ix_tasks_scheduled_at_pending", table_name="tasks")
    op.drop_column("tasks", "scheduled_at")
