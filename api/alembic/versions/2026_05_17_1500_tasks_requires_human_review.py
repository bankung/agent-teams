"""tasks: requires_human_review flag (Kanban #1121 — L14 prevention)

Revision ID: 0037_tasks_requires_human_review
Revises: 0036_tasks_template_confirmed_at
Create Date: 2026-05-17 15:00 UTC

L14 prevention layer for the red-team Phase 7 sleeper-attack chain. API
previously had ZERO content moderation on task fields: a body containing
`TRUNCATE tasks_history` or `DROP TABLE projects` looked indistinguishable
from a benign feature task. Combined with auto-headless `run_mode` + a stale
recurrence template, that's the end-to-end sleeper destruction path.

The new column lets the moderation scanner in
`src/services/content_moderation.py` TAG (not block) suspicious tasks. The
auto-headless gate in `routers/tasks.py` then refuses to flip `run_mode` to
`auto_headless` until a human reviewer clears the flag — preserving the
operator's ability to legitimately FILE destructive work (e.g. quarterly
archive purge) while preventing accidental auto-pickup.

## Column shape

- `tasks.requires_human_review BOOLEAN NOT NULL DEFAULT FALSE` — default
  matches the "trust until matched" semantics: a freshly-filed task is
  unflagged; the scanner stamps `true` when one of the destructive patterns
  hits. Re-PATCHing the task body re-scans (POST + PATCH both call the
  scanner); the flag is sticky in one direction only (false → true on
  match) — the reviewer must EXPLICITLY clear it (PATCH
  `requires_human_review=false`).
- NOT NULL: the field is always a boolean, never tri-state. NULL would make
  the auto-headless gate ambiguous (treat NULL as "unscanned" or "unflagged"?).
  Explicit DEFAULT FALSE removes the question.
- No CHECK constraint — `BOOLEAN` already constrains the domain.
- No partial index — the column is scanned only on the row currently being
  PATCHed (single-row lookup by id), not in any cross-row query.

## Sibling layers

- L17 (already shipped): worker pickup-time scanner at
  `langgraph/content_safety.py`. Currently has its own inline pattern list
  because L14 wasn't ready; a follow-up task will refactor it to import
  from the canonical `src/services/content_moderation.py`.
- L18 (#1115 — already shipped): payload-size caps on description / AC /
  subagent_models. Bounds per-row growth.
- L21 (#1125 — already shipped): per-template cap on concurrently-active
  children. Caps recurrence blast radius.

No data backfill needed — column is additive with a NOT NULL default; every
existing row picks up `false`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0037_tasks_requires_human_review"
down_revision = "0036_tasks_template_confirmed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "requires_human_review")
