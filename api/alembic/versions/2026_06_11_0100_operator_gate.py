"""tasks.operator_gate + operator_gate_note — queryable 'blocked-on-operator'
marker (Kanban #2127)

Revision ID: 0064_operator_gate
Revises: 0063_session_runs_provider_model
Create Date: 2026-06-11 01:00 UTC

GAP (operator-flagged 2026-06-09): there was no queryable way to answer "what
work is blocked on ME (the operator)?". Operator-dependency lived only in
free-text descriptions; a heuristic text-scan was noisy + lossy. This migration
adds a structured marker.

OPERATOR-CONFIRMED DESIGN (locked 2026-06-11):

1. TASK-LEVEL ROLLUP — two nullable TEXT columns on `tasks`:
   - `operator_gate`      (5-enum TEXT: key | commit | decision | hitl | external)
   - `operator_gate_note` (free-form advisory text)
   Both set DIRECTLY by the Lead — NO auto-derivation, NO trigger, NO sweep
   (explicit prohibition). NULL operator_gate = not gated at the task level.

   ENUM STORAGE: follows the #1677 model_override precedent — the value is gated
   by a Pydantic Literal at the API boundary (422 on any other value); NO DB
   CHECK constraint on operator_gate (parity with run_mode's older CHECK is
   intentionally NOT mirrored here — the Literal is the single gate, same posture
   as model_override's nullable TEXT). operator_gate_note has no length cap
   (advisory) and is settable independently of operator_gate.

   PG 16 metadata-only ADD COLUMN (nullable, no server_default) — no heap
   rewrite, no full-table scan under ACCESS EXCLUSIVE.

2. AC-LEVEL = SOURCE OF TRUTH (per-criterion gate): `acceptance_criteria` JSONB
   items gain OPTIONAL fields `gate` (only legal value 'operator') + `gate_kind`
   (the 5-enum). An AC gates ONLY while its `status=='pending'` — passed/na
   clears it automatically. Element shape is enforced at the Pydantic boundary
   (AcceptanceCriterion); the column stays plain JSONB with no CHECK.

3. GIN INDEX for the AC-level filter predicate:
     ix_tasks_ac_gin ON tasks USING GIN (acceptance_criteria jsonb_path_ops)

   WHY jsonb_path_ops + the @> containment operator (NOT jsonb_path_exists):
   `jsonb_path_ops` is the smaller/faster GIN opclass that indexes ONLY the
   @> (containment) operator. The filter therefore uses @> containment, e.g.
     acceptance_criteria @> '[{"gate":"operator","status":"pending"}]'        (any)
     acceptance_criteria @> '[{"gate":"operator","status":"pending","gate_kind":"key"}]'  (specific)
   On a JSONB ARRAY, `arr @> '[{...}]'` is TRUE iff at least one array element
   contains the right-hand object — which is EXACTLY "≥1 AC item with
   gate='operator' AND status='pending' [AND gate_kind=<value>]". This is the
   indexable pairing: jsonb_path_ops supports @> but does NOT support
   jsonb_path_exists (`@?`) — picking jsonb_path_exists here would leave the
   query unindexed. (`jsonb_ops`, the default opclass, supports both @> and @?
   but is larger; we deliberately choose the leaner jsonb_path_ops since @> is
   the only operator this feature needs.)

   NULL acceptance_criteria rows are simply never matched by @> (NULL @> x is
   NULL/false) and are not stored in the GIN index for these keys — cheap.

HISTORY CAPTURE is FREE: the existing `tasks_audit_trg` (migration 0001)
snapshots `to_jsonb(OLD)`, which auto-captures the two new columns. A PATCH that
sets/clears operator_gate / operator_gate_note lands in `tasks_history`
automatically (operation 'U') — NO trigger change needed (WARN-5).

Downgrade drops the index + both columns; the marker state is discarded
silently (feature is opt-in; every existing row is un-gated, NULL).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0064_operator_gate"
down_revision = "0063_session_runs_provider_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Task-level rollup columns. Nullable TEXT, no server_default, NO DB CHECK
    # (Pydantic Literal gates the value at the API boundary — #1677 precedent).
    # PG 16 metadata-only ADD COLUMN — no heap rewrite.
    op.add_column(
        "tasks",
        sa.Column("operator_gate", sa.Text(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("operator_gate_note", sa.Text(), nullable=True),
    )
    # GIN index for the AC-level @> containment predicate (jsonb_path_ops opclass
    # — indexes the @> operator only; see migration docstring for why @> not
    # jsonb_path_exists). Supports both the `any` and `gate_kind`-specific filters.
    op.create_index(
        "ix_tasks_ac_gin",
        "tasks",
        ["acceptance_criteria"],
        postgresql_using="gin",
        postgresql_ops={"acceptance_criteria": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_ac_gin", table_name="tasks")
    op.drop_column("tasks", "operator_gate_note")
    op.drop_column("tasks", "operator_gate")
