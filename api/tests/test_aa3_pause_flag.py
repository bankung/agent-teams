"""Kanban #1211 — AA3 soft-pause governance backend tests.

Coverage:
- POST /api/projects/{id}/pause — happy path + 409 idempotent + 422 reason.
- POST /api/projects/{id}/unpause — happy path + 409 idempotent.
- DB CHECK mutex: cannot is_paused=true on an is_killed=true project (via ORM).
- POST /api/tasks against paused project returns 423 (no escape hatch).
- POST /api/tasks against paused project WITH allow_during_pause=true +
  valid reason → 201 + projects_audit row with action='pause_override'.
- POST /api/tasks against paused project WITH allow_during_pause=true but
  reason too short → 422 (Pydantic boundary).
- PATCH audit task to DONE creates a flag task (recommendation=review).
- PATCH 2nd audit task to DONE for same project UPDATES existing flag
  (breach_streak_days=2; no new flag row).
- PATCH audit task to DONE with recommendation=pause ALSO sets is_paused.
- POST /api/tasks/{flag_id}/resolve-flag action=continue → flag DONE + unpause.
- POST /resolve-flag action=adjust_continue + adjustments → applied + unpause.
- POST /resolve-flag action=keep_paused → flag DONE; is_paused stays.
- POST /resolve-flag action=terminate → AA1 kill called + flag DONE.
- Resolve-flag rolls back on bad adjustments (no partial state).

Runs against `agent_teams_test` per conftest.py rewrite. Live `agent_teams`
row count MUST NOT drift across the session — `_live_db_row_count_invariant`
in conftest.py asserts that.
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
        "description": f"k1211 fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


def _task_create_payload(
    project_id: int,
    *,
    title: str = "k1211 fixture task",
    process_status: int = 1,
    task_type: str = "feature",
    allow_during_pause: bool = False,
    allow_during_pause_reason: str | None = None,
) -> dict:
    body: dict = {
        "project_id": project_id,
        "title": title,
        "description": "k1211 test task",
        "process_status": process_status,
        "task_type": task_type,
    }
    if allow_during_pause:
        body["allow_during_pause"] = True
    if allow_during_pause_reason is not None:
        body["allow_during_pause_reason"] = allow_during_pause_reason
    return body


_VALID_PAUSE_REASON = "smoke pause — Kanban #1211 AA3 verification"
_VALID_OVERRIDE_REASON = "operator approves bypass for hotfix work"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1211"))
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
# 1. pause happy path + 409 idempotent + 422 short reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_project_happy_path(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["action"] == "pause"
        assert body["is_paused"] is True
        assert body["paused_reason"] == _VALID_PAUSE_REASON
        assert body["paused_at"] is not None
        assert isinstance(body["drain_summary"], dict)
        # Soft-pause does NOT freeze in-flight or open TODOs (load-bearing
        # semantic vs AA1 kill).
        assert body["drain_summary"]["in_flight_marked"] == 0
        assert body["drain_summary"]["frozen_tasks"] == 0
        # GET reflects the new state.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is True
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_pause_project_idempotent_returns_409(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        first = await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON + " (retry)"},
        )
        assert second.status_code == 409, second.text
        assert "already paused" in second.json()["detail"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_pause_project_short_reason_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": "too short"},  # 9 chars
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 2. unpause happy + 409 not-paused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpause_happy_path_preserves_history(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        unpause_resp = await client.post(
            f"/api/projects/{project_id}/unpause", json={}
        )
        assert unpause_resp.status_code == 200, unpause_resp.text
        body = unpause_resp.json()
        assert body["is_paused"] is False
        # D4 — paused_at + paused_reason PRESERVED as history.
        assert body["paused_at"] is not None
        assert body["paused_reason"] == _VALID_PAUSE_REASON
        # GET reflects unpause + preserved history.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is False
        assert get_resp.json()["paused_at"] is not None
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_unpause_non_paused_returns_409(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await client.post(
            f"/api/projects/{project_id}/unpause", json={}
        )
        assert resp.status_code == 409, resp.text
        assert "not paused" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 3. mutex: cannot pause a killed project (app-layer) + DB CHECK backstop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_blocked_when_project_killed_returns_409(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": "kill before pause smoke test #1211"},
        )
        resp = await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        assert resp.status_code == 409, resp.text
        # Detail mentions kill + revive instructions.
        detail = resp.json()["detail"]
        assert "killed" in detail
        assert "revive" in detail
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_db_check_mutex_kill_and_pause_directly(
    client, scaffold_cleanup, db_session
) -> None:
    """Direct ORM write — flipping is_paused=true on an is_killed=true row
    must fail at the DB CHECK ck_projects_kill_pause_mutex.
    """
    from sqlalchemy.exc import IntegrityError

    from src.models.project import Project

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        # Kill via API (clean path).
        await client.post(
            f"/api/projects/{project_id}/kill",
            json={"reason": "kill for mutex CHECK test #1211"},
        )
        # Now try to flip is_paused=true via ORM in the test DB session.
        # SQLAlchemy will emit a single-row UPDATE; PG fires the CHECK.
        row = await db_session.get(Project, project_id)
        assert row is not None
        assert row.is_killed is True
        row.is_paused = True
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 4. POST /api/tasks gating — paused project + escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_on_paused_project_returns_423(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(project_id, title="should block"),
        )
        assert resp.status_code == 423, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert "paused" in detail["message"].lower()
        assert detail["paused_reason"] == _VALID_PAUSE_REASON
        assert detail["paused_at"] is not None
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_on_paused_project_with_override_succeeds(
    client, scaffold_cleanup, db_session
) -> None:
    from src.models.projects_audit import ProjectsAudit

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(
                project_id,
                title="hotfix during pause",
                allow_during_pause=True,
                allow_during_pause_reason=_VALID_OVERRIDE_REASON,
            ),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["allow_during_pause"] is True
        assert body["allow_during_pause_reason"] == _VALID_OVERRIDE_REASON

        # Verify projects_audit row with action='pause_override' written.
        rows = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == project_id)
                .where(ProjectsAudit.action == "pause_override")
            )
        ).scalars().all()
        assert len(rows) == 1, rows
        audit = rows[0]
        assert audit.reason == _VALID_OVERRIDE_REASON
        assert audit.actor == "operator"
        assert audit.drain_summary.get("task_id") == body["id"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_with_short_override_reason_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": _VALID_PAUSE_REASON},
        )
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(
                project_id,
                title="hotfix short reason",
                allow_during_pause=True,
                allow_during_pause_reason="too short",  # 9 chars
            ),
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 5. Audit-task DONE flip → flag pipeline
# ---------------------------------------------------------------------------


async def _create_audit_task_with_report(
    client, project_id: int, *, recommendation: str = "review"
) -> dict:
    """Create an audit task (task_type='audit') already in IN_PROGRESS state
    with an audit_report set. Returns the created task dict."""
    body = {
        "project_id": project_id,
        "title": f"audit-{recommendation} for project {project_id}",
        "description": "k1211 audit fixture",
        "process_status": 2,  # IN_PROGRESS
        "task_type": "audit",
        "started_at": "2026-05-19T19:00:00Z",
    }
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=body,
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    # Stamp the audit_report via PATCH (the engine would do this; we shortcut).
    patch_resp = await client.patch(
        f"/api/tasks/{task['id']}",
        headers={"X-Project-Id": str(project_id)},
        json={
            "audit_report": {
                "verdict": "budget_over_limit",
                "severity": "high",
                "recommendation": recommendation,
                "evidence": [
                    {"summary": "daily spend exceeded cap by 23%"},
                ],
            }
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return patch_resp.json()


@pytest.mark.asyncio
async def test_audit_done_creates_new_flag(
    client, scaffold_cleanup, db_session
) -> None:
    from src.models.task import Task

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        audit_task = await _create_audit_task_with_report(
            client, project_id, recommendation="review"
        )
        # Flip audit task to DONE — the post-PATCH hook should fire.
        done_resp = await client.patch(
            f"/api/tasks/{audit_task['id']}",
            headers={"X-Project-Id": str(project_id)},
            json={"process_status": 5},
        )
        assert done_resp.status_code == 200, done_resp.text

        # Look up the flag task that was auto-created.
        flag_rows = (
            await db_session.execute(
                select(Task)
                .where(Task.project_id == project_id)
                .where(Task.interaction_kind == "question")
                .where(Task.status == 1)
            )
        ).scalars().all()
        # Filter to AA3 audit flags (is_audit_flag in question_payload).
        flags = [
            t
            for t in flag_rows
            if (t.question_payload or {}).get("is_audit_flag")
        ]
        assert len(flags) == 1, flags
        flag = flags[0]
        payload = flag.question_payload
        assert payload["breach_streak_days"] == 1
        assert payload["audit_history"] == [audit_task["id"]]
        assert payload["latest_audit"] == audit_task["id"]
        assert payload["options"] == [
            "continue",
            "adjust_continue",
            "keep_paused",
            "terminate",
        ]
        # Question text mentions Day 1.
        assert "Day 1" in payload["question"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_second_audit_updates_existing_flag(
    client, scaffold_cleanup, db_session
) -> None:
    """A second audit (recommendation=review) on the same project must
    UPDATE the existing flag (streak=2, no new flag row).
    """
    from src.models.task import Task

    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        # First audit → creates flag (streak=1).
        audit1 = await _create_audit_task_with_report(
            client, project_id, recommendation="review"
        )
        await client.patch(
            f"/api/tasks/{audit1['id']}",
            headers={"X-Project-Id": str(project_id)},
            json={"process_status": 5},
        )

        # Second audit → bumps streak.
        audit2 = await _create_audit_task_with_report(
            client, project_id, recommendation="review"
        )
        await client.patch(
            f"/api/tasks/{audit2['id']}",
            headers={"X-Project-Id": str(project_id)},
            json={"process_status": 5},
        )

        # Verify: exactly one AA3 flag still, streak=2, audit_history has both.
        flag_rows = (
            await db_session.execute(
                select(Task)
                .where(Task.project_id == project_id)
                .where(Task.interaction_kind == "question")
                .where(Task.status == 1)
            )
        ).scalars().all()
        flags = [
            t
            for t in flag_rows
            if (t.question_payload or {}).get("is_audit_flag")
        ]
        assert len(flags) == 1, flags
        payload = flags[0].question_payload
        assert payload["breach_streak_days"] == 2
        assert audit1["id"] in payload["audit_history"]
        assert audit2["id"] in payload["audit_history"]
        assert payload["latest_audit"] == audit2["id"]
        assert "Day 2" in payload["question"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_audit_done_with_pause_recommendation_pauses_project(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        audit_task = await _create_audit_task_with_report(
            client, project_id, recommendation="pause"
        )
        await client.patch(
            f"/api/tasks/{audit_task['id']}",
            headers={"X-Project-Id": str(project_id)},
            json={"process_status": 5},
        )
        # Project should now be paused.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is True
        # Reason mentions the audit task.
        assert str(audit_task["id"]) in (
            get_resp.json()["paused_reason"] or ""
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 6. resolve-flag — four branches
# ---------------------------------------------------------------------------


async def _seed_flag(client, project_id: int) -> dict:
    """Helper: create an audit task with recommendation='pause' → flag +
    is_paused=true. Returns the created flag task dict."""
    audit_task = await _create_audit_task_with_report(
        client, project_id, recommendation="pause"
    )
    await client.patch(
        f"/api/tasks/{audit_task['id']}",
        headers={"X-Project-Id": str(project_id)},
        json={"process_status": 5},
    )
    # List active question tasks via API to find the flag.
    resp = await client.get(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 200, resp.text
    flags = [
        t
        for t in resp.json()
        if t.get("interaction_kind") == "question"
        and (t.get("question_payload") or {}).get("is_audit_flag")
    ]
    assert len(flags) == 1
    return flags[0]


@pytest.mark.asyncio
async def test_resolve_flag_continue_unpauses(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _seed_flag(client, project_id)
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={"action": "continue"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "continue"
        assert body["is_paused"] is False
        # Project actually unpaused.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is False
        # Flag is DONE.
        flag_resp = await client.get(
            f"/api/tasks/{flag['id']}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert flag_resp.json()["process_status"] == 5
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_resolve_flag_adjust_continue_applies_allowlisted_keys(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _seed_flag(client, project_id)
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {
                    "budget_daily_usd": "50.00",
                    # Not in allowlist — silently dropped.
                    "name": "should-be-dropped",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "adjust_continue"
        assert body["is_paused"] is False
        assert "budget_daily_usd" in (body.get("adjustments_applied") or {})
        # Project actually unpaused + budget applied.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is False
        assert get_resp.json()["budget_daily_usd"] == "50.00"
        # Name was NOT changed (dropped by allowlist).
        assert get_resp.json()["name"] != "should-be-dropped"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_resolve_flag_adjust_continue_empty_adjustments_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _seed_flag(client, project_id)
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={"action": "adjust_continue", "adjustments": {}},
        )
        assert resp.status_code == 422, resp.text
        # Project still paused (no partial state).
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is True
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_resolve_flag_keep_paused_keeps_pause(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _seed_flag(client, project_id)
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={"action": "keep_paused"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "keep_paused"
        # is_paused stays true (no audit row this branch — flag itself records).
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is True
        # Flag is DONE with resolved annotation.
        flag_resp = await client.get(
            f"/api/tasks/{flag['id']}",
            headers={"X-Project-Id": str(project_id)},
        )
        flag_payload = flag_resp.json()
        assert flag_payload["process_status"] == 5
        assert flag_payload["question_payload"]["resolved_action"] == "keep_paused"
    finally:
        # cleanup: unpause then delete (delete is idempotent on paused too,
        # but doing it cleanly).
        await client.post(
            f"/api/projects/{project_id}/unpause", json={}
        )
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_resolve_flag_terminate_kills_project(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _seed_flag(client, project_id)
        # Project is currently PAUSED — for terminate to call kill, we need
        # to unpause first (kill rejects paused projects via the mutex).
        # In v1 resolve_flag flow: terminate from a paused-flag context
        # SHOULD unpause-then-kill. We unpause first to test the kill leg
        # cleanly without conflating with the mutex.
        await client.post(
            f"/api/projects/{project_id}/unpause", json={}
        )
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={"action": "terminate"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "terminate"
        assert body["is_killed"] is True
        # Project killed.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_killed"] is True
        # Flag is DONE.
        flag_resp = await client.get(
            f"/api/tasks/{flag['id']}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert flag_resp.json()["process_status"] == 5
    finally:
        # Cleanup: revive first (delete works on killed but unkilling keeps
        # the cleanup graph clean).
        try:
            await client.post(
                f"/api/projects/{project_id}/revive", json={}
            )
        except Exception:
            pass
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_resolve_flag_invalid_action_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _seed_flag(client, project_id)
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={"action": "bogus_action"},
        )
        assert resp.status_code == 422, resp.text
        # Project still paused (no partial state).
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["is_paused"] is True
    finally:
        await client.post(
            f"/api/projects/{project_id}/unpause", json={}
        )
        await client.delete(f"/api/projects/{project_id}")
