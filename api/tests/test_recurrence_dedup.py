"""Tests for the recurrence dedup gate (Kanban #1728).

Covers:
1. fire_template with one existing open child (ps=1) returns None, no new child
   inserted, next_fire_at advanced.
2. fire_template with zero open children spawns a child (no regression).
3. fire_template with open child in ps=2/3/4 also deduplicates.
4. L21 cap behavior unchanged when active_count >= cap (still halts, not dedup).
5. tick_once integration: a second tick on an already-fired template does not
   add a second child.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# -----------------------------------------------------------------------------
# Helpers (mirrors test_recurrence_runtime.py / test_recurrence_max_children.py)
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


def _past_iso(minutes: int = 2) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


async def _make_template(client, project_id: int) -> int:
    body = {
        "project_id": project_id,
        "title": "dedup-test-template",
        "task_kind": "ai",
        "run_mode": "auto_pickup",
        "is_template": True,
        "recurrence_rule": "* * * * *",
        "recurrence_timezone": "UTC",
        "next_fire_at": _future_iso(),
    }
    resp = await client.post(
        "/api/tasks", json=body, headers={"X-Project-Id": str(project_id)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# =============================================================================
# 1. Dedup: one open child (ps=TODO) → skip spawn, advance next_fire_at
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_dedup_with_open_todo_child(
    client, scaffold_cleanup
) -> None:
    """fire_template with one existing open (ps=1) child returns None, inserts
    NO new child, and still advances next_fire_at."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("dedup-todo")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id)

        # First fire: no open children → spawns normally.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            first_child = await fire_template(db, tpl)
            assert first_child is not None, "first fire must spawn a child"
            first_child_id = first_child.id

        # Verify the first child is open (ps=1).
        async with SessionLocal() as db:
            ch = await db.get(Task, first_child_id)
            assert ch.process_status == TaskStatus.TODO

        # Record next_fire_at after first spawn (should be future).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            next_after_first = tpl.next_fire_at

        # Force next_fire_at into the past so a scheduler tick would pick it up again.
        await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"next_fire_at": _past_iso()},
            headers=headers,
        )

        # Second fire: one open child exists → dedup skips spawn, returns None.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            result = await fire_template(db, tpl)
            assert result is None, "dedup must return None when an open child exists"

        # Verify: still only ONE child for this template.
        children = await client.get("/api/tasks?limit=500", headers=headers)
        from_tpl = [
            t for t in children.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(from_tpl) == 1, (
            f"dedup must not insert a new child; found {len(from_tpl)} children"
        )
        assert from_tpl[0]["id"] == first_child_id

        # Verify: next_fire_at was still advanced (not stuck at the past value).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.next_fire_at is not None
            assert tpl.next_fire_at > datetime.now(timezone.utc), (
                f"next_fire_at={tpl.next_fire_at} must be in the future after dedup"
            )

        # Verify: template is NOT halted (dedup is not L21 halt).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.TODO, (
                f"template must stay TODO after dedup, got {tpl.process_status}"
            )
            assert tpl.halt_reason is None

        # Cleanup.
        await client.delete(f"/api/tasks/{first_child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 2. No regression: zero open children → spawns normally
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_no_dedup_when_no_open_children(
    client, scaffold_cleanup
) -> None:
    """fire_template with zero open children spawns a child as before."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("dedup-zero")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id)

        # No prior children — should spawn.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            assert child is not None, "must spawn when no open children exist"
            child_id = child.id

        children = await client.get("/api/tasks?limit=500", headers=headers)
        from_tpl = [
            t for t in children.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(from_tpl) == 1

        # Cleanup.
        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 3. Dedup: open child in ps=2/3/4 is also treated as "open"
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("open_ps", [2, 3, 4])
async def test_fire_template_dedup_with_open_nontodo_child(
    client, scaffold_cleanup, open_ps: int
) -> None:
    """fire_template deduplicates when the existing child is in ps=2 (in_progress),
    ps=3 (review), or ps=4 (blocked) — all non-terminal open states."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name(f"dedup-ps{open_ps}")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id)

        # Spawn first child normally.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            first = await fire_template(db, tpl)
            assert first is not None
            first_id = first.id

        # Transition the child to the target open ps via PATCH.
        patch_resp = await client.patch(
            f"/api/tasks/{first_id}",
            json={"process_status": open_ps},
            headers=headers,
        )
        assert patch_resp.status_code == 200, patch_resp.text

        # Force next_fire_at into the past.
        await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"next_fire_at": _past_iso()},
            headers=headers,
        )

        # Second fire — open child in ps=open_ps should trigger dedup.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            result = await fire_template(db, tpl)
            assert result is None, (
                f"dedup must skip spawn when child is open at ps={open_ps}"
            )

        # Still only one child.
        children = await client.get("/api/tasks?limit=500", headers=headers)
        from_tpl = [
            t for t in children.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(from_tpl) == 1, (
            f"must not insert a second child when open child at ps={open_ps} exists"
        )

        # next_fire_at advanced.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.next_fire_at > datetime.now(timezone.utc)

        # Template not halted.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.TODO
            assert tpl.halt_reason is None

        # Cleanup.
        await client.delete(f"/api/tasks/{first_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 4. L21 cap unchanged: active_count >= cap → halt (not dedup)
# =============================================================================


@pytest.mark.asyncio
async def test_l21_cap_still_halts_at_cap(client, scaffold_cleanup) -> None:
    """When active_count reaches cap, the L21 halt path fires (not dedup).

    With dedup installed, a template with cap=1 will: first fire → spawn (count=0
    → spawn); second fire → L21 halt (count=1 >= cap=1, checked BEFORE dedup).
    The dedup gate only fires when count >= 1 AND count < cap, i.e., when the
    cap is more than 1. With cap=1, active_count=1 hits the cap branch first.
    Template process_status must become BLOCKED and halt_reason must be set.
    """
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("l21-cap-still")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Template with cap=1: first spawn succeeds (count=0 < cap=1),
        # second call hits L21 (count=1 >= cap=1) → BLOCKED.
        body = {
            "project_id": project_id,
            "title": "l21-cap-test",
            "task_kind": "ai",
            "run_mode": "auto_pickup",
            "is_template": True,
            "recurrence_rule": "* * * * *",
            "next_fire_at": _future_iso(),
            "max_active_children": 1,
        }
        resp = await client.post(
            "/api/tasks", json=body, headers=headers
        )
        assert resp.status_code == 201, resp.text
        tpl_id = resp.json()["id"]

        # First fire: active_count=0 < cap=1 → spawn succeeds.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            assert child is not None, "first fire (count=0 < cap=1) must spawn"
            child_id = child.id

        # Second fire: active_count=1 >= cap=1 → L21 halt (takes priority over dedup).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            result = await fire_template(db, tpl)
            assert result is None, "L21 cap must prevent spawn"

        # Template is BLOCKED (L21 halt path), not TODO (dedup path).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.BLOCKED, (
                f"expected BLOCKED at cap, got ps={tpl.process_status}"
            )
            assert tpl.halt_reason == "max_active_children_reached"

        # Cleanup.
        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 5. tick_once integration: second tick does not add a second child
# =============================================================================


@pytest.mark.asyncio
async def test_tick_once_dedup_does_not_pile_up(client, scaffold_cleanup) -> None:
    """Two consecutive tick_once calls on a template with one open child:
    first tick spawns 1 child; second tick deduplicates — still only 1 child."""
    from src.db import SessionLocal
    from src.services.recurrence import tick_once

    name = _unique_name("dedup-tick")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id)

        # Put next_fire_at in the past so the first tick picks it up.
        await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"next_fire_at": _past_iso()},
            headers=headers,
        )

        # First tick → spawns 1 child.
        result1 = await tick_once(SessionLocal)
        assert result1["spawned"] >= 1, f"first tick must spawn; got {result1}"

        children_after_1 = await client.get("/api/tasks?limit=500", headers=headers)
        from_tpl_after_1 = [
            t
            for t in children_after_1.json()
            if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(from_tpl_after_1) == 1, (
            f"expected exactly 1 child after first tick, got {len(from_tpl_after_1)}"
        )

        # Force next_fire_at into the past again for second tick.
        await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"next_fire_at": _past_iso()},
            headers=headers,
        )

        # Second tick → dedup skips spawn (open child still exists).
        result2 = await tick_once(SessionLocal)
        # spawned might be 0 for this template (dedup) but could be >0 for
        # other projects' templates — check the child count directly.

        children_after_2 = await client.get("/api/tasks?limit=500", headers=headers)
        from_tpl_after_2 = [
            t
            for t in children_after_2.json()
            if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(from_tpl_after_2) == 1, (
            f"dedup must prevent pile-up; expected 1 child after second tick, "
            f"got {len(from_tpl_after_2)}"
        )

        # Cleanup.
        for t in from_tpl_after_2:
            await client.delete(f"/api/tasks/{t['id']}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")
