"""Pydantic schemas for the `task_gates` table (Kanban #2564 — async HITL).

The wire contract for the async-HITL gate foundation (`async-hitl-gates.md` §4):
open a gate, resolve a gate (gate_id-keyed, distinct from the legacy
`/decide`), read a gate, and the unified pending-gate read element that unions
legacy operator-HITL with open `task_gates` rows (§7 "two writers, one reader").

Enum Literals here gate the value set at the API boundary (422 on any other
value); the DB CHECKs in migration 0072 / `models/task_gate.py` are
defense-in-depth (same posture as #1677 model_override / #2127 operator_gate).
`gate_tier` reuses `OperatorGateLiteral` from `schemas.task` so the gate and the
task-level `operator_gate` lane speak the same vocabulary.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.task import OperatorGateLiteral

# Per-field size ceilings (serialised JSON bytes) — mirrors Kanban #1115
# per-field cap discipline. The request-size middleware is Content-Length-only
# and misses chunked payloads, so we enforce here.
_QUESTION_PAYLOAD_MAX_BYTES = 8_192   # ~8 KB: covers rich option lists comfortably
_ANSWER_MAX_BYTES = 4_096             # ~4 KB: covers free-text answers

# Wire enums (no src.constants tuple / DB CHECK pairing beyond the migration
# 0072 mirror — gated solely by these Literals at the API boundary, 422 on any
# other value; mirrors the #2127 OperatorGateLiteral posture).
GateKindLiteral = Literal["question", "decision"]
GateStatusLiteral = Literal["open", "answered", "cancelled", "expired"]
GateAnsweredViaLiteral = Literal["web", "telegram"]


class GateOpenRequest(BaseModel):
    """Request body for `POST /api/tasks/{task_id}/gates` — open a gate.

    INSERTs a `task_gates` row (status='open', server-allocated seq) and halts
    the work-task: `process_status=8` + `operator_gate=<gate_tier>` (halted_at
    auto-stamps via _STATUS_TIMESTAMP_FIELDS). The task_id comes from the path,
    NOT the body.

    `question_payload` is free-form ({question, options[]} by convention, §4) —
    Mode-A / Telegram callers carry varied shapes, so it is an unconstrained
    dict (mirrors the JSONB-nullable column) rather than a typed model.

    `extra='forbid'` rejects unknown keys at 422.
    """

    model_config = ConfigDict(extra="forbid")

    kind: GateKindLiteral
    gate_tier: OperatorGateLiteral
    question_payload: dict[str, Any] | None = None

    @field_validator("question_payload")
    @classmethod
    def _cap_question_payload(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        size = len(json.dumps(v, separators=(",", ":")))
        if size > _QUESTION_PAYLOAD_MAX_BYTES:
            raise ValueError(
                f"question_payload serialises to {size} bytes; "
                f"max is {_QUESTION_PAYLOAD_MAX_BYTES}"
            )
        return v


class GateResolveRequest(BaseModel):
    """Request body for `POST /api/task-gates/{gate_id}/resolve` — resolve a gate.

    The gate_id comes from the path. Body carries the operator's answer + its
    provenance. Answering a non-'open' gate is an idempotent stale-reject (409
    from the router, NOT a 5xx) — see the endpoint.

    - `answer`       — required; the chosen option id / free-text / structured
                       object. JSONB column, so any JSON-serialisable value.
    - `provenance`   — required; the channel the answer arrived on
                       ('web' | 'telegram') → written to `answered_via`.
    - `answered_by`  — optional; operator id / chat id → written to
                       `answered_by` (NULL when omitted).

    `extra='forbid'` rejects unknown keys at 422.
    """

    model_config = ConfigDict(extra="forbid")

    answer: Any = Field(description="Chosen option id / free-text / structured answer")
    provenance: GateAnsweredViaLiteral
    answered_by: str | None = Field(default=None, max_length=255)

    @field_validator("answer")
    @classmethod
    def _validate_answer(cls, v: Any) -> Any:
        # N2: reject null — an "answered with NULL" gate is semantically invalid.
        if v is None:
            raise ValueError("answer must not be null")
        # SEC-NIT-1: cap at ~4 KB serialised to bound attacker-controlled payload.
        size = len(json.dumps(v, separators=(",", ":")))
        if size > _ANSWER_MAX_BYTES:
            raise ValueError(
                f"answer serialises to {size} bytes; max is {_ANSWER_MAX_BYTES}"
            )
        return v


class GateRead(BaseModel):
    """A `task_gates` row as returned by open + read paths."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    seq: int
    kind: GateKindLiteral
    question_payload: dict[str, Any] | None
    status: GateStatusLiteral
    answer: Any | None
    gate_tier: OperatorGateLiteral
    answered_by: str | None
    answered_via: GateAnsweredViaLiteral | None
    created_at: datetime
    answered_at: datetime | None


class GateResolveResponse(BaseModel):
    """Response body for `POST /api/task-gates/{gate_id}/resolve`.

    Mirrors the legacy HitlResolveResponse shape (task_id, process_status,
    resume_context) + the gate-specific `gate_id` and the concurrency-critical
    `open_gate_count_remaining` (the work-task flips to actionable ONLY when
    this reaches 0).
    """

    model_config = ConfigDict(extra="forbid")

    gate_id: int
    task_id: int
    process_status: int
    open_gate_count_remaining: int
    resume_context: dict[str, Any] | None
    resolved_at: datetime


class PendingGateItem(BaseModel):
    """One element of the unified `GET /api/operator-gates/pending` read.

    Unions two sources into one shape every caller can read (§7):
      - source='task_gate'      — an open `task_gates` row (gate_id populated).
      - source='legacy_operator' — a task on the legacy operator-HITL lane
                                    (operator_gate set OR a pending
                                    gate='operator' AC item); gate_id is NULL.

    `source` tags each element so the caller can route it. Fields common to
    both are populated; source-specific fields (gate_id, seq, kind,
    question_payload) are NULL for the legacy source.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["task_gate", "legacy_operator"]
    task_id: int
    title: str
    process_status: int
    gate_tier: str | None
    # task_gate-only fields (NULL for legacy_operator rows).
    gate_id: int | None = None
    seq: int | None = None
    kind: GateKindLiteral | None = None
    question_payload: dict[str, Any] | None = None
    created_at: datetime
