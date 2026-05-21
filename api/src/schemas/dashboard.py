"""Dashboard cross-project aggregation schemas (Kanban #945).

Operator-level cross-project surfaces that don't fit naturally inside the
per-project /api/projects/{id}/* tree. Currently:

  - `DashboardActiveTaskRow` / `DashboardActiveTasks` — flat list of tasks
    with process_status in {IN_PROGRESS, REVIEW, BLOCKED} across all active
    (status=1) projects. Project fields denormalized into each row so the
    FE doesn't N+1 to resolve project_id → project_name.

Mirrors the cross-project /api/pnl pattern (operator-level, no
X-Project-Id header). See routers/dashboard.py for the endpoint.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DashboardActiveTaskRow(BaseModel):
    """One row in the cross-project active-tasks list (Kanban #945).

    Subset of `TaskRead` plus denormalized project fields (`project_name`,
    `team`). The denormalization is conscious — the dashboard list is the
    only consumer today and it would otherwise issue an N-query lookup or
    cross-join client-side. Keep this row narrow on purpose; if you need
    a field that's not here, click through to the per-project board where
    the full `TaskRead` is available.

    Filtered to `process_status IN (2, 3, 4)` — IN_PROGRESS, REVIEW,
    BLOCKED. TODO (1), DONE (5), CANCELLED (6) are excluded by design:
    the list is the operator's "what's actively going on" surface, not a
    historical log.

    `blocked_by` carries the upstream task id when present (only meaningful
    on rows with `process_status=4`; the FK is nullable on the rest).
    """

    model_config = ConfigDict(extra="forbid")

    task_id: int
    title: str
    project_id: int
    project_name: str
    team: str
    process_status: int  # 2 / 3 / 4 (gated at the SQL layer)
    run_mode: str  # 'manual' / 'auto_pickup' / 'auto_headless'
    task_kind: str  # 'human' / 'ai'
    assigned_role: int | None
    priority: int
    updated_at: datetime
    blocked_by: int | None


class DashboardActiveTasks(BaseModel):
    """Response wrapper for GET /api/dashboard/active-tasks.

    `rows` are pre-sorted by (project_name ASC, updated_at DESC) so the FE
    can group adjacent rows by project without re-sorting. `total_count`
    is a redundant convenience (len(rows)) — exposed so the FE can render
    a header count without iterating the rows array.
    """

    model_config = ConfigDict(extra="forbid")

    rows: list[DashboardActiveTaskRow]
    total_count: int
