"""Audit-archive sweep (Kanban #1240).

A daily APScheduler job that flips `tasks.is_active=false` on COMPLETED audit
tasks whose `completed_at` is older than a configurable TTL — so the board /
list endpoints stop surfacing stale governance-audit rows.

Query contract (AC1 / AC2 / AC3):
  - task_type = 'audit'
  - completed_at IS NOT NULL AND completed_at < now() - INTERVAL '<TTL> days'
  - tasks.status = 1 (active soft-delete) — never re-touch deleted rows
  - is_active = true — only flip rows that are currently visible (idempotent;
    a second tick is a no-op)
  - the task's project has audit_enabled != false — projects that opted OUT of
    governance audits are SKIPPED (AC3). Done via a join on projects so the
    skip is enforced in SQL, not post-filtered in Python.

TTL (AC2): `AUDIT_ARCHIVE_DAYS` env var (default 30). Read via os.environ at
tick time (mirrors recurrence / hitl_nudge) so a .env change / test
monkeypatch applies without depending on a cached Settings singleton.

Logging (AC4): one INFO line per project with a non-zero archived count, plus
a final summary line with the total archived + total cycle time (ms). The
returned dict surfaces the same data for caller observability + tests.

Audit trail: the UPDATE goes through the SQLAlchemy ORM so the existing
`tasks_audit_trg` (AFTER UPDATE ON tasks) captures each flip as a
`tasks_history` row (operation 'U'). Direct SQL DML is forbidden by repo
policy (CLAUDE.md "Raw SQL DML is human-only") — this is service-layer ORM,
which the audit policy explicitly allows.

Scheduler integration: `schedule_audit_archive_job(scheduler)` registers a
daily cron job into the existing AsyncIOScheduler from the FastAPI lifespan in
main.py — no parallel scheduler (mirrors hitl_nudge.schedule_nudge_job).

Scoped: V1 is single-process (same caveat as recurrence). Multi-replica
deploys would need a pg-advisory lock — out of scope for this slice.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from src.constants import RecordStatus, TaskType
from src.models.project import Project
from src.models.task import Task

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# Fallback TTL when AUDIT_ARCHIVE_DAYS is unset. Mirrors Settings.audit_archive_days.
_AUDIT_ARCHIVE_DAYS_FALLBACK = 30

# Daily cron — 03:30 UTC (off the recurrence tick + the typical nightly backup
# window). The exact minute is unimportant; the sweep is idempotent.
_AUDIT_ARCHIVE_CRON = "30 3 * * *"


def _resolve_ttl_days() -> int:
    """Resolve the archive TTL (days) from AUDIT_ARCHIVE_DAYS at CALL time.

    Reads os.environ so a .env change / test monkeypatch applies without a
    cached Settings singleton (parity with recurrence's MAX_ACTIVE_CHILDREN
    handling). Falls back to 30; a non-positive / unparseable value is clamped
    to the fallback (a zero/negative TTL would archive same-day completions).
    """
    raw = os.environ.get("AUDIT_ARCHIVE_DAYS")
    if not raw:
        return _AUDIT_ARCHIVE_DAYS_FALLBACK
    try:
        val = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "audit_archive: AUDIT_ARCHIVE_DAYS=%r is not an int — using %d",
            raw,
            _AUDIT_ARCHIVE_DAYS_FALLBACK,
        )
        return _AUDIT_ARCHIVE_DAYS_FALLBACK
    if val < 1:
        logger.warning(
            "audit_archive: AUDIT_ARCHIVE_DAYS=%d is < 1 — using %d",
            val,
            _AUDIT_ARCHIVE_DAYS_FALLBACK,
        )
        return _AUDIT_ARCHIVE_DAYS_FALLBACK
    return val


async def sweep_old_audit_tasks(session: "AsyncSession") -> dict:
    """Archive completed audit tasks older than the TTL. Idempotent.

    Returns a summary dict::

        {
          "ttl_days": int,
          "total_archived": int,
          "per_project": {project_id: count, ...},
          "cycle_ms": float,
        }

    Caller is responsible for closing the session. This function commits the
    UPDATE itself so the audit trigger fires and the row state is durable
    before returning the summary.
    """
    started = time.monotonic()
    ttl_days = _resolve_ttl_days()

    # Cutoff computed in SQL via now() - interval so the comparison happens
    # server-side (single source of "now", TZ-safe — completed_at is timestamptz).
    # func.make_interval(days=...) keeps the interval parametrized (no string
    # interpolation of the TTL into SQL).
    cutoff = func.now() - func.make_interval(0, 0, 0, ttl_days)

    # Candidate predicate (AC1/AC2/AC3). The audit_enabled skip (AC3) is a join
    # condition on projects: COALESCE so a NULL audit_enabled is treated as
    # "enabled" (the column is NOT NULL DEFAULT true, so NULL shouldn't occur —
    # COALESCE is defense-in-depth for legacy/hand-edited rows). is_active=true
    # filter makes the sweep idempotent (already-archived rows are skipped).
    candidate_where = (
        Task.task_type == TaskType.AUDIT,
        Task.status == RecordStatus.ACTIVE,
        Task.is_active.is_(True),
        Task.completed_at.is_not(None),
        Task.completed_at < cutoff,
    )

    # Per-project counts BEFORE the flip — for the AC4 per-project log lines.
    # Joins projects with audit_enabled != false so opted-out projects are
    # excluded from both the count AND the UPDATE below (same predicate).
    per_project_stmt = (
        select(Task.project_id, func.count())
        .join(Project, Project.id == Task.project_id)
        .where(
            *candidate_where,
            func.coalesce(Project.audit_enabled, True).is_(True),
            Project.status == RecordStatus.ACTIVE,
        )
        .group_by(Task.project_id)
    )
    rows = (await session.execute(per_project_stmt)).all()
    per_project: dict[int, int] = {pid: count for pid, count in rows}
    pre_flight_count = sum(per_project.values())

    if pre_flight_count == 0:
        cycle_ms = (time.monotonic() - started) * 1000
        logger.info(
            "audit_archive: sweep found 0 tasks to archive "
            "(ttl_days=%d, cycle_ms=%.1f)",
            ttl_days,
            cycle_ms,
        )
        return {
            "ttl_days": ttl_days,
            "total_archived": 0,
            "per_project": {},
            "cycle_ms": cycle_ms,
        }

    # The archive UPDATE. The project audit_enabled / status skip is expressed
    # as a correlated EXISTS so the bulk UPDATE matches EXACTLY the same set the
    # per-project count measured (a JOIN in an UPDATE is non-portable; EXISTS is
    # the standard SQLAlchemy idiom for "update rows whose related row matches").
    project_ok = (
        select(Project.id)
        .where(
            Project.id == Task.project_id,
            func.coalesce(Project.audit_enabled, True).is_(True),
            Project.status == RecordStatus.ACTIVE,
        )
        .exists()
    )
    upd = (
        update(Task)
        .where(*candidate_where, project_ok)
        .values(is_active=False, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(upd)
    await session.commit()

    # F-12: use UPDATE's authoritative rowcount — not the pre-flight SELECT sum
    # (which is a snapshot that can drift between the SELECT and the UPDATE).
    total_archived = result.rowcount

    cycle_ms = (time.monotonic() - started) * 1000

    # AC4: one line per project with a non-zero archived count.
    for pid, count in sorted(per_project.items()):
        logger.info(
            "audit_archive: project_id=%d archived %d audit task(s)",
            pid,
            count,
        )
    # AC4: total + cycle time summary.
    logger.info(
        "audit_archive: sweep complete — total_archived=%d "
        "projects=%d ttl_days=%d cycle_ms=%.1f",
        total_archived,
        len(per_project),
        ttl_days,
        cycle_ms,
    )

    return {
        "ttl_days": ttl_days,
        "total_archived": total_archived,
        "per_project": per_project,
        "cycle_ms": cycle_ms,
    }


async def _audit_archive_tick() -> None:
    """APScheduler wrapper — opens its own session via SessionLocal.

    Mirrors main.py `_recurrence_tick()` / hitl_nudge `_nudge_tick()`:
    lazy-import the session factory, own the session lifecycle, and a catch-all
    guard so APScheduler never silently drops the job on an unhandled error.
    """
    from src.db import SessionLocal

    try:
        async with SessionLocal() as session:
            await sweep_old_audit_tasks(session)
    except Exception:
        logger.exception("audit_archive: _audit_archive_tick unhandled error")


# FIND-03: 5-field cron pattern (minute hour dom month dow) — each field is
# one or more non-whitespace characters. This is a structural check; semantic
# errors (e.g. "99 * * * *") are caught by CronTrigger.from_crontab().
_CRON_5FIELD_RE = re.compile(r"^(\S+\s+){4}\S+$")


def _resolve_cron_rule() -> str:
    """Validate and return the AUDIT_ARCHIVE_CRON env value.

    Falls back to _AUDIT_ARCHIVE_CRON on missing, malformed, or semantically
    invalid (next-fire < 1 hour away) values, logging a warning each time.
    """
    from apscheduler.triggers.cron import CronTrigger
    from datetime import datetime, timezone

    raw = os.environ.get("AUDIT_ARCHIVE_CRON")
    if not raw:
        return _AUDIT_ARCHIVE_CRON

    if not _CRON_5FIELD_RE.match(raw.strip()):
        logger.warning(
            "audit_archive: AUDIT_ARCHIVE_CRON=%r is not a valid 5-field cron "
            "expression — falling back to default %r",
            raw,
            _AUDIT_ARCHIVE_CRON,
        )
        return _AUDIT_ARCHIVE_CRON

    # Sanity check: next fire must be at least 1 hour from now.
    try:
        trigger = CronTrigger.from_crontab(raw.strip(), timezone="UTC")
        next_fire = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
        if next_fire is None or (next_fire - datetime.now(timezone.utc)).total_seconds() < 3600:
            logger.warning(
                "audit_archive: AUDIT_ARCHIVE_CRON=%r fires in <1 h (next=%s) "
                "— falling back to default %r",
                raw,
                next_fire,
                _AUDIT_ARCHIVE_CRON,
            )
            return _AUDIT_ARCHIVE_CRON
    except Exception as exc:
        logger.warning(
            "audit_archive: AUDIT_ARCHIVE_CRON=%r failed validation (%s) "
            "— falling back to default %r",
            raw,
            exc,
            _AUDIT_ARCHIVE_CRON,
        )
        return _AUDIT_ARCHIVE_CRON

    return raw.strip()


def schedule_audit_archive_job(scheduler: "AsyncIOScheduler") -> None:
    """Register the daily audit-archive sweep into an existing AsyncIOScheduler.

    Called from main.py lifespan startup AFTER the scheduler is created but
    BEFORE scheduler.start() (mirrors hitl_nudge.schedule_nudge_job). Cron
    override via AUDIT_ARCHIVE_CRON env (default '30 3 * * *', UTC). The TTL
    itself (AUDIT_ARCHIVE_DAYS) is resolved per-tick inside the sweep, not here.
    """
    from apscheduler.triggers.cron import CronTrigger

    cron_rule = _resolve_cron_rule()
    scheduler.add_job(
        _audit_archive_tick,
        trigger=CronTrigger.from_crontab(cron_rule, timezone="UTC"),
        id="audit_archive_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "audit_archive: job registered — cron=%r tz=UTC (job_id=audit_archive_tick)",
        cron_rule,
    )
