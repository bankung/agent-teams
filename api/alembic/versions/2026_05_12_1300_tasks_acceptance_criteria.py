"""tasks.acceptance_criteria — structured per-criterion pass/fail tracker (Kanban #797)

Revision ID: 0014_tasks_acceptance_criteria
Revises: 0013_tasks_halt_reason
Create Date: 2026-05-12 13:00 UTC

Adds one optional JSONB column to the `tasks` table to capture exit criteria as
a structured array, addressing the post-#789 retrospective finding that exit
criteria buried in description text are easy to miss at done-time (claimed WIN
on #794 was actually 1.5/4 of the criteria).

- `acceptance_criteria` (JSONB, NULL) — array of objects, each shaped as:
    {
      "text":        str,                                   # required
      "status":      "pending" | "passed" | "failed" | "na", # default "pending"
      "verified_by": str | null,                            # role / agent id
      "verified_at": ISO-8601 datetime str | null,
      "notes":       str | null
    }
  NULL = field unset (task was filed without structured criteria).
  Empty array [] = task had criteria explicitly cleared.
  Pydantic schema `AcceptanceCriterion` validates element shape at the API
  boundary (text min_length=1, status Literal, etc.).

Semantics (design lock 2026-05-12, user signoff):
- 1C: JSONB structured (rejected free-form text 1A and split-table 1B).
- 2A: Optional on every task (no schema constraint requiring it on filing).
- 3B: Soft enforce via agent prompts (NOT a hard API done-guard). Block on
  process_status=5 with pending items is intentionally NOT enforced this slice
  — the prompts work lives in Kanban #798.

Deliberately NOT introduced this slice:
- No DB CHECK on element shape. PG can't cheaply enforce structured JSONB
  invariants, and Pydantic at the API boundary is sufficient — same precedent
  as projects.paths / projects.stack / projects.config JSONB columns.
- No GIN index. Auto-pickup query does NOT filter on criteria; there's no
  read path that would benefit. Add later only if a real plan regresses.
- No backfill of existing rows. Tasks created before this lands legitimately
  have NULL criteria — optional follow-up task as noted in the spec.
- No atomic single-item PATCH endpoint (e.g., /api/tasks/{id}/criteria/{idx}).
  Full array replace via the standard PATCH route is the MVP — KISS.

PG 16 treats a nullable JSONB column add with no default literal as
metadata-only — no heap rewrite, no row backfill, instant on the existing rows.

Down: drop the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0014_tasks_acceptance_criteria"
down_revision = "0013_tasks_halt_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("acceptance_criteria", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "acceptance_criteria")
