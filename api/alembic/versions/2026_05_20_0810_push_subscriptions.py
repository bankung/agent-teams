"""push_subscriptions table — Web Push adapter for the #1224 router (Kanban #955.A)

Revision ID: 0046_push_subscriptions
Revises: 0045_handoff_templates
Create Date: 2026-05-20 08:10 UTC

Slice 955.A of Web Push notifications. Plugs into the existing #1224
notification router as a NEW adapter (notify_web_push.py); rows here store
per-browser subscription endpoints + VAPID keys that the adapter calls.
Slices 955.B (event hooks) + 955.C (FE service worker + permission + settings)
build on this foundation.

Schema (locked by Lead spawn brief):

  `push_subscriptions` table
    - `project_id` BIGINT NULLABLE FK projects(id) ON DELETE CASCADE — NULL
      means the subscription receives notifications for ALL projects. Filtering
      is the resolver's job in 955.B; THIS slice just exposes the column.
    - `endpoint` TEXT NOT NULL — the Push Service URL the browser hands the FE
      via PushSubscription.endpoint. UNIQUE across all rows (including soft-
      deleted) so the INSERT ... ON CONFLICT DO UPDATE pattern (D5) reuses
      slots. Re-subscribing a soft-deleted endpoint resurrects via UPDATE.
    - `p256dh` / `auth` TEXT NOT NULL — the public/secret pair from
      PushSubscription.keys; the Web Push library uses these to encrypt the
      payload.
    - `kinds_enabled` JSONB NOT NULL DEFAULT JSON object with 4 bool keys.
      Element shape validated at the API boundary by Pydantic `KindsEnabled`
      (`extra='forbid'` — typo'd keys 422). NO DB CHECK on shape (same
      precedent as `acceptance_criteria` / `agent_overrides` / `sources`).
    - `user_agent` TEXT NULLABLE — captured at subscribe-time so the operator
      can identify subscriptions in the settings UI.
    - `status` SMALLINT NOT NULL DEFAULT 1 CHECK IN (0, 1) — uniform soft-
      delete flag.
    - `created_at` / `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now() — standard.

Indexes:
  - `ux_push_subscriptions_endpoint` — UNIQUE on `endpoint`. Across all rows
    (no `WHERE status=1` predicate); this enables the
    `ON CONFLICT(endpoint) DO UPDATE` resubscribe pattern to flip a soft-
    deleted row back to active without colliding.
  - `ix_push_subscriptions_status` — keeps default-filter (`status=1`)
    selective.
  - `ix_push_subscriptions_project_id` — list filter by project.

NO audit trigger (mirrors `sessions` + `handoff_templates` precedent —
operator-CRUD metadata, not lifecycle-tracked work).

PG 16 new table — no heap rewrite concerns.

Downgrade caveat:
  - Drops the table; any registered subscriptions are lost. Operators
    re-subscribe by re-allowing browser permission (FE flow in 955.C).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0046_push_subscriptions"
down_revision = "0045_handoff_templates"
branch_labels = None
depends_on = None


_KINDS_ENABLED_DEFAULT = (
    '{"hitl_needed": true, "task_done": true, '
    '"task_failed": true, "budget_warn": true}'
)


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column(
            "kinds_enabled",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(f"'{_KINDS_ENABLED_DEFAULT}'::jsonb"),
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN (0, 1)", name="ck_push_subscriptions_status_valid"
        ),
    )

    # UNIQUE across ALL rows (no soft-delete predicate) so the ON CONFLICT
    # resubscribe pattern (D5) reuses the slot on a soft-deleted row.
    op.create_index(
        "ux_push_subscriptions_endpoint",
        "push_subscriptions",
        ["endpoint"],
        unique=True,
    )
    op.create_index(
        "ix_push_subscriptions_status",
        "push_subscriptions",
        ["status"],
    )
    op.create_index(
        "ix_push_subscriptions_project_id",
        "push_subscriptions",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_push_subscriptions_project_id", table_name="push_subscriptions"
    )
    op.drop_index(
        "ix_push_subscriptions_status", table_name="push_subscriptions"
    )
    op.drop_index(
        "ux_push_subscriptions_endpoint", table_name="push_subscriptions"
    )
    op.drop_table("push_subscriptions")
