"""ProjectCredential + CredentialAccessLog ORM models (Kanban #1326 M3).

Mirrors migration `0048_credentials_vault`. The vault holds Fernet-encrypted
secrets per project; the access log is the append-only audit trail.

`ciphertext` is opaque bytes — encryption happens in
`services/credentials_crypto.py`, NOT here. The ORM never touches plaintext.

The DB column is named `metadata` but SQLAlchemy's DeclarativeBase reserves
the `metadata` attribute on Base (Table registry). We expose it as `meta`
on the Python side via `mapped_column("metadata", ...)` so the column name
on disk stays `metadata` while ORM access reads `cred.meta`.

Soft-delete via uniform `status` SMALLINT (0=deleted, 1=active). The
UNIQUE (project_id, name) index spans both states — a soft-deleted slot
holds the name until hard-delete. Routers default to filtering
`status=1` on list endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants import RecordStatus
from src.models.base import Base


# Vocabulary mirror — kept in lockstep with the CHECK in migration 0048 and
# the Pydantic Literal in schemas/credential.py.
CREDENTIAL_KINDS: tuple[str, ...] = (
    "api_key",
    "oauth_token",
    "webhook_secret",
    "app_password",
)

# Vocabulary for credential_access_log.action — mirrors the CHECK.
CREDENTIAL_ACCESS_ACTIONS: tuple[str, ...] = (
    "use",
    "create",
    "update",
    "delete",
    "view_metadata",
)


class ProjectCredential(Base):
    """One Fernet-encrypted credential row scoped to a project (Kanban #1326)."""

    __tablename__ = "project_credentials"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # Column-name on disk is `metadata`; Python attribute is `meta` because
    # DeclarativeBase reserves `metadata` (sa.MetaData registry collision).
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    access_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        default=0,
    )

    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("1"),
        default=RecordStatus.ACTIVE,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('api_key', 'oauth_token', 'webhook_secret', 'app_password')",
            name="ck_project_credentials_kind_valid",
        ),
        CheckConstraint(
            "status IN (0, 1)",
            name="ck_project_credentials_status_valid",
        ),
        Index(
            "ux_project_credentials_project_name",
            "project_id",
            "name",
            unique=True,
        ),
        Index(
            "ix_project_credentials_project_id_last_accessed",
            "project_id",
            "last_accessed_at",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProjectCredential id={self.id} project_id={self.project_id} "
            f"name={self.name!r} kind={self.kind!r} status={self.status}>"
        )


class CredentialAccessLog(Base):
    """Append-only audit row for every credential access (Kanban #1326)."""

    __tablename__ = "credential_access_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    credential_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("project_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )

    accessed_by: Mapped[str] = mapped_column(Text, nullable=False)

    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # No FK — no hitl_approvals table in M3. Reserved column for the deferred
    # HITL approval flow (operator can correlate via id externally for now).
    hitl_approval_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    action: Mapped[str] = mapped_column(Text, nullable=False)

    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('use', 'create', 'update', 'delete', 'view_metadata')",
            name="ck_credential_access_log_action_valid",
        ),
        Index(
            "ix_credential_access_log_credential_id",
            "credential_id",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CredentialAccessLog id={self.id} credential_id={self.credential_id} "
            f"action={self.action!r} accessed_by={self.accessed_by!r}>"
        )
