"""HITL aging nudge cron job (Kanban #1011).

Scans for interaction tasks (question / decision) in TODO or REVIEW that
have been waiting past a per-project threshold without user attention.
For each match it fires a web-push nudge via the existing #955 deliver()
pipeline, then updates tasks.last_nudge_at to now() — preventing re-fire
within 24 hours (dedup predicate).

Design decisions (locked by Lead spawn brief):

  event_kind="hitl_needed"   — reuses the existing kinds_enabled key from
  slice 955.A; operators use the same toggle to control both initial HITL
  pushes AND aging nudges. No new event kind.

  kind="web_push"            — same adapter path as PATCH-triggered HITL
  pushes in the tasks router.

  last_nudge_at ALWAYS updated after deliver() regardless of outcome —
  AC4 lock: a task that has no subscriptions or all-410 adapters would
  otherwise be retried every 30 minutes indefinitely (noisy). A failed
  deliver still bumps the column; operator re-queues by clearing the column.

  LIMIT 100 per tick — pace large backlogs so a single tick cannot
  overwhelm the push adapter. The next tick picks up the next batch.

Scheduler integration: `schedule_nudge_job(scheduler)` is called from the
FastAPI lifespan in main.py with the existing AsyncIOScheduler instance —
no parallel scheduler.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, text, update
from sqlalchemy.sql import func

from src.constants import RecordStatus, TaskInteractionKind, TaskStatus

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# Maximum tasks processed per scan tick — prevents a single tick from
# overwhelming the push adapter when a large backlog accumulates.
_NUDGE_BATCH_LIMIT = 100

# Age threshold for the dedup predicate in hours.
_NUDGE_DEDUP_HOURS = 24


async def scan_and_nudge(session: AsyncSession) -> int:
    """Query aged interaction tasks and fire one push nudge per task.

    Returns the count of tasks where nudge was attempted (deliver called).
    Caller is responsible for closing the session.

    Query contract (AC1):
      - tasks.status = 1 (active)
      - interaction_kind IN ('question', 'decision')
      - process_status IN (1, 3, 4) — TODO, REVIEW, BLOCKED
        Note: BLOCKED is included because a HITL question/decision task
        awaiting an answer is set to BLOCKED by the langgraph interrupt
        (langgraph/nodes.py:31). The blocked_by IS NULL predicate below
        ensures we only match the question task itself, not dependent work
        tasks blocked *by* the question. (#2426)
      - blocked_by IS NULL
      - nudge_disabled = false
      - projects.hitl_nudge_threshold_hours IS NOT NULL AND > 0
      - projects.is_killed = false, is_paused = false, status = 1
      - now() - created_at > (threshold * '1 hour')
      - last_nudge_at IS NULL OR last_nudge_at < now() - 24h
    """
    from src.models.project import Project
    from src.models.task import Task
    from src.services.notification_router import deliver

    now = datetime.now(timezone.utc)

    stmt = (
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(
            Task.status == RecordStatus.ACTIVE,
            Task.interaction_kind.in_(
                [TaskInteractionKind.QUESTION, TaskInteractionKind.DECISION]
            ),
            Task.process_status.in_([TaskStatus.TODO, TaskStatus.REVIEW, TaskStatus.BLOCKED]),
            Task.blocked_by.is_(None),
            Task.nudge_disabled.is_(False),
            Project.hitl_nudge_threshold_hours.isnot(None),
            Project.hitl_nudge_threshold_hours > 0,
            Project.is_killed.is_(False),
            Project.is_paused.is_(False),
            Project.status == RecordStatus.ACTIVE,
            # Age past threshold
            func.now() - Task.created_at
            > text(
                "(SELECT hitl_nudge_threshold_hours FROM projects WHERE id = tasks.project_id)"
                " * interval '1 hour'"
            ),
            # Dedup: never nudged OR last nudge was > 24h ago
            (
                Task.last_nudge_at.is_(None)
                | (Task.last_nudge_at < text(f"now() - interval '{_NUDGE_DEDUP_HOURS} hours'"))
            ),
        )
        .order_by(Task.created_at.asc())
        .limit(_NUDGE_BATCH_LIMIT)
    )

    result = await session.execute(stmt)
    tasks = result.scalars().all()

    if not tasks:
        return 0

    attempted = 0
    for task in tasks:
        # Compute age in hours for the push body.
        age_hours = int((now - task.created_at.replace(tzinfo=timezone.utc)).total_seconds() // 3600)

        payload = {
            "title": f"HITL nudge: {task.title}",
            "body": f"Open for ~{age_hours}h — your attention is needed",
            "url": f"/tasks/{task.id}",
        }

        try:
            await deliver(
                task_id=task.id,
                payload=payload,
                kind="web_push",
                session=session,
                event_kind="hitl_needed",
            )
        except Exception:
            # deliver() commits internally on success; on exception (e.g. task
            # was deleted between query and delivery) log and continue. We still
            # bump last_nudge_at below — AC4 lock.
            logger.warning(
                "hitl_nudge: deliver() raised for task_id=%d — bumping last_nudge_at anyway",
                task.id,
                exc_info=True,
            )

        # AC4 lock: ALWAYS update last_nudge_at after the deliver call, regardless
        # of outcome, so the same task is NOT retried in the next 24h.
        # Use a direct UPDATE rather than session.add() on the task ORM object
        # because deliver() committed above (which expires in-memory ORM state).
        await session.execute(
            update(type(task))
            .where(type(task).id == task.id)
            .values(last_nudge_at=func.now())
        )
        await session.commit()
        attempted += 1

    logger.info("hitl_nudge: scan_and_nudge attempted %d nudges", attempted)
    return attempted


async def _nudge_tick() -> None:
    """APScheduler wrapper — opens its own session via SessionLocal.

    Mirrors the pattern in main.py `_recurrence_tick()`: lazy-import,
    own session lifecycle, catch-all exception guard so APScheduler
    never drops the job silently.
    """
    from src.db import SessionLocal

    try:
        async with SessionLocal() as session:
            await scan_and_nudge(session)
    except Exception:
        logger.exception("hitl_nudge: _nudge_tick unhandled error")


def schedule_nudge_job(scheduler: "AsyncIOScheduler") -> None:
    """Register the nudge cron job into an existing AsyncIOScheduler.

    Called from main.py lifespan startup AFTER the scheduler is created
    but BEFORE scheduler.start(). The interval comes from
    settings.hitl_nudge_interval_minutes (env: HITL_NUDGE_INTERVAL_MINUTES,
    default 30, range 5..240).
    """
    from apscheduler.triggers.interval import IntervalTrigger

    interval_minutes = int(
        os.environ.get("HITL_NUDGE_INTERVAL_MINUTES", "30")
    )
    # Clamp to the valid range (mirrors the Pydantic Field ge=5, le=240) so a
    # bad env value doesn't crash the scheduler registration.
    interval_minutes = max(5, min(240, interval_minutes))

    scheduler.add_job(
        _nudge_tick,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="hitl_nudge_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "hitl_nudge: job registered — every %d minutes (job_id=hitl_nudge_tick)",
        interval_minutes,
    )
