"""PushSubscription ORM model — Web Push adapter rows (Kanban #955.A).

Mirrors migration `0046_push_subscriptions`. One row per browser endpoint;
the Web Push adapter (`api/src/services/notify_web_push.py`) looks up rows
by id (string-cast from NotificationTarget.chat_id) and POSTs the encrypted
payload to `endpoint` using `p256dh` + `auth`.

`project_id` NULL means the subscription receives notifications for ALL
projects — the resolver in 955.B's event hooks does the matching.

`kinds_enabled` JSONB is validated at the API boundary by
`schemas/push_subscription.py::KindsEnabled` (Pydantic `extra='forbid'`).
The DB column has no shape CHECK (parity with `acceptance_criteria` /
`agent_overrides` / `sources`).

Soft-delete via uniform `status` SMALLINT (0=deleted, 1=active). Endpoint
UNIQUE index spans BOTH states so resubscribing a soft-deleted row resurrects
the existing slot rather than colliding (D5 — ON CONFLICT DO UPDATE pattern
in the POST handler).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.constants import RecordStatus
from src.models.base import Base


# kinds_enabled default — kept in lockstep with Pydantic `KindsEnabled`.
# The DB server_default in migration 0046_push_subscriptions omits
# `task_halted`; the resolver treats absent keys as False (correct opt-in
# behaviour for Kanban #1841) so no migration is required for that key.
_KINDS_ENABLED_DEFAULT: dict[str, bool] = {
    "hitl_needed": True,
    "task_done": True,
    "task_failed": True,
    "budget_warn": True,
    "task_halted": False,  # Kanban #1841 — OPT-IN, default False
}


class PushSubscription(Base):
    """One Web Push subscription endpoint registered by an operator's browser.

    `project_id` NULL → receive notifications for all projects (the resolver
    in 955.B's event hooks handles the matching).
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    project_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )

    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)

    kinds_enabled: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            "'{\"hitl_needed\": true, \"task_done\": true, "
            "\"task_failed\": true, \"budget_warn\": true}'::jsonb"
        ),
        default=lambda: dict(_KINDS_ENABLED_DEFAULT),
    )

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default="1",
        default=RecordStatus.ACTIVE,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN (0, 1)", name="ck_push_subscriptions_status_valid"
        ),
        # UNIQUE across ALL rows (no soft-delete predicate) — supports the
        # POST handler's ON CONFLICT(endpoint) DO UPDATE resurrect pattern.
        Index(
            "ux_push_subscriptions_endpoint", "endpoint", unique=True
        ),
        Index("ix_push_subscriptions_status", "status"),
        Index("ix_push_subscriptions_project_id", "project_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PushSubscription id={self.id} project_id={self.project_id} "
            f"status={self.status}>"
        )
