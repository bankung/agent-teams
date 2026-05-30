"""GOV1 hard kill switch service (Kanban #1209).

Two entry points:
- `kill_project(project_id, reason, force, actor)` — operator emergency-stop.
- `revive_project(project_id, actor)` — inverse: clear is_killed, resume drain.

Both:
- Are idempotent in the "already-in-target-state" sense — 2nd kill on a killed
  project / revive on a non-killed project raises HTTPException 409 with a
  detail referencing the current state. Callers (UI / tests) treat 409 as a
  benign "no-op" signal.
- Write exactly one `projects_audit` row per successful action with the full
  drain_summary captured at action time.
- Run in a single transaction — the row mutations + the audit row commit
  together. A failure mid-drain rolls back the whole thing (no partially-killed
  state).

Drain semantics (kill):
  (a) recurring tasks → set `next_fire_at = NULL` (preserve `recurrence_rule`
      for revive to recompute).
  (b) in-flight (IN_PROGRESS) tasks → set `kill_frozen=true` + stamp
      `status_change_reason` so the langgraph worker (which polls) sees the
      kill state and self-checkpoints. v1 best-effort signal-via-marker;
      full langgraph integration is a followup (out of scope this slice).
  (c) new task POSTs blocked — enforced at the router layer
      (`routers/tasks.py::create_task`), not here.
  (d) new Agent spawns blocked — enforced at the PreToolUse hook layer
      (D6 — design pending; out of scope this slice).
  (e) open TODO tasks → set `kill_frozen=true`. Frozen-in-place, NOT archived
      (D3 — "ค้างไว้แบบไหน กลับมาแบบนั้น").

Revive semantics:
  - is_killed=false (CLEAR); killed_at + killed_reason PRESERVED as history.
  - All `kill_frozen=true` rows in the project → clear to false.
  - Recurring tasks (recurrence_rule IS NOT NULL AND next_fire_at IS NULL) →
    recompute `next_fire_at` via `next_cron_fire(rule, tz)`. Staleness gate:
    templates whose last-fire (approximated by killed_at) is older than
    `REVIVE_MAX_STALENESS_DAYS` (7) get a `halt_reason='revive_stale'` stamp
    instead of auto-resume — operator must explicitly re-arm.

`REVIVE_MAX_STALENESS_DAYS` lives as a module constant for v1; promotion to
per-project env / config is a followup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskStatus
from src.db import get_active_project_or_404
from src.models.project import Project
from src.models.projects_audit import ProjectsAudit
from src.models.task import Task
from src.services.recurrence import next_cron_fire

logger = logging.getLogger(__name__)


# v1 default — promote to per-project env / config in a followup. 7 days
# matches AC#5 spec ("revive_max_staleness_days=7; older = require manual
# restart"). Module-level so tests can monkeypatch.
REVIVE_MAX_STALENESS_DAYS = 7


# Source-text-locked detail strings (#122 pattern). Pin in router-side tests
# if the wire contract needs to stay stable across releases.
_DETAIL_ALREADY_KILLED = (
    "Project {project_id} is already killed (since {killed_at}). "
    "POST /api/projects/{project_id}/revive to undo."
)
_DETAIL_NOT_KILLED = (
    "Project {project_id} is not killed; revive is a no-op. "
    "POST /api/projects/{project_id}/kill to suspend."
)


async def kill_project(
    *,
    project_id: int,
    reason: str,
    force: bool = False,
    actor: str = "operator",
    session: AsyncSession,
) -> dict[str, Any]:
    """Hard-pause a project. Drain semantics per the module docstring.

    Returns a dict with kill outcome + drain_summary. Raises 404 if the
    project does not exist / is soft-deleted; 409 if already killed.

    `force=True` is reserved for the AC#6 emergency path (skip the 30s grace
    on in-flight langgraph runs). v1 implementation treats both modes
    identically for the marker write — the langgraph worker contract is the
    place where grace lives, not here. The `force` value is captured into
    drain_summary so the audit row reflects which path the operator took.
    """
    project = await _get_active_project_or_404(session, project_id)

    if project.is_killed:
        raise HTTPException(
            status_code=409,
            detail=_DETAIL_ALREADY_KILLED.format(
                project_id=project_id, killed_at=project.killed_at
            ),
        )

    now = datetime.now(timezone.utc)

    # ---- (a) suspend recurring -----------------------------------------------
    # SELECT every recurring task in the project. Two semantically distinct
    # subsets here, both treated as "suspended" in drain_summary:
    #
    #   - Non-template rows with recurrence_rule (rare in v1 — by convention
    #     recurrence_rule lives on templates per ck_tasks_template_recurrence_complete)
    #     → set next_fire_at = NULL. Revive recomputes from recurrence_rule.
    #   - Template rows (is_template=true) → CANNOT null next_fire_at without
    #     violating ck_tasks_template_recurrence_complete. Instead mark
    #     kill_frozen=true on the template. The scheduler integration to honor
    #     kill_frozen on templates is followup work (Kanban followup for the
    #     PreToolUse hook spawn). v1 fallback: the project-level is_killed=true
    #     check fires at the next service-level scheduler tick (TODO) — for now
    #     the marker is the signal.
    #
    # status=1 (not soft-deleted).
    recurring_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.recurrence_rule.is_not(None),
        Task.next_fire_at.is_not(None),
    )
    recurring_rows = list((await session.execute(recurring_stmt)).scalars().all())
    for row in recurring_rows:
        if row.is_template:
            # Mark via kill_frozen; do NOT clear next_fire_at (CHECK
            # ck_tasks_template_recurrence_complete forbids the null).
            # The follow-up scheduler integration honors kill_frozen.
            row.kill_frozen = True
        else:
            row.next_fire_at = None
    recurring_suspended = len(recurring_rows)

    # ---- (b) in-flight langgraph (marker write) ------------------------------
    # IN_PROGRESS tasks get kill_frozen=true + a status_change_reason note so
    # the langgraph worker's next poll sees the kill state. v1 best-effort.
    in_flight_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.process_status == TaskStatus.IN_PROGRESS,
        Task.kill_frozen.is_(False),
    )
    in_flight_rows = list((await session.execute(in_flight_stmt)).scalars().all())
    for row in in_flight_rows:
        row.kill_frozen = True
        row.status_change_reason = (
            f"GOV1 kill: graceful checkpoint requested (force={force})"
        )
    in_flight_marked = len(in_flight_rows)

    # ---- (e) open TODO tasks → freeze-in-place ------------------------------
    # Skip rows already covered by (b). TODO + BLOCKED rows that are not yet
    # frozen. (REVIEW is omitted — review-stage tasks are operator-facing,
    # not worker-facing; freezing them doesn't change behavior since the
    # auto-pickup gate already won't touch them.)
    open_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.process_status.in_([TaskStatus.TODO, TaskStatus.BLOCKED]),
        Task.kill_frozen.is_(False),
    )
    open_rows = list((await session.execute(open_stmt)).scalars().all())
    for row in open_rows:
        row.kill_frozen = True
    frozen_tasks = len(open_rows)

    # ---- flip the project state ---------------------------------------------
    project.is_killed = True
    project.killed_at = now
    project.killed_reason = reason

    # ---- audit row -----------------------------------------------------------
    drain_summary: dict[str, Any] = {
        "recurring_suspended": recurring_suspended,
        "in_flight_marked": in_flight_marked,
        "frozen_tasks": frozen_tasks,
        # (c) + (d) live at the router / hook layer; surface that they are
        # active so the audit row is honest about which gates ARE expected
        # to be enforcing this kill.
        "router_gate_active": True,
        "spawn_hook_gate_pending": True,
        "force": force,
    }
    audit = ProjectsAudit(
        project_id=project_id,
        actor=actor,
        action="kill",
        reason=reason,
        drain_summary=drain_summary,
    )
    session.add(audit)

    await session.commit()
    await session.refresh(project)
    await session.refresh(audit)

    logger.info(
        "kill_project: project_id=%d actor=%s force=%s drain=%s",
        project_id,
        actor,
        force,
        drain_summary,
    )

    return {
        "success": True,
        "project_id": project_id,
        "action": "kill",
        "is_killed": True,
        "killed_at": project.killed_at,
        "killed_reason": project.killed_reason,
        "drain_summary": drain_summary,
        "audit_id": audit.id,
    }


async def revive_project(
    *,
    project_id: int,
    actor: str = "operator",
    session: AsyncSession,
) -> dict[str, Any]:
    """Inverse of kill_project. Restore the project to a runnable state.

    Returns a dict with revive outcome + drain_summary (resumed counts).
    Raises 404 if the project does not exist / is soft-deleted; 409 if the
    project is not currently killed.

    Preserves `killed_at` + `killed_reason` as historical signal — revive
    does NOT erase the kill record (D4). Only clears `is_killed=false` +
    the per-task `kill_frozen` markers.

    Recurring tasks are re-armed by recomputing `next_fire_at` from the
    preserved `recurrence_rule`. Tasks where the project has been killed
    longer than `REVIVE_MAX_STALENESS_DAYS` (default 7) get a
    `halt_reason='revive_stale'` stamp instead of auto-resume — operator
    must explicitly re-arm via the existing manual-template path.
    """
    project = await _get_active_project_or_404(session, project_id)

    if not project.is_killed:
        raise HTTPException(
            status_code=409,
            detail=_DETAIL_NOT_KILLED.format(project_id=project_id),
        )

    now = datetime.now(timezone.utc)
    staleness_cutoff = now - timedelta(days=REVIVE_MAX_STALENESS_DAYS)
    stale = project.killed_at is not None and project.killed_at < staleness_cutoff

    # ---- recompute next_fire_at on recurring tasks --------------------------
    # Re-arm only the tasks the kill suspended (recurrence_rule set,
    # next_fire_at currently NULL). Staleness gate fires per-task because the
    # operator might revive into a stale window and we'd rather halt + ask
    # than fire 7 days of catch-up cron slots silently. (Recurrence catch-up
    # policy is single-fire per #707, but the operator should still consent
    # to that resume after a long pause.)
    recurring_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.recurrence_rule.is_not(None),
        Task.next_fire_at.is_(None),
    )
    recurring_rows = list((await session.execute(recurring_stmt)).scalars().all())
    resumed_recurring = 0
    halted_stale = 0
    for row in recurring_rows:
        if stale:
            # Stale revive: halt the template rather than auto-fire.
            row.halt_reason = "revive_stale"
            row.status_change_reason = (
                f"GOV1 revive: project killed > {REVIVE_MAX_STALENESS_DAYS} "
                f"days; manual re-arm required"
            )
            halted_stale += 1
        else:
            row.next_fire_at = next_cron_fire(
                row.recurrence_rule, row.recurrence_timezone, anchor=now
            )
            resumed_recurring += 1

    # ---- unfreeze every kill_frozen row in the project ----------------------
    # NOTE (dev-reviewer P1-3 audit on #1209): unfrozen_tasks on revive is a
    # superset of the kill's `frozen_tasks` counter. The kill split its
    # accounting into three buckets (recurring_suspended, in_flight_marked,
    # frozen_tasks) but only the open-TODO bucket got `frozen_tasks`-named;
    # template kills (counted in recurring_suspended) and in-flight marks
    # (counted in in_flight_marked) ALSO set kill_frozen=true. Revive sweeps
    # every kill_frozen=true row in one query, so unfrozen_tasks may exceed
    # the original frozen_tasks at kill time — by design. Audit consumers
    # (project-auditor #1210) should NOT assume frozen_tasks == unfrozen_tasks.
    frozen_stmt = select(Task).where(
        Task.project_id == project_id,
        Task.status == RecordStatus.ACTIVE,
        Task.kill_frozen.is_(True),
    )
    frozen_rows = list((await session.execute(frozen_stmt)).scalars().all())
    for row in frozen_rows:
        row.kill_frozen = False
    unfrozen_tasks = len(frozen_rows)

    # ---- flip the project state ---------------------------------------------
    # PRESERVE killed_at + killed_reason — D4: keep the historical kill record.
    project.is_killed = False

    # ---- audit row -----------------------------------------------------------
    drain_summary: dict[str, Any] = {
        "resumed_recurring": resumed_recurring,
        "halted_stale": halted_stale,
        "unfrozen_tasks": unfrozen_tasks,
        "killed_at_at_revive": project.killed_at.isoformat()
        if project.killed_at
        else None,
        "stale_revive": stale,
    }
    audit = ProjectsAudit(
        project_id=project_id,
        actor=actor,
        action="revive",
        reason=None,
        drain_summary=drain_summary,
    )
    session.add(audit)

    await session.commit()
    await session.refresh(project)
    await session.refresh(audit)

    logger.info(
        "revive_project: project_id=%d actor=%s stale=%s drain=%s",
        project_id,
        actor,
        stale,
        drain_summary,
    )

    return {
        "success": True,
        "project_id": project_id,
        "action": "revive",
        "is_killed": False,
        "killed_at": project.killed_at,
        "killed_reason": project.killed_reason,
        "drain_summary": drain_summary,
        "audit_id": audit.id,
    }


# Backward-compat alias — pause_switch.py imports this name from kill_switch.
# The implementation now lives in db.get_active_project_or_404 (Kanban #1682).
async def _get_active_project_or_404(
    session: AsyncSession, project_id: int
) -> Project:
    """Fetch the project row; 404 on missing OR soft-deleted.

    Delegates to db.get_active_project_or_404 — kept here so pause_switch.py
    can continue to import it from this module without change.
    """
    return await get_active_project_or_404(session, project_id)
