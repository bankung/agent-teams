"""Periodic Health monitor sweep (Kanban #960).

Background `AsyncIOScheduler` job that runs every N minutes (default 15) and
inspects every active autorun task across every project for trouble signals.
Four detectors:

  1. STALE STATE     — process_status=IN_PROGRESS for longer than the stale
                       threshold without a halt_reason. Severity HIGH; auto-
                       pauses by flipping `run_mode='manual'` (the worker
                       skips manual tasks; checkpoint is preserved).

  2. REPEATED RETRIES — `audit_retry_count >= max_retry_cycles`. Severity
                        MEDIUM. Doesn't auto-pause (the auditor has its own
                        cap at 3; this fires when something deeper is wrong).

  3. TOKEN BURN WITHOUT PROGRESS
                     — tokens grow > token_burn_threshold_per_hour (scaled by
                       elapsed) AND the status_change_reason hash is
                       unchanged across sweeps. Severity MEDIUM. Needs cross-
                       sweep state — kept as an in-memory dict on the
                       HealthMonitor instance (resets on container restart;
                       acceptable for MVP).

  4. BURN RATE SPIKE — today's projected daily spend > 7-day average ×
                       `burn_spike_multiplier`. Severity LOW (warning only).

Severity actions:
  - LOW    → write health_alert JSONB only.
  - MEDIUM → write health_alert JSONB only.  Push delivery deferred — see
             `# TODO(#955): wire push here when web push lands`.
  - HIGH   → write health_alert JSONB AND PATCH `run_mode='manual'` via the
             public PATCH /api/tasks/{id} endpoint (preserves audit triggers
             + cross-table validators).

Per-task threshold resolution merges the project's `health_thresholds` JSONB
over the env defaults. `{enabled: false}` skips the project entirely.

Disable the whole subsystem by setting `HEALTH_MONITOR_DISABLED=1` in env;
otherwise the scheduler wires it up on lifespan boot (mirror of the recurrence
tick / backup pattern in `src.main`).

Audit trail: stdout via the standard `src.*` logger pattern. The single-column
`tasks.health_alert` JSONB is the latest snapshot; history flows through
`tasks_history` via the existing audit trigger.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import TaskRunMode, TaskStatus
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionRun
from src.models.task import Task

logger = logging.getLogger(__name__)


# -- Config -----------------------------------------------------------------


@dataclass
class HealthMonitorConfig:
    """Env-driven runtime config for the Health monitor sweep.

    Unlike `BackupConfig`, the monitor is ENABLED by default — set
    `HEALTH_MONITOR_DISABLED=1` (or `true`/`yes`) to skip scheduler wiring.
    The defaults are tuned for the agent-teams workload (autorun tasks
    typically complete inside an hour; a 4-hour silence is "something is
    wrong"; >50k tokens/hour on a stuck task is "burning money").

    `api_base` is the URL the monitor uses to call PATCH /api/tasks/{id} for
    the auto-pause path. Defaults to the in-container loopback. Tests pass
    a custom value (or skip the HTTP path entirely via dependency-injection).
    """

    is_enabled: bool = True
    interval_minutes: int = 15
    stale_hours: float = 4.0
    max_retry_cycles: int = 5
    token_burn_threshold_per_hour: int = 50_000
    burn_spike_multiplier: float = 2.0
    api_base: str = "http://localhost:8000"
    request_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> HealthMonitorConfig:
        e = env if env is not None else os.environ
        disabled = e.get("HEALTH_MONITOR_DISABLED", "").lower() in ("1", "true", "yes")
        return cls(
            is_enabled=not disabled,
            interval_minutes=int(e.get("HEALTH_MONITOR_INTERVAL_MINUTES", "15")),
            stale_hours=float(e.get("HEALTH_MONITOR_DEFAULT_STALE_HOURS", "4")),
            max_retry_cycles=int(e.get("HEALTH_MONITOR_DEFAULT_MAX_RETRIES", "5")),
            token_burn_threshold_per_hour=int(
                e.get("HEALTH_MONITOR_DEFAULT_TOKEN_BURN_PER_HOUR", "50000")
            ),
            burn_spike_multiplier=float(
                e.get("HEALTH_MONITOR_DEFAULT_BURN_SPIKE_MULTIPLIER", "2.0")
            ),
            api_base=e.get("HEALTH_MONITOR_API_BASE", "http://localhost:8000"),
            request_timeout_seconds=float(
                e.get("HEALTH_MONITOR_REQUEST_TIMEOUT_SECONDS", "10")
            ),
        )


# -- Threshold resolution ---------------------------------------------------


@dataclass
class ResolvedThresholds:
    """Per-project effective thresholds after merging project override over env."""

    enabled: bool
    stale_hours: float
    max_retry_cycles: int
    token_burn_threshold_per_hour: int
    burn_spike_multiplier: float

    @classmethod
    def merge(
        cls,
        defaults: HealthMonitorConfig,
        project_override: dict[str, Any] | None,
    ) -> ResolvedThresholds:
        """Apply project override (if any) over the env defaults.

        Unknown keys in the project override are silently ignored — same
        spirit as Pydantic's `extra="ignore"` on PATCH bodies. Wrong-typed
        values fall back to the default (defensive — a hand-edited JSONB
        shouldn't crash the sweep).
        """
        override = project_override or {}

        def _get(key: str, default, cast):
            if key not in override:
                return default
            try:
                return cast(override[key])
            except (TypeError, ValueError):
                logger.warning(
                    "health_monitor: bad project threshold %s=%r, using default",
                    key, override.get(key),
                )
                return default

        return cls(
            enabled=bool(override.get("enabled", True)),
            stale_hours=_get("stale_hours", defaults.stale_hours, float),
            max_retry_cycles=_get(
                "max_retry_cycles", defaults.max_retry_cycles, int
            ),
            token_burn_threshold_per_hour=_get(
                "token_burn_threshold_per_hour",
                defaults.token_burn_threshold_per_hour,
                int,
            ),
            burn_spike_multiplier=_get(
                "burn_spike_multiplier",
                defaults.burn_spike_multiplier,
                float,
            ),
        )


# -- Detector helpers -------------------------------------------------------


def _status_reason_hash(s: str | None) -> str:
    """Stable short hash of status_change_reason. None / '' → fixed sentinel."""
    if not s:
        return "_null"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


@dataclass
class _TaskBurnSnapshot:
    """In-memory snapshot kept across sweeps for the token-burn detector.

    Resets on container restart — acceptable for MVP per #960 scope. The
    detector silently degrades to "no signal yet" on the first sweep after
    a restart, which is the same behavior as a fresh task.
    """

    tokens: int
    reason_hash: str
    seen_at: datetime


def _detect_stale_state(
    task: Task, thresholds: ResolvedThresholds, now: datetime
) -> dict[str, Any] | None:
    """Detector 1 — HIGH severity, triggers auto-pause.

    process_status=IN_PROGRESS + updated_at older than stale_hours +
    no halt_reason → the worker is stuck without a recorded halt.
    """
    if task.process_status != TaskStatus.IN_PROGRESS:
        return None
    if task.halt_reason:
        return None
    cutoff = now - timedelta(hours=thresholds.stale_hours)
    if task.updated_at >= cutoff:
        return None
    return {
        "detector": "stale_state",
        "severity": "high",
        "evidence": {
            "updated_at": task.updated_at.isoformat(),
            "now": now.isoformat(),
            "idle_hours": round(
                (now - task.updated_at).total_seconds() / 3600.0, 2
            ),
        },
        "threshold_used": {"stale_hours": thresholds.stale_hours},
    }


def _detect_repeated_retries(
    task: Task, thresholds: ResolvedThresholds, now: datetime
) -> dict[str, Any] | None:
    """Detector 3 — MEDIUM severity. audit_retry_count >= cap on active task."""
    if task.process_status not in (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
        return None
    if task.audit_retry_count < thresholds.max_retry_cycles:
        return None
    return {
        "detector": "repeated_retries",
        "severity": "medium",
        "evidence": {
            "audit_retry_count": task.audit_retry_count,
        },
        "threshold_used": {"max_retry_cycles": thresholds.max_retry_cycles},
    }


def _detect_token_burn_without_progress(
    task: Task,
    thresholds: ResolvedThresholds,
    now: datetime,
    prev: _TaskBurnSnapshot | None,
) -> dict[str, Any] | None:
    """Detector 2 — MEDIUM severity. Cross-sweep state required.

    Returns (alert | None). The caller is responsible for stashing the
    fresh snapshot for the next sweep regardless of whether an alert fires.
    """
    if prev is None:
        # First sweep observing this task — no signal yet.
        return None
    if task.estimated_input_tokens is None:
        return None
    current_tokens = int(task.estimated_input_tokens or 0)
    delta_tokens = current_tokens - prev.tokens
    if delta_tokens <= 0:
        return None
    elapsed_seconds = (now - prev.seen_at).total_seconds()
    if elapsed_seconds <= 0:
        return None
    elapsed_hours = elapsed_seconds / 3600.0
    burn_per_hour = delta_tokens / elapsed_hours
    if burn_per_hour <= thresholds.token_burn_threshold_per_hour:
        return None
    current_hash = _status_reason_hash(task.status_change_reason)
    if current_hash != prev.reason_hash:
        # Progress was made (reason changed) — burn is acceptable.
        return None
    return {
        "detector": "token_burn_without_progress",
        "severity": "medium",
        "evidence": {
            "tokens_prev": prev.tokens,
            "tokens_now": current_tokens,
            "delta_tokens": delta_tokens,
            "elapsed_hours": round(elapsed_hours, 4),
            "burn_per_hour": round(burn_per_hour, 1),
            "reason_hash_stable": current_hash,
        },
        "threshold_used": {
            "token_burn_threshold_per_hour": (
                thresholds.token_burn_threshold_per_hour
            ),
        },
    }


def _detect_burn_rate_spike(
    task: Task,
    thresholds: ResolvedThresholds,
    now: datetime,
    project_7day_avg_daily_usd: Decimal | None,
    today_spend_usd: Decimal,
) -> dict[str, Any] | None:
    """Detector 4 — LOW severity (warning).

    `today_spend_usd` is the running daily total for the project (from the
    sweep's pre-fetched aggregate). `project_7day_avg_daily_usd` is the
    rolling baseline. If baseline is None / 0 (cold project), skip.

    Note: this fires per-task even though the signal is project-wide — every
    task in an over-budget project carries the same low-severity warning so
    the operator sees it on whatever task they look at. Cheap; the cost is
    one JSONB write per task.
    """
    if project_7day_avg_daily_usd is None or project_7day_avg_daily_usd <= 0:
        return None
    multiplier = Decimal(str(thresholds.burn_spike_multiplier))
    cap = project_7day_avg_daily_usd * multiplier
    if today_spend_usd <= cap:
        return None
    return {
        "detector": "burn_rate_spike",
        "severity": "low",
        "evidence": {
            "today_spend_usd": str(today_spend_usd),
            "baseline_7day_avg_daily_usd": str(project_7day_avg_daily_usd),
            "multiplier": str(multiplier),
            "cap_usd": str(cap),
        },
        "threshold_used": {
            "burn_spike_multiplier": thresholds.burn_spike_multiplier,
        },
    }


# -- Sweep orchestrator -----------------------------------------------------


@dataclass
class SweepMetrics:
    """Return shape for `run_sweep()` — observability counters."""

    checked: int = 0
    alerts_low: int = 0
    alerts_medium: int = 0
    alerts_high: int = 0
    auto_paused: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class HealthMonitor:
    """Holds cross-sweep state + dependencies (SessionLocal, HTTP client config).

    One instance lives for the lifetime of the api container. APScheduler
    invokes `run_sweep()` on its interval. The method is exception-safe at
    the top level — any failure logs + returns the metrics envelope without
    crashing the scheduler.
    """

    def __init__(
        self,
        cfg: HealthMonitorConfig,
        session_factory=None,
        http_client_factory=None,
    ):
        self.cfg = cfg
        # Lazy bind so tests can inject their own SessionLocal.
        if session_factory is None:
            from src.db import SessionLocal as _SessionLocal
            session_factory = _SessionLocal
        self._session_factory = session_factory
        # Lazy HTTP client factory — tests inject a mock that captures calls.
        self._http_client_factory = http_client_factory or self._default_http_client
        # Cross-sweep state for the token-burn detector.
        self._burn_snapshots: dict[int, _TaskBurnSnapshot] = {}

    def _default_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.cfg.api_base,
            timeout=self.cfg.request_timeout_seconds,
        )

    # -- Public entrypoint --------------------------------------------------

    async def run_sweep(self) -> dict[str, int]:
        """Run one sweep across all active autorun tasks. Safe inside scheduler.

        Returns the metrics dict (checked / alerts_low / alerts_medium /
        alerts_high / auto_paused).
        """
        metrics = SweepMetrics()
        try:
            await self._sweep_inner(metrics)
        except Exception:
            logger.exception("health_monitor.run_sweep: unhandled error")
        logger.info(
            "health_monitor.sweep checked=%d low=%d medium=%d high=%d auto_paused=%d",
            metrics.checked,
            metrics.alerts_low,
            metrics.alerts_medium,
            metrics.alerts_high,
            metrics.auto_paused,
        )
        return metrics.to_dict()

    # -- Internals ----------------------------------------------------------

    async def _sweep_inner(self, metrics: SweepMetrics) -> None:
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            # Pre-fetch the active autorun task set + the project map + the
            # per-project 7-day spend baseline + the per-project today spend
            # in three queries (avoids N+1).
            tasks = await self._fetch_active_autorun_tasks(session)
            if not tasks:
                return
            project_ids = sorted({t.project_id for t in tasks})
            projects = await self._fetch_projects(session, project_ids)
            baseline_by_project = await self._fetch_7day_baseline(
                session, project_ids, now
            )
            today_by_project = await self._fetch_today_spend(
                session, project_ids, now
            )

        # Collect tasks to auto-pause AFTER closing the read session, so the
        # PATCH calls go through the public HTTP endpoint (preserves audit
        # triggers + run_mode consent validators). Lead's rule: DB writes
        # through FastAPI, not direct SQL.
        auto_pause_ids: list[int] = []
        alert_writes: list[tuple[int, int, dict[str, Any]]] = []

        for task in tasks:
            metrics.checked += 1
            project = projects.get(task.project_id)
            if project is None:
                continue
            thresholds = ResolvedThresholds.merge(
                self.cfg, project.health_thresholds
            )
            if not thresholds.enabled:
                continue

            # Token-burn detector requires cross-sweep state — pull-and-replace.
            prev_snapshot = self._burn_snapshots.get(task.id)
            self._burn_snapshots[task.id] = _TaskBurnSnapshot(
                tokens=int(task.estimated_input_tokens or 0),
                reason_hash=_status_reason_hash(task.status_change_reason),
                seen_at=now,
            )

            alert = self._evaluate_detectors(
                task,
                thresholds,
                now,
                prev_snapshot,
                baseline_by_project.get(task.project_id),
                today_by_project.get(task.project_id, Decimal("0")),
            )
            if alert is None:
                continue
            alert["alerted_at"] = now.isoformat()
            severity = alert["severity"]
            if severity == "low":
                metrics.alerts_low += 1
            elif severity == "medium":
                metrics.alerts_medium += 1
                # TODO(#955): wire push delivery here when web push lands.
            elif severity == "high":
                metrics.alerts_high += 1
                auto_pause_ids.append(task.id)

            alert_writes.append((task.id, task.project_id, alert))

        # Write alerts via PATCH so the audit trigger sees the change. For
        # high-severity tasks we bundle `run_mode='manual'` into the same
        # PATCH — single round-trip per task, atomic at the DB layer.
        if alert_writes:
            await self._flush_alerts(alert_writes, auto_pause_ids, metrics)

    def _evaluate_detectors(
        self,
        task: Task,
        thresholds: ResolvedThresholds,
        now: datetime,
        prev_snapshot: _TaskBurnSnapshot | None,
        project_baseline: Decimal | None,
        project_today: Decimal,
    ) -> dict[str, Any] | None:
        """Run detectors in priority order; the FIRST hit wins.

        Priority (highest severity / most actionable first):
          1. stale_state (HIGH — auto-pause)
          2. repeated_retries (MEDIUM)
          3. token_burn_without_progress (MEDIUM)
          4. burn_rate_spike (LOW)

        Single-alert-per-task keeps the wire surface simple — the UI shows
        the most-urgent thing first; once cleared, the next sweep picks up
        any remaining signal.
        """
        for detector in (
            lambda: _detect_stale_state(task, thresholds, now),
            lambda: _detect_repeated_retries(task, thresholds, now),
            lambda: _detect_token_burn_without_progress(
                task, thresholds, now, prev_snapshot
            ),
            lambda: _detect_burn_rate_spike(
                task, thresholds, now, project_baseline, project_today
            ),
        ):
            alert = detector()
            if alert is not None:
                return alert
        return None

    async def _flush_alerts(
        self,
        alert_writes: list[tuple[int, int, dict[str, Any]]],
        auto_pause_ids: list[int],
        metrics: SweepMetrics,
    ) -> None:
        """PATCH each alerted task. High-severity rows also flip to manual.

        Uses one shared AsyncClient for connection reuse. Failures are
        logged per-task and don't abort the sweep.
        """
        client = self._http_client_factory()
        try:
            for task_id, project_id, alert in alert_writes:
                body: dict[str, Any] = {"health_alert": alert}
                if task_id in auto_pause_ids:
                    body["run_mode"] = TaskRunMode.MANUAL
                try:
                    resp = await client.patch(
                        f"/api/tasks/{task_id}",
                        json=body,
                        headers={"X-Project-Id": str(project_id)},
                    )
                    if resp.status_code >= 400:
                        logger.warning(
                            "health_monitor.flush_alerts: PATCH %d -> %d (%s)",
                            task_id, resp.status_code, resp.text[:200],
                        )
                        continue
                    if task_id in auto_pause_ids:
                        metrics.auto_paused += 1
                except Exception as exc:
                    logger.warning(
                        "health_monitor.flush_alerts: PATCH %d failed: %s",
                        task_id, exc,
                    )
        finally:
            await client.aclose()

    # -- Query helpers ------------------------------------------------------

    async def _fetch_active_autorun_tasks(
        self, session: AsyncSession
    ) -> list[Task]:
        """Active autorun tasks only — the worker's relevant set.

        We include BLOCKED (process_status=4) because the auditor retries
        detector watches for repeated retries on rows the auditor has
        flipped to BLOCKED.
        """
        stmt = (
            select(Task)
            .where(
                Task.process_status.in_(
                    [TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]
                ),
                Task.run_mode.in_(
                    [TaskRunMode.AUTO_PICKUP, TaskRunMode.AUTO_HEADLESS]
                ),
            )
        )
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    async def _fetch_projects(
        self, session: AsyncSession, project_ids: list[int]
    ) -> dict[int, Project]:
        if not project_ids:
            return {}
        stmt = select(Project).where(Project.id.in_(project_ids))
        rows = (await session.execute(stmt)).scalars().all()
        return {p.id: p for p in rows}

    async def _fetch_spend_since(
        self,
        session: AsyncSession,
        project_ids: list[int],
        since: datetime,
    ) -> dict[int, Decimal]:
        """Raw SUM(total_cost_usd) per project for session_runs since `since`.

        Returns Decimal("0") for projects with no rows in the window.
        Callers apply their own post-processing (/7 + None-for-zero vs raw).
        """
        stmt = (
            select(
                SessionModel.project_id,
                func.coalesce(
                    func.sum(SessionRun.total_cost_usd), Decimal("0")
                ).label("total"),
            )
            .join(SessionModel, SessionRun.session_id == SessionModel.id)
            .where(
                SessionModel.project_id.in_(project_ids),
                SessionRun.created_at >= since,
            )
            .group_by(SessionModel.project_id)
        )
        rows = (await session.execute(stmt)).all()
        result: dict[int, Decimal] = {pid: Decimal("0") for pid in project_ids}
        for project_id, total in rows:
            result[project_id] = Decimal(str(total or 0))
        return result

    async def _fetch_7day_baseline(
        self,
        session: AsyncSession,
        project_ids: list[int],
        now: datetime,
    ) -> dict[int, Decimal | None]:
        """7-day average daily spend per project, from session_runs.

        Avg = SUM(total_cost_usd) / 7 over the trailing 7×24h window. If a
        project has zero session_runs in the window, returns None (skips
        the burn-spike detector for that project).
        """
        if not project_ids:
            return {}
        raw = await self._fetch_spend_since(
            session, project_ids, now - timedelta(days=7)
        )
        result: dict[int, Decimal | None] = {pid: None for pid in project_ids}
        for pid, total in raw.items():
            if total > 0:
                result[pid] = total / Decimal("7")
        return result

    async def _fetch_today_spend(
        self,
        session: AsyncSession,
        project_ids: list[int],
        now: datetime,
    ) -> dict[int, Decimal]:
        """Per-project running daily total since UTC midnight today."""
        if not project_ids:
            return {}
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return await self._fetch_spend_since(session, project_ids, midnight)
