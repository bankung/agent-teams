"""telegram_chat_state: per-chat sticky project + update_id dedup watermark (Kanban #2778)

Revision ID: 0076_telegram_chat_state
Revises: 0075_audit_agent_config
Create Date: 2026-07-03 02:00 UTC

Phase 1 of the Telegram command surface (`telegram-command-surface-2720.md`
D1/D2/D4). One row per Telegram chat:

  - chat_id (PK)       : Telegram's numeric chat id, stored as TEXT (can
                          exceed int32/int64-signed range on some Telegram
                          ids; TEXT avoids a silent overflow — mirrors the
                          poller's `str(chat_id)` posture in
                          telegram_poller.py / notify_telegram.py).
  - project_id (FK)     : the D2 "sticky project" set via `/project <name>`.
                          NULLable — a chat with no sticky project yet must
                          reject read-verb commands (AC2), not 500.
  - last_update_id      : the D4/AC4 dedup watermark — the highest Telegram
                          `update_id` processed for this chat. A repeat
                          update_id <= this value is a no-op (§ "runs BEFORE
                          dispatch").
  - created_at / updated_at.

No CHECK constraints needed (project_id FK already enforces referential
integrity; last_update_id has no enum shape). Mirrors push_subscriptions'
minimal-column style (migration 0046) rather than task_gates' enum-heavy
shape — this table carries no enum column.

BUILD-ONLY (per the #2778 spawn brief): NOT applied by this spawn. The Lead
gates the `alembic upgrade` behind a review pass. `api/src/models/telegram_chat_state.py`
imports cleanly pre-apply (plain ORM class); only queries against the live
DB will fail with UndefinedTable until this migration is run.

Downgrade drops the table (fully reversible; no data-migration concern for a
brand-new phase-1 table).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0076_telegram_chat_state"
down_revision = "0075_audit_agent_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_chat_state",
        # Telegram chat id, stored as TEXT (mirrors notify_telegram's
        # `str(chat_id)` — some Telegram ids exceed safe int ranges).
        sa.Column("chat_id", sa.Text(), primary_key=True),
        # The D2 sticky project. NULL until `/project <name>` is run.
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # AC4 dedup watermark — highest Telegram update_id processed for this
        # chat. NULL before the first command.
        sa.Column("last_update_id", sa.BigInteger(), nullable=True),
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
    )
    # project_id lookups (e.g. an admin "which chats target project X" query)
    # are rare but cheap to index given the tiny row count; kept for parity
    # with push_subscriptions' ix_..._project_id.
    op.create_index(
        "ix_telegram_chat_state_project_id",
        "telegram_chat_state",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_chat_state_project_id", table_name="telegram_chat_state")
    op.drop_table("telegram_chat_state")
