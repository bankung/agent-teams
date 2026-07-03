"""TelegramChatState ORM model — per-chat sticky project + dedup watermark
(Kanban #2778, Phase 1 of `telegram-command-surface-2720.md`).

Mirrors migration `0076_telegram_chat_state`. One row per Telegram chat that
has ever run a command:

  - chat_id        : PK, TEXT (mirrors notify_telegram's str(chat_id) posture).
  - project_id      : D2 sticky project, set via `/project <name>`. NULL until set.
  - last_update_id  : D4/AC4 dedup watermark (highest processed update_id).

D1: this is the ONLY state the Telegram command surface persists — no
parse/authz logic lives here (that's routers/telegram_command.py). D2: do NOT
confuse this with `_runtime/lead_project_id.txt` (a session-write signal the
poller follows for gate-notify routing) — this table is the command-TARGETING
mechanism, keyed by chat, never read from or written to that file.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TelegramChatState(Base):
    """Per-chat sticky project + update_id dedup watermark."""

    __tablename__ = "telegram_chat_state"

    chat_id: Mapped[str] = mapped_column(Text, primary_key=True)

    project_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    last_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

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
        Index("ix_telegram_chat_state_project_id", "project_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TelegramChatState chat_id={self.chat_id!r} "
            f"project_id={self.project_id} last_update_id={self.last_update_id}>"
        )
