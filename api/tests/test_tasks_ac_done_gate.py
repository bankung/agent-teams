"""Kanban #2765 (E1) — server-side validation gate: process_status=5 (DONE)
requires every acceptance_criteria item to be resolved ('passed' or 'na').

Mirrors the resolved-final PATCH-gate test style used by
test_tasks_scheduled_at.py: project+task fixture per test, PATCH the live
endpoint, assert status code + detail string. Six cases:

1. reject — one item 'pending' + process_status=5 -> 422
2. reject — one item 'failed' + process_status=5 -> 422
3. allow  — all items 'passed' + process_status=5 -> 200
4. allow  — item 'na' + process_status=5 -> 200
5. allow  — acceptance_criteria empty/null + process_status=5 -> 200
6. allow  — combined body: fresh verified AC array + process_status=5 in ONE
   PATCH succeeds (the zb-task-done paved path; resolved-final judges the
   NEW array, not stale stored state)

Plus a source-text-lock for the new detail string constant.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k2765 AC-done-gate fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _create_project_and_task(client, scaffold_cleanup, *, acceptance_criteria=None):
    name = scaffold_cleanup(_unique_name("k2765"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "k2765 AC-done-gate fixture task",
        "description": "AC-done-gate test task",
    }
    if acceptance_criteria is not None:
        payload["acceptance_criteria"] = acceptance_criteria

    post = await client.post("/api/tasks", json=payload, headers=headers)
    assert post.status_code == 201, post.text
    task_id = post.json()["id"]
    return project_id, task_id, headers


def _ac_item(status: str, text: str = "AC under test") -> dict:
    return {"text": text, "status": status}


# =============================================================================
# 1. reject — pending AC + process_status=5
# =============================================================================


@pytest.mark.asyncio
async def test_patch_done_with_pending_ac_rejected_422(client, scaffold_cleanup) -> None:
    project_id, task_id, headers = await _create_project_and_task(
        client, scaffold_cleanup, acceptance_criteria=[_ac_item("pending", "must ship X")]
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "must ship X" in detail
        assert "unresolved" in detail
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 2. reject — failed AC + process_status=5
# =============================================================================


@pytest.mark.asyncio
async def test_patch_done_with_failed_ac_rejected_422(client, scaffold_cleanup) -> None:
    project_id, task_id, headers = await _create_project_and_task(
        client, scaffold_cleanup, acceptance_criteria=[_ac_item("failed", "must not crash")]
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "must not crash" in detail
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 3. allow — all passed + process_status=5
# =============================================================================


@pytest.mark.asyncio
async def test_patch_done_with_all_passed_ac_allowed_200(client, scaffold_cleanup) -> None:
    project_id, task_id, headers = await _create_project_and_task(
        client,
        scaffold_cleanup,
        acceptance_criteria=[_ac_item("passed", "criterion one"), _ac_item("passed", "criterion two")],
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["process_status"] == 5
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 4. allow — na + process_status=5
# =============================================================================


@pytest.mark.asyncio
async def test_patch_done_with_na_ac_allowed_200(client, scaffold_cleanup) -> None:
    project_id, task_id, headers = await _create_project_and_task(
        client, scaffold_cleanup, acceptance_criteria=[_ac_item("na", "not applicable here")]
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["process_status"] == 5
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 5. allow — AC empty/null + process_status=5
# =============================================================================


@pytest.mark.asyncio
async def test_patch_done_with_null_ac_allowed_200(client, scaffold_cleanup) -> None:
    project_id, task_id, headers = await _create_project_and_task(
        client, scaffold_cleanup, acceptance_criteria=None
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["process_status"] == 5
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_done_with_empty_list_ac_allowed_200(client, scaffold_cleanup) -> None:
    project_id, task_id, headers = await _create_project_and_task(
        client, scaffold_cleanup, acceptance_criteria=None
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 5, "acceptance_criteria": []},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["process_status"] == 5
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 6. allow — combined body (fresh verified AC array + process_status=5 in
#    ONE PATCH; the zb-task-done paved path)
# =============================================================================


@pytest.mark.asyncio
async def test_patch_combined_ac_verify_and_done_same_body_allowed_200(
    client, scaffold_cleanup
) -> None:
    """Row starts with a PENDING stored AC (would 422 on a bare {process_status:5}).
    A single PATCH carrying BOTH the freshly-verified AC array (all passed)
    AND process_status=5 must succeed — the gate must judge the NEW array
    from the PATCH body, not the stale stored 'pending' state."""
    project_id, task_id, headers = await _create_project_and_task(
        client, scaffold_cleanup, acceptance_criteria=[_ac_item("pending", "verify me")]
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={
                "acceptance_criteria": [_ac_item("passed", "verify me")],
                "process_status": 5,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["process_status"] == 5
        assert body["acceptance_criteria"][0]["status"] == "passed"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# Source-text-lock
# =============================================================================


def test_ac_done_gate_detail_pinned_in_router_source() -> None:
    """Source-text-lock per Kanban #122 pattern: the 422 detail is wire
    contract — drift breaks any FE/skill that string-matches it."""
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")
    assert "_DETAIL_AC_NOT_ALL_RESOLVED" in source
    assert (
        '"cannot set process_status=5 (DONE): acceptance_criteria has unresolved "'
        in source
    )
