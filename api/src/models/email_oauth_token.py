"""EmailOAuthToken ORM model — durable, Fernet-encrypted email OAuth creds.

Backs the email-tools token_store (Kanban #1604 Gmail / #1608 Outlook). The
prior implementation kept creds in a process-local dict (`_STORE`) so Gmail +
Outlook OAuth credentials were LOST on every api restart/reload. This table is
the durability layer the token_store docstring promised: "alembic table +
Fernet encryption using the existing credentials_crypto module".

One row per (provider, project_id):
  - `provider`   TEXT — 'gmail' | 'outlook' (CHECK-gated, mirrors the values
    the email clients pass to token_store.put).
  - `project_id` BIGINT FK projects(id) ON DELETE CASCADE — creds are
    per-project; deleting a project removes its stored tokens atomically.
  - `encrypted_creds` BYTEA — Fernet ciphertext of the serialized creds
    (gmail: Credentials.to_json(); outlook: json.dumps(token_dict)).
    Encryption happens in services/credentials_crypto.py; the ORM never
    touches plaintext. Matches `project_credentials.ciphertext` column type
    (LargeBinary / BYTEA).
  - `updated_at` TIMESTAMPTZ — stamped on every UPSERT (re-auth overwrites).

PRIMARY KEY (provider, project_id) — a composite natural key gives us a clean
ON CONFLICT target for the UPSERT and matches the (provider, project_id) tuple
the token_store has always keyed on.

Mirrors migration `0054_email_oauth_tokens`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base

# Provider vocabulary — kept in lockstep with the CHECK in migration 0054 and
# the literal provider strings the email clients pass to token_store.put.
EMAIL_OAUTH_PROVIDERS: tuple[str, ...] = ("gmail", "outlook")


class EmailOAuthToken(Base):
    """One Fernet-encrypted email OAuth credential row per (provider, project)."""

    __tablename__ = "email_oauth_tokens"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Fernet ciphertext of the serialized creds. Opaque bytes — encrypt/decrypt
    # lives in services/credentials_crypto.py. Same type as
    # project_credentials.ciphertext (LargeBinary -> BYTEA).
    encrypted_creds: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('gmail', 'outlook')",
            name="ck_email_oauth_tokens_provider_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EmailOAuthToken provider={self.provider!r} "
            f"project_id={self.project_id} updated_at={self.updated_at}>"
        )
