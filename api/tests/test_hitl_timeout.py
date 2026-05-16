"""Kanban #989 — per-project HITL timeout policy.

On-demand enforcement gate inside GET /api/tasks/next-autorun. Mirrors the
#951 budget-cap pattern: no APScheduler, stamp on every poll. NULL
`projects.hitl_timeout_hours` = pause indefinitely (preserves pre-#989
behavior).

Coverage matrix (AC#3 — all bullets):
  - test_null_timeout_indefinite_pause       → NULL timeout never stamps
  - test_timeout_exceeded_stamps_halt_reason → 24h cap + 25h-stale task →
                                               halt_reason='hitl_timeout'
  - test_timeout_not_exceeded_stays_paused   → 24h cap + 12h-stale task →
                                               halt_reason unchanged
  - test_resumed_tasks_unaffected            → interaction_kind='work' or
                                               halt_reason NULL → not touched
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.project import Project
from src.models.task import Task


# ---------------------------------------------------------------------------
# Helpers (mirror test_tasks_next_autorun.py)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"test fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str, **extras) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get_next_autorun(client, project_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _set_project_timeout(client, project_id: int, hours: int | None) -> None:
    """PATCH hitl_timeout_hours on a project."""
    resp = await client.patch(
        f"/api/projects/{project_id}",
        json={"hitl_timeout_hours": hours},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hitl_timeout_hours"] == hours


async def _stamp_paused_hitl(
    db_session, task_id: int, halt_reason: str, updated_at: datetime
) -> None:
    """Put a task into the BLOCKED/HITL state used by the headless engine.

    Sets process_status=BLOCKED (4), halt_reason to 'question'/'decision'
    (the literal sentinels the gate matches), and overrides updated_at to
    the desired age. The trigger that bumps updated_at fires on write —
    we set it AFTER the assignment cascade so it lands as configured.
    """
    task = await db_session.get(Task, task_id)
    assert task is not None, f"task_id={task_id} not found"
    task.process_status = 4  # TaskStatus.BLOCKED
    task.halt_reason = halt_reason
    task.updated_at = updated_at
    await db_session.commit()


async def _get_task(db_session, task_id: int) -> Task:
    task = await db_session.get(Task, task_id)
    assert task is not None, f"task_id={task_id} not found"
    await db_session.refresh(task)
    return task


# ---------------------------------------------------------------------------
# test_null_timeout_indefinite_pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_timeout_indefinite_pause(
    client, scaffold_cleanup, db_session
) -> None:
    """NULL hitl_timeout_hours → no stamping regardless of how old the task is.

    Preserves pre-#989 behavior — projects without a configured timeout
    pause indefinitely.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k989-null")
    # Confirm default is NULL.
    proj = await db_session.get(Project, pid)
    assert proj.hitl_timeout_hours is None, (
        f"expected NULL timeout default, got {proj.hitl_timeout_hours}"
    )

    # Create a HITL question task and age it 100h.
    t = await _make_task(
        client,
        pid,
        "stale question",
        interaction_kind="question",
        question_payload={"question": "anything?"},
        run_mode="manual",
        task_kind="human",
    )
    very_old = datetime.now(timezone.utc) - timedelta(hours=100)
    await _stamp_paused_hitl(db_session, t["id"], "question", very_old)

    await _get_next_autorun(client, pid)

    after = await _get_task(db_session, t["id"])
    assert after.halt_reason == "question", (
        f"NULL timeout must not stamp halt_reason; got {after.halt_reason!r}"
    )


# ---------------------------------------------------------------------------
# test_timeout_exceeded_stamps_halt_reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_exceeded_stamps_halt_reason(
    client, scaffold_cleanup, db_session
) -> None:
    """24h cap + task stale 25h → halt_reason='hitl_timeout'.

    Covers both question and decision halt_reason values — the gate
    matches both literal sentinels.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k989-over")
    await _set_project_timeout(client, pid, 24)

    q = await _make_task(
        client,
        pid,
        "stale question",
        interaction_kind="question",
        question_payload={"question": "go ahead?"},
        run_mode="manual",
        task_kind="human",
    )
    d = await _make_task(
        client,
        pid,
        "stale decision",
        interaction_kind="decision",
        question_payload={"question": "A or B?", "options": ["A", "B"]},
        run_mode="manual",
        task_kind="human",
    )
    stale = datetime.now(timezone.utc) - timedelta(hours=25)
    await _stamp_paused_hitl(db_session, q["id"], "question", stale)
    await _stamp_paused_hitl(db_session, d["id"], "decision", stale)

    await _get_next_autorun(client, pid)

    q_after = await _get_task(db_session, q["id"])
    d_after = await _get_task(db_session, d["id"])
    assert q_after.halt_reason == "hitl_timeout", (
        f"question task should be stamped; got {q_after.halt_reason!r}"
    )
    assert d_after.halt_reason == "hitl_timeout", (
        f"decision task should be stamped; got {d_after.halt_reason!r}"
    )
    # Task stays BLOCKED — halt-only, no auto-cancel.
    assert q_after.process_status == 4, (
        f"task must stay BLOCKED; got process_status={q_after.process_status}"
    )


# ---------------------------------------------------------------------------
# test_timeout_not_exceeded_stays_paused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_not_exceeded_stays_paused(
    client, scaffold_cleanup, db_session
) -> None:
    """24h cap + task stale 12h → halt_reason unchanged."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k989-under")
    await _set_project_timeout(client, pid, 24)

    t = await _make_task(
        client,
        pid,
        "fresh question",
        interaction_kind="question",
        question_payload={"question": "ok?"},
        run_mode="manual",
        task_kind="human",
    )
    fresh = datetime.now(timezone.utc) - timedelta(hours=12)
    await _stamp_paused_hitl(db_session, t["id"], "question", fresh)

    await _get_next_autorun(client, pid)

    after = await _get_task(db_session, t["id"])
    assert after.halt_reason == "question", (
        f"under-threshold task must not be stamped; got {after.halt_reason!r}"
    )


# ---------------------------------------------------------------------------
# test_resumed_tasks_unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resumed_tasks_unaffected(
    client, scaffold_cleanup, db_session
) -> None:
    """A 'work' task (already resumed / non-HITL) is not touched.

    Also confirms tasks with halt_reason free-form text (not the
    'question'/'decision' sentinels) are NOT swept — the gate is tight on
    the two literal sentinels the headless engine writes.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k989-work")
    await _set_project_timeout(client, pid, 24)

    work_task = await _make_task(
        client,
        pid,
        "work task halted long ago",
        interaction_kind="work",
        run_mode="auto_pickup",
        task_kind="ai",
        halt_reason="waiting for some other gate",
    )
    very_stale = datetime.now(timezone.utc) - timedelta(hours=100)
    # Re-use the same helper; set halt_reason to a non-sentinel free-form value.
    await _stamp_paused_hitl(
        db_session, work_task["id"], "waiting for some other gate", very_stale
    )

    await _get_next_autorun(client, pid)

    after = await _get_task(db_session, work_task["id"])
    assert after.halt_reason == "waiting for some other gate", (
        f"non-sentinel halt_reason must be left alone; got {after.halt_reason!r}"
    )
    assert after.interaction_kind == "work", after.interaction_kind
