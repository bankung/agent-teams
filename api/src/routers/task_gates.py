"""HTTP routes for async-HITL gates (`task_gates`) — Kanban #2564.

The async-HITL gate foundation (`async-hitl-gates.md` §4 + §7). Three endpoints:

  - POST /api/tasks/{task_id}/gates          — open a gate (halt the work-task)
  - POST /api/task-gates/{gate_id}/resolve   — resolve a gate (gate_id-keyed)
  - GET  /api/operator-gates/pending         — unified pending-gate read

SCOPE GUARD (this slice owns ONLY the model + resolve + the unified read):
  - NO Telegram / notify changes — `_fire_hitl_push` is UNTOUCHED (Task B #2565).
  - NO `next-autorun` picker selection changes (Task C #2566).
  - `blocked_by` semantics UNCHANGED (HITL is an operator-gate, not a dep — §3).

All routes require the `X-Project-Id` header (the uniform task-endpoint contract,
`services/session_project`). DB writes happen via these endpoints only — no raw
SQL DML.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.db import get_or_404, get_session
from src.models.project import Project
from src.models.task import Task
from src.models.task_gate import TaskGate
from src.services.notify_gate import notify_gate_opened
from src.schemas.task_gate import (
    GateOpenRequest,
    GateRead,
    GateResolveRequest,
    GateResolveResponse,
    PendingGateItem,
)
from src.services.session_project import (
    assert_task_belongs_to_session,
    require_project_id_header,
)

logger = logging.getLogger("api.task_gates")

router = APIRouter(tags=["task-gates"])

# The lifecycle status a halted work-task resumes to once its open-gate count
# reaches 0. TODO(1) is the actionable lane the next-autorun picker reads — so a
# resolved gate hands the task back to the picker. §4: "ps 8->actionable ->
# picker/runner re-selects the work-task, resumes from resume_context".
_RESUME_PROCESS_STATUS = TaskStatus.TODO


async def _count_open_gates(
    session: AsyncSession, task_id: int, exclude_gate_id: int | None = None
) -> int:
    """Count `task_gates` rows still 'open' for a task.

    `exclude_gate_id` lets the resolver compute the post-answer remaining count
    inside the same transaction WITHOUT relying on the just-mutated row's flush
    ordering — the gate being resolved is excluded explicitly.
    """
    stmt = select(func.count()).where(
        TaskGate.task_id == task_id,
        TaskGate.status == "open",
    )
    if exclude_gate_id is not None:
        stmt = stmt.where(TaskGate.id != exclude_gate_id)
    return int((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# (a) Open a gate
# ---------------------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/gates",
    response_model=GateRead,
    status_code=201,
)
async def open_gate(
    task_id: int,
    payload: GateOpenRequest,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> TaskGate:
    """POST /api/tasks/{task_id}/gates — open an async-HITL gate.

    INSERTs a gate (status='open', next seq) + halts the work-task
    (process_status=8, operator_gate=<gate_tier>; halted_at auto-stamps) — one
    transaction. Returns the created gate.

    Errors:
      - 404 — task not found.
      - 400 — task belongs to a different project (X-Project-Id mismatch).
      - 422 — invalid body (bad kind / gate_tier).
    """
    task = await get_or_404(
        session, Task, detail=f"Task id={task_id} not found", id=task_id
    )
    assert_task_belongs_to_session(task_id, task.project_id, session_project_id)

    # Allocate the next per-task seq. COALESCE(MAX(seq), 0) + 1 — seq is
    # per-task (not a global sequence), so the first gate on a task gets seq=1.
    next_seq = int(
        (
            await session.execute(
                select(func.coalesce(func.max(TaskGate.seq), 0) + 1).where(
                    TaskGate.task_id == task_id
                )
            )
        ).scalar_one()
    )

    gate = TaskGate(
        task_id=task_id,
        seq=next_seq,
        kind=payload.kind,
        question_payload=payload.question_payload,
        status="open",
        gate_tier=payload.gate_tier,
    )
    session.add(gate)

    # Halt the work-task. ps->8 (HALTED_PENDING_USER) + operator_gate=<tier> so
    # the task surfaces on the "what's on me?" lane. halted_at stamps only when
    # currently NULL (mirror of _STATUS_TIMESTAMP_FIELDS setdefault semantics in
    # routers/tasks.py — a re-halt never re-stamps). operator_gate is set to the
    # gate's tier regardless (this gate is the live ask).
    task.process_status = TaskStatus.HALTED_PENDING_USER
    # M2: last-writer-wins rollup — concurrent gates on the same task may carry
    # different tiers; the gate rows each record the correct tier, but this
    # task-level field reflects whichever open_gate call landed last.
    task.operator_gate = payload.gate_tier
    if task.halted_at is None:
        task.halted_at = datetime.now(timezone.utc)
    task.updated_at = func.now()

    await session.commit()
    await session.refresh(gate)
    logger.info(
        "gate_open: task=%d gate=%d seq=%d tier=%s",
        task_id,
        gate.id,
        gate.seq,
        gate.gate_tier,
    )

    # Kanban #2565: best-effort Telegram notify AFTER the commit (the gate is
    # durably open before we touch the network). Soft-fail — notify_gate_opened
    # never raises (mirrors _fire_hitl_push); a missing telegram target or token
    # is a silent no-op. The tier->channel policy (forbidden/informed/simple)
    # lives in notify_gate, shared with the runner (Task C #2566).
    project = await session.get(Project, task.project_id)
    if project is None:
        logger.warning(
            "gate_open: project not found for task=%d project_id=%d; Telegram notify skipped",
            task_id,
            task.project_id,
        )
    else:
        await notify_gate_opened(
            session=session,
            task=task,
            project=project,
            gate_id=gate.id,
            gate_tier=gate.gate_tier,
            question_payload=gate.question_payload,
        )

    return gate


# ---------------------------------------------------------------------------
# (b) Resolve a gate (gate_id-keyed — distinct from the legacy /decide)
# ---------------------------------------------------------------------------


@router.post(
    "/task-gates/{gate_id}/resolve",
    response_model=GateResolveResponse,
)
async def resolve_gate(
    gate_id: int,
    payload: GateResolveRequest,
    session_project_id: int = Depends(require_project_id_header),
    session: AsyncSession = Depends(get_session),
) -> GateResolveResponse:
    """POST /api/task-gates/{gate_id}/resolve — resolve a gate by its id.

    Distinct from the legacy `/api/tasks/{id}/decide` (which is task-keyed and
    clears is_pending). This is the NEW gate-id-keyed resolver:

      1. Validate the gate is still 'open' — answering a closed / cancelled /
         expired / already-answered gate is an idempotent STALE-REJECT (409, a
         clear 4xx, NOT a 5xx). A late/out-of-order Telegram answer binds to the
         wrong gate iff this guard is missing — §9 (the one structural need).
      2. Write answer + answered_by + answered_via + answered_at + status=
         'answered'.
      3. Fold the answer into the work-task `resume_context` (the self-sufficient
         resume snapshot — §8; a fresh run resumes from row + rail alone).
      4. Flip the work-task ps 8 -> actionable ONLY when the task's remaining
         open-gate count == 0 (all answered). Concurrency: multiple open gates
         per task are native; out-of-order answers bind by gate_id; the task
         becomes actionable only when open-gate-count -> 0 (§4 LOCKED).

    All of 2-4 happen in ONE transaction.

    Errors:
      - 404 — gate not found.
      - 400 — the gate's task belongs to a different project (X-Project-Id
              mismatch).
      - 409 — gate is not 'open' (stale-reject: already answered / cancelled /
              expired).
      - 422 — invalid body (missing answer / bad provenance).
    """
    # Kanban #2565 (SEC-NIT-2 from Task A): row-lock the gate with SELECT ... FOR
    # UPDATE so two concurrent resolves (web + telegram, or a double-tap from the
    # poller) SERIALIZE on the row. The second waiter re-reads status='answered'
    # AFTER the first commits and falls into the 409 stale-reject below — closing
    # the read-check-write race that the status guard alone leaves open under
    # concurrent Telegram delivery (exactly this task's risk). Cheap: one extra
    # lock clause on a PK lookup. 404 preserved when the gate id does not exist.
    gate = (
        await session.execute(
            select(TaskGate).where(TaskGate.id == gate_id).with_for_update()
        )
    ).scalar_one_or_none()
    if gate is None:
        raise HTTPException(status_code=404, detail=f"Gate id={gate_id} not found")

    # Load the parent work-task + enforce project scoping (the gate carries no
    # project_id of its own — it inherits the task's).
    task = await get_or_404(
        session, Task, detail=f"Task id={gate.task_id} not found", id=gate.task_id
    )
    assert_task_belongs_to_session(gate.task_id, task.project_id, session_project_id)

    # Step 1: stale-reject. A single 409 covers all not-resolvable states
    # (already-answered / cancelled / expired). Idempotent: a re-tap on an
    # already-answered gate returns the same 409 so the caller can re-read.
    # The FOR UPDATE lock above makes this guard race-safe: a concurrent resolve
    # that lost the lock race re-reads status here AFTER the winner committed.
    if gate.status != "open":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Gate id={gate_id} is not open (status='{gate.status}') — "
                f"already resolved or no longer answerable"
            ),
        )

    now_utc = datetime.now(timezone.utc)

    # Step 2: write the answer onto the gate row.
    gate.answer = payload.answer
    gate.answered_by = payload.answered_by
    gate.answered_via = payload.provenance
    gate.answered_at = now_utc
    gate.status = "answered"

    # Step 3: fold the answer into the work-task resume_context. Merge into any
    # existing resume_context (Lead may have stored mid-task state); the new
    # gate-answer keys win on conflict. Keyed by gate so an out-of-order answer
    # is self-describing in the snapshot (which gate, which seq, the answer, the
    # channel). This is the §8 self-sufficiency contract: a fresh run reads this
    # off the row alone.
    existing_rc: dict = dict(task.resume_context or {})
    answered_gates: dict = dict(existing_rc.get("answered_gates") or {})
    answered_gates[str(gate.id)] = {
        "gate_id": gate.id,
        "seq": gate.seq,
        "kind": gate.kind,
        "gate_tier": gate.gate_tier,
        "answer": payload.answer,
        "answered_by": payload.answered_by,
        "answered_via": payload.provenance,
        "answered_at": now_utc.isoformat(),
    }
    existing_rc["answered_gates"] = answered_gates
    # Convenience pointer to the most-recently answered gate (matches the
    # legacy resume_context "latest decision" ergonomic).
    existing_rc["last_answered_gate_id"] = gate.id
    task.resume_context = existing_rc

    # Step 4: count the gates STILL open after this answer (exclude the
    # just-answered gate explicitly so we don't depend on flush ordering), then
    # flip the work-task to actionable ONLY when that count reaches 0.
    remaining_open = await _count_open_gates(
        session, gate.task_id, exclude_gate_id=gate.id
    )
    if remaining_open == 0:
        task.process_status = _RESUME_PROCESS_STATUS
        # The operator-gate lane is cleared — nothing is on the operator now.
        task.operator_gate = None
    # else: leave the task HALTED (ps=8) + operator_gate as-is; sibling gates
    # are still open.
    task.updated_at = func.now()

    await session.commit()
    await session.refresh(task)
    await session.refresh(gate)

    logger.info(
        "gate_resolve: gate=%d task=%d remaining_open=%d task_ps=%d via=%s",
        gate.id,
        task.id,
        remaining_open,
        task.process_status,
        payload.provenance,
    )

    return GateResolveResponse(
        gate_id=gate.id,
        task_id=task.id,
        process_status=task.process_status,
        open_gate_count_remaining=remaining_open,
        resume_context=task.resume_context,
        resolved_at=now_utc,
    )


# ---------------------------------------------------------------------------
# (c) Unified pending-gate read (legacy operator-HITL  ∪  open task_gates)
# ---------------------------------------------------------------------------


@router.get(
    "/operator-gates/pending",
    response_model=list[PendingGateItem],
)
async def list_pending_operator_gates(
    session_project_id: int = Depends(require_project_id_header),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[PendingGateItem]:
    """GET /api/operator-gates/pending — the unified "what needs a human?" read.

    Unions TWO sources into ONE shape (§7 "two writers, one reader") so every
    surface (inbox, Telegram poller, dashboards) reads a single thing:

      (i)  open `task_gates` rows (the new async-HITL flow), tagged
           source='task_gate'; and
      (ii) legacy operator-HITL tasks (the existing "blocked-on-operator"
           semantics from Kanban #2127), tagged source='legacy_operator' — a
           task whose `operator_gate IS NOT NULL` OR which has >=1
           acceptance_criteria item with gate='operator' AND status='pending'.

    Both are scoped to the session project + active (non-soft-deleted) rows.
    Ordered task_gate rows first (created_at ASC), then legacy rows
    (created_at ASC). `limit` caps the COMBINED result.

    Note (v1 limitation): legacy rows are starved when the open-gate branch
    already consumes `limit` items. Acceptable for v1; revisit at the v0.9.0
    consolidation when the two writers are merged.
    """
    items: list[PendingGateItem] = []

    # --- (i) open task_gates rows -----------------------------------------
    # Join to the parent task for project scoping + title/ps. Only OPEN gates
    # (the partial ix_task_gates_open index serves this).
    gate_stmt = (
        select(TaskGate, Task.title, Task.process_status)
        .join(Task, TaskGate.task_id == Task.id)
        .where(
            Task.project_id == session_project_id,
            Task.status == RecordStatus.ACTIVE,
            TaskGate.status == "open",
        )
        .order_by(TaskGate.created_at.asc(), TaskGate.id.asc())
        .limit(limit)
    )
    for gate, title, ps in (await session.execute(gate_stmt)).all():
        items.append(
            PendingGateItem(
                source="task_gate",
                task_id=gate.task_id,
                title=title,
                process_status=ps,
                gate_tier=gate.gate_tier,
                gate_id=gate.id,
                seq=gate.seq,
                kind=gate.kind,
                question_payload=gate.question_payload,
                created_at=gate.created_at,
            )
        )

    # --- (ii) legacy operator-HITL tasks ----------------------------------
    # Reuse the #2127 OR-rule exactly (routers/tasks.py list_tasks): task-level
    # operator_gate IS NOT NULL OR >=1 pending gate='operator' AC item. The AC
    # predicate uses @> containment so it rides the ix_tasks_ac_gin GIN index.
    #
    # H1 dedup fix: open_gate() sets task.operator_gate so any task with an OPEN
    # task_gates row would ALSO match the `operator_gate IS NOT NULL` predicate
    # and appear twice. Exclude those tasks here — they're already represented
    # above as source='task_gate'.
    open_gate_task_ids = (
        select(TaskGate.task_id).where(TaskGate.status == "open").scalar_subquery()
    )
    _ac_match = {"gate": "operator", "status": "pending"}
    _ac_contains = Task.acceptance_criteria.op("@>")(cast([_ac_match], JSONB))
    legacy_stmt = (
        select(Task)
        .where(
            Task.project_id == session_project_id,
            Task.status == RecordStatus.ACTIVE,
            Task.id.not_in(open_gate_task_ids),
            or_(Task.operator_gate.is_not(None), _ac_contains),
        )
        .order_by(Task.created_at.asc(), Task.id.asc())
        .limit(limit)
    )
    for task in (await session.execute(legacy_stmt)).scalars().all():
        items.append(
            PendingGateItem(
                source="legacy_operator",
                task_id=task.id,
                title=task.title,
                process_status=task.process_status,
                gate_tier=task.operator_gate,
                gate_id=None,
                seq=None,
                kind=None,
                question_payload=None,
                created_at=task.created_at,
            )
        )

    return items[:limit]
