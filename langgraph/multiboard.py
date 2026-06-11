"""Multi-board discovery helpers for the Kanban poll worker (Kanban #2184).

Pure functions and a session cache — no httpx I/O here so these are trivially
unit-testable. The worker calls them; transport stays in worker.py.

Eligibility contract (operator-locked):
  is_active=True AND auto_run_consent_at != null
  AND tools_config.tools_enabled == True
  AND NOT is_paused AND NOT is_killed

Refresh cadence: every MULTIBOARD_REFRESH_TICKS ticks (default 6;
overridable via LANGGRAPH_MULTIBOARD_REFRESH_TICKS).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("langgraph.worker")

# Default refresh cadence — every N poll ticks. #2184
_DEFAULT_REFRESH_TICKS = 6


def multiboard_refresh_ticks() -> int:
    """Read LANGGRAPH_MULTIBOARD_REFRESH_TICKS at call time (test-monkeyable)."""
    raw = os.getenv("LANGGRAPH_MULTIBOARD_REFRESH_TICKS", "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return _DEFAULT_REFRESH_TICKS


def is_eligible(project: dict[str, Any]) -> bool:
    """Return True iff a project dict meets multi-board eligibility. #2184.

    Checks: is_active, auto_run_consent_at, tools_config.tools_enabled,
    NOT is_paused, NOT is_killed.
    """
    if not project.get("is_active"):
        return False
    if project.get("auto_run_consent_at") is None:
        return False
    tc = project.get("tools_config") or {}
    if not tc.get("tools_enabled"):
        return False
    if project.get("is_paused"):
        return False
    if project.get("is_killed"):
        return False
    return True


def filter_eligible(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return eligible subset in stable (id-ascending) order. #2184."""
    return sorted(
        [p for p in projects if is_eligible(p)],
        key=lambda p: p["id"],
    )


# ---------------------------------------------------------------------------
# Session cache — one session per served project per worker process. #2184
# ---------------------------------------------------------------------------

# {project_id: session_id}  — process-local; cleared on restart.
_session_cache: dict[int, int] = {}


def session_cache_clear() -> None:
    """Test hook — clear the process-local session cache."""
    _session_cache.clear()


def get_cached_session(project_id: int) -> int | None:
    """Return cached session_id for project_id, or None."""
    return _session_cache.get(project_id)


def put_cached_session(project_id: int, session_id: int) -> None:
    """Store a session_id for project_id."""
    _session_cache[project_id] = session_id
