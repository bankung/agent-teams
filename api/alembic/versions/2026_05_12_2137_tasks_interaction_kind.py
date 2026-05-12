"""tasks.interaction_kind + question_payload + resume_context (Kanban #830)

Revision ID: 0019_tasks_interaction_kind
Revises: 0018_tasks_sort_order
Create Date: 2026-05-12 21:37 UTC

Adds three columns to `tasks` to support the headless auto-run
question/decision flow introduced in Kanban #830.

1. `interaction_kind` VARCHAR(16) NOT NULL DEFAULT 'work'
   CHECK ck_tasks_interaction_kind_valid:
     interaction_kind IN ('work', 'question', 'decision')
   - 'work'     (default): task executed by an agent.
   - 'question': auto-run halted; needs a user answer before proceeding.
   - 'decision': Option A/B choice requiring human judgment.

2. `question_payload` JSONB NULL
   Holds the question + answer history for 'question'/'decision' tasks.
   Shape validated at the API boundary by Pydantic
   (QuestionPayload / AnswerHistoryEntry — added in this slice).
   No DB CHECK on element shape — same precedent as acceptance_criteria
   (Pydantic validation at the boundary is sufficient).

3. `resume_context` JSONB NULL
   Free-form partial-work state stored by Lead when auto-run halts
   mid-task. Used by the re-spawn brief on resume. No shape constraint.

Pattern references:
- `interaction_kind`: mirrors migration 0015 (task_type) — VARCHAR + DEFAULT
  + CHECK; local `_in_clause_text` copy per
  context/standards/sqlalchemy/migrations.md "Helper duplication between
  app and migration" (migrations don't import app code).
- `question_payload` / `resume_context`: mirror migration 0014
  (acceptance_criteria) — nullable JSONB, no DB CHECK on element shape.

Existing rows backfill cleanly:
- `interaction_kind` = 'work' (DB DEFAULT on ADD COLUMN NOT NULL;
  PG 16 ADD COLUMN with constant DEFAULT is metadata-only — no heap
  rewrite).
- `question_payload` = NULL, `resume_context` = NULL (nullable JSONB ADD
  COLUMN; also metadata-only).

Down: drop the CHECK, drop all three columns (reverse order).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0019_tasks_interaction_kind"
down_revision = "0018_tasks_sort_order"
branch_labels = None
depends_on = None


# Kept in sync with src/constants.py TaskInteractionKind.ALL — duplicated per
# context/standards/sqlalchemy/migrations.md "Helper duplication between app
# and migration" (migrations don't import app code).
_INTERACTION_KIND_ALL = ("work", "question", "decision")


def _in_clause_text(column: str, values: tuple[str, ...]) -> str:
    # Mirror of src.constants.in_clause_text — duplicated locally so the
    # migration has zero app-code imports.
    _allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    for v in values:
        if not v or any(c not in _allowed for c in v):
            raise ValueError(
                f"_in_clause_text only allows [a-z0-9_-]+ values; got {v!r}"
            )
    return f"{column} IN ({', '.join(f'{chr(39)}{v}{chr(39)}' for v in values)})"


def upgrade() -> None:
    # 1. interaction_kind — DEFAULT 'work' covers existing rows without a
    #    backfill UPDATE statement (PG 16 metadata-only ADD COLUMN).
    op.add_column(
        "tasks",
        sa.Column(
            "interaction_kind",
            sa.String(length=16),
            server_default="work",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_interaction_kind_valid",
        "tasks",
        _in_clause_text("interaction_kind", _INTERACTION_KIND_ALL),
    )

    # 2. question_payload — nullable JSONB, no DB CHECK on element shape
    #    (Pydantic validates at the API boundary; same precedent as
    #    acceptance_criteria in migration 0014).
    op.add_column(
        "tasks",
        sa.Column("question_payload", postgresql.JSONB, nullable=True),
    )

    # 3. resume_context — free-form partial-work state. Nullable JSONB,
    #    no shape constraint.
    op.add_column(
        "tasks",
        sa.Column("resume_context", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "resume_context")
    op.drop_column("tasks", "question_payload")
    op.drop_constraint("ck_tasks_interaction_kind_valid", "tasks", type_="check")
    op.drop_column("tasks", "interaction_kind")
