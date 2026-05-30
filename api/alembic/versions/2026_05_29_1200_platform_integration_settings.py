"""platform_integration_settings — operator-toggled integration enable flags (Kanban #1655)

Revision ID: 0052_integration_settings
Revises: 0051_drop_projects_team_check
Create Date: 2026-05-29 12:00 UTC

NOTE: the revision id is kept <=32 chars — `alembic_version.version_num` is
VARCHAR(32). A longer id (e.g. `0052_platform_integration_settings`, 34 chars)
fails the version-stamp UPDATE with StringDataRightTruncationError.

One table backing the "Integrations" settings popup. It stores ONLY the
operator's per-integration enable/disable toggle — it NEVER stores secret
values. Secret presence + configured-ness is computed LIVE from os.environ at
request time (see services/integrations_registry.py + routers/settings.py).

  `platform_integration_settings`
    - `id` TEXT PRIMARY KEY — the integration's registry id (e.g. "llm_anthropic",
      "telegram"). Matches an entry in INTEGRATIONS_REGISTRY. A TEXT PK (not a
      surrogate BIGSERIAL) is deliberate: the row is a singleton-per-integration
      toggle keyed by a stable string id, and the upsert path (PATCH) keys on it
      directly. There is no second row per id, so a natural key is the simplest
      correct shape.
    - `enabled` BOOLEAN NOT NULL DEFAULT false — the toggle. Absent row ==
      disabled (the platform runs with zero keys by default — see #1655 Option A).
    - `updated_at` TIMESTAMPTZ NULL — stamped on each PATCH upsert.

No `created_at` — the row's existence is purely an operator action; the only
time worth tracking is "last toggled", which `updated_at` covers. No CHECK on
`id` (the registry is the source of truth; the router 404s an unknown id before
any write, so the DB never sees a non-registry id via the API).

No audit trigger (parity with sessions / push_subscriptions / credentials —
operator-CRUD metadata, not lifecycle-tracked work).

PG 16 — new table, no heap-rewrite concerns.

Downgrade: drops the table. Toggle state is lost, but since absent-row ==
disabled, a fresh upgrade simply starts every integration back at its default
OFF state — non-destructive to any actual secret (keys live in .env, untouched).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0052_integration_settings"
down_revision = "0051_drop_projects_team_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("platform_integration_settings")
