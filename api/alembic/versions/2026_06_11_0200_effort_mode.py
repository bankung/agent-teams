"""projects.effort_mode + tasks.effort_override + session_runs.effort —
per-project Anthropic thinking/effort cost-governance lever (Kanban #2300, Slice 1)

Revision ID: 0065_effort_mode
Revises: 0064_operator_gate
Create Date: 2026-06-11 02:00 UTC

The lever (design lock 2026-06-11, verified vs live Anthropic docs): Anthropic's
`output_config.effort` + `thinking:{type:adaptive}` — NOT the removed budget_tokens.
Three nullable TEXT carriers, all enum-gated by Pydantic Literal at the API
boundary (NO DB CHECK — #1677 posture, mirrors model_override / operator_gate):

1. `projects.effort_mode`  ∈ {off,low,medium,high,extra,auto} | NULL.
   NULL = global default = off (no project silently pays). Deliberately NOT a key
   inside projects.agent_overrides — that JSONB is a strict role->tier map and a
   mode string would break its AgentModelLiteral contract.

2. `tasks.effort_override` ∈ {off,low,medium,high,extra,max} | NULL.
   Per-task carrier (mirrors tasks.model_override #1677). Precedence:
   task.effort_override > project.effort_mode > off. In 'auto' project mode the
   worker WRITES the resolved level here at spawn for visibility. 'max' is
   manual-only (Slice-2 UI); auto never selects it.

3. `session_runs.effort` (TEXT | NULL) — the resolved effort for the run, so
   per-effort spend is comparable in usage reporting. Thinking bills as OUTPUT
   tokens; no pricing.py rate change.

PG 16 metadata-only ADD COLUMN on all three (nullable, no server_default) — no
heap rewrite, no full-table scan under ACCESS EXCLUSIVE. Existing rows read NULL.

Downgrade drops all three; the lever state is discarded silently (feature is
opt-in; every existing row is un-set, NULL).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0065_effort_mode"
down_revision = "0064_operator_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All three nullable TEXT, no server_default, NO DB CHECK (Pydantic Literal
    # gates the value at the API boundary — #1677 precedent). PG 16
    # metadata-only ADD COLUMN — no heap rewrite.
    op.add_column(
        "projects",
        sa.Column("effort_mode", sa.Text(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("effort_override", sa.Text(), nullable=True),
    )
    op.add_column(
        "session_runs",
        sa.Column("effort", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_runs", "effort")
    op.drop_column("tasks", "effort_override")
    op.drop_column("projects", "effort_mode")
