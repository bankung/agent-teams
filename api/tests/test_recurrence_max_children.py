"""Tests for the recurrence max_active_children cap (Kanban #1125 — L21 prevention).

Covers:
1. Pydantic schema accepts / rejects max_active_children boundary values.
2. fire_template halts template when active children reach the cap.
3. Active count ignores DONE / CANCELLED children (terminal states OK).
4. Active count ignores SOFT-DELETED children (status=0 OK).
5. tick_once spawn count is 0 (not 1) on a capped halt tick.
6. POST /api/tasks/{id}/fire-now → 409 when cap reached.
7. Env-default fallback (no per-template override) kicks in.
8. DB CHECK constraint rejects raw-SQL writes of zero / negative cap.
9. Bumping max_active_children + clearing halt resumes spawning.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


# -----------------------------------------------------------------------------
# Helpers (mirrored from test_recurrence_runtime.py)
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


async def _make_template(
    client, project_id: int, *, max_children: int | None = None
) -> int:
    """Helper: POST a recurrence template, optionally with max_active_children."""
    body: dict = {
        "project_id": project_id,
        "title": "cap-test-template",
        "task_kind": "ai",
        "run_mode": "auto_pickup",
        "is_template": True,
        "recurrence_rule": "* * * * *",
        "recurrence_timezone": "UTC",
        "next_fire_at": _future_iso(),
    }
    if max_children is not None:
        body["max_active_children"] = max_children
    resp = await client.post(
        "/api/tasks", json=body, headers={"X-Project-Id": str(project_id)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# =============================================================================
# 1. Pydantic schema
# =============================================================================


def test_task_create_accepts_max_active_children_int() -> None:
    from src.schemas.task import TaskCreate

    body = TaskCreate(
        project_id=1,
        title="t",
        is_template=True,
        recurrence_rule="* * * * *",
        next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
        max_active_children=42,
    )
    assert body.max_active_children == 42


def test_task_create_accepts_null_max_active_children() -> None:
    from src.schemas.task import TaskCreate

    body = TaskCreate(project_id=1, title="t")
    assert body.max_active_children is None


def test_task_create_rejects_max_active_children_zero() -> None:
    from pydantic import ValidationError
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as exc_info:
        TaskCreate(project_id=1, title="t", max_active_children=0)
    # ge=1 fires
    assert "greater than or equal to 1" in str(exc_info.value).lower()


def test_task_create_rejects_max_active_children_negative() -> None:
    from pydantic import ValidationError
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError):
        TaskCreate(project_id=1, title="t", max_active_children=-5)


def test_task_create_rejects_max_active_children_above_ceiling() -> None:
    from pydantic import ValidationError
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as exc_info:
        TaskCreate(project_id=1, title="t", max_active_children=10_001)
    assert "less than or equal to 10000" in str(exc_info.value).lower()


def test_task_update_accepts_explicit_null_clears_cap() -> None:
    """PATCH semantics: explicit null is meaningful (clears the per-template
    cap so the env default applies at next fire)."""
    from src.schemas.task import TaskUpdate

    upd = TaskUpdate(max_active_children=None)
    assert "max_active_children" in upd.model_fields_set
    dumped = upd.model_dump(exclude_unset=True)
    assert dumped == {"max_active_children": None}


# =============================================================================
# 2. fire_template cap gate — direct unit (bypass scheduler)
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_halts_at_cap(client, scaffold_cleanup) -> None:
    """Template with max_active_children=3: fire 3 times → 3 children + still
    TODO. 4th fire → no child, template → BLOCKED + halt_reason set."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("cap-halt")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id, max_children=3)

        # Fire 3 times — all spawn successfully, template stays TODO.
        spawned_ids: list[int] = []
        for _ in range(3):
            async with SessionLocal() as db:
                tpl = await db.get(Task, tpl_id)
                child = await fire_template(db, tpl)
                assert child is not None, "first 3 fires must spawn a child"
                spawned_ids.append(child.id)

        # Verify state after 3 successful spawns: template still TODO, 3 children.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.TODO, (
                f"template should remain TODO after 3 spawns, got {tpl.process_status}"
            )
            assert tpl.halt_reason is None

        # 4th fire — cap reached, template halts, no child spawned.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            result = await fire_template(db, tpl)
            assert result is None, "4th fire at cap must return None"

        # Verify halted template state.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.BLOCKED
            assert tpl.halt_reason == "max_active_children_reached"
            assert tpl.status_change_reason is not None
            assert "3 active children" in tpl.status_change_reason
            assert "cap 3" in tpl.status_change_reason

        # Verify no 4th child landed.
        children = await client.get("/api/tasks?limit=500", headers=headers)
        spawned_from_tpl = [
            t for t in children.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(spawned_from_tpl) == 3, (
            f"expected exactly 3 children, got {len(spawned_from_tpl)}"
        )

        # Cleanup
        for cid in spawned_ids:
            await client.delete(f"/api/tasks/{cid}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 3. Active count ignores DONE / CANCELLED children
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_terminal_children_dont_count(
    client, scaffold_cleanup
) -> None:
    """Mark all 3 spawned children DONE → 4th fire should succeed (no halt)."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("cap-terminal")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id, max_children=2)

        # Fire 2 → 2 children.
        first_ids: list[int] = []
        for _ in range(2):
            async with SessionLocal() as db:
                tpl = await db.get(Task, tpl_id)
                child = await fire_template(db, tpl)
                assert child is not None
                first_ids.append(child.id)

        # Mark both DONE via the public API.
        for cid in first_ids:
            await client.patch(
                f"/api/tasks/{cid}",
                json={"process_status": TaskStatus.DONE},
                headers=headers,
            )

        # 3rd fire — cap is 2 but active count is 0 (both DONE), should spawn.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            assert child is not None, (
                "spawn must succeed when prior children are all DONE"
            )
            third_id = child.id

        # Template still TODO.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.TODO
            assert tpl.halt_reason is None

        # Cleanup
        await client.delete(f"/api/tasks/{third_id}", headers=headers)
        for cid in first_ids:
            await client.delete(f"/api/tasks/{cid}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 4. Soft-deleted children don't count
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_soft_deleted_children_dont_count(
    client, scaffold_cleanup
) -> None:
    """DELETE /api/tasks/{id} soft-deletes (status=0); next fire should succeed."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("cap-soft-del")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id, max_children=2)

        first_ids: list[int] = []
        for _ in range(2):
            async with SessionLocal() as db:
                tpl = await db.get(Task, tpl_id)
                child = await fire_template(db, tpl)
                first_ids.append(child.id)

        # Soft-delete both.
        for cid in first_ids:
            await client.delete(f"/api/tasks/{cid}", headers=headers)

        # Next fire — active count should be 0 (soft-deleted excluded).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            assert child is not None
            third_id = child.id
            assert tpl.process_status == TaskStatus.TODO

        await client.delete(f"/api/tasks/{third_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 5. tick_once spawn count
# =============================================================================


@pytest.mark.asyncio
async def test_tick_once_does_not_count_halted_spawn(
    client, scaffold_cleanup
) -> None:
    """A halted-at-cap fire must NOT increment tick_once's `spawned` counter."""
    from datetime import datetime, timedelta, timezone

    from src.db import SessionLocal
    from src.services.recurrence import fire_template, tick_once
    from src.models.task import Task

    name = _unique_name("cap-tick")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id, max_children=1)

        # Pre-fill the cap: spawn 1 child via direct fire_template, NOT via the
        # tick (we want a clean tick_once test below).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            child_id = child.id

        # Push template's next_fire_at into the past so tick_once picks it up.
        past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"next_fire_at": past},
            headers=headers,
        )

        # Run a tick — template is at cap, should halt without incrementing.
        result = await tick_once(SessionLocal)
        assert result["spawned"] == 0, (
            f"tick at cap must report spawned=0, got {result}"
        )

        # Verify the halt landed.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.halt_reason == "max_active_children_reached"

        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 6. POST /api/tasks/{id}/fire-now → 409 on cap
# =============================================================================


@pytest.mark.asyncio
async def test_fire_now_409_when_cap_reached(client, scaffold_cleanup) -> None:
    """fire-now must respect the cap (parity with scheduler tick) — 409 not 200."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("cap-fn")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id, max_children=1)

        # Pre-fill to cap.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            child_id = child.id

        # fire-now should refuse with 409.
        resp = await client.post(f"/api/tasks/{tpl_id}/fire-now", headers=headers)
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert f"Task id={tpl_id}" in body["detail"]
        assert "max_active_children" in body["detail"]

        # Template is now halted.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.BLOCKED
            assert tpl.halt_reason == "max_active_children_reached"

        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 7. Env-default fallback
# =============================================================================


@pytest.mark.asyncio
async def test_env_default_fallback_kicks_in(
    client, scaffold_cleanup, monkeypatch
) -> None:
    """Template with max_active_children=NULL falls back to
    MAX_ACTIVE_CHILDREN_DEFAULT. Override env to 2, fire 2, third should halt."""
    monkeypatch.setenv("MAX_ACTIVE_CHILDREN_DEFAULT", "2")

    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("cap-env")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Template WITHOUT explicit max_active_children — env default applies.
        tpl_id = await _make_template(client, project_id, max_children=None)

        # Sanity: column is NULL.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.max_active_children is None

        child_ids: list[int] = []
        for _ in range(2):
            async with SessionLocal() as db:
                tpl = await db.get(Task, tpl_id)
                child = await fire_template(db, tpl)
                assert child is not None
                child_ids.append(child.id)

        # 3rd fire — env cap is 2, should halt.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            result = await fire_template(db, tpl)
            assert result is None

        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.process_status == TaskStatus.BLOCKED
            assert tpl.halt_reason == "max_active_children_reached"
            # status_change_reason mentions cap=2 (the env value, NOT the hardcoded 100).
            assert "cap 2" in tpl.status_change_reason

        for cid in child_ids:
            await client.delete(f"/api/tasks/{cid}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 8. DB CHECK constraint (defense-in-depth)
# =============================================================================


@pytest.mark.asyncio
async def test_db_check_rejects_zero_max_active_children(db_session) -> None:
    """Raw SQL bypassing the Pydantic validator hits the CHECK."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as exc_info:
        # Use a separate transaction so the failure doesn't poison db_session.
        await db_session.execute(
            text(
                "INSERT INTO tasks (project_id, title, max_active_children) "
                "VALUES (1, 'check-test', 0)"
            )
        )
        await db_session.commit()
    assert "ck_tasks_max_active_children_positive" in str(exc_info.value)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_db_check_rejects_negative_max_active_children(db_session) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text(
                "INSERT INTO tasks (project_id, title, max_active_children) "
                "VALUES (1, 'check-test', -1)"
            )
        )
        await db_session.commit()
    assert "ck_tasks_max_active_children_positive" in str(exc_info.value)
    await db_session.rollback()


# =============================================================================
# 9. Resume after operator un-halts
# =============================================================================


@pytest.mark.asyncio
async def test_resume_after_clearing_halt_and_raising_cap(
    client, scaffold_cleanup
) -> None:
    """Operator workflow: template halted → PATCH max_active_children up,
    clear halt_reason, flip ps back to TODO → next fire succeeds."""
    from src.constants import TaskStatus
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("cap-resume")
    scaffold_cleanup(name)
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        tpl_id = await _make_template(client, project_id, max_children=1)

        # Spawn 1, then hit cap on 2nd.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            first = await fire_template(db, tpl)
            first_id = first.id

        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert await fire_template(db, tpl) is None  # halted

        # Operator workflow: raise cap to 5, clear halt_reason, restore TODO.
        # halt_reason explicit-null is meaningful (TaskUpdate semantics).
        patch_resp = await client.patch(
            f"/api/tasks/{tpl_id}",
            json={
                "max_active_children": 5,
                "halt_reason": None,
                "process_status": TaskStatus.TODO,
            },
            headers=headers,
        )
        assert patch_resp.status_code == 200, patch_resp.text
        body = patch_resp.json()
        assert body["max_active_children"] == 5
        assert body["halt_reason"] is None
        assert body["process_status"] == TaskStatus.TODO

        # Next fire — should succeed (1 child < new cap of 5).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            second = await fire_template(db, tpl)
            assert second is not None, "post-resume fire must spawn"
            second_id = second.id

        # Cleanup
        for cid in (first_id, second_id):
            await client.delete(f"/api/tasks/{cid}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")
