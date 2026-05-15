"""ToolCall ORM model — specialist-tool audit row (Kanban #980).

One row per specialist-tool invocation issued by the LangGraph specialist
nodes. Mirrors `tool_calls` migration `0028_tool_calls`. See that
migration's docstring for the schema rationale; this module is the SQLAlchemy
surface only.

NO soft-delete column (audit append-only — `tasks_history` precedent).
NO audit trigger on this table (it IS the audit log).
Project ownership is derived through `task_id -> tasks.project_id`;
the GET endpoint enforces X-Project-Id at the task level.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)  # noqa: F401  (Integer kept on the import list for parity with sibling models)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.task import Task


class ToolCall(Base):
    """One specialist-tool invocation audit row.

    Append-only. Every column except `error_code`, `error_msg`, and
    `output_summary` is NOT NULL — the writer service guarantees the
    contract. Clients cannot create/edit audit rows; the only public
    surface is `GET /api/tasks/{task_id}/tool-calls`.
    """

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    # BIGINT to match `tasks.id` (also BIGINT identity). Deviation from the
    # #980 spawn brief documented in dev-sr-backend/current-state.md.
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    invoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    # tier / permission_decision: free-form text (no CHECK). The langgraph
    # container is the source of truth; the audit log should not 23514 on a
    # tier or verdict that hasn't been added to a DB-side enum yet.
    tier: Mapped[str] = mapped_column(Text, nullable=False)

    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # error_msg truncated to 1 KB by the writer service (#949 Q10 lock).
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    # output_summary: first 256 chars of ToolResult.output, raw byte cut
    # (#949 Q10 → A). NULL when output is None.
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    permission_decision: Mapped[str] = mapped_column(Text, nullable=False)

    # Convenience relationship for service-layer reads (NOT required for the
    # endpoint, which fetches by task_id directly). Not back-populated on
    # Task — audit rows don't fan out from the task listing.
    task: Mapped["Task"] = relationship("Task")

    __table_args__ = (
        # Composite index — matches the GET endpoint's WHERE + ORDER BY.
        Index(
            "ix_tool_calls_task_id_invoked_at",
            "task_id",
            text("invoked_at DESC"),
        ),
        Index("ix_tool_calls_invoked_at", "invoked_at"),
        Index("ix_tool_calls_tool_name", "tool_name"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ToolCall id={self.id} task_id={self.task_id} "
            f"tool={self.tool_name!r} success={self.success} "
            f"decision={self.permission_decision!r}>"
        )
