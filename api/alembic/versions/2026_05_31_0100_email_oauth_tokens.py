"""email_oauth_tokens — durable encrypted store for email OAuth creds (Kanban #1604/#1608)

Revision ID: 0054_email_oauth_tokens
Revises: 0053_drop_integration_settings
Create Date: 2026-05-31 01:00 UTC

Durability layer for the email-tools token_store. Previously the store was a
process-local dict (`src/tools/email/token_store.py::_STORE`) so Gmail +
Outlook OAuth credentials were LOST on every api restart/reload. This is the
"later phase" the token_store docstring documented: an alembic table + Fernet
encryption via the existing `services/credentials_crypto.py` module.

  `email_oauth_tokens` — one row per (provider, project_id)
    - `provider` TEXT NOT NULL — 'gmail' | 'outlook', gated via CHECK
      `ck_email_oauth_tokens_provider_valid`. Mirrors the literal provider
      strings the email clients pass to token_store.put.
    - `project_id` BIGINT NOT NULL FK projects(id) ON DELETE CASCADE — creds
      are per-project; deleting a project removes its stored tokens atomically
      (mirrors project_credentials).
    - `encrypted_creds` BYTEA NOT NULL — Fernet ciphertext of the serialized
      creds (gmail: Credentials.to_json(); outlook: json.dumps(token_dict)).
      Same column type as `project_credentials.ciphertext` (sa.LargeBinary →
      BYTEA). The env `CREDENTIALS_MASTER_KEY` (Fernet url-safe base64) drives
      encrypt/decrypt — see services/credentials_crypto.py.
    - `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now() — stamped on each UPSERT
      (re-auth overwrites the existing row).

Primary key:
  - Composite PRIMARY KEY (provider, project_id) — the natural key the
    token_store has always used. Gives the UPSERT (`token_store.put`) a clean
    ON CONFLICT (provider, project_id) DO UPDATE target and prevents duplicate
    creds for the same provider+project.

No audit trigger (parity with sessions / push_subscriptions / credentials_vault
— operator-CRUD metadata, not lifecycle-tracked work). No JSONB metadata column
(the encrypted blob is the entire payload; status/email/expiry are projected at
read time by the client `creds_summary` helpers, not stored).

PG 16 — new table; no heap rewrite concerns.

Downgrade caveat:
  - Drops the table; any stored creds are gone. The operator re-runs the OAuth
    dance (POST /api/tools/email/auth/{gmail,outlook}/start) to repopulate —
    same recovery posture as the pre-durability in-memory store after a restart.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0054_email_oauth_tokens"
down_revision = "0053_drop_integration_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_oauth_tokens",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_creds", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "provider", "project_id", name="pk_email_oauth_tokens"
        ),
        sa.CheckConstraint(
            "provider IN ('gmail', 'outlook')",
            name="ck_email_oauth_tokens_provider_valid",
        ),
    )


def downgrade() -> None:
    op.drop_table("email_oauth_tokens")
