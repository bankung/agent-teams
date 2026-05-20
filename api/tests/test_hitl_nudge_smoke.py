"""Kanban #1011 — HITL aging nudge contract smoke tests.

First-pass smokes covering:

  (1) Aged task gets nudge + last_nudge_at updates:
      A task whose created_at is older than the project threshold AND meets
      all scan predicates causes scan_and_nudge() to call deliver() and
      update last_nudge_at. POSITIVE: deliver called, last_nudge_at set.
      NEGATIVE: a fresh task (created just now) is NOT nudged in the same scan.

  (2) Dedup: last_nudge_at < now()-24h is required — a task nudged within 24h
      is skipped. POSITIVE: first scan fires. NEGATIVE: immediate re-scan does
      NOT fire again (last_nudge_at is now too recent).

  (3) nudge_disabled=true task is skipped even when aged past threshold.
      POSITIVE: non-disabled control task gets nudged.
      NEGATIVE: nudge_disabled=true task is not in nudged set.

  (4) POST /api/tasks/{id}/snooze endpoint shifts next eligible nudge time.
      POSITIVE: 200 response, last_nudge_at set to now + (hours-24)h,
      confirmed by a second scan NOT firing before the snooze window expires.

Scaffold pollution mitigation: `scaffold_project_folder` is monkeypatched to
a no-op so no context/projects/<name>/ directories land on disk during tests.
No manual cleanup needed when this mock is in place.

Rigorous suite (edge cases, project-level disable, project filter, concurrent
ticks, all-410 adapter paths, snooze boundary values, etc.) is dev-tester's
domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _unique_project_name(suffix: str = "") -> str:
    return f"nudge-smoke-{uuid.uuid4().hex[:8]}{suffix}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "team": "dev",
        "paths": {"web": "/tmp/ns/web", "api": "/tmp/ns/api", "db": "/tmp/ns/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
    }


async def _create_project(client, name: str | None = None) -> dict:
    n = name or _unique_project_name()
    resp = await client.post("/api/projects", json=_project_payload(n))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_hitl_task(
    client,
    project_id: int,
    title: str = "HITL smoke task",
    interaction_kind: str = "question",
) -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={
            "title": title,
            "project_id": project_id,
            "interaction_kind": interaction_kind,
            "question_payload": {
                "question": "What do you want to do?",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Fixture: mock scaffold_project_folder + stub deliver
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_scaffold(monkeypatch):
    """Monkeypatch scaffold_project_folder to a no-op so no on-disk dirs land."""
    import src.routers.projects as proj_router

    monkeypatch.setattr(proj_router, "scaffold_project_folder", lambda *a, **kw: None)


@pytest.fixture()
def stub_deliver(monkeypatch):
    """Replace notification_router.deliver with a recording stub.

    Returns a list that accumulates kwargs dicts for each call.
    The stub does NOT commit (unlike the real deliver) — it just records the
    call so we can assert on task_id, event_kind, payload shape.
    """
    import src.services.notification_router as nr

    calls: list[dict] = []

    async def _stub(**kwargs):
        calls.append(dict(kwargs))
        return {"task_id": kwargs.get("task_id"), "attempts": []}

    monkeypatch.setattr(nr, "deliver", _stub)
    yield calls


# ---------------------------------------------------------------------------
# Shared helper: set task.created_at back in time + set project threshold
# ---------------------------------------------------------------------------


async def _age_task_and_configure_project(
    db_session,
    task_id: int,
    project_id: int,
    *,
    age_hours: int = 30,
    threshold_hours: int = 24,
):
    """Manipulate test-DB rows directly so the scan query matches.

    - Moves task.created_at back `age_hours` hours.
    - Sets project.hitl_nudge_threshold_hours = threshold_hours.

    Uses the db_session fixture (direct AsyncSession) so we can write without
    going through the router (the threshold column is only on PATCH /api/projects
    which requires a separate call).
    """
    from sqlalchemy import update

    from src.models.project import Project
    from src.models.task import Task

    old_time = datetime.now(timezone.utc) - timedelta(hours=age_hours)

    await db_session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(created_at=old_time)
    )
    await db_session.execute(
        update(Project)
        .where(Project.id == project_id)
        .values(hitl_nudge_threshold_hours=threshold_hours)
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# (1) Aged task gets nudge + last_nudge_at updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aged_task_gets_nudged_and_last_nudge_at_set(
    client, no_scaffold, stub_deliver, db_session
) -> None:
    """scan_and_nudge() fires deliver() for an aged HITL task and stamps
    last_nudge_at.

    POSITIVE: deliver called with the correct task_id and event_kind.
    POSITIVE: last_nudge_at is non-null after the scan.
    NEGATIVE: a fresh task (created just now) is NOT in deliver calls.
    """
    from sqlalchemy import select

    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.hitl_nudge import scan_and_nudge

    proj = await _create_project(client)
    proj_id = proj["id"]

    # Aged task — will be nudged.
    task_aged = await _create_hitl_task(client, proj_id, "Aged HITL task")
    task_aged_id = task_aged["id"]

    # Fresh task (just created) — must NOT be nudged in this scan.
    task_fresh = await _create_hitl_task(client, proj_id, "Fresh HITL task (should not nudge)")
    task_fresh_id = task_fresh["id"]

    # Age the first task + configure the project threshold.
    await _age_task_and_configure_project(
        db_session, task_aged_id, proj_id, age_hours=30, threshold_hours=24
    )
    # Fresh task stays at created_at=now() — 0h age < 24h threshold.

    async with SessionLocal() as session:
        count = await scan_and_nudge(session)

    # POSITIVE: at least the aged task was attempted.
    assert count >= 1, "POSITIVE: scan must attempt at least the aged task"

    # POSITIVE: deliver called with correct task_id + event_kind.
    nudged_ids = {c["task_id"] for c in stub_deliver}
    assert task_aged_id in nudged_ids, (
        "POSITIVE: deliver must have been called for the aged task"
    )
    kinds = {c.get("event_kind") for c in stub_deliver if c["task_id"] == task_aged_id}
    assert "hitl_needed" in kinds, (
        "POSITIVE: event_kind must be 'hitl_needed'"
    )

    # NEGATIVE: fresh task must NOT have been nudged.
    assert task_fresh_id not in nudged_ids, (
        "NEGATIVE: fresh task (within threshold) must not be nudged"
    )

    # POSITIVE: last_nudge_at set on the aged task.
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Task).where(Task.id == task_aged_id))
        ).scalar_one()
    assert row.last_nudge_at is not None, (
        "POSITIVE: last_nudge_at must be set after scan"
    )


# ---------------------------------------------------------------------------
# (2) Dedup: re-scan within 24h does NOT fire again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_prevents_second_nudge_within_24h(
    client, no_scaffold, stub_deliver, db_session
) -> None:
    """An aged task that was nudged within the last 24h is excluded on re-scan.

    POSITIVE: first scan fires deliver() for the aged task.
    NEGATIVE: immediate re-scan does NOT fire deliver() for the same task
    (because last_nudge_at is now within the 24h window).
    """
    from src.db import SessionLocal
    from src.services.hitl_nudge import scan_and_nudge

    proj = await _create_project(client)
    proj_id = proj["id"]
    task = await _create_hitl_task(client, proj_id, "Dedup test task")
    task_id = task["id"]

    await _age_task_and_configure_project(
        db_session, task_id, proj_id, age_hours=30, threshold_hours=24
    )

    # POSITIVE: first scan fires.
    async with SessionLocal() as session:
        count1 = await scan_and_nudge(session)
    assert count1 >= 1, "POSITIVE: first scan must attempt the aged task"
    first_calls = len([c for c in stub_deliver if c["task_id"] == task_id])
    assert first_calls >= 1, "POSITIVE: deliver must have been called for the task"

    # NEGATIVE: immediate re-scan — last_nudge_at is just set to now(),
    # so the dedup predicate (< now()-24h) excludes the task.
    async with SessionLocal() as session:
        count2 = await scan_and_nudge(session)

    second_calls = len([c for c in stub_deliver if c["task_id"] == task_id])
    assert second_calls == first_calls, (
        f"NEGATIVE: re-scan must NOT fire again (calls before={first_calls}, "
        f"calls after={second_calls})"
    )


# ---------------------------------------------------------------------------
# (3) nudge_disabled=true task is skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nudge_disabled_task_is_skipped(
    client, no_scaffold, stub_deliver, db_session
) -> None:
    """A task with nudge_disabled=true is never nudged even when aged.

    POSITIVE: control task (nudge_disabled=false) IS nudged.
    NEGATIVE: disabled task is NOT in deliver calls.
    """
    from sqlalchemy import update

    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.hitl_nudge import scan_and_nudge

    proj = await _create_project(client)
    proj_id = proj["id"]

    control_task = await _create_hitl_task(client, proj_id, "Control — should nudge")
    control_id = control_task["id"]

    disabled_task = await _create_hitl_task(client, proj_id, "Disabled — must not nudge")
    disabled_id = disabled_task["id"]

    # Age both tasks + configure threshold.
    await _age_task_and_configure_project(
        db_session, control_id, proj_id, age_hours=30, threshold_hours=24
    )
    await _age_task_and_configure_project(
        db_session, disabled_id, proj_id, age_hours=30, threshold_hours=24
    )

    # Flip nudge_disabled on the second task.
    await db_session.execute(
        update(Task).where(Task.id == disabled_id).values(nudge_disabled=True)
    )
    await db_session.commit()

    async with SessionLocal() as session:
        await scan_and_nudge(session)

    nudged_ids = {c["task_id"] for c in stub_deliver}

    # POSITIVE: control task nudged.
    assert control_id in nudged_ids, (
        "POSITIVE: control task (nudge_disabled=false) must be nudged"
    )

    # NEGATIVE: disabled task NOT nudged.
    assert disabled_id not in nudged_ids, (
        "NEGATIVE: task with nudge_disabled=true must not be nudged"
    )


# ---------------------------------------------------------------------------
# (4) POST /api/tasks/{id}/snooze shifts next eligible nudge time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_endpoint_shifts_next_eligible_nudge(
    client, no_scaffold, stub_deliver, db_session
) -> None:
    """POST /api/tasks/{id}/snooze sets last_nudge_at so the next scan skips.

    POSITIVE: 200 response, last_nudge_at is set (non-null), and the value
    puts next eligible nudge in the future (i.e. a subsequent scan does NOT
    fire deliver() for the snoozed task).

    NEGATIVE: scan immediately after snooze does NOT call deliver() for the
    snoozed task.
    """
    from sqlalchemy import select

    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.hitl_nudge import scan_and_nudge

    proj = await _create_project(client)
    proj_id = proj["id"]
    task = await _create_hitl_task(client, proj_id, "Snooze smoke task")
    task_id = task["id"]

    await _age_task_and_configure_project(
        db_session, task_id, proj_id, age_hours=30, threshold_hours=24
    )

    # POST snooze with 4 hours.
    snooze_resp = await client.post(
        f"/api/tasks/{task_id}/snooze",
        headers={"X-Project-Id": str(proj_id)},
        json={"hours": 4},
    )
    assert snooze_resp.status_code == 200, snooze_resp.text
    body = snooze_resp.json()

    # POSITIVE: response shape — last_nudge_at is set, nudge_disabled is present.
    assert body["last_nudge_at"] is not None, (
        "POSITIVE: snooze response must have last_nudge_at set"
    )
    assert "nudge_disabled" in body, "POSITIVE: response must include nudge_disabled field"

    # POSITIVE: last_nudge_at stored in DB is consistent with a 4h snooze
    # (it should be roughly now - 20h so next eligible = now + 4h).
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Task).where(Task.id == task_id))
        ).scalar_one()
    assert row.last_nudge_at is not None, "POSITIVE: last_nudge_at must be stored"

    # Verify the value puts next eligible in the future (last_nudge_at + 24h > now).
    now = datetime.now(timezone.utc)
    next_eligible = row.last_nudge_at.replace(tzinfo=timezone.utc) + timedelta(hours=24)
    assert next_eligible > now, (
        "POSITIVE: snooze must defer next eligible nudge to the future"
    )

    # NEGATIVE: an immediate scan must NOT fire deliver() for the snoozed task.
    stub_deliver.clear()
    async with SessionLocal() as session:
        await scan_and_nudge(session)

    nudged_after_snooze = {c["task_id"] for c in stub_deliver}
    assert task_id not in nudged_after_snooze, (
        "NEGATIVE: snoozed task must NOT be nudged by an immediate scan"
    )
