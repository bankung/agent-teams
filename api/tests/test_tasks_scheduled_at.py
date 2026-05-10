"""Tests for tasks.scheduled_at + scheduled_xor_template — Kanban #723.

T1 audit follow-up (2026-05-10). Covers six surfaces (mirrors
test_task_kind_recurrence.py structure):

1. Schema-level (Pydantic): TaskCreate / TaskUpdate field acceptance, default
   values, XOR model_validators on both schemas.
2. POST /api/tasks happy-path round-trip with scheduled_at.
3. PATCH /api/tasks/{id} un-schedule + reschedule; resolved-final XOR.
4. POST /api/tasks both-fields-in-payload XOR rejection at 422.
5. Behavioral backfill — seeded rows have scheduled_at=NULL after migration.
6. Source-text-locks for the new 400/422 detail string + the partial index.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


# -----------------------------------------------------------------------------
# Helpers (mirror of test_task_kind_recurrence.py — local to avoid import churn)
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
# 1. Schema-level (Pydantic) tests
# =============================================================================


def test_task_create_scheduled_at_defaults_to_none() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(project_id=1, title="x")
    assert task.scheduled_at is None


def test_task_create_accepts_future_scheduled_at() -> None:
    from src.schemas.task import TaskCreate

    when = datetime.now(timezone.utc) + timedelta(hours=1)
    task = TaskCreate(project_id=1, title="x", scheduled_at=when)
    assert task.scheduled_at == when


def test_task_create_template_with_scheduled_at_rejected_422() -> None:
    """is_template=true + scheduled_at → 422 with BOTH field names in detail."""
    from src.schemas.task import TaskCreate

    when = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(ValidationError) as ei:
        TaskCreate(
            project_id=1,
            title="x",
            is_template=True,
            recurrence_rule="0 9 * * MON",
            next_fire_at=when,
            scheduled_at=when,
        )
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "scheduled_at" in msg and "is_template" in msg


def test_task_update_template_with_scheduled_at_rejected_422() -> None:
    """PATCH body sets BOTH is_template=true and scheduled_at → 422."""
    from src.schemas.task import TaskUpdate

    when = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(ValidationError) as ei:
        TaskUpdate(is_template=True, scheduled_at=when)
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "scheduled_at" in msg and "is_template" in msg


def test_task_update_scheduled_at_alone_works() -> None:
    """PATCH with scheduled_at alone (no is_template) is accepted at schema layer."""
    from src.schemas.task import TaskUpdate

    when = datetime.now(timezone.utc) + timedelta(hours=1)
    upd = TaskUpdate(scheduled_at=when)
    assert upd.scheduled_at == when


# =============================================================================
# 2. POST /api/tasks happy-path round-trip
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_with_scheduled_at_201_round_trips(
    client, scaffold_cleanup
) -> None:
    """POST regular task with future scheduled_at → 201; round-trips via GET."""
    name = _unique_name("sched-rt")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        when = _future_iso()
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "one-shot scheduled",
                "scheduled_at": when,
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["scheduled_at"] is not None
        assert body["is_template"] is False

        got = await client.get(f"/api/tasks/{body['id']}", headers=headers)
        assert got.json()["scheduled_at"] is not None

        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 3. PATCH /api/tasks/{id} un-schedule + reschedule + resolved-final XOR
# =============================================================================


@pytest.mark.asyncio
async def test_patch_task_unschedule_with_null_200(client, scaffold_cleanup) -> None:
    """Existing row with scheduled_at; PATCH {scheduled_at: null} → 200 cleared."""
    name = _unique_name("sched-clear")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        when = _future_iso()
        post = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "to be cleared",
                "scheduled_at": when,
            },
            headers=headers,
        )
        task_id = post.json()["id"]
        assert post.json()["scheduled_at"] is not None

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"scheduled_at": None},
            headers=headers,
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["scheduled_at"] is None

        await client.delete(f"/api/tasks/{task_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_template_with_scheduled_at_resolved_422(
    client, scaffold_cleanup
) -> None:
    """Existing template row (is_template=true); PATCH {scheduled_at: ...}
    alone → 422 resolved-final with both field names in locked detail."""
    name = _unique_name("sched-resolved")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        post = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "template parent",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        task_id = post.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"scheduled_at": _future_iso(2)},
            headers=headers,
        )
        assert patch.status_code == 422, patch.text
        detail = patch.json()["detail"]
        assert "scheduled_at" in detail and "is_template" in detail

        await client.delete(f"/api/tasks/{task_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 4. POST /api/tasks both-fields-in-payload XOR rejection
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_template_with_scheduled_at_returns_422(
    client, scaffold_cleanup
) -> None:
    """POST is_template=true + scheduled_at → 422 (Pydantic catches first;
    DB CHECK is the backstop). Detail mentions both fields."""
    name = _unique_name("sched-xor-post")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        when = _future_iso()
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "xor bad",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "next_fire_at": when,
                "scheduled_at": when,
            },
            headers=headers,
        )
        assert resp.status_code == 422
        body_text = str(resp.json())
        assert "scheduled_at" in body_text and "is_template" in body_text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 5. Behavioral backfill — seeded rows + the migration's NULL backfill
# =============================================================================


@pytest.mark.asyncio
async def test_seeded_tasks_have_scheduled_at_null(client) -> None:
    """All existing tasks (seeded) carry scheduled_at=NULL after migration 0010
    applies (the column is nullable; ADD COLUMN backfills NULL metadata-only
    on PG 16). Defends server-default correctness on the partial index too."""
    headers = {"X-Project-Id": "1"}
    resp = await client.get("/api/tasks?limit=500", headers=headers)
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 1

    for t in tasks:
        assert t["scheduled_at"] is None, (
            f"task {t['id']} backfilled with scheduled_at={t['scheduled_at']!r}"
        )


@pytest.mark.asyncio
async def test_partial_index_exists_in_test_db(db_session) -> None:
    """Confirm ix_tasks_scheduled_at_pending exists on the test DB (proxies
    that migration 0010 ran cleanly). Partial — `WHERE` clause confirms shape."""
    from sqlalchemy import text

    row = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND tablename = 'tasks' "
                "AND indexname = 'ix_tasks_scheduled_at_pending'"
            )
        )
    ).first()
    assert row is not None, "ix_tasks_scheduled_at_pending missing from test DB"
    indexdef = row[0]
    assert "scheduled_at IS NOT NULL" in indexdef
    assert "process_status = 1" in indexdef
    assert "status = 1" in indexdef


# =============================================================================
# 6. Source-text-lock for the new wire-contract detail string
# =============================================================================


def test_scheduled_xor_template_detail_pinned_in_router_source() -> None:
    """Source-text-lock per Kanban #122 pattern: the 400/422 detail for the
    scheduled_at ↔ is_template XOR is wire contract — drift breaks any FE
    that string-matches it."""
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")
    pinned_constant_name = "_DETAIL_SCHEDULED_XOR_TEMPLATE"
    pinned_text = (
        '"scheduled_at is incompatible with is_template=true "\n'
        '    "(use recurrence_rule for templates)"'
    )
    assert pinned_constant_name in source, (
        f"{pinned_constant_name} dropped from routers/tasks.py"
    )
    assert pinned_text in source, (
        "scheduled_at XOR detail string drifted in routers/tasks.py"
    )
    # Constraint-name mapping must still translate the IntegrityError fallback.
    assert '"ck_tasks_scheduled_xor_template"' in source
