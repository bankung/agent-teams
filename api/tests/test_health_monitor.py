"""Unit tests for the periodic Health monitor sweep (Kanban #960).

Coverage:
  - HealthMonitorConfig env parsing + enable/disable gate
  - ResolvedThresholds merge of project override over env defaults
  - Detector 1 (stale_state) — fires on IN_PROGRESS + old updated_at + no halt
  - Detector 2 (token_burn_without_progress) — two-sweep flow with stash
  - Detector 3 (repeated_retries) — fires at/above max_retry_cycles
  - Detector 4 (burn_rate_spike) — today_spend > baseline * multiplier
  - Severity action — high triggers run_mode='manual' PATCH; low/medium do not
  - Per-project enabled=false skips entirely
  - run_sweep() returns the metrics envelope shape

Detector-level tests synthesize Task / Project rows in-memory (no DB) — the
detectors are pure functions over their inputs. The sweep-level tests use an
injected fake session_factory + httpx mock so they remain fast and offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from src.constants import TaskRunMode, TaskStatus
from src.services.health_monitor import (
    HealthMonitor,
    HealthMonitorConfig,
    ResolvedThresholds,
    SweepMetrics,
    _detect_burn_rate_spike,
    _detect_repeated_retries,
    _detect_stale_state,
    _detect_token_burn_without_progress,
    _status_reason_hash,
    _TaskBurnSnapshot,
)


# ---------------------------------------------------------------------------
# Light-weight test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeTask:
    """Just-enough Task for detector unit tests. Matches the attribute surface
    the detectors read — keeps the unit tests free of DB / SQLAlchemy."""

    id: int
    project_id: int
    process_status: int
    run_mode: str = TaskRunMode.AUTO_PICKUP
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    audit_retry_count: int = 0
    halt_reason: str | None = None
    estimated_input_tokens: int | None = None
    status_change_reason: str | None = None


@dataclass
class _FakeProject:
    id: int
    health_thresholds: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# HealthMonitorConfig — env parsing
# ---------------------------------------------------------------------------


def test_config_defaults_when_env_empty() -> None:
    cfg = HealthMonitorConfig.from_env({})
    assert cfg.is_enabled is True
    assert cfg.interval_minutes == 15
    assert cfg.stale_hours == 4.0
    assert cfg.max_retry_cycles == 5
    assert cfg.token_burn_threshold_per_hour == 50_000
    assert cfg.burn_spike_multiplier == 2.0
    assert cfg.api_base == "http://localhost:8000"
    assert cfg.request_timeout_seconds == 10.0


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes"])
def test_config_disabled_via_env(val: str) -> None:
    cfg = HealthMonitorConfig.from_env({"HEALTH_MONITOR_DISABLED": val})
    assert cfg.is_enabled is False


@pytest.mark.parametrize("val", ["0", "false", "no", ""])
def test_config_enabled_when_disable_env_falsy(val: str) -> None:
    cfg = HealthMonitorConfig.from_env({"HEALTH_MONITOR_DISABLED": val})
    assert cfg.is_enabled is True


def test_config_per_key_overrides() -> None:
    cfg = HealthMonitorConfig.from_env(
        {
            "HEALTH_MONITOR_INTERVAL_MINUTES": "5",
            "HEALTH_MONITOR_DEFAULT_STALE_HOURS": "1.5",
            "HEALTH_MONITOR_DEFAULT_MAX_RETRIES": "3",
            "HEALTH_MONITOR_DEFAULT_TOKEN_BURN_PER_HOUR": "100000",
            "HEALTH_MONITOR_DEFAULT_BURN_SPIKE_MULTIPLIER": "3.5",
            "HEALTH_MONITOR_API_BASE": "http://api:8000",
            "HEALTH_MONITOR_REQUEST_TIMEOUT_SECONDS": "20",
        }
    )
    assert cfg.interval_minutes == 5
    assert cfg.stale_hours == 1.5
    assert cfg.max_retry_cycles == 3
    assert cfg.token_burn_threshold_per_hour == 100_000
    assert cfg.burn_spike_multiplier == 3.5
    assert cfg.api_base == "http://api:8000"
    assert cfg.request_timeout_seconds == 20.0


# ---------------------------------------------------------------------------
# ResolvedThresholds — merge defaults <- project override
# ---------------------------------------------------------------------------


def test_resolved_thresholds_no_override_uses_defaults() -> None:
    cfg = HealthMonitorConfig.from_env({})
    r = ResolvedThresholds.merge(cfg, None)
    assert r.enabled is True
    assert r.stale_hours == 4.0
    assert r.max_retry_cycles == 5
    assert r.token_burn_threshold_per_hour == 50_000
    assert r.burn_spike_multiplier == 2.0


def test_resolved_thresholds_partial_override_merges() -> None:
    cfg = HealthMonitorConfig.from_env({})
    r = ResolvedThresholds.merge(
        cfg, {"stale_hours": 8, "max_retry_cycles": 10}
    )
    assert r.stale_hours == 8.0
    assert r.max_retry_cycles == 10
    assert r.token_burn_threshold_per_hour == 50_000  # default preserved
    assert r.burn_spike_multiplier == 2.0


def test_resolved_thresholds_enabled_false_short_circuits() -> None:
    cfg = HealthMonitorConfig.from_env({})
    r = ResolvedThresholds.merge(cfg, {"enabled": False})
    assert r.enabled is False


def test_resolved_thresholds_bad_value_falls_back_to_default() -> None:
    """Hand-edited JSONB with wrong types shouldn't crash the sweep."""
    cfg = HealthMonitorConfig.from_env({})
    r = ResolvedThresholds.merge(
        cfg, {"stale_hours": "not-a-number", "max_retry_cycles": "abc"}
    )
    assert r.stale_hours == 4.0
    assert r.max_retry_cycles == 5


# ---------------------------------------------------------------------------
# Detector 1 — stale_state (HIGH)
# ---------------------------------------------------------------------------


def _thresholds_default() -> ResolvedThresholds:
    return ResolvedThresholds.merge(HealthMonitorConfig.from_env({}), None)


def test_detect_stale_state_fires_at_5h() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=5),
    )
    alert = _detect_stale_state(task, _thresholds_default(), now)
    assert alert is not None
    assert alert["detector"] == "stale_state"
    assert alert["severity"] == "high"
    assert alert["evidence"]["idle_hours"] == 5.0


def test_detect_stale_state_does_not_fire_at_1h() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=1),
    )
    assert _detect_stale_state(task, _thresholds_default(), now) is None


def test_detect_stale_state_skips_when_halt_reason_set() -> None:
    """A task halted explicitly is not 'stale' — operator is aware."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=8),
        halt_reason="awaiting user",
    )
    assert _detect_stale_state(task, _thresholds_default(), now) is None


def test_detect_stale_state_skips_non_in_progress() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.TODO,
        updated_at=now - timedelta(hours=12),
    )
    assert _detect_stale_state(task, _thresholds_default(), now) is None


# ---------------------------------------------------------------------------
# Detector 3 — repeated_retries (MEDIUM)
# ---------------------------------------------------------------------------


def test_detect_repeated_retries_fires_at_threshold() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        audit_retry_count=6,
    )
    alert = _detect_repeated_retries(task, _thresholds_default(), now)
    assert alert is not None
    assert alert["detector"] == "repeated_retries"
    assert alert["severity"] == "medium"
    assert alert["evidence"]["audit_retry_count"] == 6


def test_detect_repeated_retries_does_not_fire_below_threshold() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        audit_retry_count=4,
    )
    assert _detect_repeated_retries(task, _thresholds_default(), now) is None


def test_detect_repeated_retries_also_fires_when_blocked() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.BLOCKED,
        audit_retry_count=5,
    )
    assert _detect_repeated_retries(task, _thresholds_default(), now) is not None


def test_detect_repeated_retries_skips_done_task() -> None:
    """Retries on a closed task aren't actionable."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.DONE,
        audit_retry_count=10,
    )
    assert _detect_repeated_retries(task, _thresholds_default(), now) is None


# ---------------------------------------------------------------------------
# Detector 2 — token_burn_without_progress (MEDIUM, cross-sweep)
# ---------------------------------------------------------------------------


def test_detect_token_burn_first_sweep_no_signal() -> None:
    """No prev snapshot → no signal (first observation seeds the stash)."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        estimated_input_tokens=10_000,
    )
    alert = _detect_token_burn_without_progress(
        task, _thresholds_default(), now, prev=None
    )
    assert alert is None


def test_detect_token_burn_grew_with_no_progress_fires() -> None:
    """tokens grew by 100k in 1h with same reason hash → MEDIUM alert."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    prev = _TaskBurnSnapshot(
        tokens=50_000,
        reason_hash=_status_reason_hash("working"),
        seen_at=now - timedelta(hours=1),
    )
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        estimated_input_tokens=150_000,
        status_change_reason="working",
    )
    alert = _detect_token_burn_without_progress(
        task, _thresholds_default(), now, prev
    )
    assert alert is not None
    assert alert["detector"] == "token_burn_without_progress"
    assert alert["severity"] == "medium"
    assert alert["evidence"]["delta_tokens"] == 100_000
    assert alert["evidence"]["burn_per_hour"] == 100_000.0


def test_detect_token_burn_progress_made_clears_signal() -> None:
    """tokens grew but the status_change_reason changed → progress made → no alert."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    prev = _TaskBurnSnapshot(
        tokens=50_000,
        reason_hash=_status_reason_hash("step 1"),
        seen_at=now - timedelta(hours=1),
    )
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        estimated_input_tokens=150_000,
        status_change_reason="step 2",  # different reason → progress
    )
    assert (
        _detect_token_burn_without_progress(
            task, _thresholds_default(), now, prev
        )
        is None
    )


def test_detect_token_burn_below_threshold_no_alert() -> None:
    """Default 50k/hr; 10k delta over 1h is fine."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    prev = _TaskBurnSnapshot(
        tokens=50_000,
        reason_hash=_status_reason_hash("working"),
        seen_at=now - timedelta(hours=1),
    )
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        estimated_input_tokens=60_000,
        status_change_reason="working",
    )
    assert (
        _detect_token_burn_without_progress(
            task, _thresholds_default(), now, prev
        )
        is None
    )


def test_detect_token_burn_negative_delta_no_alert() -> None:
    """Token estimate dropped (re-estimate after cleanup) → no signal."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    prev = _TaskBurnSnapshot(
        tokens=200_000,
        reason_hash=_status_reason_hash("working"),
        seen_at=now - timedelta(hours=1),
    )
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        estimated_input_tokens=100_000,
        status_change_reason="working",
    )
    assert (
        _detect_token_burn_without_progress(
            task, _thresholds_default(), now, prev
        )
        is None
    )


# ---------------------------------------------------------------------------
# Detector 4 — burn_rate_spike (LOW)
# ---------------------------------------------------------------------------


def test_detect_burn_spike_fires_at_2x() -> None:
    """baseline $100/day, today $250 → over 2x multiplier → LOW alert."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1, project_id=1, process_status=TaskStatus.IN_PROGRESS
    )
    alert = _detect_burn_rate_spike(
        task,
        _thresholds_default(),
        now,
        project_7day_avg_daily_usd=Decimal("100"),
        today_spend_usd=Decimal("250"),
    )
    assert alert is not None
    assert alert["detector"] == "burn_rate_spike"
    assert alert["severity"] == "low"


def test_detect_burn_spike_does_not_fire_below_multiplier() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1, project_id=1, process_status=TaskStatus.IN_PROGRESS
    )
    alert = _detect_burn_rate_spike(
        task,
        _thresholds_default(),
        now,
        project_7day_avg_daily_usd=Decimal("100"),
        today_spend_usd=Decimal("150"),
    )
    assert alert is None


def test_detect_burn_spike_no_baseline_skips() -> None:
    """Cold project with no spend history → no signal."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1, project_id=1, process_status=TaskStatus.IN_PROGRESS
    )
    alert = _detect_burn_rate_spike(
        task,
        _thresholds_default(),
        now,
        project_7day_avg_daily_usd=None,
        today_spend_usd=Decimal("9999"),
    )
    assert alert is None


def test_detect_burn_spike_zero_baseline_skips() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1, project_id=1, process_status=TaskStatus.IN_PROGRESS
    )
    alert = _detect_burn_rate_spike(
        task,
        _thresholds_default(),
        now,
        project_7day_avg_daily_usd=Decimal("0"),
        today_spend_usd=Decimal("100"),
    )
    assert alert is None


# ---------------------------------------------------------------------------
# Detector priority — first hit wins
# ---------------------------------------------------------------------------


def test_evaluate_detectors_stale_wins_over_retries() -> None:
    """HIGH severity (stale) takes priority over MEDIUM (retries)."""
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=10),
        audit_retry_count=10,
    )
    monitor = HealthMonitor(HealthMonitorConfig.from_env({}))
    alert = monitor._evaluate_detectors(
        task,
        _thresholds_default(),
        now,
        prev_snapshot=None,
        project_baseline=None,
        project_today=Decimal("0"),
    )
    assert alert is not None
    assert alert["detector"] == "stale_state"


# ---------------------------------------------------------------------------
# Sweep-level — injected fakes for session_factory + http_client
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal AsyncSession stand-in. Returns canned query results based on
    pattern-matching the statement's main table.
    """

    def __init__(self, tasks, projects, baseline_rows, today_rows):
        self.tasks = tasks
        self.projects = projects
        self.baseline_rows = baseline_rows
        self.today_rows = today_rows
        self._next_aggregate = None  # alternates baseline / today

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, stmt):
        # The first aggregate call is the 7-day baseline; the second is
        # today's spend. Track via a counter on self.
        from src.models.task import Task as TaskModel

        result = MagicMock()
        # The stmt has a `column_descriptions` attribute we can sniff —
        # in practice, easier to track call order. The sweep calls in this
        # order: tasks, projects, baseline, today.
        call_idx = getattr(self, "_call_idx", 0)
        self._call_idx = call_idx + 1
        if call_idx == 0:
            # tasks query
            result.scalars.return_value.all.return_value = self.tasks
        elif call_idx == 1:
            # projects query
            result.scalars.return_value.all.return_value = self.projects
        elif call_idx == 2:
            # baseline aggregate
            result.all.return_value = self.baseline_rows
        elif call_idx == 3:
            # today aggregate
            result.all.return_value = self.today_rows
        return result


class _CapturingHTTPClient:
    """httpx.AsyncClient stand-in that records PATCH calls without I/O."""

    def __init__(self):
        self.calls: list[dict] = []

    async def patch(self, url, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})

        class _R:
            status_code = 200
            text = "ok"

        return _R()

    async def aclose(self):
        return None


def _make_session_factory(tasks, projects, baseline_rows=None, today_rows=None):
    """Build a session_factory callable the HealthMonitor accepts."""
    baseline_rows = baseline_rows or []
    today_rows = today_rows or []

    def _factory():
        return _FakeSession(tasks, projects, baseline_rows, today_rows)

    return _factory


async def test_run_sweep_returns_metrics_dict_shape() -> None:
    """Sweep with no tasks → metrics dict shape matches contract."""
    cfg = HealthMonitorConfig.from_env({})
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([], []),
        http_client_factory=_CapturingHTTPClient,
    )
    result = await monitor.run_sweep()
    assert result == {
        "checked": 0,
        "alerts_low": 0,
        "alerts_medium": 0,
        "alerts_high": 0,
        "auto_paused": 0,
    }


async def test_run_sweep_high_severity_auto_pauses_via_patch() -> None:
    """Stale task triggers HIGH → PATCH includes run_mode='manual'."""
    now = datetime.now(timezone.utc)
    cfg = HealthMonitorConfig.from_env({})
    stale_task = _FakeTask(
        id=42,
        project_id=7,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=10),
        run_mode=TaskRunMode.AUTO_HEADLESS,
    )
    project = _FakeProject(id=7, health_thresholds=None)
    captured = _CapturingHTTPClient()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([stale_task], [project]),
        http_client_factory=lambda: captured,
    )

    result = await monitor.run_sweep()

    assert result["checked"] == 1
    assert result["alerts_high"] == 1
    assert result["auto_paused"] == 1
    assert len(captured.calls) == 1
    call = captured.calls[0]
    assert call["url"] == "/api/tasks/42"
    assert call["json"]["run_mode"] == "manual"
    assert call["json"]["health_alert"]["detector"] == "stale_state"
    assert call["json"]["health_alert"]["severity"] == "high"
    assert call["headers"]["X-Project-Id"] == "7"


async def test_run_sweep_medium_severity_no_auto_pause() -> None:
    """audit_retry_count=6 → MEDIUM; PATCH writes health_alert only (no run_mode)."""
    now = datetime.now(timezone.utc)
    cfg = HealthMonitorConfig.from_env({})
    retry_task = _FakeTask(
        id=99,
        project_id=3,
        process_status=TaskStatus.IN_PROGRESS,
        audit_retry_count=6,
        updated_at=now - timedelta(minutes=10),  # recent enough — not stale
    )
    project = _FakeProject(id=3)
    captured = _CapturingHTTPClient()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([retry_task], [project]),
        http_client_factory=lambda: captured,
    )

    result = await monitor.run_sweep()

    assert result["checked"] == 1
    assert result["alerts_medium"] == 1
    assert result["alerts_high"] == 0
    assert result["auto_paused"] == 0
    assert len(captured.calls) == 1
    call = captured.calls[0]
    assert "run_mode" not in call["json"]
    assert call["json"]["health_alert"]["severity"] == "medium"


async def test_run_sweep_skips_disabled_project() -> None:
    """Project with health_thresholds.enabled=false → no detector even runs."""
    now = datetime.now(timezone.utc)
    cfg = HealthMonitorConfig.from_env({})
    stale_task = _FakeTask(
        id=42,
        project_id=7,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=10),
    )
    project = _FakeProject(id=7, health_thresholds={"enabled": False})
    captured = _CapturingHTTPClient()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([stale_task], [project]),
        http_client_factory=lambda: captured,
    )

    result = await monitor.run_sweep()

    # Counted as checked, but no alerts fire and no PATCH happens.
    assert result["checked"] == 1
    assert result["alerts_high"] == 0
    assert result["auto_paused"] == 0
    assert len(captured.calls) == 0


async def test_run_sweep_no_alert_no_patch() -> None:
    """Healthy task → no detectors fire → no PATCH."""
    now = datetime.now(timezone.utc)
    cfg = HealthMonitorConfig.from_env({})
    healthy = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(minutes=5),
        audit_retry_count=0,
    )
    project = _FakeProject(id=1)
    captured = _CapturingHTTPClient()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([healthy], [project]),
        http_client_factory=lambda: captured,
    )

    result = await monitor.run_sweep()
    assert result["checked"] == 1
    assert result["alerts_low"] == 0
    assert result["alerts_medium"] == 0
    assert result["alerts_high"] == 0
    assert len(captured.calls) == 0


async def test_run_sweep_uses_project_override_threshold() -> None:
    """Project override stale_hours=12 → task at 6h does NOT trigger."""
    now = datetime.now(timezone.utc)
    cfg = HealthMonitorConfig.from_env({})
    task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=6),
    )
    project = _FakeProject(id=1, health_thresholds={"stale_hours": 12})
    captured = _CapturingHTTPClient()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([task], [project]),
        http_client_factory=lambda: captured,
    )

    result = await monitor.run_sweep()
    # With override 12h, a 6h-old task is fresh.
    assert result["alerts_high"] == 0
    assert len(captured.calls) == 0


async def test_run_sweep_burn_snapshot_persists_across_sweeps() -> None:
    """Two-sweep flow: first sweep stashes; second sweep with grown tokens fires."""
    cfg = HealthMonitorConfig.from_env({})
    now1 = datetime.now(timezone.utc)
    task1 = _FakeTask(
        id=5,
        project_id=2,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now1 - timedelta(minutes=5),
        estimated_input_tokens=50_000,
        status_change_reason="working",
    )
    project = _FakeProject(id=2)
    captured = _CapturingHTTPClient()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([task1], [project]),
        http_client_factory=lambda: captured,
    )

    # First sweep — seeds the snapshot, no alert (no prev).
    result1 = await monitor.run_sweep()
    assert result1["alerts_medium"] == 0
    assert 5 in monitor._burn_snapshots

    # Backdate the stashed snapshot so the second sweep computes a 1h elapsed.
    snap = monitor._burn_snapshots[5]
    snap.seen_at = now1 - timedelta(hours=1)

    # Second sweep — tokens grew by 200k, reason unchanged → MEDIUM alert.
    task2 = _FakeTask(
        id=5,
        project_id=2,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now1 - timedelta(minutes=2),  # still fresh, not stale
        estimated_input_tokens=250_000,
        status_change_reason="working",  # same as snapshot
        audit_retry_count=0,
    )
    monitor._session_factory = _make_session_factory([task2], [project])
    result2 = await monitor.run_sweep()
    assert result2["alerts_medium"] == 1
    # PATCH called once for the token-burn alert.
    assert len(captured.calls) == 1
    assert (
        captured.calls[0]["json"]["health_alert"]["detector"]
        == "token_burn_without_progress"
    )


async def test_run_sweep_exception_returns_metrics_without_crashing() -> None:
    """Inner exception logged + metrics envelope still returned."""
    cfg = HealthMonitorConfig.from_env({})

    def _exploding_factory():
        raise RuntimeError("boom")

    monitor = HealthMonitor(
        cfg,
        session_factory=_exploding_factory,
        http_client_factory=_CapturingHTTPClient,
    )
    result = await monitor.run_sweep()
    # No crash; metrics dict shape still present.
    assert set(result.keys()) == {
        "checked", "alerts_low", "alerts_medium", "alerts_high", "auto_paused"
    }


async def test_run_sweep_patch_4xx_does_not_crash() -> None:
    """A 4xx from the API is logged + sweep continues; auto_paused not incremented."""

    class _BadResponseHTTP:
        def __init__(self):
            self.calls = []

        async def patch(self, url, json, headers):
            self.calls.append({"url": url, "json": json, "headers": headers})

            class _R:
                status_code = 422
                text = "validation error"

            return _R()

        async def aclose(self):
            return None

    now = datetime.now(timezone.utc)
    cfg = HealthMonitorConfig.from_env({})
    stale_task = _FakeTask(
        id=1,
        project_id=1,
        process_status=TaskStatus.IN_PROGRESS,
        updated_at=now - timedelta(hours=10),
    )
    project = _FakeProject(id=1)
    captured = _BadResponseHTTP()
    monitor = HealthMonitor(
        cfg,
        session_factory=_make_session_factory([stale_task], [project]),
        http_client_factory=lambda: captured,
    )
    result = await monitor.run_sweep()

    assert result["alerts_high"] == 1
    assert result["auto_paused"] == 0  # PATCH failed → not counted
    assert len(captured.calls) == 1


# ---------------------------------------------------------------------------
# Status-change-reason hash — stability + sentinel
# ---------------------------------------------------------------------------


def test_status_reason_hash_stable() -> None:
    assert _status_reason_hash("x") == _status_reason_hash("x")
    assert _status_reason_hash("x") != _status_reason_hash("y")


def test_status_reason_hash_null_sentinel() -> None:
    assert _status_reason_hash(None) == _status_reason_hash("")
    assert _status_reason_hash(None) == "_null"
