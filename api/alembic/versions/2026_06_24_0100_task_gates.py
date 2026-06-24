"""task_gates: async-HITL gate child table (Kanban #2564)

Revision ID: 0072_task_gates
Revises: 0071_ix_tasks_next_autorun
Create Date: 2026-06-24 01:00 UTC

Creates the `task_gates` child table per the LOCKED schema in
`context/projects/agent-teams/shared/design/async-hitl-gates.md` §4 — the
foundation for async HITL over a chat channel (the "stuck" path of the Mode-A
continuous runner). One work-task : N gates; a gate is a sub-event of a task,
NOT a board task (no question-task clutter per round).

Coexists with the legacy HITL flow (interaction_kind + blocked_by, used by
Mode B) — see §7. `blocked_by` is intentionally UNTOUCHED.

DDL:
  - CREATE TABLE task_gates (id PK, task_id FK->tasks.id ON DELETE CASCADE,
    seq, kind, question_payload jsonb, status, answer jsonb, gate_tier,
    answered_by, answered_via, created_at, answered_at).
  - Two enum CHECKs (kind, status) + one (gate_tier) mirror the Pydantic
    Literals at the API boundary — DB defense-in-depth against raw-SQL drift
    (parity with task_comments' author_kind CHECK). Kept as literal IN-lists
    here because migrations import zero app code
    (standards/sqlalchemy/migrations.md "Helper duplication").
  - ix_task_gates_task_id_seq: composite (task_id, seq) — the per-task ordered
    gate list + seq-allocation MAX(seq) scan.
  - ix_task_gates_open: PARTIAL index on (task_id) WHERE status='open' — the
    hot path: the remaining-open-gate-count on resolve + the unified
    pending-gate read (only ever scans the sparse 'open' subset).

Downgrade drops both indexes then the table (fully reversible). FK ON DELETE
CASCADE means a hard-deleted task takes its gates with it; the app never
hard-deletes, so this is defense-in-depth (parity with task_comments).

Wire-contract mirrors (atomic with this migration — see #2564 spawn brief):
  - api/src/models/task_gate.py     : TaskGate ORM model (CHECKs + indexes mirror)
  - api/src/models/__init__.py      : import TaskGate so alembic autogenerate sees it
  - api/src/schemas/task_gate.py    : open/resolve/read/unified-pending schemas
  - api/src/routers/task_gates.py   : POST /api/tasks/{id}/gates,
                                      POST /api/task-gates/{gate_id}/resolve,
                                      GET /api/operator-gates/pending
  - api/src/main.py                 : include_router(task_gates_router)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0072_task_gates"
down_revision = "0071_ix_tasks_next_autorun"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_gates",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        # The work-task this gate halts. ON DELETE CASCADE — the gate dies with
        # its task (the only removal path; the app never hard-deletes).
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Order within the task. Allocated server-side as MAX(seq)+1 per task_id.
        sa.Column("seq", sa.Integer(), nullable=False),
        # 'question' | 'decision' — mirror of GateKindLiteral.
        sa.Column("kind", sa.Text(), nullable=False),
        # {question, options[]} — the ask payload. Nullable: a bare decision
        # gate may carry no structured options.
        sa.Column(
            "question_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # 'open' | 'answered' | 'cancelled' | 'expired' — mirror of
        # GateStatusLiteral. DB DEFAULT 'open' covers INSERT.
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        # The chosen option / free-text. JSONB so it holds either a scalar
        # string or a structured object. NULL until answered.
        sa.Column(
            "answer",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Mirrors tasks.operator_gate — key/commit/decision/hitl/external.
        # Mirror of OperatorGateLiteral.
        sa.Column("gate_tier", sa.Text(), nullable=False),
        # Provenance — operator id / chat id. NULL until answered.
        sa.Column("answered_by", sa.Text(), nullable=True),
        # 'web' | 'telegram' — the channel the answer arrived on. Mirror of
        # GateAnsweredViaLiteral. NULL until answered.
        sa.Column("answered_via", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "answered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # Enum gates — mirror of the Pydantic Literals (defense-in-depth vs
        # raw-SQL drift; the API boundary fires the friendlier 422 first).
        sa.CheckConstraint(
            "kind IN ('question', 'decision')",
            name="ck_task_gates_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'cancelled', 'expired')",
            name="ck_task_gates_status_valid",
        ),
        sa.CheckConstraint(
            "gate_tier IN ('key', 'commit', 'decision', 'hitl', 'external')",
            name="ck_task_gates_gate_tier_valid",
        ),
        sa.CheckConstraint(
            "answered_via IS NULL OR answered_via IN ('web', 'telegram')",
            name="ck_task_gates_answered_via_valid",
        ),
    )
    # Per-task ordered gate list + MAX(seq) allocation scan. UNIQUE enforces the
    # §4 "order explicit (seq)" invariant — a concurrent double-open on the same
    # task_id+seq becomes a retriable DB error instead of a silent duplicate seq.
    op.create_index(
        "ix_task_gates_task_id_seq",
        "task_gates",
        ["task_id", "seq"],
        unique=True,
    )
    # Partial index on the open subset — the hot path: remaining-open-gate-count
    # on resolve + the unified pending-gate read. Keeps the index sparse (only
    # currently-open gates) so both scans stay cheap.
    op.create_index(
        "ix_task_gates_open",
        "task_gates",
        ["task_id"],
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_task_gates_open", table_name="task_gates")
    op.drop_index("ix_task_gates_task_id_seq", table_name="task_gates")
    op.drop_table("task_gates")
