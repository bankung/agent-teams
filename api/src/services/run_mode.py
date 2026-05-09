"""Cross-table invariants for tasks.run_mode (Kanban #481/#483/#690).

The rule `task.run_mode = 'auto_headless'` is valid only if the parent
project has `auto_run_consent_at IS NOT NULL`. This spans two tables and
therefore does NOT live as a DB CHECK — it is enforced at the router /
service layer per the methodology decision in
`context/teams/dev/decisions.md` 2026-05-09 entry.

Stable wire detail strings (pinned by source-text-lock test in
`tests/test_run_mode_consent.py`):

    "project_id {project_id} does not exist"           # no active row
    "project {project_id} has not granted auto-headless consent"  # NULL consent

#690: the SELECT returns two columns (id + auto_run_consent_at) so that
"no active row" (FK-style operator mistake) is disambiguated from "row
exists but consent is NULL". Without the disambiguation, a bogus or
soft-deleted project_id with run_mode='auto_headless' surfaces the
consent string instead of the FK-style string the same payload would
get with run_mode='manual' — wire-contract drift.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import RecordStatus, TaskRunMode
from src.models.project import Project


async def assert_consent_for_run_mode(
    db: AsyncSession,
    project_id: int,
    run_mode: str | None,
) -> None:
    """Raise 400 if `run_mode='auto_headless'` and either:

    - the project does not exist (or is soft-deleted), OR
    - the project exists but `auto_run_consent_at IS NULL`.

    No-op for any other mode. Reads only id + consent column (single
    round-trip) to keep this cheap on the hot POST/PATCH path.

    Caller contract:
    - POST /api/tasks: pass `payload.project_id` and `payload.run_mode`.
    - PATCH /api/tasks/{id}: pass the EXISTING task's `project_id` (V1 forbids
      re-parenting) and the RESOLVED run_mode (existing or PATCH-supplied).
      Only assert when the resolved final value is `auto_headless`.
    """
    if run_mode != TaskRunMode.AUTO_HEADLESS:
        return
    result = await db.execute(
        select(Project.id, Project.auto_run_consent_at).where(
            Project.id == project_id,
            Project.status == RecordStatus.ACTIVE,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=f"project_id {project_id} does not exist",
        )
    if row.auto_run_consent_at is None:
        raise HTTPException(
            status_code=400,
            detail=f"project {project_id} has not granted auto-headless consent",
        )
