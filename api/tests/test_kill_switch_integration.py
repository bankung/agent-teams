"""Kanban #1209 AC#6 — Integration test: full multi-component drain scenario.

Exercises the complete kill / revive lifecycle across 2 parallel projects (A
and B) with a realistic task mix in Project A:

  - 2 specialist spawn tasks  (process_status=1, task_kind=ai, task_type=feature)
  - 1 recurring non-template task (recurrence_rule + next_fire_at set, is_template=False)
  - 1 open question_payload task (interaction_kind='question', halt_reason='question')
  - 1 in-flight langgraph task   (process_status=2 with a fake_in_flight_marker)
  - 1 scheduled-send placeholder (task_type='chore', description tag 'scheduled_send')

Kill / revive / force-kill are exercised in sequence; drain_summary, audit rows,
scoping (A blocked / B passes), and D4 history contract verified at each step.

Test file location: api/tests/test_kill_switch_integration.py
Runs against `agent_teams_test` (per conftest.py rewrite + 3-layer isolation).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

# ---------------------------------------------------------------------------
# helpers (mirrors pattern from test_kill_switch.py)
# ---------------------------------------------------------------------------

_KILL_REASON = (
    "integration test kill — Kanban #1209 AC#6 full multi-component drain"
)
_IN_FLIGHT_MARKER = "fake_in_flight_marker_for_test"


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k1209-integration fixture — {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1209int"))
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, **fields) -> dict:
    body: dict = {
        "project_id": project_id,
        "title": fields.pop("title", "k1209 integration fixture task"),
        "description": fields.pop("description", "k1209 integration"),
        "process_status": fields.pop("process_status", 1),
        **fields,
    }
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=body,
    )
    assert resp.status_code == 201, f"create_task failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Integration test — full multi-component drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_full_multi_component_drain(
    client, scaffold_cleanup, db_session
) -> None:
    """AC#6: end-to-end kill/revive/force-kill across 2 parallel projects.

    Scenario (Project A tasks):
      - 2 specialist spawn TODOs  (ps=1, task_kind=ai, task_type=feature)
      - 1 recurring non-template  (recurrence_rule + next_fire_at, ps=1)
      - 1 question_payload task   (interaction_kind=question, halt_reason=question, ps=1)
      - 1 in-flight LangGraph sim (ps=2, status_change_reason=fake_in_flight_marker)
      - 1 scheduled-send marker   (task_type=chore, description contains 'scheduled_send')

    Project B: one plain TODO for scoping verification.

    Steps:
      1. Kill A (clean, force=False) → verify drain counts + audit + 423 gate.
      2. Revive A → verify un-freeze + recurring re-arm + D4 + audit.
      3. Kill A again (force=True) → verify drain_summary.force=True.
    """
    from src.models.projects_audit import ProjectsAudit
    from src.models.task import Task

    # ---- setup: 2 parallel projects ----------------------------------------
    project_a = await _create_project(client, scaffold_cleanup)
    project_b = await _create_project(client, scaffold_cleanup)
    a_id, b_id = project_a["id"], project_b["id"]

    try:
        future_dt = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        # -- Project A: 6 tasks -----------------------------------------------

        # T1, T2 — specialist spawn TODOs
        t1 = await _create_task(
            client,
            a_id,
            title="specialist spawn A1",
            task_kind="ai",
            task_type="feature",
        )
        t2 = await _create_task(
            client,
            a_id,
            title="specialist spawn A2",
            task_kind="ai",
            task_type="feature",
        )

        # T3 — recurring non-template (can null next_fire_at on kill)
        t3 = await _create_task(
            client,
            a_id,
            title="recurring non-template",
            recurrence_rule="0 9 * * 1",  # every Monday 09:00
            recurrence_timezone="UTC",
            next_fire_at=future_dt,
        )
        assert t3["next_fire_at"] is not None, "T3 must have next_fire_at set"
        assert t3["recurrence_rule"] == "0 9 * * 1"

        # T4 — question_payload task (interaction_kind=question, halt_reason=question)
        t4 = await _create_task(
            client,
            a_id,
            title="open question gate",
            interaction_kind="question",
            halt_reason="question",
            question_payload={"question": "k1209 test question?", "options": []},
        )

        # T5 — simulated in-flight langgraph task
        # POST as ps=1 first (router may require ps=1 on create), then PATCH to ps=2
        t5 = await _create_task(
            client,
            a_id,
            title="in-flight langgraph simulation",
        )
        patch_resp = await client.patch(
            f"/api/tasks/{t5['id']}",
            headers={"X-Project-Id": str(a_id)},
            json={
                "process_status": 2,
                "status_change_reason": _IN_FLIGHT_MARKER,
            },
        )
        assert patch_resp.status_code == 200, f"patch to ps=2 failed: {patch_resp.text}"
        t5 = patch_resp.json()
        assert t5["process_status"] == 2
        assert t5["status_change_reason"] == _IN_FLIGHT_MARKER

        # T6 — scheduled-send marker (chore with tag in description)
        t6 = await _create_task(
            client,
            a_id,
            title="scheduled external send placeholder",
            task_type="chore",
            description="scheduled_send: queued notification dispatch — k1209 integration",
        )

        # -- Project B: 1 plain TODO for scoping verification -----------------
        tb = await _create_task(client, b_id, title="B task — should stay alive")

        # Collect IDs for later DB-layer assertions
        a_todo_ids = [t1["id"], t2["id"], t4["id"], t6["id"]]  # ps=1 open TODOs
        a_in_flight_id = t5["id"]
        a_recurring_id = t3["id"]
        all_a_ids = [t1["id"], t2["id"], t3["id"], t4["id"], t5["id"], t6["id"]]
        b_task_id = tb["id"]

        # ====================================================================
        # Step 1 — Kill A (clean drain, force=False)
        # ====================================================================
        kill_resp = await client.post(
            f"/api/projects/{a_id}/kill",
            json={"reason": _KILL_REASON},
        )
        assert kill_resp.status_code == 200, kill_resp.text
        kill_body = kill_resp.json()

        # -- wire-level contract --
        assert kill_body["success"] is True
        assert kill_body["project_id"] == a_id
        assert kill_body["action"] == "kill"
        assert kill_body["is_killed"] is True
        assert kill_body["killed_reason"] == _KILL_REASON
        assert kill_body["killed_at"] is not None

        drain = kill_body["drain_summary"]
        assert isinstance(drain, dict)
        # force flag captured
        assert drain["force"] is False
        # recurring non-template: next_fire_at → NULL → counted as recurring_suspended
        assert drain["recurring_suspended"] >= 1, (
            f"expected recurring_suspended >= 1, got {drain['recurring_suspended']}"
        )
        # in-flight task: counted as in_flight_marked
        assert drain["in_flight_marked"] >= 1, (
            f"expected in_flight_marked >= 1, got {drain['in_flight_marked']}"
        )
        # open TODOs: t1, t2, t4, t6 (ps=1 active) → frozen_tasks
        assert drain["frozen_tasks"] >= 4, (
            f"expected frozen_tasks >= 4, got {drain['frozen_tasks']}"
        )

        # audit row must exist and be non-zero
        assert isinstance(kill_body["audit_id"], int)
        kill_audit_id = kill_body["audit_id"]

        # -- DB-layer assertions (kill_frozen, recurring null, in-flight marker) --
        db_session.expire_all()

        todo_rows = (
            await db_session.execute(
                select(Task.id, Task.kill_frozen, Task.process_status)
                .where(Task.id.in_(a_todo_ids))
            )
        ).all()
        for row in todo_rows:
            assert row.kill_frozen is True, (
                f"task {row.id} (ps={row.process_status}) should be kill_frozen=True"
            )

        # recurring non-template: next_fire_at should be NULL now
        rec_row = (
            await db_session.execute(
                select(Task.kill_frozen, Task.next_fire_at, Task.recurrence_rule)
                .where(Task.id == a_recurring_id)
            )
        ).first()
        assert rec_row.next_fire_at is None, "recurring task next_fire_at should be NULL after kill"
        assert rec_row.recurrence_rule == "0 9 * * 1", "recurrence_rule must be preserved"

        # in-flight task: kill_frozen=True, status_change_reason mentions AA1 kill
        # (service REPLACES status_change_reason for in-flight rows)
        inf_row = (
            await db_session.execute(
                select(Task.kill_frozen, Task.status_change_reason)
                .where(Task.id == a_in_flight_id)
            )
        ).first()
        assert inf_row.kill_frozen is True, "in-flight task should be kill_frozen=True"
        assert inf_row.status_change_reason is not None
        assert "AA1 kill" in inf_row.status_change_reason, (
            f"expected 'AA1 kill' in status_change_reason, got: {inf_row.status_change_reason!r}"
        )
        assert "graceful checkpoint requested" in inf_row.status_change_reason

        # projects_audit row written with correct fields
        audit_row = (
            await db_session.execute(
                select(ProjectsAudit).where(ProjectsAudit.id == kill_audit_id)
            )
        ).scalar_one()
        assert audit_row.action == "kill"
        assert audit_row.actor == "operator"
        assert audit_row.reason == _KILL_REASON
        assert audit_row.project_id == a_id
        assert isinstance(audit_row.drain_summary, dict)
        assert "recurring_suspended" in audit_row.drain_summary
        assert audit_row.drain_summary["recurring_suspended"] >= 1
        assert audit_row.drain_summary["frozen_tasks"] >= 4
        assert audit_row.drain_summary["in_flight_marked"] >= 1

        # -- 423 gate: POST /api/tasks against A blocked ----------------------
        locked_resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(a_id)},
            json={
                "project_id": a_id,
                "title": "should be locked",
                "description": "k1209 AC#6 lock test",
                "process_status": 1,
            },
        )
        assert locked_resp.status_code == 423, (
            f"expected 423, got {locked_resp.status_code}: {locked_resp.text}"
        )
        locked_detail = locked_resp.json()["detail"]
        assert isinstance(locked_detail, dict), "detail should be a dict with sub-fields"
        assert "killed" in locked_detail["message"].lower()
        assert locked_detail["killed_reason"] == _KILL_REASON
        assert locked_detail["killed_at"] is not None

        # -- scoping: POST /api/tasks against B still works -------------------
        b_ok_resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(b_id)},
            json={
                "project_id": b_id,
                "title": "B task during A kill",
                "description": "k1209 B scoping",
                "process_status": 1,
            },
        )
        assert b_ok_resp.status_code == 201, (
            f"expected 201 for project B, got {b_ok_resp.status_code}: {b_ok_resp.text}"
        )
        # Clean up the extra B task we just created
        b_extra_id = b_ok_resp.json()["id"]

        # ====================================================================
        # Step 2 — Revive A
        # ====================================================================
        revive_resp = await client.post(
            f"/api/projects/{a_id}/revive", json={}
        )
        assert revive_resp.status_code == 200, revive_resp.text
        revive_body = revive_resp.json()

        assert revive_body["success"] is True
        assert revive_body["action"] == "revive"
        assert revive_body["is_killed"] is False

        # D4: killed_at + killed_reason PRESERVED after revive
        assert revive_body["killed_at"] is not None, "killed_at must be preserved (D4)"
        assert revive_body["killed_reason"] == _KILL_REASON, (
            "killed_reason must be preserved (D4)"
        )

        revive_drain = revive_body["drain_summary"]
        # recurring non-template was NULL'd → resumed_recurring incremented
        assert revive_drain["resumed_recurring"] >= 1, (
            f"expected resumed_recurring >= 1, got {revive_drain['resumed_recurring']}"
        )
        # all frozen tasks unfrozen (t1,t2,t3-via-kill_frozen,t4,t5,t6)
        assert revive_drain["unfrozen_tasks"] >= 5, (
            f"expected unfrozen_tasks >= 5, got {revive_drain['unfrozen_tasks']}"
        )

        revive_audit_id = revive_body["audit_id"]
        assert isinstance(revive_audit_id, int)

        # -- DB-layer: kill_frozen cleared on all A tasks ---------------------
        db_session.expire_all()

        all_a_rows = (
            await db_session.execute(
                select(Task.id, Task.kill_frozen).where(Task.id.in_(all_a_ids))
            )
        ).all()
        for row in all_a_rows:
            assert row.kill_frozen is False, (
                f"task {row.id} kill_frozen should be False after revive"
            )

        # recurring task: next_fire_at recomputed (non-null)
        rec_row_post = (
            await db_session.execute(
                select(Task.next_fire_at, Task.recurrence_rule)
                .where(Task.id == a_recurring_id)
            )
        ).first()
        assert rec_row_post.next_fire_at is not None, (
            "recurring task next_fire_at should be recomputed (non-null) after revive"
        )
        assert rec_row_post.recurrence_rule == "0 9 * * 1", "recurrence_rule preserved"

        # projects_audit row for revive
        revive_audit_row = (
            await db_session.execute(
                select(ProjectsAudit).where(ProjectsAudit.id == revive_audit_id)
            )
        ).scalar_one()
        assert revive_audit_row.action == "revive"
        assert revive_audit_row.project_id == a_id
        assert revive_audit_row.reason is None
        assert isinstance(revive_audit_row.drain_summary, dict)
        assert "unfrozen_tasks" in revive_audit_row.drain_summary
        assert "resumed_recurring" in revive_audit_row.drain_summary
        assert revive_audit_row.drain_summary["resumed_recurring"] >= 1

        # GET reflects revived project (is_killed=false, history preserved)
        get_resp = await client.get(f"/api/projects/{a_id}")
        assert get_resp.status_code == 200, get_resp.text
        proj_state = get_resp.json()
        assert proj_state["is_killed"] is False
        assert proj_state["killed_at"] is not None   # D4: history kept
        assert proj_state["killed_reason"] == _KILL_REASON  # D4: history kept

        # POST /api/tasks against A now works again
        revived_task_resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(a_id)},
            json={
                "project_id": a_id,
                "title": "post-revive task — should succeed",
                "description": "k1209 AC#6 revive gate check",
                "process_status": 1,
            },
        )
        assert revived_task_resp.status_code == 201, (
            f"expected 201 after revive, got {revived_task_resp.status_code}: {revived_task_resp.text}"
        )
        revived_extra_id = revived_task_resp.json()["id"]

        # ====================================================================
        # Step 3 — Kill A again (force=True)
        # ====================================================================
        force_kill_resp = await client.post(
            f"/api/projects/{a_id}/kill",
            params={"force": "true"},
            json={"reason": _KILL_REASON + " — force=True second kill"},
        )
        assert force_kill_resp.status_code == 200, force_kill_resp.text
        fk_body = force_kill_resp.json()

        assert fk_body["success"] is True
        assert fk_body["is_killed"] is True

        fk_drain = fk_body["drain_summary"]
        # force flag captured in drain_summary
        assert fk_drain["force"] is True, (
            f"expected drain_summary.force=True for force kill, got: {fk_drain}"
        )
        # recurring still has rule (was just re-armed by revive) → suspended again
        assert fk_drain["recurring_suspended"] >= 1, (
            f"expected recurring_suspended >= 1 on force kill, got {fk_drain['recurring_suspended']}"
        )

        # projects_audit drain_summary for force kill also captures force=True
        fk_audit_id = fk_body["audit_id"]
        db_session.expire_all()
        fk_audit_row = (
            await db_session.execute(
                select(ProjectsAudit).where(ProjectsAudit.id == fk_audit_id)
            )
        ).scalar_one()
        assert fk_audit_row.action == "kill"
        assert fk_audit_row.drain_summary["force"] is True

    finally:
        # Clean up — soft-delete both projects (also cascades tasks via DB FK)
        await client.delete(f"/api/projects/{a_id}")
        await client.delete(f"/api/projects/{b_id}")
