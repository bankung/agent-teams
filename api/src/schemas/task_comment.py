"""Pydantic schemas for the `task_comments` table (Kanban #1005).

An APPEND-ONLY comment thread per task. `TaskCommentCreate` is the POST body;
`TaskCommentRead` is the response row. There is intentionally NO TaskCommentUpdate
— the thread is append-only (AC#7): no PATCH, no DELETE on comments.

`author_kind` is gated by `CommentAuthorKindLiteral`, kept in lockstep with
`constants.CommentAuthorKind.ALL` by the guard at the module bottom (mirror of
the TaskRunModeLiteral / InteractionKindLiteral guards in schemas/task.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.constants import CommentAuthorKind

# Wire enum for task_comments.author_kind (#1005); lockstep guard at module bottom.
CommentAuthorKindLiteral = Literal["user", "agent", "system"]

# Payload-size caps (parity with the #1115 L18 hammer-test posture on tasks):
# bound attacker-controlled fluff at the API boundary. `body` is generous
# (markdown progress notes can be long); `author_label` is a short attribution.
_BODY_MAX = 20_000
_AUTHOR_LABEL_MAX = 200


class TaskCommentCreate(BaseModel):
    """Request body for POST /api/tasks/{id}/comments.

    `author_kind` is required (the discriminator). `author_label` is optional
    attribution. `body` is required (min_length=1 — empty comments are noise).
    `body_markdown` defaults to true (matches the DB DEFAULT).

    `extra='forbid'` rejects unknown keys at 422 (parity with the other
    request-body schemas in this codebase).
    """

    model_config = ConfigDict(extra="forbid")

    author_kind: CommentAuthorKindLiteral
    author_label: str | None = Field(default=None, min_length=1, max_length=_AUTHOR_LABEL_MAX)
    body: str = Field(min_length=1, max_length=_BODY_MAX)
    body_markdown: bool = True


class TaskCommentRead(BaseModel):
    """One comment row as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    author_kind: CommentAuthorKindLiteral
    author_label: str | None
    body: str
    body_markdown: bool
    created_at: datetime


# Sanity: the Literal stays in lockstep with constants.CommentAuthorKind.ALL.
# Use a real exception (not `assert`) so the guard survives `python -O`.
# Mirrors the TaskRunModeLiteral <-> TaskRunMode.ALL guard in schemas/task.py.
if set(CommentAuthorKindLiteral.__args__) != set(CommentAuthorKind.ALL):  # type: ignore[attr-defined]
    raise RuntimeError(
        f"CommentAuthorKindLiteral {CommentAuthorKindLiteral.__args__!r} drifted "  # type: ignore[attr-defined]
        f"from CommentAuthorKind.ALL {CommentAuthorKind.ALL!r}"
    )
