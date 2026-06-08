"""task_comments table — append-only comment thread per task (Kanban #1005)

Revision ID: 0062_task_comments
Revises: 0061_tasks_is_active
Create Date: 2026-06-08 01:00 UTC

A first-class APPEND-ONLY comment thread attached to a task. Any actor (a human
operator, a specialist subagent recording a progress note, or an automated
system event) appends a comment; rows are NEVER edited or deleted via the API
(AC#7). This migration is SCHEMA + ORM + Pydantic + endpoints (#1005 backend
foundation) — the FE is a separate dev-sr-frontend spawn. No existing code
queries `task_comments`, so the live API stays healthy with the table absent
until this migration applies.

DESIGN (locked spec, #1005):

1. `task_comments` table — the append-only thread entity.
   - `id` BIGSERIAL PK — monotonic with insertion order, so id-ordering IS
     chronological order. Used as the pagination cursor (`?before=<id>`) — no
     created_at-timestamp tiebreaker needed (two comments in the same ms still
     order deterministically by id).
   - `task_id` BIGINT NOT NULL, FK ON DELETE CASCADE — a comment dies with its
     task. Mirrors `tasks` / `project_resources.project_id` cascade posture; the
     thread is meaningless once the task is gone.
   - `author_kind` TEXT NOT NULL + CHECK IN ('user','agent','system') — the
     discriminator (constants.CommentAuthorKind). WHO appended the comment.
   - `author_label` TEXT NULLABLE — optional human-readable attribution
     (e.g. 'dev-backend', 'Lead', the operator's name). NULL when unattributed.
   - `body` TEXT NOT NULL — the comment text (markdown or plain per body_markdown).
   - `body_markdown` BOOLEAN NOT NULL DEFAULT true — whether `body` is markdown
     (true) or plain text (false). Lets the FE pick a renderer per-comment.
   - `created_at` TIMESTAMPTZ NOT NULL DEFAULT now() — append timestamp.

   NO `updated_at` / NO soft-delete `status` column — the thread is append-only
   (AC#7): there is no edit path and no delete path, so neither column would ever
   change. Omitting them keeps the surface honest (a `status` column would imply
   a soft-delete capability the API does not expose).

   CHECK `ck_task_comments_author_kind_valid` gates the discriminator value —
   mirror of constants.CommentAuthorKind.ALL. Defense-in-depth against raw-SQL
   writes; the Pydantic Literal fires the friendlier 422 first at the API layer.

2. Index `ix_task_comments_task_id_created_at` ON (task_id, created_at) (AC#2) —
   serves the chronological fetch `WHERE task_id=$1 ORDER BY created_at` (or, in
   practice, ORDER BY id — same order) directly: leading equality on task_id,
   then the ordered scan. Composite so the thread for one task is a single
   contiguous index range.

History capture: NO audit trigger on `task_comments` (mirrors `milestones` /
`project_resources` / `task_templates` precedent — operator/agent metadata, and
here ALREADY append-only, so there is no update/delete history to capture).

Downgrade caveat: dropping `task_comments` deletes every comment thread. No
restore path; the feature is additive (no existing data depends on it).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0062_task_comments"
down_revision = "0061_tasks_is_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_comments",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        # ON DELETE CASCADE — comment dies with its task (AC: deleting the task
        # cascades the thread away; that's the only removal path).
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_kind", sa.Text(), nullable=False),
        sa.Column("author_label", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "body_markdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Discriminator enum gate — mirror of constants.CommentAuthorKind.ALL.
        sa.CheckConstraint(
            "author_kind IN ('user', 'agent', 'system')",
            name="ck_task_comments_author_kind_valid",
        ),
    )
    # AC#2: composite (task_id, id) index — aligns with ORDER BY id ASC cursor pagination.
    op.create_index(
        "ix_task_comments_task_id_id",
        "task_comments",
        ["task_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_comments_task_id_id", table_name="task_comments"
    )
    op.drop_table("task_comments")
