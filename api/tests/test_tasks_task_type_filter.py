"""Contract-smoke tests for GET /api/tasks?task_type= — Kanban #2699 F5.

Covers:
1. task_type=audit alone — returns only audit rows, excludes other types.
2. task_type composes with process_status — both predicates apply (AND).
3. Invalid task_type value — 422 at the FastAPI boundary (Literal-Query
   mirrors run_mode's validation pattern; see routers/tasks.py list_tasks).
"""

from __future__ import annotations

import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _task_payload(
    project_id: int,
    title: str,
    task_type: str | None = None,
    process_status: int | None = None,
) -> dict:
    payload: dict = {"project_id": project_id, "title": title}
    if task_type is not None:
        payload["task_type"] = task_type
    if process_status is not None:
        payload["process_status"] = process_status
    return payload


@pytest.mark.asyncio
async def test_task_type_filter_returns_only_matching_rows(
    client, scaffold_cleanup
) -> None:
    """task_type=audit alone: only audit rows returned; other types excluded."""
    name = _unique_name("tt-filter")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        audit = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "audit-row", task_type="audit"),
            headers=headers,
        )
        feature = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "feature-row", task_type="feature"),
            headers=headers,
        )
        bug = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "bug-row", task_type="bug"),
            headers=headers,
        )
        assert all(r.status_code == 201 for r in (audit, feature, bug))

        resp = await client.get(
            "/api/tasks?task_type=audit&limit=50", headers=headers
        )
        assert resp.status_code == 200
        rows = resp.json()
        ids = {t["id"] for t in rows}

        # Positive: the mutation (filter) actually happens.
        assert audit.json()["id"] in ids
        assert all(t["task_type"] == "audit" for t in rows)
        # Negative: other types are excluded, not vacuously included.
        assert feature.json()["id"] not in ids
        assert bug.json()["id"] not in ids
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_task_type_composes_with_process_status(
    client, scaffold_cleanup
) -> None:
    """task_type=audit&process_status=5: both predicates apply (AND, not OR)."""
    name = _unique_name("tt-compose")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        audit_done = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "audit-done", task_type="audit"),
            headers=headers,
        )
        assert audit_done.status_code == 201
        audit_done_id = audit_done.json()["id"]
        patch = await client.patch(
            f"/api/tasks/{audit_done_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert patch.status_code == 200

        audit_todo = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "audit-todo", task_type="audit"),
            headers=headers,
        )
        feature_done = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "feature-done", task_type="feature"),
            headers=headers,
        )
        assert feature_done.status_code == 201
        feature_done_id = feature_done.json()["id"]
        patch2 = await client.patch(
            f"/api/tasks/{feature_done_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert patch2.status_code == 200
        assert audit_todo.status_code == 201

        resp = await client.get(
            "/api/tasks?task_type=audit&process_status=5&limit=50",
            headers=headers,
        )
        assert resp.status_code == 200
        rows = resp.json()
        ids = {t["id"] for t in rows}

        # Positive: the AND-composed row is present.
        assert audit_done_id in ids
        assert all(
            t["task_type"] == "audit" and t["process_status"] == 5 for t in rows
        )
        # Negative: audit-but-not-done and done-but-not-audit are both excluded.
        assert audit_todo.json()["id"] not in ids
        assert feature_done_id not in ids
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_task_type_invalid_value_returns_422(client, scaffold_cleanup) -> None:
    """task_type=zzz (not in TaskType.ALL): 422 at the FastAPI Query boundary.

    Mirrors run_mode's validation behavior — task_type is declared as an
    inline Literal[...] Query param (routers/tasks.py list_tasks), so
    FastAPI/Pydantic reject an out-of-enum value before the handler body
    runs (unlike process_status, which is a bare `int` with no enum check).
    """
    name = _unique_name("tt-invalid")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.get("/api/tasks?task_type=zzz", headers=headers)
        assert resp.status_code == 422
    finally:
        await client.delete(f"/api/projects/{project_id}")
