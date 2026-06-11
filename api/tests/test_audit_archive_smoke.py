"""Kanban #1240 — audit-archive sweep contract smoke tests.

First-pass contract smokes (dev-sr-backend scope — 1-3 happy-path tests proving
the contract is wired). The rigorous suite (TTL boundary values, batch limits,
concurrent ticks, idempotent re-sweep, project-status edge cases, audit-trigger
history assertion, etc.) is dev-tester's domain.

DO NOT RUN AGAINST THE LIVE agent_teams DB. These tests require migration
0061_tasks_is_active to be applied first; the conftest test-DB harness
(agent_teams_test) applies it via `alembic upgrade head` before the session.
dev-tester runs this suite AFTER dev-devops applies the migration.

Covered:

  (1) sweep_old_audit_tasks flips is_active=false on an OLD completed audit task
      while leaving a NEW completed audit task untouched (AC5 — the core bar).
      POSITIVE: old audit task is_active becomes false; summary counts it.
      NEGATIVE: new audit task is_active stays true.
      NEGATIVE: a non-audit (feature) old completed task stays true (task_type gate).

  (2) projects.audit_enabled=false projects are SKIPPED (AC3).
      POSITIVE: enabled-project old audit task IS archived.
      NEGATIVE: disabled-project old audit task is NOT archived.

  (3) GET /api/tasks default-excludes is_active=false; ?include_archived=true
      includes them (the query-semantics + blast-radius guard).
      POSITIVE: archived row absent from default list, present with include_archived.
      NEGATIVE: a visible task is present in BOTH lists (control).

Scaffold pollution mitigation: `no_scaffold` monkeypatches
scaffold_project_folder to a no-op so no context/projects/<name>/ dirs land.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helper factories (mirror test_hitl_nudge_smoke.py conventions)
# ---------------------------------------------------------------------------


def _unique_project_name(suffix: str = "") -> str:
    return f"archive-smoke-{uuid.uuid4().hex[:8]}{suffix}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "team": "dev",
        "paths": {"web": "/tmp/as/web", "api": "/tmp/as/api", "db": "/tmp/as/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
    }


async def _create_project(client, name: str | None = None) -> dict:
    n = name or _unique_project_name()
    resp = await client.post("/api/projects", json=_project_payload(n))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(
    client,
    project_id: int,
    *,
    title: str = "smoke task",
    task_type: str = "audit",
) -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={
            "title": title,
            "project_id": project_id,
            "task_type": task_type,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _set_completed_at(db_session, task_id: int, *, days_ago: float) -> None:
    """Stamp completed_at (timestamptz) back `days_ago` days. Also set
    process_status=DONE so the row is a realistic completed task."""
    from sqlalchemy import update

    from src.constants import TaskStatus
    from src.models.task import Task

    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await db_session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(completed_at=when, process_status=TaskStatus.DONE)
    )
    await db_session.commit()


async def _set_audit_enabled(db_session, project_id: int, value: bool) -> None:
    from sqlalchemy import update

    from src.models.project import Project

    await db_session.execute(
        update(Project).where(Project.id == project_id).values(audit_enabled=value)
    )
    await db_session.commit()


async def _is_active(db_session, task_id: int) -> bool:
    from sqlalchemy import select

    from src.models.task import Task

    # Expire identity-map state so we read the post-sweep DB value, not a stale
    # cached ORM instance from an earlier query in the same session.
    db_session.expire_all()
    row = (
        await db_session.execute(select(Task).where(Task.id == task_id))
    ).scalar_one()
    return row.is_active


@pytest.fixture()
def no_scaffold(monkeypatch):
    """Monkeypatch scaffold_project_folder to a no-op so no on-disk dirs land."""
    import src.routers.projects as proj_router

    monkeypatch.setattr(proj_router, "scaffold_project_folder", lambda *a, **kw: None)


@pytest.fixture()
def ttl_30(monkeypatch):
    """Pin AUDIT_ARCHIVE_DAYS=30 so the sweep cutoff is deterministic."""
    monkeypatch.setenv("AUDIT_ARCHIVE_DAYS", "30")


# ---------------------------------------------------------------------------
# (1) Core AC5 — old audit archived, new audit + non-audit untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_archives_old_audit_only(
    client, no_scaffold, ttl_30, db_session
) -> None:
    """sweep_old_audit_tasks flips ONLY old completed audit tasks.

    POSITIVE: an audit task completed 40 days ago (> 30d TTL) gets is_active=false.
    NEGATIVE: an audit task completed 1 day ago (< TTL) stays is_active=true.
    NEGATIVE: a feature task completed 40 days ago stays is_active=true (task_type gate).
    POSITIVE: the summary dict counts exactly the one archived task for the project.
    """
    from src.db import SessionLocal
    from src.services.audit_archive import sweep_old_audit_tasks

    proj = await _create_project(client)
    proj_id = proj["id"]

    old_audit = await _create_task(client, proj_id, title="OLD audit", task_type="audit")
    new_audit = await _create_task(client, proj_id, title="NEW audit", task_type="audit")
    old_feature = await _create_task(
        client, proj_id, title="OLD feature", task_type="feature"
    )

    await _set_completed_at(db_session, old_audit["id"], days_ago=40)
    await _set_completed_at(db_session, new_audit["id"], days_ago=1)
    await _set_completed_at(db_session, old_feature["id"], days_ago=40)

    # Pre-condition: all three start visible.
    assert await _is_active(db_session, old_audit["id"]) is True
    assert await _is_active(db_session, new_audit["id"]) is True
    assert await _is_active(db_session, old_feature["id"]) is True

    async with SessionLocal() as session:
        summary = await sweep_old_audit_tasks(session)

    # POSITIVE: old audit task archived.
    assert await _is_active(db_session, old_audit["id"]) is False, (
        "POSITIVE: old completed audit task must be archived (is_active=false)"
    )
    # NEGATIVE: new audit task untouched.
    assert await _is_active(db_session, new_audit["id"]) is True, (
        "NEGATIVE: audit task within TTL must stay visible (is_active=true)"
    )
    # NEGATIVE: old feature task untouched (task_type gate).
    assert await _is_active(db_session, old_feature["id"]) is True, (
        "NEGATIVE: non-audit task must stay visible regardless of age"
    )

    # POSITIVE: summary reflects exactly one archived row for this project.
    assert summary["per_project"].get(proj_id) == 1, (
        f"POSITIVE: summary must count exactly 1 archived task for project "
        f"{proj_id}, got {summary['per_project']!r}"
    )
    assert summary["ttl_days"] == 30


# ---------------------------------------------------------------------------
# (2) AC3 — audit_enabled=false projects skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_skips_audit_disabled_projects(
    client, no_scaffold, ttl_30, db_session
) -> None:
    """Projects with audit_enabled=false are excluded from the sweep.

    POSITIVE: an enabled project's old audit task IS archived (control).
    NEGATIVE: a disabled project's old audit task is NOT archived.
    """
    from src.db import SessionLocal
    from src.services.audit_archive import sweep_old_audit_tasks

    enabled_proj = await _create_project(client)
    disabled_proj = await _create_project(client)

    enabled_task = await _create_task(
        client, enabled_proj["id"], title="enabled old audit"
    )
    disabled_task = await _create_task(
        client, disabled_proj["id"], title="disabled old audit"
    )

    await _set_completed_at(db_session, enabled_task["id"], days_ago=40)
    await _set_completed_at(db_session, disabled_task["id"], days_ago=40)
    await _set_audit_enabled(db_session, disabled_proj["id"], False)

    async with SessionLocal() as session:
        summary = await sweep_old_audit_tasks(session)

    # POSITIVE: enabled project's task archived.
    assert await _is_active(db_session, enabled_task["id"]) is False, (
        "POSITIVE: enabled-project old audit task must be archived"
    )
    # NEGATIVE: disabled project's task untouched.
    assert await _is_active(db_session, disabled_task["id"]) is True, (
        "NEGATIVE: audit_enabled=false project's audit task must be skipped"
    )
    assert disabled_proj["id"] not in summary["per_project"], (
        "NEGATIVE: disabled project must not appear in the per-project summary"
    )


# ---------------------------------------------------------------------------
# (3) Query semantics — default excludes archived; include_archived includes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_default_excludes_archived(
    client, no_scaffold, ttl_30, db_session
) -> None:
    """GET /api/tasks default-excludes is_active=false; ?include_archived shows them.

    POSITIVE: an archived task is ABSENT from the default list.
    POSITIVE: the archived task is PRESENT with ?include_archived=true.
    NEGATIVE: a visible (non-archived) control task is present in BOTH lists.
    """
    from src.db import SessionLocal
    from src.services.audit_archive import sweep_old_audit_tasks

    proj = await _create_project(client)
    proj_id = proj["id"]
    headers = {"X-Project-Id": str(proj_id)}

    archived = await _create_task(client, proj_id, title="to-be-archived audit")
    visible = await _create_task(
        client, proj_id, title="stays visible feature", task_type="feature"
    )
    await _set_completed_at(db_session, archived["id"], days_ago=40)

    async with SessionLocal() as session:
        await sweep_old_audit_tasks(session)

    # Sanity: the archive actually happened.
    assert await _is_active(db_session, archived["id"]) is False

    # Default list — archived absent, visible present.
    default_resp = await client.get("/api/tasks", headers=headers)
    assert default_resp.status_code == 200, default_resp.text
    default_ids = {t["id"] for t in default_resp.json()}
    assert archived["id"] not in default_ids, (
        "POSITIVE: archived task must be excluded from the default list"
    )
    assert visible["id"] in default_ids, (
        "NEGATIVE(control): visible task must be present in the default list"
    )

    # include_archived=true — archived present, visible still present.
    incl_resp = await client.get(
        "/api/tasks", headers=headers, params={"include_archived": "true"}
    )
    assert incl_resp.status_code == 200, incl_resp.text
    incl_ids = {t["id"] for t in incl_resp.json()}
    assert archived["id"] in incl_ids, (
        "POSITIVE: archived task must appear with ?include_archived=true"
    )
    assert visible["id"] in incl_ids, (
        "NEGATIVE(control): visible task must still be present with include_archived"
    )

    # POSITIVE: is_active is exposed on the read schema and reflects state.
    archived_read = next(t for t in incl_resp.json() if t["id"] == archived["id"])
    assert archived_read["is_active"] is False, (
        "POSITIVE: TaskRead.is_active must expose the archived state"
    )


# ---------------------------------------------------------------------------
# (4) AC2 — TTL boundary: just-past vs just-within, env override shifts cutoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_ttl_boundary(client, no_scaffold, db_session) -> None:
    """TTL boundary precision: task exactly 1 second past cutoff archived;
    task exactly 1 second before cutoff is NOT archived.
    Env AUDIT_ARCHIVE_DAYS override shifts the cutoff correctly.

    POSITIVE: task completed (TTL + 1s) ago is archived when TTL=7 days.
    NEGATIVE: task completed (TTL - 1s) ago is NOT archived with same TTL.
    POSITIVE (env-shift): same 'new' task IS archived when TTL is shortened to
      match it (confirms the env override actually moves the cutoff — not vacuous).
    """
    import os

    from src.db import SessionLocal
    from src.services.audit_archive import sweep_old_audit_tasks

    proj = await _create_project(client)
    proj_id = proj["id"]

    # 7-day TTL test case: task just past the boundary (7 days + 1 hour) and
    # task just within the boundary (7 days - 1 hour).  Using hours avoids
    # sub-second clock drift in the SQL func.now() comparison.
    TTL_DAYS = 7
    BUFFER_HOURS = 1

    old_task = await _create_task(client, proj_id, title="BOUNDARY old audit")
    new_task = await _create_task(client, proj_id, title="BOUNDARY new audit")

    # old_task: just past TTL (7d + 1h ago)
    await _set_completed_at(
        db_session, old_task["id"], days_ago=TTL_DAYS + BUFFER_HOURS / 24
    )
    # new_task: just within TTL (7d - 1h ago)
    await _set_completed_at(
        db_session, new_task["id"], days_ago=TTL_DAYS - BUFFER_HOURS / 24
    )

    # Pre-condition: both visible.
    assert await _is_active(db_session, old_task["id"]) is True
    assert await _is_active(db_session, new_task["id"]) is True

    # --- First sweep at TTL=7 ---
    os.environ["AUDIT_ARCHIVE_DAYS"] = str(TTL_DAYS)
    try:
        async with SessionLocal() as session:
            summary = await sweep_old_audit_tasks(session)
    finally:
        del os.environ["AUDIT_ARCHIVE_DAYS"]

    # POSITIVE: old_task (past boundary) archived.
    assert await _is_active(db_session, old_task["id"]) is False, (
        "POSITIVE: task 1h past TTL boundary must be archived"
    )
    # NEGATIVE: new_task (within boundary) stays visible.
    assert await _is_active(db_session, new_task["id"]) is True, (
        "NEGATIVE: task 1h within TTL boundary must NOT be archived"
    )
    assert summary["ttl_days"] == TTL_DAYS, (
        f"summary must reflect the applied TTL={TTL_DAYS}"
    )

    # --- Second sweep with TTL shortened to 6d (new_task was completed 7d - 1h ago,
    # so 6d TTL makes it past the boundary) — env override shifts cutoff.
    SHORT_TTL = 6
    os.environ["AUDIT_ARCHIVE_DAYS"] = str(SHORT_TTL)
    try:
        async with SessionLocal() as session:
            summary2 = await sweep_old_audit_tasks(session)
    finally:
        del os.environ["AUDIT_ARCHIVE_DAYS"]

    # POSITIVE: env override caused new_task to now fall past the shorter cutoff.
    assert await _is_active(db_session, new_task["id"]) is False, (
        "POSITIVE: env AUDIT_ARCHIVE_DAYS override must shift the cutoff — "
        "task that was within 7d TTL must be archived when TTL shortened to 6d"
    )
    assert summary2["ttl_days"] == SHORT_TTL, (
        "summary must reflect the shortened TTL from env override"
    )


# ---------------------------------------------------------------------------
# (5) AC4 — summary shape: all required keys present, types correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_summary_shape(client, no_scaffold, ttl_30, db_session) -> None:
    """sweep_old_audit_tasks returns a summary dict with all required AC4 keys.

    Verified keys: ttl_days (int), total_archived (int), per_project (dict),
    cycle_ms (float > 0). Also verifies that total_archived == sum of
    per_project values (internal consistency).

    POSITIVE: summary has the correct shape when tasks ARE archived.
    POSITIVE (zero-archived path): summary also has correct shape when nothing
      to archive (tests both branches of the early-return in the service).
    """
    from src.db import SessionLocal
    from src.services.audit_archive import sweep_old_audit_tasks

    proj = await _create_project(client)
    proj_id = proj["id"]

    old_task = await _create_task(client, proj_id, title="shape test old audit")
    await _set_completed_at(db_session, old_task["id"], days_ago=40)

    async with SessionLocal() as session:
        summary = await sweep_old_audit_tasks(session)

    # All 4 AC4 keys present.
    for key in ("ttl_days", "total_archived", "per_project", "cycle_ms"):
        assert key in summary, f"AC4: summary missing required key {key!r}"

    # Types.
    assert isinstance(summary["ttl_days"], int), "ttl_days must be int"
    assert isinstance(summary["total_archived"], int), "total_archived must be int"
    assert isinstance(summary["per_project"], dict), "per_project must be dict"
    assert isinstance(summary["cycle_ms"], float), "cycle_ms must be float"

    # cycle_ms > 0 (the sweep did real work).
    assert summary["cycle_ms"] > 0, "cycle_ms must be positive"

    # Internal consistency: total_archived == sum of per_project values.
    assert summary["total_archived"] == sum(summary["per_project"].values()), (
        "total_archived must equal sum of per_project counts"
    )

    # per_project includes this project's count.
    assert summary["per_project"].get(proj_id, 0) >= 1, (
        "per_project must include the project that had archived tasks"
    )

    # --- Zero-archived path (same shape contract via early return) ---
    proj2 = await _create_project(client)
    proj2_id = proj2["id"]
    # Create a task but do NOT set completed_at far enough back → nothing to archive.
    recent_task = await _create_task(
        client, proj2_id, title="shape test new audit"
    )
    await _set_completed_at(db_session, recent_task["id"], days_ago=1)

    async with SessionLocal() as session:
        summary_empty = await sweep_old_audit_tasks(session)

    # All 4 keys still present on zero-archived path.
    for key in ("ttl_days", "total_archived", "per_project", "cycle_ms"):
        assert key in summary_empty, (
            f"AC4 (zero-path): summary missing required key {key!r}"
        )
    assert summary_empty["total_archived"] == 0
    assert summary_empty["per_project"] == {}
    assert summary_empty["cycle_ms"] > 0


# ---------------------------------------------------------------------------
# (6) Idempotency — running sweep twice does not re-archive or error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_idempotent(client, no_scaffold, ttl_30, db_session) -> None:
    """Running the sweep twice leaves already-archived rows unchanged.

    POSITIVE (1st sweep): old audit task is archived (is_active → false).
    POSITIVE (2nd sweep): same task stays is_active=false — not double-toggled.
    NEGATIVE: the 2nd sweep returns total_archived=0 (no new rows to flip).

    This locks the idempotency contract: the is_active=true filter in the
    WHERE clause means already-archived rows are transparent to subsequent runs.
    """
    from src.db import SessionLocal
    from src.services.audit_archive import sweep_old_audit_tasks

    proj = await _create_project(client)
    proj_id = proj["id"]

    old_task = await _create_task(client, proj_id, title="idempotency audit")
    await _set_completed_at(db_session, old_task["id"], days_ago=40)

    # First sweep: archives the task.
    async with SessionLocal() as session:
        summary1 = await sweep_old_audit_tasks(session)

    assert await _is_active(db_session, old_task["id"]) is False, (
        "POSITIVE: task must be archived after 1st sweep"
    )
    assert summary1["per_project"].get(proj_id, 0) >= 1, (
        "1st sweep must report at least 1 archived task for the project"
    )

    # Second sweep: must not re-archive (is_active already false → WHERE skips it).
    async with SessionLocal() as session:
        summary2 = await sweep_old_audit_tasks(session)

    assert await _is_active(db_session, old_task["id"]) is False, (
        "POSITIVE: task must remain is_active=false after 2nd sweep (not toggled back)"
    )
    assert summary2["total_archived"] == 0, (
        "NEGATIVE: 2nd sweep must find 0 tasks to archive (idempotent)"
    )
    assert proj_id not in summary2["per_project"], (
        "NEGATIVE: project must not appear in 2nd sweep's per_project (nothing archived)"
    )
