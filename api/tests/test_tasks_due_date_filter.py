"""Contract-smoke tests for GET /api/tasks?due_from=&due_to= — Calendar M2.

Covers:
1. due_from alone — returns tasks on/after the bound, excludes earlier + NULL.
2. due_to alone  — returns tasks on/before the bound, excludes later + NULL.
3. Both bounds   — returns only tasks within the range, excludes out-of-range + NULL.
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


def _task_payload(project_id: int, title: str, due_date: str | None = None) -> dict:
    payload: dict = {"project_id": project_id, "title": title}
    if due_date is not None:
        payload["due_date"] = due_date
    return payload


@pytest.mark.asyncio
async def test_due_from_filter(client, scaffold_cleanup) -> None:
    """due_from alone: tasks on/after the bound returned; earlier + NULL excluded."""
    name = _unique_name("due-from")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Create 3 tasks: before bound, on bound, after bound, plus one with NULL due_date
        before = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "before", "2026-06-01"),
            headers=headers,
        )
        on_bound = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "on-bound", "2026-06-10"),
            headers=headers,
        )
        after = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "after", "2026-06-20"),
            headers=headers,
        )
        null_due = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "no-due-date"),
            headers=headers,
        )
        assert all(r.status_code == 201 for r in (before, on_bound, after, null_due))

        resp = await client.get(
            "/api/tasks?due_from=2026-06-10&limit=50", headers=headers
        )
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()}

        assert on_bound.json()["id"] in ids
        assert after.json()["id"] in ids
        assert before.json()["id"] not in ids
        assert null_due.json()["id"] not in ids
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_due_to_filter(client, scaffold_cleanup) -> None:
    """due_to alone: tasks on/before the bound returned; later + NULL excluded."""
    name = _unique_name("due-to")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        before = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "before", "2026-06-01"),
            headers=headers,
        )
        on_bound = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "on-bound", "2026-06-10"),
            headers=headers,
        )
        after = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "after", "2026-06-20"),
            headers=headers,
        )
        null_due = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "no-due-date"),
            headers=headers,
        )
        assert all(r.status_code == 201 for r in (before, on_bound, after, null_due))

        resp = await client.get(
            "/api/tasks?due_to=2026-06-10&limit=50", headers=headers
        )
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()}

        assert before.json()["id"] in ids
        assert on_bound.json()["id"] in ids
        assert after.json()["id"] not in ids
        assert null_due.json()["id"] not in ids
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_due_from_and_due_to_filter(client, scaffold_cleanup) -> None:
    """Both bounds: only tasks strictly inside the range returned; NULL excluded."""
    name = _unique_name("due-range")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        early = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "early", "2026-05-31"),
            headers=headers,
        )
        in_range = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "in-range", "2026-06-15"),
            headers=headers,
        )
        late = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "late", "2026-07-01"),
            headers=headers,
        )
        null_due = await client.post(
            "/api/tasks",
            json=_task_payload(project_id, "no-due-date"),
            headers=headers,
        )
        assert all(r.status_code == 201 for r in (early, in_range, late, null_due))

        resp = await client.get(
            "/api/tasks?due_from=2026-06-01&due_to=2026-06-30&limit=50",
            headers=headers,
        )
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()}

        assert in_range.json()["id"] in ids
        assert early.json()["id"] not in ids
        assert late.json()["id"] not in ids
        assert null_due.json()["id"] not in ids
    finally:
        await client.delete(f"/api/projects/{project_id}")
