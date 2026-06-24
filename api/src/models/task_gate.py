"""TaskGate ORM model (Kanban #2564 — async-HITL gate child table).

One work-task : N gates. A gate is a SUB-EVENT of a task (an async HITL ask),
NOT a board task — so a multi-round HITL exchange produces N `task_gates` rows
instead of N question-tasks cluttering the board. Mirrors migration
`0072_task_gates`. Locked schema: `async-hitl-gates.md` §4.

Lifecycle (§4):
  hit gate -> INSERT gate(status='open') + work-task ps->8 + operator_gate=<tier>
           -> operator answers async -> resolve checks gate still 'open'
              -> write answer + status='answered' + fold into resume_context
              -> ps 8->actionable ONLY when the task's open-gate count -> 0
              -> picker re-selects, resumes from resume_context
           -> next gate = INSERT new row (seq+1) -> repeat

Concurrency (§4, LOCKED): multiple 'open' rows per task_id are native; the
work-task becomes actionable only when its open-gate count -> 0. Out-of-order
answers bind by gate_id (each answer carries its own id) — exactly where the
table + gate-id pays off over a single overwritten payload slot.

`blocked_by` is INTENTIONALLY UNTOUCHED — HITL is an operator-gate, not a
task-dependency (§3).

ENUM VALIDATION: `kind` / `status` / `gate_tier` / `answered_via` each carry a
DB CHECK (mirror of migration 0072) so ORM autogenerate stays in lockstep with
the live DDL, AND the Pydantic Literals in `schemas/task_gate.py` gate the value
at the API boundary (the friendlier 422). Same posture as task_comments'
author_kind.

HISTORY: round history flows via the existing `tasks_history` PG trigger on the
`tasks` table (the ps flips are auditable there) — NO new history table and NO
trigger on `task_gates` itself this slice (§5 of the spawn brief: "reuse
tasks_history; a trigger on task_gates is OPTIONAL/minimal-only"). The gate rows
themselves are the audit log of asks+answers (status + answered_at + answer).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TaskGate(Base):
    """One async-HITL gate (a sub-event of a work-task). See module docstring."""

    __tablename__ = "task_gates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # The work-task this gate halts. ON DELETE CASCADE — the gate dies with its
    # task (the only removal path; the app never hard-deletes). Mirror of
    # task_comments.task_id posture.
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Order within the task. Allocated server-side as MAX(seq)+1 per task_id at
    # open time (the open-gate endpoint computes it; no DB sequence — seq is
    # per-task, not global).
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # 'question' | 'decision' — gated by GateKindLiteral at the API boundary +
    # the DB CHECK below (mirror of migration 0072).
    kind: Mapped[str] = mapped_column(Text, nullable=False)

    # {question, options[]} — the ask payload. NULL = a bare gate with no
    # structured options.
    question_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 'open' | 'answered' | 'cancelled' | 'expired'. DB DEFAULT 'open' covers
    # INSERT; gated by GateStatusLiteral + the DB CHECK below.
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'open'"),
    )

    # The chosen option / free-text answer. JSONB so it holds either a scalar
    # string or a structured object. NULL until answered.
    answer: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    # Mirrors tasks.operator_gate — key/commit/decision/hitl/external. Gated by
    # OperatorGateLiteral + the DB CHECK below.
    gate_tier: Mapped[str] = mapped_column(Text, nullable=False)

    # Provenance — operator id / chat id. NULL until answered.
    answered_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 'web' | 'telegram' — the channel the answer arrived on. NULL until
    # answered; gated by GateAnsweredViaLiteral + the DB CHECK below.
    answered_via: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # Enum gates — mirror of migration 0072's CHECK predicates (kept in
        # lockstep so ORM autogenerate produces no spurious diff).
        CheckConstraint(
            "kind IN ('question', 'decision')",
            name="ck_task_gates_kind_valid",
        ),
        CheckConstraint(
            "status IN ('open', 'answered', 'cancelled', 'expired')",
            name="ck_task_gates_status_valid",
        ),
        CheckConstraint(
            "gate_tier IN ('key', 'commit', 'decision', 'hitl', 'external')",
            name="ck_task_gates_gate_tier_valid",
        ),
        CheckConstraint(
            "answered_via IS NULL OR answered_via IN ('web', 'telegram')",
            name="ck_task_gates_answered_via_valid",
        ),
        # Per-task ordered gate list + MAX(seq) allocation scan. UNIQUE enforces
        # the §4 "order explicit (seq)" invariant — a concurrent double-open on
        # the same task_id+seq becomes a retriable DB error instead of a silent
        # duplicate seq.
        Index(
            "ix_task_gates_task_id_seq",
            "task_id",
            "seq",
            unique=True,
        ),
        # Partial index on the open subset — the hot path: remaining-open-gate
        # count on resolve + the unified pending-gate read. Mirror of migration
        # 0072's postgresql_where so the index stays sparse.
        Index(
            "ix_task_gates_open",
            "task_id",
            postgresql_where=text("status = 'open'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskGate id={self.id} task_id={self.task_id} seq={self.seq} "
            f"kind={self.kind!r} status={self.status!r} gate_tier={self.gate_tier!r}>"
        )
