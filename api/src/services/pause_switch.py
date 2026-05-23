"""GOV3 soft-pause service (Kanban #1211).

Two entry points + one resolve helper:
- `pause_project(project_id, reason, actor)`    — soft pause for review.
- `unpause_project(project_id, actor, ?reason)` — inverse: clear is_paused.
- `resolve_flag(flag_id, action, adjustments?, actor)` — atomic single-txn
  handler for the four operator answers on an GOV3 flag task.

Soft-pause semantics (D3) — DIFFERENT from GOV1 hard kill:
  (a) recurring tasks → suspended same way as kill (next_fire_at→NULL on
      non-template rows; kill_frozen=true on templates).
  (b) in-flight tasks → DO NOT freeze. They complete naturally. The signal
      is "no new pickups for this project", not "drop what you're doing".
  (c) open TODO tasks → DO NOT freeze. Operator may want to resolve them
      via the resolve-flag escape hatch.
  (d) new POSTs → blocked at the router layer unless the per-task escape
      hatch (`allow_during_pause=true` + reason >=10 chars) is set.
  (e) escape-hatch usage → logged in `projects_audit` with
      action='pause_override' so the GOV5 cycle can flag over-use.

Mutual exclusion (D3): a project cannot be both killed AND paused. The DB
CHECK `ck_projects_kill_pause_mutex` is the load-bearing invariant; the
service-layer 409 fires first for the friendlier error path.

All three functions:
- Run in a single transaction — row mutations + the audit row commit
  together. Partial failure rolls back the whole thing.
- Are idempotent in the "already-in-target-state" sense — 2nd pause / 2nd
  unpause raises HTTPException 409 with a stable detail.
- Write exactly one `projects_audit` row per successful action.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus
from src.models.project import Project
from src.models.projects_audit import ProjectsAudit
from src.models.task import Task
from src.services.kill_switch import (
    _get_active_project_or_404,
    kill_project,
)
from src.services.recurrence import next_cron_fire

logger = logging.getLogger(__name__)


# Source-text-locked detail strings (#122 pattern). Pin in router-side tests
# if the wire contract needs to stay stable across releases.
_DETAIL_ALREADY_PAUSED = (
    "Project {project_id} is already paused (since {paused_at}). "
    "POST /api/projects/{project_id}/unpause to undo."
)
_DETAIL_NOT_PAUSED = (
    "Project {project_id} is not paused; unpause is a no-op. "
    "POST /api/projects/{project_id}/pause to suspend."
)
_DETAIL_PAUSE_BLOCKED_BY_KILL = (
    "Project {project_id} is killed; cannot pause a killed project. "
    "Revive it first via POST /api/projects/{project_id}/revive, then pause."
)


# Vocabulary for resolve-flag actions (D4). The Pydantic Literal in
# schemas/project.py stays in lockstep with this tuple.
RESOLVE_FLAG_ACTIONS: tuple[str, ...] = (
    "continue",
    "adjust_continue",
    "keep_paused",
    "terminate",
)

# Which project columns the `adjust_continue` action is allowed to bump /
# tweak. The deliberately narrow allowlist prevents an operator-supplied
# adjustments dict from rewriting unrelated state (team / name / paths) —
# the GOV5 cycle will refine this as new tuning surfaces land.
ADJUST_CONTINUE_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "budget_daily_usd",
        "budget_monthly_usd",
        "budget_total_usd",
        "health_thresholds",
        "approval_policies",
        "hitl_timeout_hours",
        "audit_enabled",
    }
)


async def pause_project(
    *,
    project_id: int,
    reason: str,
    actor: str = "operator",
    session: AsyncSession,
) -> dict[str, Any]:
    """Soft-pause a project. Drain semantics per the module docstring.

    Returns a dict with pause outcome + drain_summary. Raises 404 if the
    project does not exist / is soft-deleted; 409 if already paused OR if
    the project is currently killed (mutex via app-layer pre-check + the
    DB CHECK as the load-bearing backstop).
    """
    project = await _get_active_project_or_404(session, project_id)

    if project.is_killed:
        # App-layer check first — friendlier 409 than the DB CHECK 400 fallback.
        raise HTTPException(
            status_code=409,
            detail=_DETAIL_PAUSE_BLOCKED_BY_KILL.format(project_id=project_id),
        )

    if project.is_paused:
        raise HTTPException(
            status_code=409,
            detail=_DETAIL_ALREADY_PAUSED.format(
                project_id=project_id, paused_at=project.paused_at
            ),
        )

    now = datetime.now(timezone.utc)

    # ---- (a) suspend recurring (same shape as GOV1 kill) --------------------
    # Non-template rows with recurrence_rule: NULL out next_fire_at; unpause
    # recomputes via next_cron_fire. Template rows: ck_tasks_template_recurrence_complete
    # forbids NULL on either field — mark kill_frozen=true instead. The
    # scheduler integration to honor kill_frozen-during-pause rides the same
    # follow-up as GOV1's kill-frozen handling (out of scope this slice; the
    # router-layer POST gate + the recurring-NULL mechanism cover v1).
    recurring_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.recurrence_rule.is_not(None),
        Task.next_fire_at.is_not(None),
    )
    recurring_rows = list((await session.execute(recurring_stmt)).scalars().all())
    for row in recurring_rows:
        if row.is_template:
            row.kill_frozen = True
        else:
            row.next_fire_at = None
    recurring_suspended = len(recurring_rows)

    # ---- (b) + (c) NOT applied for soft-pause --------------------------------
    # In-flight + open TODO tasks ride through the pause unchanged. The pause
    # is a "no new pickups" gate, not a "stop everything in motion" gate.
    # This is the load-bearing semantic difference from GOV1.

    # ---- flip project state -------------------------------------------------
    project.is_paused = True
    project.paused_at = now
    project.paused_reason = reason

    # ---- audit row -----------------------------------------------------------
    drain_summary: dict[str, Any] = {
        "recurring_suspended": recurring_suspended,
        # (b) + (c) NOT applied — surface explicitly so audit consumers
        # (project-auditor, GOV5 reviewer) see the soft-vs-hard distinction.
        "in_flight_marked": 0,
        "frozen_tasks": 0,
        "router_gate_active": True,
        # (d) PreToolUse-hook integration for spawn-block ride on the same
        # follow-up wave as GOV1's spawn_hook_gate_pending.
        "spawn_hook_gate_pending": True,
    }
    audit = ProjectsAudit(
        project_id=project_id,
        actor=actor,
        action="pause",
        reason=reason,
        drain_summary=drain_summary,
    )
    session.add(audit)

    await session.commit()
    await session.refresh(project)
    await session.refresh(audit)

    logger.info(
        "pause_project: project_id=%d actor=%s drain=%s",
        project_id,
        actor,
        drain_summary,
    )

    return {
        "success": True,
        "project_id": project_id,
        "action": "pause",
        "is_paused": True,
        "paused_at": project.paused_at,
        "paused_reason": project.paused_reason,
        "drain_summary": drain_summary,
        "audit_id": audit.id,
    }


async def unpause_project(
    *,
    project_id: int,
    actor: str = "operator",
    reason: str | None = None,
    session: AsyncSession,
) -> dict[str, Any]:
    """Inverse of pause_project. Restore the project to a runnable state.

    Returns a dict with unpause outcome + drain_summary (resumed counts).
    Raises 404 if the project does not exist / is soft-deleted; 409 if the
    project is not currently paused.

    Preserves `paused_at` + `paused_reason` as historical signal (D4) —
    only clears `is_paused=false` + the per-task kill_frozen markers AND
    recomputes `next_fire_at` for recurring templates. `reason` is captured
    into the audit row so resolve-flag invocations can record the source
    (e.g. 'resolve_continue', 'resolve_adjust_continue').
    """
    project = await _get_active_project_or_404(session, project_id)

    if not project.is_paused:
        raise HTTPException(
            status_code=409,
            detail=_DETAIL_NOT_PAUSED.format(project_id=project_id),
        )

    now = datetime.now(timezone.utc)

    # ---- recompute next_fire_at on recurring tasks --------------------------
    # Mirrors GOV1's revive logic. No staleness gate this slice — soft-pause
    # cadence is expected to be days, not weeks; if GOV5 surfaces stale-revive
    # noise, port the GOV1 REVIVE_MAX_STALENESS_DAYS gate over.
    recurring_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.recurrence_rule.is_not(None),
        Task.next_fire_at.is_(None),
    )
    recurring_rows = list((await session.execute(recurring_stmt)).scalars().all())
    for row in recurring_rows:
        row.next_fire_at = next_cron_fire(
            row.recurrence_rule, row.recurrence_timezone, anchor=now
        )
    resumed_recurring = len(recurring_rows)

    # ---- unfreeze every kill_frozen=true row in the project -----------------
    # Pause only ever sets kill_frozen=true on templates (see pause_project
    # comment); revive sweeps every kill_frozen=true row defensively in case
    # an out-of-band pause + GOV1 kill chain left an inconsistency. Cheap
    # full-project scan — no FK-driven cascade concerns at this scale.
    frozen_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.kill_frozen.is_(True),
    )
    frozen_rows = list((await session.execute(frozen_stmt)).scalars().all())
    for row in frozen_rows:
        row.kill_frozen = False
    unfrozen_tasks = len(frozen_rows)

    # ---- flip project state -------------------------------------------------
    # PRESERVE paused_at + paused_reason — D4: keep the historical record.
    project.is_paused = False

    # ---- audit row -----------------------------------------------------------
    drain_summary: dict[str, Any] = {
        "resumed_recurring": resumed_recurring,
        "unfrozen_tasks": unfrozen_tasks,
        "paused_at_at_unpause": (
            project.paused_at.isoformat() if project.paused_at else None
        ),
    }
    audit = ProjectsAudit(
        project_id=project_id,
        actor=actor,
        action="unpause",
        reason=reason,
        drain_summary=drain_summary,
    )
    session.add(audit)

    await session.commit()
    await session.refresh(project)
    await session.refresh(audit)

    logger.info(
        "unpause_project: project_id=%d actor=%s drain=%s",
        project_id,
        actor,
        drain_summary,
    )

    return {
        "success": True,
        "project_id": project_id,
        "action": "unpause",
        "is_paused": False,
        "paused_at": project.paused_at,
        "paused_reason": project.paused_reason,
        "drain_summary": drain_summary,
        "audit_id": audit.id,
    }


# ---------------------------------------------------------------------------
# Resolve-flag (D4 atomic handler)
# ---------------------------------------------------------------------------


def _filter_adjustments(adjustments: dict[str, Any]) -> dict[str, Any]:
    """Drop any key not in ADJUST_CONTINUE_ALLOWED_KEYS. Returns the filtered
    dict (does NOT mutate input). Caller surfaces a 422 if the filtered dict
    is empty when the operator supplied non-empty input — better than silently
    no-op-ing.
    """
    return {
        k: v for k, v in adjustments.items() if k in ADJUST_CONTINUE_ALLOWED_KEYS
    }


async def resolve_flag(
    *,
    flag_id: int,
    action: str,
    adjustments: dict[str, Any] | None = None,
    actor: str = "operator",
    session: AsyncSession,
) -> dict[str, Any]:
    """Atomic single-transaction handler for the four GOV3 flag actions (D4).

    The flag task is identified by `flag_id`. The handler:
    1. Validates `flag_id` exists + is an GOV3 flag (`interaction_kind='question'`
       AND `question_payload.is_audit_flag=true`).
    2. Validates the action against the flag's `question_payload.options`
       (defensive — the auditor sets these; we don't trust the caller).
    3. Branches:
       - continue        → flag DONE + unpause_project (audit reason=resolve_continue)
       - adjust_continue → apply adjustments to project + flag DONE +
                            unpause_project (audit reason=resolve_adjust_continue)
       - keep_paused     → flag DONE; is_paused stays true (no audit row this
                            branch — the flag-DONE itself IS the signal;
                            avoiding a 'pause' duplicate row keeps the audit
                            trail honest about who paused when).
       - terminate       → call GOV1 kill_project (reason auto-formatted),
                            flag DONE.

    Single commit wraps everything; partial failure rolls back. Imports
    `kill_project` lazily — the GOV1 service handles its own commits, so the
    terminate branch uses two commits (kill, then flag) but the second has
    no rollback risk by design.
    """
    if action not in RESOLVE_FLAG_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"action must be one of {sorted(RESOLVE_FLAG_ACTIONS)}; "
                f"got {action!r}"
            ),
        )

    # ---- fetch + validate the flag task -------------------------------------
    flag = await session.get(Task, flag_id)
    if flag is None or flag.status != RecordStatus.ACTIVE:
        raise HTTPException(
            status_code=404,
            detail=f"Flag task id={flag_id} not found",
        )

    # Defensive guards — the auditor sets these; refusing here catches
    # operator confusion (curl-ing the wrong task id).
    if flag.interaction_kind != "question":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Task id={flag_id} is not a question task "
                f"(interaction_kind={flag.interaction_kind!r}); "
                "resolve-flag only applies to GOV3 audit flags"
            ),
        )
    payload = flag.question_payload or {}
    if not payload.get("is_audit_flag"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Task id={flag_id} is not an GOV3 audit flag "
                "(question_payload.is_audit_flag is missing/false)"
            ),
        )

    # Honor the auditor-supplied option set (defensive — narrows the operator's
    # surface in case a future audit profile restricts the choices).
    options = payload.get("options") or list(RESOLVE_FLAG_ACTIONS)
    if action not in options:
        raise HTTPException(
            status_code=422,
            detail=(
                f"action {action!r} not offered by this flag; "
                f"valid options: {sorted(options)}"
            ),
        )

    project_id = flag.project_id
    flag_done_response: dict[str, Any] = {
        "flag_id": flag_id,
        "project_id": project_id,
        "action": action,
    }

    if action == "continue":
        # Single-transaction flow: flag DONE + unpause. Inline rather than
        # calling unpause_project — that helper commits independently, which
        # would split the atomic boundary. We replicate the unpause logic
        # here and commit once at the end of the handler.
        result = await _flag_done_and_unpause(
            session=session,
            flag=flag,
            actor=actor,
            audit_reason="resolve_continue",
        )
        flag_done_response.update(result)
        return flag_done_response

    if action == "adjust_continue":
        if not adjustments:
            raise HTTPException(
                status_code=422,
                detail="adjust_continue requires a non-empty adjustments object",
            )
        filtered = _filter_adjustments(adjustments)
        if not filtered:
            raise HTTPException(
                status_code=422,
                detail=(
                    "adjust_continue adjustments had no allowlisted keys "
                    f"(allowed: {sorted(ADJUST_CONTINUE_ALLOWED_KEYS)})"
                ),
            )
        # Apply adjustments BEFORE unpausing — if a key write fails (type
        # mismatch from a careless caller), the whole transaction rolls back
        # and the flag stays open + project stays paused.
        project = await _get_active_project_or_404(session, project_id)
        applied: dict[str, Any] = {}
        for key, value in filtered.items():
            setattr(project, key, value)
            applied[key] = value
        result = await _flag_done_and_unpause(
            session=session,
            flag=flag,
            actor=actor,
            audit_reason="resolve_adjust_continue",
            extra_drain={"adjustments_applied": applied},
        )
        flag_done_response.update(result)
        flag_done_response["adjustments_applied"] = applied
        return flag_done_response

    if action == "keep_paused":
        # Flag DONE; no project state change (is_paused stays true). No audit
        # row this branch — the flag itself records the operator's intent;
        # writing a duplicate 'pause' row would lie about who paused when.
        flag.process_status = 5  # TaskStatus.DONE
        flag.completed_at = datetime.now(timezone.utc)
        # Annotate the question_payload with the resolution so the GOV4 UI
        # surfaces "kept paused on YYYY-MM-DD" rather than just "DONE".
        new_payload = dict(payload)
        new_payload["resolved_action"] = "keep_paused"
        new_payload["resolved_at"] = datetime.now(timezone.utc).isoformat()
        new_payload["resolved_by"] = actor
        flag.question_payload = new_payload
        await session.commit()
        await session.refresh(flag)
        flag_done_response.update(
            {
                "is_paused": True,
                "flag_completed_at": flag.completed_at,
                "audit_id": None,
            }
        )
        return flag_done_response

    if action == "terminate":
        # Delegate to GOV1. kill_project commits independently — we accept the
        # two-commit split because the kill is the unrecoverable step
        # (operator-explicit) and the flag-DONE write that follows has no
        # rollback risk (it's a single column flip on a row we just loaded).
        kill_reason = (
            f"resolved via GOV3 flag #{flag_id} terminate action by {actor}"
        )
        kill_result = await kill_project(
            project_id=project_id,
            reason=kill_reason,
            force=False,
            actor=actor,
            session=session,
        )
        # Flag DONE + annotation.
        flag.process_status = 5
        flag.completed_at = datetime.now(timezone.utc)
        new_payload = dict(payload)
        new_payload["resolved_action"] = "terminate"
        new_payload["resolved_at"] = datetime.now(timezone.utc).isoformat()
        new_payload["resolved_by"] = actor
        new_payload["kill_audit_id"] = kill_result["audit_id"]
        flag.question_payload = new_payload
        await session.commit()
        await session.refresh(flag)
        flag_done_response.update(
            {
                "is_killed": True,
                "kill_audit_id": kill_result["audit_id"],
                "flag_completed_at": flag.completed_at,
            }
        )
        return flag_done_response

    # Defensive — should be unreachable given the action allowlist check above.
    raise HTTPException(
        status_code=500,
        detail=f"resolve_flag: unhandled action {action!r}",
    )


async def _flag_done_and_unpause(
    *,
    session: AsyncSession,
    flag: Task,
    actor: str,
    audit_reason: str,
    extra_drain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inline atomic flag-DONE + unpause-project for continue / adjust_continue.

    Mirrors unpause_project's logic but stays in the caller's transaction
    (single commit at end). Writes ONE projects_audit row with the supplied
    `audit_reason` so the resolve cause is preserved.
    """
    project_id = flag.project_id
    project = await _get_active_project_or_404(session, project_id)

    if not project.is_paused:
        # Flag was opened against a project that subsequently got unpaused
        # out of band. Treat as "flag is stale" — close it but don't write
        # a duplicate unpause audit row.
        flag.process_status = 5
        flag.completed_at = datetime.now(timezone.utc)
        payload = dict(flag.question_payload or {})
        payload["resolved_action"] = audit_reason
        payload["resolved_at"] = datetime.now(timezone.utc).isoformat()
        payload["resolved_by"] = actor
        payload["note"] = "project already unpaused at resolve time"
        flag.question_payload = payload
        await session.commit()
        await session.refresh(flag)
        return {
            "is_paused": False,
            "flag_completed_at": flag.completed_at,
            "audit_id": None,
            "stale": True,
        }

    now = datetime.now(timezone.utc)

    # Recompute recurrence (mirrors unpause_project).
    recurring_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.recurrence_rule.is_not(None),
        Task.next_fire_at.is_(None),
    )
    recurring_rows = list((await session.execute(recurring_stmt)).scalars().all())
    for row in recurring_rows:
        row.next_fire_at = next_cron_fire(
            row.recurrence_rule, row.recurrence_timezone, anchor=now
        )
    resumed_recurring = len(recurring_rows)

    # Clear kill_frozen markers.
    frozen_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.kill_frozen.is_(True),
    )
    frozen_rows = list((await session.execute(frozen_stmt)).scalars().all())
    for row in frozen_rows:
        row.kill_frozen = False
    unfrozen_tasks = len(frozen_rows)

    # Flip project state. Preserve paused_at + paused_reason as history (D4).
    project.is_paused = False

    drain_summary: dict[str, Any] = {
        "resumed_recurring": resumed_recurring,
        "unfrozen_tasks": unfrozen_tasks,
        "paused_at_at_unpause": (
            project.paused_at.isoformat() if project.paused_at else None
        ),
    }
    if extra_drain:
        drain_summary.update(extra_drain)

    audit = ProjectsAudit(
        project_id=project_id,
        actor=actor,
        action="unpause",
        reason=audit_reason,
        drain_summary=drain_summary,
    )
    session.add(audit)

    # Flag DONE + annotation.
    flag.process_status = 5
    flag.completed_at = datetime.now(timezone.utc)
    payload = dict(flag.question_payload or {})
    payload["resolved_action"] = audit_reason
    payload["resolved_at"] = datetime.now(timezone.utc).isoformat()
    payload["resolved_by"] = actor
    flag.question_payload = payload

    await session.commit()
    await session.refresh(flag)
    await session.refresh(audit)

    return {
        "is_paused": False,
        "flag_completed_at": flag.completed_at,
        "audit_id": audit.id,
        "drain_summary": drain_summary,
    }
