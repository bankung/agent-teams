"""Tests for tasks.task_type — Kanban #803.

Adds the bug/feature/chore/docs/refactor classification column. Mirror of the
task_kind test pattern (test_task_kind_recurrence.py) but smaller in scope —
task_type has no cross-table validator, only the Literal-based constraint.

Covers:
1. Schema (Pydantic): TaskCreate default, valid values, invalid rejection;
   TaskUpdate optionality; TaskTypeLiteral lockstep guard.
2. POST /api/tasks: default 'feature' on omit, explicit value round-trips,
   422 on invalid.
3. PATCH /api/tasks/{id}: update task_type, omitted key leaves unchanged.
4. Behavioral backfill — seeded rows carry task_type='feature' from migration
   0015's server_default.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
from pydantic import ValidationError

from src.constants import TaskType


# -----------------------------------------------------------------------------
# Helpers (mirror of test_task_kind_recurrence.py — kept local)
# -----------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


# =============================================================================
# 1. Schema-level (Pydantic) tests
# =============================================================================


def test_task_create_task_type_default_is_feature() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(project_id=1, title="x")
    assert task.task_type == TaskType.FEATURE


def test_task_create_task_type_accepts_each_valid_value() -> None:
    from src.schemas.task import TaskCreate

    for value in TaskType.ALL:
        task = TaskCreate(project_id=1, title="x", task_type=value)
        assert task.task_type == value


def test_task_create_task_type_rejects_unknown() -> None:
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", task_type="invalid")
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    # Pydantic's Literal validator names each allowed value in the error message.
    for v in TaskType.ALL:
        assert v in msg


def test_task_update_task_type_optional() -> None:
    """PATCH semantics: omitted key is fine, optional field stays absent from
    model_fields_set."""
    from src.schemas.task import TaskUpdate

    upd = TaskUpdate(title="renamed")
    assert "task_type" not in upd.model_fields_set


def test_task_update_task_type_accepts_valid() -> None:
    from src.schemas.task import TaskUpdate

    upd = TaskUpdate(task_type="bug")
    assert upd.task_type == "bug"
    assert "task_type" in upd.model_fields_set


def test_task_update_task_type_rejects_unknown() -> None:
    from src.schemas.task import TaskUpdate

    with pytest.raises(ValidationError) as ei:
        TaskUpdate(task_type="invalid")
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "bug" in msg and "feature" in msg


def test_task_type_literal_matches_constants_all() -> None:
    """Lockstep guard's positive case — sets equal at import time."""
    from src.schemas.task import TaskTypeLiteral

    assert set(TaskTypeLiteral.__args__) == set(TaskType.ALL)  # type: ignore[attr-defined]


def test_task_type_literal_drift_raises_at_import(monkeypatch) -> None:
    """Force drift between TaskType.ALL and the Literal — guard at the bottom
    of schemas/task.py must raise RuntimeError. Mirrors the TaskKind drift
    test."""
    import src.constants as constants_mod
    import src.schemas.task as task_schema_mod

    monkeypatch.setattr(
        constants_mod.TaskType, "ALL", ("bug", "feature", "wrong_extra")
    )
    with pytest.raises(RuntimeError, match="drifted"):
        importlib.reload(task_schema_mod)
    # Restore module state so other tests aren't poisoned.
    monkeypatch.undo()
    importlib.reload(task_schema_mod)


# =============================================================================
# 2. POST /api/tasks round-trips
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_default_task_type_is_feature(
    client, scaffold_cleanup
) -> None:
    """POST without task_type → defaults to 'feature' end-to-end."""
    name = _unique_name("tt-default")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "no task_type sent"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_type"] == "feature"
        # Round-trip via GET to confirm DB persisted the default.
        got = await client.get(f"/api/tasks/{body['id']}", headers=headers)
        assert got.json()["task_type"] == "feature"
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_explicit_task_type_bug_201(
    client, scaffold_cleanup
) -> None:
    """POST task_type='bug' → 201 + round-trips."""
    name = _unique_name("tt-bug")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "bug fix",
                "task_type": "bug",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_type"] == "bug"
        got = await client.get(f"/api/tasks/{body['id']}", headers=headers)
        assert got.json()["task_type"] == "bug"
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_invalid_task_type_returns_422(
    client, scaffold_cleanup
) -> None:
    """POST task_type='invalid' → 422 with the field name in the error body."""
    name = _unique_name("tt-bad")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "bad type",
                "task_type": "invalid",
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert "task_type" in str(resp.json())
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_all_valid_task_types_201(
    client, scaffold_cleanup
) -> None:
    """Spot-check every valid value goes round-trip — defends the Literal
    against drift slipping through this test file."""
    name = _unique_name("tt-all")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        for value in TaskType.ALL:
            resp = await client.post(
                "/api/tasks",
                json={
                    "project_id": project_id,
                    "title": f"value: {value}",
                    "task_type": value,
                },
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["task_type"] == value
            await client.delete(
                f"/api/tasks/{resp.json()['id']}", headers=headers
            )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 3. PATCH /api/tasks/{id}
# =============================================================================


@pytest.mark.asyncio
async def test_patch_task_type_updates_value_200(
    client, scaffold_cleanup
) -> None:
    """PATCH task_type='chore' on existing task → 200; subsequent GET returns
    'chore'."""
    name = _unique_name("tt-patch")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "patch target"},
        headers=headers,
    )
    task_id = task.json()["id"]
    # Sanity: default is 'feature' before the PATCH.
    assert task.json()["task_type"] == "feature"

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"task_type": "chore"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["task_type"] == "chore"
        # GET to confirm DB-persisted.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.json()["task_type"] == "chore"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_without_task_type_leaves_value_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH with no task_type key → 200; field stays at its prior value
    ('refactor' here)."""
    name = _unique_name("tt-noop")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "preserve task_type",
            "task_type": "refactor",
        },
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "renamed but type unchanged"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["task_type"] == "refactor"
        # Belt-and-braces: GET as well.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.json()["task_type"] == "refactor"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_type_invalid_returns_422(
    client, scaffold_cleanup
) -> None:
    """PATCH task_type='invalid' → 422 (Literal validator catches before DB)."""
    name = _unique_name("tt-patch-bad")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "patch invalid target"},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"task_type": "invalid"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert "task_type" in str(resp.json())
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 4. Behavioral backfill — existing rows pick up the migration default
# =============================================================================


@pytest.mark.asyncio
async def test_seeded_tasks_have_task_type_feature_backfill(client) -> None:
    """Migration 0015's server_default='feature' must cover all existing rows.
    Defends server_default correctness on ADD COLUMN."""
    headers = {"X-Project-Id": "1"}
    resp = await client.get("/api/tasks?limit=500", headers=headers)
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 1, "expected at least the seeded tasks"

    for t in tasks:
        assert t["task_type"] == "feature", (
            f"task {t['id']} backfilled to {t['task_type']!r} not 'feature'"
        )
