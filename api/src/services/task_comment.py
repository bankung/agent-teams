"""Append-a-comment helper for the task comment thread (Kanban #1005, AC#4).

A thin convenience so callers (the router, or any in-process service that wants
to drop a progress / system note onto a task) can append a comment without
re-stating the INSERT each time. APPEND-ONLY: there is no update/delete helper
by design (AC#7).

The helper does NOT validate that the task exists / belongs to a project — that
is the ROUTER's job (get_or_404 + assert_task_belongs_to_session) at the API
boundary. In-process system callers are trusted to pass a real task_id; a bad
FK surfaces as an IntegrityError on flush, which the caller handles.

Does NOT commit — the caller owns the transaction boundary (so a comment can be
written atomically alongside other work, e.g. a status flip + a 'system' note).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.task_comment import TaskComment


async def post_task_comment(
    session: AsyncSession,
    task_id: int,
    author_kind: str,
    body: str,
    *,
    author_label: str | None = None,
    body_markdown: bool = True,
) -> TaskComment:
    """Append a comment to a task's thread and flush (no commit).

    Args:
        session:       the active AsyncSession (caller owns commit/rollback).
        task_id:       the task whose thread to append to.
        author_kind:   'user' / 'agent' / 'system' (CommentAuthorKind). The
                       router gates this via the Pydantic Literal; in-process
                       callers pass a constant.
        body:          the comment text (non-empty).
        author_label:  optional human-readable attribution (e.g. 'dev-backend').
        body_markdown: whether `body` is markdown (default) or plain text.

    Returns the persisted TaskComment (id assigned via flush).
    """
    comment = TaskComment(
        task_id=task_id,
        author_kind=author_kind,
        author_label=author_label,
        body=body,
        body_markdown=body_markdown,
    )
    session.add(comment)
    await session.flush()  # assigns comment.id without committing
    return comment
