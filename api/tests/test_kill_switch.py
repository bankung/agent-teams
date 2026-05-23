"""Kanban #1209 — GOV1 hard kill switch backend tests.

Coverage:
- POST /api/projects/{id}/kill — happy path + 409 idempotent guard.
- POST /api/projects/{id}/revive — happy path + 409 idempotent guard.
- Kill drains recurring tasks (next_fire_at → NULL).
- Kill freezes open TODO + in-flight tasks via kill_frozen.
- Revive recomputes recurring next_fire_at + clears kill_frozen.
- POST /api/tasks against killed project returns 423 Locked.
- POST /api/tasks against non-killed project works normally.
- projects_audit rows written for both kill + revive with non-empty drain_summary.
- Multi-project scoping: kill A doesn't block B's POST.

Tests run against `agent_teams_test` (per conftest.py rewrite). Live
`agent_teams` row count must NOT drift across the session — the
`_live_db_row_count_invariant` fixture in conftest.py asserts that.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k1209 kill-switch fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


def _task_create_payload(
    project_id: int,
    *,
    title: str = "k1209 fixture task",
    process_status: int = 1,
    recurrence_rule: str | None = None,
    is_template: bool = False,
    next_fire_at: str | None = None,
) -> dict:
    body: dict = {
        "project_id": project_id,
        "title": title,
        "description": "k1209 test task",
        "process_status": process_status,
    }
    if recurrence_rule is not None:
        body["recurrence_rule"] = recurrence_rule
        body["recurrence_timezone"] = "UTC"
    if is_template:
        body["is_template"] = True
    if next_fire_at is not None:
        body["next_fire_at"] = next_fire_at
    return body


_VALID_KILL_REASON = "smoke test kill — Kanban #1209 verifying GOV1 hard switch"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1209"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, **kwargs) -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=_task_create_payload(project_id, **kwargs),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. happy-path kill + 409 idempotent guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_project_happy_path_returns_drain_summary(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        kill_resp = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        assert kill_resp.status_code == 200, kill_resp.text
        body = kill_resp.json()
        assert body["success"] is True
        assert body["project_id"] == project_id
        assert body["action"] == "kill"
        assert body["is_killed"] is True
        assert body["killed_reason"] == _VALID_KILL_REASON
        assert body["killed_at"] is not None
        assert isinstance(body["drain_summary"], dict)
        # zero-task project: counts are zero but the keys are present.
        assert body["drain_summary"]["recurring_suspended"] == 0
        assert body["drain_summary"]["in_flight_marked"] == 0
        assert body["drain_summary"]["frozen_tasks"] == 0
        assert body["drain_summary"]["force"] is False
        assert isinstance(body["audit_id"], int)
        # GET reflects the new state.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["is_killed"] is True
        assert get_resp.json()["killed_reason"] == _VALID_KILL_REASON
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_kill_project_idempotent_returns_409(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        first = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON + " (retry)"},
        )
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        assert "already killed" in detail
        assert f"Project {project_id}" in detail
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_kill_project_rejects_short_reason_422(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": "too short"},  # 9 chars
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_kill_project_404_on_missing(client) -> None:
    resp = await client.post(
        "/api/projects/9999999/kill",
        json={"reason": _VALID_KILL_REASON},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 2. revive happy path + 409 idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revive_project_happy_path(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        revive_resp = await client.post(
            f"/api/projects/{project_id}/revive", json={}
        )
        assert revive_resp.status_code == 200, revive_resp.text
        body = revive_resp.json()
        assert body["success"] is True
        assert body["action"] == "revive"
        assert body["is_killed"] is False
        # D4 — killed_at + killed_reason PRESERVED as history.
        assert body["killed_at"] is not None
        assert body["killed_reason"] == _VALID_KILL_REASON
        assert body["drain_summary"]["unfrozen_tasks"] == 0
        # GET reflects revived state.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_killed"] is False
        assert get_resp.json()["killed_at"] is not None  # history preserved
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_revive_non_killed_project_returns_409(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            f"/api/projects/{project_id}/revive", json={}
        )
        assert resp.status_code == 409, resp.text
        assert "not killed" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 3. drain semantics — recurring tasks suspended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_suspends_recurring_template_via_kill_frozen(
    client, scaffold_cleanup, db_session
) -> None:
    """A template task (is_template=true) keeps next_fire_at intact (CHECK
    forbids the null) but receives kill_frozen=true as the suspend marker.
    Scheduler integration to honor kill_frozen is followup work.
    """
    from src.models.task import Task

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        # Template task. is_template=true requires recurrence_rule + next_fire_at
        # per ck_tasks_template_recurrence_complete.
        from datetime import datetime, timedelta, timezone

        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        task = await _create_task(
            client,
            project_id,
            title="recurring template",
            recurrence_rule="0 9 * * *",  # daily 09:00
            is_template=True,
            next_fire_at=future,
        )
        task_id = task["id"]
        assert task["next_fire_at"] is not None
        assert task["recurrence_rule"] == "0 9 * * *"

        # Kill the project.
        kill_resp = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        assert kill_resp.status_code == 200, kill_resp.text
        assert kill_resp.json()["drain_summary"]["recurring_suspended"] == 1

        # Confirm next_fire_at PRESERVED (CHECK forbids null) and recurrence_rule
        # preserved. kill_frozen flipped to true on the template.
        get_resp = await client.get(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert get_resp.status_code == 200, get_resp.text
        body = get_resp.json()
        assert body["next_fire_at"] is not None  # CHECK protects this
        assert body["recurrence_rule"] == "0 9 * * *"

        # Verify kill_frozen via direct DB session (not exposed via TaskRead v1).
        row = (
            await db_session.execute(
                select(Task.kill_frozen, Task.is_template).where(Task.id == task_id)
            )
        ).first()
        assert row.is_template is True
        assert row.kill_frozen is True
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_revive_clears_kill_frozen_on_template(
    client, scaffold_cleanup, db_session
) -> None:
    """Templates suspended via kill_frozen get unfrozen on revive. The
    `resumed_recurring` counter is 0 for templates (next_fire_at was never
    NULL'd) — they ride out the kill via kill_frozen only. Drain accounting:
    unfrozen_tasks counts the template; resumed_recurring stays 0.
    """
    from src.models.task import Task

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        from datetime import datetime, timedelta, timezone

        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        task = await _create_task(
            client,
            project_id,
            title="recurring re-arm",
            recurrence_rule="0 9 * * *",
            is_template=True,
            next_fire_at=future,
        )
        task_id = task["id"]
        await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        revive_resp = await client.post(
            f"/api/projects/{project_id}/revive", json={}
        )
        assert revive_resp.status_code == 200, revive_resp.text
        # Template: resumed_recurring=0 (never NULL'd); unfrozen_tasks=1.
        drain = revive_resp.json()["drain_summary"]
        assert drain["resumed_recurring"] == 0
        assert drain["unfrozen_tasks"] == 1
        # Verify the row still has its next_fire_at + kill_frozen cleared.
        get_resp = await client.get(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert get_resp.json()["next_fire_at"] is not None
        row = (
            await db_session.execute(
                select(Task.kill_frozen).where(Task.id == task_id)
            )
        ).first()
        assert row.kill_frozen is False
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 4. drain semantics — open TODO + in-flight frozen / unfrozen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_freezes_open_todos_and_revive_unfreezes(
    client, scaffold_cleanup, db_session
) -> None:
    """Two open TODO tasks get kill_frozen=true on kill, false on revive."""
    from src.models.task import Task

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        t1 = await _create_task(client, project_id, title="todo a")
        t2 = await _create_task(client, project_id, title="todo b")
        ids = [t1["id"], t2["id"]]

        kill_resp = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        assert kill_resp.status_code == 200, kill_resp.text
        assert kill_resp.json()["drain_summary"]["frozen_tasks"] == 2

        # Verify via direct DB session (kill_frozen not exposed via TaskRead in v1).
        rows = (
            await db_session.execute(
                select(Task.id, Task.kill_frozen).where(Task.id.in_(ids))
            )
        ).all()
        kill_frozen_by_id = {r.id: r.kill_frozen for r in rows}
        assert all(kill_frozen_by_id[i] for i in ids), kill_frozen_by_id

        revive_resp = await client.post(
            f"/api/projects/{project_id}/revive", json={}
        )
        assert revive_resp.status_code == 200, revive_resp.text
        assert revive_resp.json()["drain_summary"]["unfrozen_tasks"] == 2

        # Re-query post-revive — make sure to expire identity-mapped state.
        db_session.expire_all()
        rows_post = (
            await db_session.execute(
                select(Task.id, Task.kill_frozen).where(Task.id.in_(ids))
            )
        ).all()
        kill_frozen_post = {r.id: r.kill_frozen for r in rows_post}
        assert not any(kill_frozen_post[i] for i in ids), kill_frozen_post
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 5. POST /api/tasks against killed project returns 423
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_on_killed_project_returns_423(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(project_id, title="should fail"),
        )
        assert resp.status_code == 423, resp.text
        detail = resp.json()["detail"]
        # Detail is a dict with message + killed_at + killed_reason
        assert isinstance(detail, dict)
        assert "killed" in detail["message"].lower()
        assert detail["killed_reason"] == _VALID_KILL_REASON
        assert detail["killed_at"] is not None
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_on_non_killed_project_works(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(project_id, title="normal flow"),
        )
        assert resp.status_code == 201, resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 6. projects_audit rows written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projects_audit_rows_written_for_kill_and_revive(
    client, scaffold_cleanup, db_session
) -> None:
    from src.models.projects_audit import ProjectsAudit

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        kill_resp = await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": _VALID_KILL_REASON},
        )
        kill_audit_id = kill_resp.json()["audit_id"]
        revive_resp = await client.post(
            f"/api/projects/{project_id}/revive", json={}
        )
        revive_audit_id = revive_resp.json()["audit_id"]

        rows = (
            await db_session.execute(
                select(ProjectsAudit).where(ProjectsAudit.project_id == project_id)
                .order_by(ProjectsAudit.created_at.asc())
            )
        ).scalars().all()
        # Filter to just the rows for this project from THIS test (in case the
        # session has audit rows from other tests via the shared DB).
        assert len(rows) >= 2, rows
        ids = {r.id for r in rows}
        assert kill_audit_id in ids
        assert revive_audit_id in ids
        kill_row = next(r for r in rows if r.id == kill_audit_id)
        revive_row = next(r for r in rows if r.id == revive_audit_id)
        assert kill_row.action == "kill"
        assert kill_row.reason == _VALID_KILL_REASON
        assert kill_row.actor == "operator"
        assert kill_row.drain_summary  # non-empty
        assert "recurring_suspended" in kill_row.drain_summary
        assert revive_row.action == "revive"
        assert revive_row.reason is None
        assert "unfrozen_tasks" in revive_row.drain_summary
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_kill_endpoint_honors_x_actor_header(
    client, scaffold_cleanup, db_session
) -> None:
    from src.models.projects_audit import ProjectsAudit

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            f"/api/projects/{project_id}/kill",
            headers={"X-Actor": "project-auditor"},
            json={"reason": _VALID_KILL_REASON},
        )
        assert resp.status_code == 200, resp.text
        audit_id = resp.json()["audit_id"]
        row = (
            await db_session.execute(
                select(ProjectsAudit).where(ProjectsAudit.id == audit_id)
            )
        ).scalar_one()
        assert row.actor == "project-auditor"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 7. multi-project scoping — kill A doesn't block B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_on_project_a_does_not_block_project_b_post(
    client, scaffold_cleanup
) -> None:
    project_a = await _create_project(client, scaffold_cleanup)
    project_b = await _create_project(client, scaffold_cleanup)
    a_id, b_id = project_a["id"], project_b["id"]
    try:
        await client.post(
            f"/api/projects/{a_id}/kill",
            json={"reason": _VALID_KILL_REASON + " for A"},
        )
        # POST against B must still succeed.
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(b_id)},
            json=_task_create_payload(b_id, title="B is fine"),
        )
        assert resp.status_code == 201, resp.text
        # And POST against A still fails.
        a_resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(a_id)},
            json=_task_create_payload(a_id, title="A is blocked"),
        )
        assert a_resp.status_code == 423, a_resp.text
    finally:
        await client.delete(f"/api/projects/{a_id}")
        await client.delete(f"/api/projects/{b_id}")
