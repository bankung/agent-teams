"""Tests for the recurrence subsystem (Kanban #707, T2).

Covers:
1. Pure-function `next_cron_fire` (UTC + Asia/Bangkok).
2. `fire_template` spawn semantics + audit-row check.
3. `tick_once` Path A — catch-up policy (single fire on resume).
4. `tick_once` Path B — one-shot scheduled task transition.
5. `tick_once` mixed tick (template + one-shot).
6. `tick_once` idle path (no due rows; existing tasks untouched).
7. POST /api/tasks/{id}/fire-now — happy path, non-template 400, 404, header gate.
8. PATCH recurrence_rule / timezone re-computes next_fire_at.
9. Lifespan smoke test (enter+exit without raising).
10. Source-text-lock for the fire-now detail string.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(
    name: str, *, team: str = "dev", is_active: bool = False
) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": is_active,
        "team": team,
    }


def _future_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# =============================================================================
# 1. Pure-function tests
# =============================================================================


def test_next_cron_fire_every_minute_utc() -> None:
    from src.services.recurrence import next_cron_fire

    nxt = next_cron_fire("* * * * *", "UTC")
    assert nxt.tzinfo is not None
    delta = nxt - datetime.now(timezone.utc)
    # Always within (0, 60s] — strictly future, at most one minute ahead.
    assert timedelta(seconds=0) < delta <= timedelta(seconds=61)


def test_next_cron_fire_monday_9am_bangkok() -> None:
    """0 9 * * MON in Asia/Bangkok — verify the result is Monday 09:00 BKK
    (UTC=02:00) and strictly in the future."""
    from src.services.recurrence import next_cron_fire

    nxt = next_cron_fire("0 9 * * MON", "Asia/Bangkok")
    assert nxt.tzinfo is not None
    bkk = nxt.astimezone(ZoneInfo("Asia/Bangkok"))
    assert bkk.weekday() == 0  # Monday
    assert bkk.hour == 9 and bkk.minute == 0
    assert nxt > datetime.now(timezone.utc)


def test_next_cron_fire_with_anchor_collapses_missed_window() -> None:
    """Anchor 3 days in the past on a daily cron returns ONE next slot in the
    past+24h window — i.e., a single fire, not three."""
    from src.services.recurrence import next_cron_fire

    anchor = datetime.now(timezone.utc) - timedelta(days=3)
    nxt = next_cron_fire("0 0 * * *", "UTC", anchor=anchor)
    # Single advance from anchor — within (anchor, anchor+24h].
    assert anchor < nxt <= anchor + timedelta(days=1, seconds=1)


# =============================================================================
# 2-6. Spawn / tick / catch-up / Path B / mixed / idle — DB-backed
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_spawn_unit(client, scaffold_cleanup, db_session) -> None:
    """fire_template spawns a child + advances next_fire_at + writes audit."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("fire-tpl")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "weekly review",
                "description": "do the thing",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "*/5 * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
                "priority": 2,
            },
            headers=headers,
        )
        assert tpl_resp.status_code == 201, tpl_resp.text
        tpl_id = tpl_resp.json()["id"]
        original_next = tpl_resp.json()["next_fire_at"]

        # Call fire_template directly — bypasses the scheduler tick.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            child_id = child.id
            new_next = tpl.next_fire_at

        # Audit: tasks_history shows at least one row for the child + UPDATE on the template.
        rows = (
            await db_session.execute(
                text(
                    "SELECT task_id, operation FROM tasks_history "
                    "WHERE task_id IN (:tpl_id, :child_id) ORDER BY changed_at"
                ),
                {"tpl_id": tpl_id, "child_id": child_id},
            )
        ).all()
        # Trigger fires AFTER UPDATE / DELETE (not INSERT); template's UPDATE
        # (next_fire_at advance) MUST land. Child has no UPDATE yet so no row
        # is required — depending on trigger config it may still be absent.
        tpl_history_rows = [r for r in rows if r[0] == tpl_id]
        assert any(r[1] == "U" for r in tpl_history_rows), (
            f"expected 'U' audit row for template {tpl_id}, got {rows!r}"
        )

        # Child row contents
        child_resp = await client.get(f"/api/tasks/{child_id}", headers=headers)
        body = child_resp.json()
        assert body["title"] == "weekly review"
        assert body["description"] == "do the thing"
        assert body["priority"] == 2
        assert body["task_kind"] == "ai"
        assert body["run_mode"] == "auto_pickup"
        assert body["is_template"] is False
        assert body["spawned_from_task_id"] == tpl_id
        assert body["process_status"] == 1

        # Template's next_fire_at advanced
        assert new_next is not None
        assert new_next.isoformat() != original_next

        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_tick_once_catch_up_single_fire(client, scaffold_cleanup) -> None:
    """Template with next_fire_at=3 days ago on a daily cron — tick_once
    spawns EXACTLY 1 child and advances next_fire_at to a future slot."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import tick_once

    name = _unique_name("catch-up")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Create template with future next_fire_at (Pydantic 422 if past+invalid).
        tpl_resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "daily catch-up",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 0 * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = tpl_resp.json()["id"]

        # Force next_fire_at to 3 days ago via PATCH (allowed — column is PATCH-able).
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"next_fire_at": past},
            headers=headers,
        )

        # Run one tick.
        result = await tick_once(SessionLocal)
        assert result["spawned"] == 1, result
        assert result["transitioned"] == 0, result

        # Verify exactly 1 child + template's next_fire_at is in the future.
        children = await client.get(
            f"/api/tasks?limit=500", headers=headers
        )
        spawned = [
            t for t in children.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(spawned) == 1, f"expected 1 child, got {len(spawned)}"

        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.next_fire_at > datetime.now(timezone.utc), (
                f"next_fire_at={tpl.next_fire_at} must be in the future"
            )

        await client.delete(f"/api/tasks/{spawned[0]['id']}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_tick_once_path_b_one_shot(client, scaffold_cleanup, db_session) -> None:
    """One-shot scheduled task with scheduled_at=now-1s → tick_once transitions
    it: ps 1->2, started_at stamped, scheduled_at cleared, audit U row."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import tick_once

    name = _unique_name("oneshot")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Create with future scheduled_at (Pydantic OK), then PATCH to past.
        post = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "one-shot due",
                "scheduled_at": _future_iso(),
            },
            headers=headers,
        )
        task_id = post.json()["id"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        await client.patch(
            f"/api/tasks/{task_id}",
            json={"scheduled_at": past},
            headers=headers,
        )

        result = await tick_once(SessionLocal)
        assert result["transitioned"] >= 1, result
        assert result["spawned"] == 0, result

        # Verify final state
        async with SessionLocal() as db:
            row = await db.get(Task, task_id)
            assert row.process_status == 2
            assert row.started_at is not None
            assert row.scheduled_at is None

        # Audit U row present
        audit = (
            await db_session.execute(
                text(
                    "SELECT operation FROM tasks_history WHERE task_id = :tid"
                ),
                {"tid": task_id},
            )
        ).all()
        assert any(r[0] == "U" for r in audit), (
            f"expected at least one 'U' audit row, got {audit!r}"
        )

        await client.delete(f"/api/tasks/{task_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_tick_once_mixed_template_and_oneshot(
    client, scaffold_cleanup
) -> None:
    """One template (due) + one one-shot (due) — tick_once handles both."""
    from src.db import SessionLocal
    from src.services.recurrence import tick_once

    name = _unique_name("mixed")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "tpl-mixed",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "*/5 * * * *",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = tpl.json()["id"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        await client.patch(
            f"/api/tasks/{tpl_id}", json={"next_fire_at": past}, headers=headers
        )

        oneshot = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "oneshot-mixed",
                "scheduled_at": _future_iso(),
            },
            headers=headers,
        )
        oneshot_id = oneshot.json()["id"]
        await client.patch(
            f"/api/tasks/{oneshot_id}",
            json={"scheduled_at": past},
            headers=headers,
        )

        result = await tick_once(SessionLocal)
        assert result["spawned"] >= 1, result
        assert result["transitioned"] >= 1, result

        # cleanup
        children = await client.get("/api/tasks?limit=500", headers=headers)
        for t in children.json():
            if t.get("spawned_from_task_id") == tpl_id:
                await client.delete(f"/api/tasks/{t['id']}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
        await client.delete(f"/api/tasks/{oneshot_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_tick_once_idle_no_due_rows(client) -> None:
    """No due templates + no due one-shots → 0 spawned + 0 transitioned. The
    seeded non-template tasks are untouched (proxy: count stays the same)."""
    from src.db import SessionLocal
    from src.services.recurrence import tick_once

    headers = {"X-Project-Id": "1"}
    before = await client.get("/api/tasks?limit=500", headers=headers)
    before_ids = sorted(t["id"] for t in before.json())

    result = await tick_once(SessionLocal)
    assert result["spawned"] == 0, result
    assert result["transitioned"] == 0, result

    after = await client.get("/api/tasks?limit=500", headers=headers)
    after_ids = sorted(t["id"] for t in after.json())
    assert before_ids == after_ids, "idle tick must not touch existing rows"


# =============================================================================
# 7. POST /api/tasks/{id}/fire-now
# =============================================================================


@pytest.mark.asyncio
async def test_fire_now_template_200(client, scaffold_cleanup) -> None:
    name = _unique_name("fn-ok")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "fn template",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "recurrence_timezone": "Asia/Bangkok",
                "next_fire_at": _future_iso(24),
            },
            headers=headers,
        )
        tpl_id = tpl.json()["id"]
        original_next = tpl.json()["next_fire_at"]

        resp = await client.post(
            f"/api/tasks/{tpl_id}/fire-now", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["spawned_from_task_id"] == tpl_id
        assert body["is_template"] is False
        assert body["process_status"] == 1

        # Template's next_fire_at advanced to next future Monday 9am Bangkok.
        tpl_after = await client.get(f"/api/tasks/{tpl_id}", headers=headers)
        assert tpl_after.json()["next_fire_at"] != original_next

        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_fire_now_non_template_400(client, scaffold_cleanup) -> None:
    name = _unique_name("fn-not-tpl")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        task = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "not a template"},
            headers=headers,
        )
        task_id = task.json()["id"]

        resp = await client.post(
            f"/api/tasks/{task_id}/fire-now", headers=headers
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {
            "detail": (
                f"Task id={task_id} is not a template; fire-now only "
                "applies to is_template=true"
            )
        }

        await client.delete(f"/api/tasks/{task_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_fire_now_404_missing(client) -> None:
    headers = {"X-Project-Id": "1"}
    resp = await client.post("/api/tasks/999999999/fire-now", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fire_now_header_missing_400(client, scaffold_cleanup) -> None:
    """No X-Project-Id header → 400 (header gate fires before body parsing)."""
    name = _unique_name("fn-noh")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    try:
        tpl = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "fn header",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "* * * * *",
                "next_fire_at": _future_iso(),
            },
            headers={"X-Project-Id": str(project_id)},
        )
        tpl_id = tpl.json()["id"]

        resp = await client.post(f"/api/tasks/{tpl_id}/fire-now")
        assert resp.status_code == 400
        assert (
            resp.json()["detail"]
            == "X-Project-Id header is required for task endpoints"
        )

        await client.delete(
            f"/api/tasks/{tpl_id}", headers={"X-Project-Id": str(project_id)}
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_fire_now_header_mismatch_400(client, scaffold_cleanup) -> None:
    """X-Project-Id != row's project_id → 400 with locked detail."""
    name = _unique_name("fn-mis")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    try:
        tpl = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "fn mismatch",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "* * * * *",
                "next_fire_at": _future_iso(),
            },
            headers={"X-Project-Id": str(project_id)},
        )
        tpl_id = tpl.json()["id"]

        resp = await client.post(
            f"/api/tasks/{tpl_id}/fire-now",
            headers={"X-Project-Id": "9999"},
        )
        assert resp.status_code == 400
        assert "does not belong to project_id" in resp.json()["detail"]

        await client.delete(
            f"/api/tasks/{tpl_id}", headers={"X-Project-Id": str(project_id)}
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 8. PATCH recurrence_rule / timezone re-computes next_fire_at
# =============================================================================


@pytest.mark.asyncio
async def test_patch_recurrence_rule_recomputes_next_fire_at(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("patch-rule")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Initial template with next_fire_at far in the future
        far_future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        tpl = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "rule-change",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "next_fire_at": far_future,
            },
            headers=headers,
        )
        tpl_id = tpl.json()["id"]
        original_nfa = tpl.json()["next_fire_at"]

        # PATCH rule alone — should recompute next_fire_at from now()
        resp = await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"recurrence_rule": "*/5 * * * *"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        new_nfa = resp.json()["next_fire_at"]
        assert new_nfa != original_nfa
        # Recomputed from now(): every 5 minutes → at most ~5 minutes ahead.
        new_dt = datetime.fromisoformat(new_nfa.replace("Z", "+00:00"))
        delta = new_dt - datetime.now(timezone.utc)
        assert timedelta(seconds=0) < delta <= timedelta(minutes=6)

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_timezone_alone_recomputes_next_fire_at(
    client, scaffold_cleanup
) -> None:
    """Changing only the timezone is enough — cron is TZ-sensitive."""
    name = _unique_name("patch-tz")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        far_future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        tpl = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "tz-change",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "recurrence_timezone": "UTC",
                "next_fire_at": far_future,
            },
            headers=headers,
        )
        tpl_id = tpl.json()["id"]
        original_nfa = tpl.json()["next_fire_at"]

        resp = await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"recurrence_timezone": "Asia/Bangkok"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["next_fire_at"] != original_nfa

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 9. Lifespan smoke
# =============================================================================


@pytest.mark.asyncio
async def test_lifespan_enter_exit_smoke() -> None:
    """The lifespan callable enters and exits without raising. Scheduler is
    disabled via APP_SCHEDULER_DISABLE so this exercises only the no-op path,
    confirming the wiring is import-clean."""
    from src.main import lifespan

    # Sanity — env var is set by conftest module-level
    assert os.environ.get("APP_SCHEDULER_DISABLE", "").lower() == "true"

    from src.main import app

    async with lifespan(app):
        pass


@pytest.mark.asyncio
async def test_lifespan_with_scheduler_enabled_smoke(monkeypatch) -> None:
    """Force-enable the scheduler via env override — verify it starts and
    shuts down without raising. Tick interval set to a large value so no
    background fire happens during the test window."""
    monkeypatch.setenv("APP_SCHEDULER_DISABLE", "false")
    monkeypatch.setenv("APP_SCHEDULER_TICK_SECONDS", "3600")

    from src.main import app, lifespan

    async with lifespan(app):
        from src.main import _scheduler

        assert _scheduler is not None
        assert _scheduler.running is True
        # job is registered
        job = _scheduler.get_job("recurrence_tick")
        assert job is not None


# =============================================================================
# 10. Source-text-locks
# =============================================================================


def test_fire_now_detail_string_pinned_in_router_source() -> None:
    """Source-text-lock per Kanban #122 pattern."""
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")
    assert "_DETAIL_FIRE_NOW_NOT_TEMPLATE_TEMPLATE" in source
    pinned = (
        '"Task id={task_id} is not a template; fire-now only applies to '
        'is_template=true"'
    )
    assert pinned in source, "fire-now detail string drifted in routers/tasks.py"


def test_recurrence_module_docstring_documents_two_paths() -> None:
    """Module docstring on services/recurrence.py must explain both paths."""
    from src.services import recurrence

    doc = recurrence.__doc__ or ""
    assert "Templates" in doc and "One-shots" in doc
    assert "catch-up" in doc.lower() or "single-fire" in doc.lower()
