"""tasks: add subagent_models JSONB column (Kanban #887)

Revision ID: 0024_tasks_subagent_models
Revises: 0023_tasks_task_kind_default_ai
Create Date: 2026-05-14 00:01 UTC

Append-only audit log of subagent spawns per task. Each element records the
agent name, model tier, and spawn timestamp:
    {"agent": "dev-backend", "model": "sonnet", "at": "2026-05-13T09:00:00Z"}

PATCH semantics are full-replace (Lead accumulates the list then sends the
whole array on each PATCH). Append logic is on Lead's side, not the API.

DDL:
  - ADD COLUMN subagent_models JSONB NOT NULL DEFAULT '[]'. PG 16 treats this
    as a metadata-only operation (no heap rewrite, no row backfill) because the
    column carries a non-null server_default — all existing rows read '[]'
    without touching the heap.

No DB-level CHECK constraint on element shape — Pydantic `SubagentModelEntry`
validates at the API boundary (same precedent as `acceptance_criteria`,
`question_payload`). No downgrade caveat: dropping the column is safe at any
time; '[]' default means no data is meaningful.

Wire-contract mirrors (atomic with this migration — see #887 spawn brief):
  - api/src/models/task.py      : subagent_models Mapped[list[dict]] JSONB column
  - api/src/schemas/task.py     : SubagentModelEntry + TaskCreate / TaskUpdate /
                                  TaskRead fields
  - api/tests/test_subagent_models.py : round-trip + validation tests
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0024_tasks_subagent_models"
down_revision = "0023_tasks_task_kind_default_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "subagent_models",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "subagent_models")
