"""Pydantic schemas for the auditor cross-project APIs (Kanban #1082, #2700).

Powers the Audit dashboard widget — one row per (project, day) showing the
breakdown of auditor verdicts written to `tasks.audit_report` (migration 0030,
populated by `langgraph/nodes.py::auditor_node`).

The bucket names on the wire (`pass`, `auto_resolved`, `escalated`,
`failed_giveup`, `pending_escalation`) are stable contract — they're the
FE-visible widget labels and the categories the user reasons about. They do
NOT 1:1 mirror the raw verdict strings the auditor writes to the DB:

  audit_report->>'verdict'      mapped bucket          condition
  ----------------------------  ----------------       ----------------------
  'pass'                        pass                   (always)
  'auto_resolve'                auto_resolved          (always; whether retry
                                                       was capped or not)
  'escalate'                    pending_escalation     when process_status in
                                                       (TODO/IN_PROGRESS/REVIEW/
                                                        BLOCKED) — operator
                                                       hasn't resolved yet
  'escalate'                    escalated              when process_status =
                                                       DONE — operator did
                                                       resolve
  (any verdict) + halt_reason   failed_giveup          gate evaluated BEFORE
  = 'auditor_giveup'                                   the verdict mapping —
                                                       a giveup row is a
                                                       giveup row regardless
                                                       of the verdict captured

The 5 bucket keys ALWAYS emit (zero-filled when absent) — same no-coalescing
contract as ProjectStatsRunModeBreakdown / ProjectStatsCounts (Kanban #769,
#871) so the FE renders without `||0` defaults.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.project import ProjectRead
from src.schemas.task import TaskRead


class AuditDailyCounts(BaseModel):
    """Per-day, per-project verdict bucket — 5 keys always present (zero-filled).

    Matches the wire-locked bucket names from Kanban #1082. See module
    docstring for the verdict-to-bucket mapping table.
    """

    pass_: int = Field(default=0, alias="pass")
    auto_resolved: int = 0
    escalated: int = 0
    failed_giveup: int = 0
    pending_escalation: int = 0

    # `populate_by_name=True` lets the router pass `pass_` kwarg while the JSON
    # output emits `pass`. `serialize_by_alias` not needed — Pydantic v2 honors
    # the alias on serialize by default when `by_alias=True` is passed to
    # `model_dump`, but FastAPI's response_model encoder applies aliases by
    # default for response serialization.
    model_config = ConfigDict(populate_by_name=True)


class AuditFlagWithProject(BaseModel):
    """Bundle of a GOV3 audit-flag question task with its parent project.

    Returned by GET /api/audit/flags (Kanban #2700). Mirrors the FE type
    `AuditFlagWithProject` in web/lib/api.ts (locked shape).
    """

    flag: TaskRead
    project: ProjectRead


class AuditDailyRollupEntry(BaseModel):
    """Single (project, day) row in the daily-rollup response.

    `day` is a calendar date (UTC). The router floors `tasks.updated_at` to
    `date_trunc('day', ...)::date` so a task touched at 23:59:59 UTC and one
    touched at 00:00:01 UTC the next day end up in different rows even though
    the wall-clock difference is 2 seconds. The window-filter is also UTC-
    based (`from`/`to` query params).

    Skips rows where `audit_report IS NULL` (no audit pass recorded) and
    soft-deleted tasks (`status=0`). Includes soft-deleted projects'
    PRE-deletion audit rows in a project's history? — NO: the SQL JOINs on
    `projects.status = ACTIVE` so a soft-deleted project disappears from the
    rollup entirely (parity with `/api/projects/stats`).
    """

    project_id: int
    project_name: str
    day: date
    counts: AuditDailyCounts
