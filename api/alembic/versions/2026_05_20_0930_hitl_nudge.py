"""hitl_nudge columns — HITL aging nudge cron (Kanban #1011)

Revision ID: 0047_hitl_nudge
Revises: 0046_push_subscriptions
Create Date: 2026-05-20 09:30 UTC

Three metadata-only ADD COLUMN statements (PG 16 — no heap rewrite):

  projects.hitl_nudge_threshold_hours  INT NULL DEFAULT 24
    NULL  = nudges disabled for this project (per-project off switch).
    0     = nudges disabled (equivalent to NULL; treated as off by the cron).
    > 0   = threshold in hours (fire nudge when task age exceeds this value).
    DB CHECK ck_projects_hitl_nudge_threshold_nonneg enforces >= 0 when non-null
    (raw-SQL defense-in-depth; Pydantic ge=0 is the first wall).
    Backfill: migration UPDATE sets all existing rows to 24 so the default is
    enabled-at-24h for live projects (the operator can disable per-project via
    PATCH hitl_nudge_threshold_hours=null).

  tasks.last_nudge_at  TIMESTAMPTZ NULL
    Dedup column. The cron sets this to now() after every nudge attempt
    (regardless of push delivery success). The query predicate
    "last_nudge_at IS NULL OR last_nudge_at < now() - interval '24 hours'"
    prevents re-nudging within 24h. No default; new tasks land NULL.

  tasks.nudge_disabled  BOOLEAN NOT NULL DEFAULT false
    Per-task on/off toggle. Set true by operator (PATCH /api/tasks/{id})
    to silence nudges for one specific task even when the project default
    would otherwise fire.

Both task columns are PG 16 metadata-only ADD COLUMN — nullable / false
default means no heap rewrite even on a large table.

Downgrade reversal: DROP COLUMN on all three (no data preservation needed).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0047_hitl_nudge"
down_revision = "0046_push_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- projects.hitl_nudge_threshold_hours ---
    op.add_column(
        "projects",
        sa.Column(
            "hitl_nudge_threshold_hours",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("24"),
        ),
    )
    op.create_check_constraint(
        "ck_projects_hitl_nudge_threshold_nonneg",
        "projects",
        "hitl_nudge_threshold_hours IS NULL OR hitl_nudge_threshold_hours >= 0",
    )

    # --- tasks.last_nudge_at ---
    op.add_column(
        "tasks",
        sa.Column(
            "last_nudge_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # --- tasks.nudge_disabled ---
    op.add_column(
        "tasks",
        sa.Column(
            "nudge_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "nudge_disabled")
    op.drop_column("tasks", "last_nudge_at")
    op.drop_constraint(
        "ck_projects_hitl_nudge_threshold_nonneg", "projects", type_="check"
    )
    op.drop_column("projects", "hitl_nudge_threshold_hours")
