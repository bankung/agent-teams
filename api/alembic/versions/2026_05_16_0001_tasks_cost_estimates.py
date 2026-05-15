"""tasks: add cost estimation columns (Kanban #944)

Revision ID: 0025_tasks_cost_estimates
Revises: 0024_tasks_subagent_models
Create Date: 2026-05-16 00:01 UTC

Per-task LLM-cost estimation captured on `process_status` transition to DONE
(5). Three nullable columns are populated atomically by the PATCH handler at
done-flip time and remain NULL until first close — so existing rows backfill
to NULL with no heap rewrite (PG 16 metadata-only ADD COLUMN).

Heuristic shape (V1):
  - input_chars  = len(description or '') + len(title or '')
  - output_chars = len(status_change_reason or '')
  - tokens = chars / chars_per_token where chars_per_token = 2 if the input is
    Thai/CJK-dominant (>30% chars in those Unicode ranges), else 4. ASCII-style
    English compresses ~4 chars/token on Anthropic tokenizers; Thai/CJK runs
    ~2 chars/token because each codepoint is a denser semantic unit.
  - cost_usd = compute_cost(provider, model, tokens_in, tokens_out) — see
    src/services/cost_tracker.py for the price card.

Default-model assumption (no env vars present): provider='anthropic',
model='claude-sonnet-4-6'. Interactive Claude Code sessions don't set
`LANGGRAPH_LLM_PROVIDER` / `ANTHROPIC_MODEL`, so this default keeps the
estimate honest for the typical "Lead drove this task from a chat" case.

Idempotency contract: the PATCH handler skips estimation when
`estimated_cost_usd IS NOT NULL`. Re-closing a previously-done task
(DONE → CANCELLED → DONE) preserves the first-close estimate. Manual reset
to NULL via psql is the human-only override (mirrors the
`status_change_reason` precedent — raw SQL DML is gated by
`.claude/hooks/block-raw-sql-dml.ps1`).

DDL:
  - ADD COLUMN estimated_input_tokens INTEGER NULL — heuristic or real-metering
    sum of input tokens at close.
  - ADD COLUMN estimated_output_tokens INTEGER NULL — same for output.
  - ADD COLUMN estimated_cost_usd NUMERIC(10,4) NULL — USD, 4 decimal places
    (parity with `session_runs.total_cost_usd` and the `compute_cost` quant).
  All three nullable with no server_default → PG 16 metadata-only on add;
  existing rows read NULL. No CHECK constraints — values are advisory
  estimates, not invariants.

Wire-contract mirrors (atomic with this migration — see #944 spawn brief):
  - api/src/models/task.py          : estimated_* Mapped columns
  - api/src/schemas/task.py         : TaskRead exposes the three fields read-only
  - api/src/services/task_cost_estimator.py : pure estimator (heuristic + real-run)
  - api/src/routers/tasks.py        : PATCH handler hook on <5 → 5 transition
  - api/tests/test_task_cost_estimator.py   : per-AC coverage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0025_tasks_cost_estimates"
down_revision = "0024_tasks_subagent_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("estimated_output_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "estimated_cost_usd")
    op.drop_column("tasks", "estimated_output_tokens")
    op.drop_column("tasks", "estimated_input_tokens")
