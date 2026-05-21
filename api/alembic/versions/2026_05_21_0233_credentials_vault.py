"""credentials vault — project_credentials + credential_access_log (Kanban #1326 M3)

Revision ID: 0048_credentials_vault
Revises: 0047_hitl_nudge
Create Date: 2026-05-21 02:33 UTC

Two new tables that together implement the per-project, Fernet-encrypted
credentials vault:

  `project_credentials` — one row per stored credential
    - `id` BIGSERIAL PK
    - `project_id` BIGINT NOT NULL FK projects(id) ON DELETE CASCADE — the
      vault is per-project so deleting a project cleans up its credentials
      atomically.
    - `name` TEXT NOT NULL — operator-supplied identifier (e.g. "openai_api",
      "stripe_test"). UNIQUE per `(project_id, name)`.
    - `kind` TEXT NOT NULL — gated vocabulary via CHECK
      `ck_project_credentials_kind_valid` IN ('api_key','oauth_token',
      'webhook_secret','app_password'). Drives a future UI hint and any
      kind-specific rotation logic (out of M3 scope).
    - `ciphertext` BYTEA NOT NULL — Fernet ciphertext of the plaintext value.
      Encryption + decryption use the env `CREDENTIALS_MASTER_KEY` (Fernet
      url-safe base64) — see services/credentials_crypto.py.
    - `metadata` JSONB NULL — free-form operator notes (e.g. last-rotated-at,
      external account id, scope description). NO DB CHECK on shape (mirrors
      `agent_overrides` / `tools_config` precedent).
    - `created_at` / `updated_at` TIMESTAMPTZ standard.
    - `last_accessed_at` TIMESTAMPTZ NULL — stamped by /use grants.
    - `access_count` INT NOT NULL DEFAULT 0 — incremented by /use grants.
    - `status` SMALLINT NOT NULL DEFAULT 1 CHECK IN (0, 1) — uniform soft-
      delete (mirrors push_subscriptions + tasks).

  `credential_access_log` — append-only audit ledger
    - `id` BIGSERIAL PK
    - `credential_id` BIGINT NOT NULL FK project_credentials(id) ON DELETE
      CASCADE — log rows are scoped to their credential; deleting a credential
      removes the trail (operator can pg_dump the table first if they want
      historical audit retention).
    - `accessed_by` TEXT NOT NULL — operator/agent identity string
      (e.g. "operator:api", "operator:api (denied=policy_unmatched)").
    - `task_id` BIGINT NULL FK tasks(id) ON DELETE SET NULL — when the access
      happened inside a task context. SET NULL because a task hard-delete
      shouldn't lose the credential-use trail.
    - `hitl_approval_id` BIGINT NULL — placeholder for the deferred HITL
      approval flow. No FK yet (no hitl_approvals table exists in M3); the
      column lands now so the future audit shape doesn't require a column-
      add migration.
    - `action` TEXT NOT NULL — gated vocabulary via CHECK
      `ck_credential_access_log_action_valid` IN ('use','create','update',
      'delete','view_metadata').
    - `accessed_at` TIMESTAMPTZ NOT NULL DEFAULT now().

Indexes:
  - `ux_project_credentials_project_name` — UNIQUE (project_id, name) so the
    same name can be reused across projects but never twice within one. Spans
    soft-deleted rows too: a deleted credential's slot stays held until a
    hard-delete (intentional — re-creating a same-name credential after
    delete is an explicit operator action, not auto-reuse).
  - `ix_project_credentials_project_id_last_accessed` — covers the usage-
    scan query (audit "find stale credentials per project").
  - `ix_credential_access_log_credential_id` — per-credential audit
    trail lookup.

No audit trigger on either table (parity with sessions / push_subscriptions /
transactions — operator-CRUD metadata, not lifecycle-tracked work). The
`credential_access_log` table IS the audit trail for this surface.

PG 16 — both tables new; no heap rewrite concerns.

Downgrade caveat:
  - Drops both tables; any stored credentials are gone (the operator has
    only the ciphertext column dump to recover from, and they'd need the
    master key + the ORM/migration restored to use it).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0048_credentials_vault"
down_revision = "0047_hitl_nudge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_credentials",
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
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
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
            nullable=True,
        ),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "access_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.CheckConstraint(
            "kind IN ('api_key', 'oauth_token', 'webhook_secret', 'app_password')",
            name="ck_project_credentials_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN (0, 1)",
            name="ck_project_credentials_status_valid",
        ),
    )

    # UNIQUE (project_id, name) — same name reusable across projects, never
    # twice within one. Spans soft-deleted rows too (see migration header).
    op.create_index(
        "ux_project_credentials_project_name",
        "project_credentials",
        ["project_id", "name"],
        unique=True,
    )
    # Usage-scan index — "list stale credentials per project, ordered by last
    # use" (admin/audit surface, not exercised by M3 routes but cheap now).
    op.create_index(
        "ix_project_credentials_project_id_last_accessed",
        "project_credentials",
        ["project_id", "last_accessed_at"],
    )

    op.create_table(
        "credential_access_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "credential_id",
            sa.BigInteger(),
            sa.ForeignKey("project_credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("accessed_by", sa.Text(), nullable=False),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # No FK on hitl_approval_id — no hitl_approvals table in M3. Column
        # lands now so the future audit-shape change is a backfill, not a
        # column-add migration.
        sa.Column(
            "hitl_approval_id",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "accessed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('use', 'create', 'update', 'delete', 'view_metadata')",
            name="ck_credential_access_log_action_valid",
        ),
    )

    op.create_index(
        "ix_credential_access_log_credential_id",
        "credential_access_log",
        ["credential_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_credential_access_log_credential_id",
        table_name="credential_access_log",
    )
    op.drop_table("credential_access_log")

    op.drop_index(
        "ix_project_credentials_project_id_last_accessed",
        table_name="project_credentials",
    )
    op.drop_index(
        "ux_project_credentials_project_name",
        table_name="project_credentials",
    )
    op.drop_table("project_credentials")
