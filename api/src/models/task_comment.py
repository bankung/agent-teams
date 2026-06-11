"""TaskComment ORM model (Kanban #1005).

An APPEND-ONLY comment in a task's thread: who appended it (`author_kind` +
optional `author_label`), the `body` (markdown or plain per `body_markdown`),
and the `created_at` timestamp. Mirrors migration `0062_task_comments`.

APPEND-ONLY (AC#7): there is NO edit path and NO delete path in the API. The
table therefore has NO `updated_at` and NO soft-delete `status` column — a row,
once written, never changes. The only removal is the FK ON DELETE CASCADE when
the parent task is hard-deleted.

ENUM VALIDATION: `author_kind` carries a DB CHECK (mirror of migration 0062 via
`in_clause_text`) so ORM autogenerate stays in lockstep with the live DDL, AND
the Pydantic `CommentAuthorKindLiteral` gates the value at the API boundary (the
friendlier 422). Both derive from `constants.CommentAuthorKind.ALL`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.constants import CommentAuthorKind, in_clause_text
from src.models.base import Base


class TaskComment(Base):
    """One append-only comment in a task's thread (Kanban #1005).

    `author_kind` (TEXT + CHECK) discriminates WHO appended the comment —
    'user' / 'agent' / 'system'. `author_label` is an optional human-readable
    attribution (e.g. 'dev-backend', 'Lead'). `body` is the comment text;
    `body_markdown` flags whether it is markdown (default) or plain text.
    """

    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ON DELETE CASCADE — the comment dies with its task (the ONLY removal path;
    # the API exposes no edit/delete on comments per AC#7).
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # App-validated against CommentAuthorKind.ALL via the Pydantic Literal AND
    # backed by the DB CHECK below (mirror of migration 0062).
    author_kind: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional human-readable attribution — NULL when unattributed.
    author_label: Mapped[str | None] = mapped_column(Text, nullable=True)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    body_markdown: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Discriminator enum gate — mirror of migration 0062's CHECK predicate.
        CheckConstraint(
            in_clause_text("author_kind", CommentAuthorKind.ALL),
            name="ck_task_comments_author_kind_valid",
        ),
        # AC#2: composite (task_id, id) index — aligns with ORDER BY id ASC
        # cursor pagination. Mirror of migration 0062's index.
        Index(
            "ix_task_comments_task_id_id",
            "task_id",
            "id",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskComment id={self.id} task_id={self.task_id} "
            f"author_kind={self.author_kind!r}>"
        )
