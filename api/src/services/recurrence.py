"""Recurrence subsystem (Kanban #707, T2).

Two fire paths run on every scheduler tick:

1. **Templates (#706 T1)** — `is_template=true AND next_fire_at <= now()`. Each
   match SPAWNS a child row (copy fields, set `is_template=false`,
   `spawned_from_task_id=<template.id>`, `process_status=1`) and advances the
   template's `next_fire_at` to the next future cron slot. Catch-up policy:
   single-fire on resume — if the scheduler was down for 3 days on a daily
   cron, ONE child is spawned and `next_fire_at` jumps to the next future slot
   (not 3 children).

2. **One-shots (#723)** — `scheduled_at <= now() AND process_status=1
   AND status=1 AND is_template=false`. Each match TRANSITIONS the existing
   row in place (Todo -> in_progress, stamp `started_at`, clear `scheduled_at`
   to NULL so a future ps->1 flip cannot re-fire it).

Both paths go through audit-trapped commits. The `tasks_audit_trg` is defined
`AFTER UPDATE OR DELETE ON tasks` (project-wide audit policy — INSERTs are not
audited until first mutation), so `tasks_history` captures the **UPDATE** on
the template advancing its `next_fire_at` (path A) and the **UPDATE** on the
existing row transitioning Todo -> in_progress (path B). Newly-INSERTed
children from path A do NOT generate `tasks_history` rows until their first
subsequent mutation. Direct SQL writes are forbidden by repo policy (see
CLAUDE.md "Raw SQL DML is human-only").

Scoped: V1 is single-process. Multi-replica deploys need a Redis lock or
pg-advisory lock — out of scope per #707 spec.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.sql import func

from src.constants import RecordStatus, TaskStatus
from src.models.task import Task

# Kanban #1125 (2026-05-17): L21 prevention — env-configurable default for
# max_active_children when the template's column is NULL. Picked at the
# fire-time tick (not import time) via os.environ.get so test monkeypatches
# can override per-test.
_MAX_ACTIVE_CHILDREN_DEFAULT_FALLBACK = 100

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def next_cron_fire(
    rule: str, tz: str, anchor: datetime | None = None
) -> datetime:
    """Compute the next cron fire time AFTER `anchor` (default now).

    Returns a timezone-aware datetime in UTC. `tz` controls the cron interpretation
    (e.g., '0 9 * * MON' in 'Asia/Bangkok' fires at 09:00 Bangkok — converted to UTC
    on return). Pure function; safe to call from validators / routers / scheduler.
    """
    zone = ZoneInfo(tz)
    base = anchor.astimezone(zone) if anchor is not None else datetime.now(zone)
    nxt = croniter(rule, base).get_next(datetime)
    # croniter returns the same TZ as `base`. Normalize to UTC for consistency
    # with the rest of the API (next_fire_at column is timestamptz; we serialize
    # in UTC with trailing Z).
    return nxt.astimezone(timezone.utc)


async def fire_template(db: "AsyncSession", template: Task) -> Task | None:
    """Spawn a child row from `template` and advance its next_fire_at.

    Used by both the scheduler tick (Path A) and the manual `fire-now` endpoint.
    Audit trail: the child INSERT does NOT trigger `tasks_audit_trg` (defined
    `AFTER UPDATE OR DELETE ON tasks` only — project-wide audit policy skips
    INSERTs). Only the template's `next_fire_at` UPDATE is captured in
    `tasks_history`; the spawned child appears there once it is first mutated.

    Kanban #1125 (2026-05-17, L21 prevention): before spawning, count active
    children (status=ACTIVE AND spawned_from_task_id=template.id AND
    process_status NOT IN (DONE, CANCELLED — terminal states)). If the count
    has reached the cap (per-template `max_active_children` if non-null, else
    env `MAX_ACTIVE_CHILDREN_DEFAULT`, fallback 100), HALT the template
    (process_status=BLOCKED, halt_reason='max_active_children_reached',
    status_change_reason set with the resolved cap + count for operator
    debugging) and return None — NO child is spawned this tick. Operator
    must resolve open children (mark them DONE / CANCELLED) or raise
    `max_active_children` to resume. The next scheduler tick re-evaluates:
    once the template is BLOCKED its `next_fire_at` is never visited again
    (no Path-A filter on process_status, so a BLOCKED template with a stale
    next_fire_at <= now() would still be picked up — but the cap check at
    the TOP of fire_template re-fires and halts again, which is a no-op
    UPDATE on an already-BLOCKED row). Once operator un-halts (PATCH
    process_status back to TODO + clear halt_reason), normal spawn resumes.
    """
    # L15 (#1122) per-template auto-headless confirmation gate — runs FIRST
    # because it's a static prerequisite (no DB I/O) and a refusal here is
    # idempotent / state-preserving (returns None without halting the
    # template, so the next tick re-evaluates after operator confirmation).
    # Contrast with L21 (cap) below, which DOES mutate template state on
    # refusal — we want to avoid that state mutation when an earlier
    # cheaper gate would have refused anyway.
    if (
        template.run_mode == "auto_headless"
        and template.template_auto_run_confirmed_at is None
    ):
        logger.warning(
            "recurrence.fire_template: REFUSING to spawn child of "
            "template_id=%d — auto_headless template without per-template "
            "confirmation (L15, Kanban #1122). Spawn skipped; "
            "next_fire_at not advanced. POST "
            "/api/tasks/%d/confirm-template-auto-run to confirm.",
            template.id,
            template.id,
        )
        return None

    # L21 cap gate — runs BEFORE the child INSERT so we don't leak a half-spawn.
    cap_env = os.environ.get("MAX_ACTIVE_CHILDREN_DEFAULT")
    cap_default = int(cap_env) if cap_env else _MAX_ACTIVE_CHILDREN_DEFAULT_FALLBACK
    cap = template.max_active_children if template.max_active_children is not None else cap_default

    # Count active children: same project, spawned by this template, soft-active,
    # process_status NOT in terminal states (DONE / CANCELLED). REVIEW / BLOCKED
    # / IN_PROGRESS / TODO all count as "active children clogging the queue".
    active_count = (
        await db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.spawned_from_task_id == template.id,
                Task.status == RecordStatus.ACTIVE,
                Task.process_status.notin_((TaskStatus.DONE, TaskStatus.CANCELLED)),
            )
        )
    ).scalar_one()

    if active_count >= cap:
        logger.warning(
            "recurrence.fire_template: template_id=%d has %d active children "
            "(cap=%d) — halting template (no child spawned)",
            template.id,
            active_count,
            cap,
        )
        template.process_status = TaskStatus.BLOCKED
        template.halt_reason = "max_active_children_reached"
        template.status_change_reason = (
            f"recurrence template halted: {active_count} active children "
            f"reached cap {cap}. Resolve open children or raise "
            "max_active_children to resume."
        )
        template.updated_at = func.now()
        await db.commit()
        await db.refresh(template)
        return None

    child = Task(
        project_id=template.project_id,
        parent_task_id=template.parent_task_id,
        title=template.title,
        description=template.description,
        priority=template.priority,
        assigned_role=template.assigned_role,
        run_mode=template.run_mode,
        task_kind=template.task_kind,
        process_status=TaskStatus.TODO,
        is_template=False,
        spawned_from_task_id=template.id,
        # Recurrence metadata is not copied — children are concrete tasks.
        recurrence_rule=None,
        recurrence_timezone="UTC",
        next_fire_at=None,
    )
    db.add(child)

    # Advance template's next_fire_at. Use UTC-now as the anchor so a missed
    # window collapses to a single fire (catch-up = single-fire-on-resume).
    template.next_fire_at = next_cron_fire(
        template.recurrence_rule or "",
        template.recurrence_timezone or "UTC",
    )
    template.updated_at = func.now()

    await db.commit()
    await db.refresh(child)
    await db.refresh(template)
    logger.info(
        "recurrence.fire_template template_id=%d -> child_id=%d "
        "next_fire_at=%s",
        template.id,
        child.id,
        template.next_fire_at.isoformat() if template.next_fire_at else None,
    )
    return child


async def fire_scheduled(db: "AsyncSession", task: Task) -> Task:
    """Path B: transition a one-shot scheduled task in place.

    process_status: 1 -> 2, stamp started_at, clear scheduled_at. Goes through
    SQLAlchemy ORM commit so the audit trigger captures the UPDATE. The resulting
    `tasks_history` row will show 'U' with the before/after snapshot.
    """
    task.process_status = TaskStatus.IN_PROGRESS
    if task.started_at is None:
        task.started_at = func.now()
    task.scheduled_at = None
    task.updated_at = func.now()
    await db.commit()
    await db.refresh(task)
    logger.info(
        "recurrence.fire_scheduled task_id=%d transitioned 1->2", task.id
    )
    return task


async def tick_once(
    session_factory: "async_sessionmaker[AsyncSession]",
    *,
    batch_limit: int = 50,
) -> dict[str, int]:
    """Run one scheduler iteration: handle both fire paths once. Idempotent.

    Returns a dict `{spawned, transitioned}` for caller observability. Each path
    runs in its own session so a failure in path A doesn't poison path B.
    """
    spawned = 0
    transitioned = 0

    # Path A: templates due
    async with session_factory() as db:
        stmt = (
            select(Task)
            .where(
                Task.is_template.is_(True),
                Task.status == RecordStatus.ACTIVE,
                Task.next_fire_at.is_not(None),
                Task.next_fire_at <= func.now(),
            )
            .order_by(Task.next_fire_at.asc())
            .limit(batch_limit)
        )
        result = await db.execute(stmt)
        templates = list(result.scalars().all())

        for tpl in templates:
            try:
                # L21 (#1125): fire_template returns None when the cap is hit —
                # template gets halted in-place, no child spawned. Don't count
                # halted ticks against `spawned` (it's an observability metric
                # for actual child creates; halts are logged separately).
                result = await fire_template(db, tpl)
                if result is not None:
                    spawned += 1
            except Exception:
                logger.exception(
                    "recurrence.tick_once: fire_template failed template_id=%d",
                    tpl.id,
                )
                await db.rollback()

    # Path B: one-shot scheduled tasks due
    async with session_factory() as db:
        stmt = (
            select(Task)
            .where(
                Task.scheduled_at.is_not(None),
                Task.scheduled_at <= func.now(),
                Task.process_status == TaskStatus.TODO,
                Task.status == RecordStatus.ACTIVE,
                Task.is_template.is_(False),
            )
            .order_by(Task.scheduled_at.asc())
            .limit(batch_limit)
        )
        result = await db.execute(stmt)
        rows = list(result.scalars().all())

        for row in rows:
            try:
                await fire_scheduled(db, row)
                transitioned += 1
            except Exception:
                logger.exception(
                    "recurrence.tick_once: fire_scheduled failed task_id=%d",
                    row.id,
                )
                await db.rollback()

    if spawned or transitioned:
        logger.info(
            "recurrence.tick_once: spawned=%d transitioned=%d",
            spawned,
            transitioned,
        )
    return {"spawned": spawned, "transitioned": transitioned}
