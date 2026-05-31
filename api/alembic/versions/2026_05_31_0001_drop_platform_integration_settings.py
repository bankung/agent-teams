"""drop platform_integration_settings — toggle removed; popup is read-only

Revision ID: 0053_drop_integration_settings
Revises: 0052_integration_settings
Create Date: 2026-05-31 00:01 UTC

The enable/disable toggle for integrations has been removed from the API.
The popup is now read-only, with runtime control via .env. The DB table
`platform_integration_settings` is no longer used by any code path.

Upgrade: drops the table.
Downgrade: recreates the table with the original column definitions from
  migration `0052_integration_settings` (id TEXT PK, enabled BOOLEAN NOT NULL
  DEFAULT false, updated_at TIMESTAMPTZ NULL). Toggle state is NOT restored on
  downgrade — absent row == disabled was always the default, so every
  integration simply reverts to its off state, which is non-destructive.

NOTE: revision id kept <=32 chars — `alembic_version.version_num` is
VARCHAR(32). See migration 0052 for background.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0053_drop_integration_settings"
down_revision = "0052_integration_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("platform_integration_settings")


def downgrade() -> None:
    op.create_table(
        "platform_integration_settings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
