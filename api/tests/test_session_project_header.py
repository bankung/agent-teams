"""Kanban #695 — mandatory `X-Project-Id` header on task endpoints.

Phase 3 of the session-scoped-active-project shift: every task-touching API
call must carry the session-bound project id on the wire so a compaction-
induced context loss surfaces as a 400 at the boundary instead of a silent
write to the wrong project.

Coverage:
1. Missing header on POST → 400 + locked detail.
2. Body project_id != header project_id on POST → 400 + locked detail
   (cross-validation; header wins on conflict).
3. GET / PATCH / DELETE on a task whose project_id != header → 400 + locked
   detail. The 400 fires AFTER `get_or_404` (so a missing id still 404s).
4. GET /api/tasks list filtered by header — only returns rows whose
   project_id matches the header value.
5. Source-text-lock for the three detail-string templates in
   `services/session_project.py` per the #122 / #690 pattern.

Tests use the seeded `agent-teams` project (id=1) as the "session A" project
and a throwaway scaffolded project as "session B" — same convention used by
test_run_mode_consent.py for cross-project setups.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


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


# =============================================================================
# 1. Missing header
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_missing_x_project_id_returns_400(client) -> None:
    """POST /api/tasks without `X-Project-Id` → 400 + the locked detail
    string. The header gate fires at the FastAPI dependency layer before any
    body validation."""
    resp = await client.post(
        "/api/tasks",
        json={"project_id": 1, "title": "missing-header probe"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


@pytest.mark.asyncio
async def test_get_task_missing_x_project_id_returns_400(client) -> None:
    """GET /api/tasks/{id} without header → 400. Locked detail."""
    resp = await client.get("/api/tasks/3")
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


@pytest.mark.asyncio
async def test_list_tasks_missing_x_project_id_returns_400(client) -> None:
    """GET /api/tasks (list) without header → 400. The legacy `?project_id=`
    query param was removed — header is the canonical channel."""
    resp = await client.get("/api/tasks?limit=10")
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


# =============================================================================
# 2. Body / header mismatch on POST
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_body_project_id_mismatch_with_header_returns_400(
    client,
) -> None:
    """POST /api/tasks with header X-Project-Id=1 and body project_id=2 → 400
    with both values in the locked detail. Header is the canonical session-
    bound channel; body's project_id is defense-in-depth."""
    resp = await client.post(
        "/api/tasks",
        json={"project_id": 2, "title": "body-header-mismatch probe"},
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "X-Project-Id header 1 does not match request body project_id 2"
    }


# =============================================================================
# 3. Cross-project header on GET / PATCH / DELETE
# =============================================================================


@pytest.mark.asyncio
async def test_get_task_belonging_to_other_project_rejected(
    client, scaffold_cleanup
) -> None:
    """GET /api/tasks/{task_in_B} with header bound to A → 400 with locked
    detail. The cross-check fires AFTER `get_or_404`, so a missing id still
    surfaces 404 (covered by test_get_task_404_exact_detail in
    test_routes_smoke.py)."""
    name_b = _unique_name("k695-cross-get")
    scaffold_cleanup(name_b)
    create_b = await client.post("/api/projects", json=_project_create_payload(name_b))
    assert create_b.status_code == 201, create_b.text
    project_b = create_b.json()["id"]
    headers_b = {"X-Project-Id": str(project_b)}

    task_in_b = await client.post(
        "/api/tasks",
        json={"project_id": project_b, "title": "k695-cross-get task in B"},
        headers=headers_b,
    )
    assert task_in_b.status_code == 201
    task_id = task_in_b.json()["id"]

    try:
        # GET the B-task with header bound to project_id=1 (the seeded
        # agent-teams project) — must reject.
        resp = await client.get(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": "1"}
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {
            "detail": f"task {task_id} does not belong to project_id 1"
        }
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_b)
        await client.delete(f"/api/projects/{project_b}")


@pytest.mark.asyncio
async def test_patch_task_belonging_to_other_project_rejected(
    client, scaffold_cleanup
) -> None:
    """PATCH /api/tasks/{task_in_B} with header bound to A → 400 with locked detail."""
    name_b = _unique_name("k695-cross-patch")
    scaffold_cleanup(name_b)
    create_b = await client.post("/api/projects", json=_project_create_payload(name_b))
    project_b = create_b.json()["id"]
    headers_b = {"X-Project-Id": str(project_b)}

    task_in_b = await client.post(
        "/api/tasks",
        json={"project_id": project_b, "title": "k695-cross-patch task in B"},
        headers=headers_b,
    )
    task_id = task_in_b.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "should not land"},
            headers={"X-Project-Id": "1"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {
            "detail": f"task {task_id} does not belong to project_id 1"
        }
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_b)
        await client.delete(f"/api/projects/{project_b}")


@pytest.mark.asyncio
async def test_delete_task_belonging_to_other_project_rejected(
    client, scaffold_cleanup
) -> None:
    """DELETE /api/tasks/{task_in_B} with header bound to A → 400 with locked detail."""
    name_b = _unique_name("k695-cross-delete")
    scaffold_cleanup(name_b)
    create_b = await client.post("/api/projects", json=_project_create_payload(name_b))
    project_b = create_b.json()["id"]
    headers_b = {"X-Project-Id": str(project_b)}

    task_in_b = await client.post(
        "/api/tasks",
        json={"project_id": project_b, "title": "k695-cross-delete task in B"},
        headers=headers_b,
    )
    task_id = task_in_b.json()["id"]

    try:
        resp = await client.delete(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": "1"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {
            "detail": f"task {task_id} does not belong to project_id 1"
        }
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_b)
        await client.delete(f"/api/projects/{project_b}")


# =============================================================================
# 4. List filtered by header
# =============================================================================


@pytest.mark.asyncio
async def test_get_tasks_list_filtered_by_session_header(
    client, scaffold_cleanup
) -> None:
    """GET /api/tasks scopes to the header's project_id. With header=A only
    A's tasks come back; with header=B only B's tasks come back. Same
    underlying rows, two disjoint windows."""
    name_b = _unique_name("k695-list-filter")
    scaffold_cleanup(name_b)
    create_b = await client.post("/api/projects", json=_project_create_payload(name_b))
    project_b = create_b.json()["id"]
    headers_b = {"X-Project-Id": str(project_b)}

    task_in_b = await client.post(
        "/api/tasks",
        json={"project_id": project_b, "title": "k695-list-filter task in B"},
        headers=headers_b,
    )
    task_b_id = task_in_b.json()["id"]

    try:
        # Header bound to seeded project (id=1) — must NOT include the B task.
        resp_a = await client.get(
            "/api/tasks?limit=500", headers={"X-Project-Id": "1"}
        )
        assert resp_a.status_code == 200, resp_a.text
        a_ids = {t["id"] for t in resp_a.json()}
        assert task_b_id not in a_ids, (
            f"B's task id={task_b_id} leaked into the A-bound list response"
        )
        # Every row returned with header=1 should have project_id=1.
        for t in resp_a.json():
            assert t["project_id"] == 1, (
                f"X-Project-Id=1 returned a row with project_id={t['project_id']!r}"
            )

        # Header bound to B — must contain ONLY B's task (it was created
        # fresh in this test, so it's the single row we expect).
        resp_b = await client.get("/api/tasks?limit=500", headers=headers_b)
        assert resp_b.status_code == 200, resp_b.text
        b_ids = {t["id"] for t in resp_b.json()}
        assert b_ids == {task_b_id}, (
            f"X-Project-Id={project_b} returned unexpected rows: {b_ids!r}"
        )
    finally:
        await client.delete(f"/api/tasks/{task_b_id}", headers=headers_b)
        await client.delete(f"/api/projects/{project_b}")


# =============================================================================
# 5. Source-text-lock — the three detail strings (#122 / #690 pattern)
# =============================================================================


def test_session_project_header_missing_detail_string_pinned_in_service_source() -> None:
    """Source-text-lock for the missing-header detail (`require_project_id_header`)."""
    from src.services import session_project as svc

    source = Path(svc.__file__).read_text(encoding="utf-8")
    pinned = '"X-Project-Id header is required for task endpoints"'
    assert pinned in source, (
        f"Kanban #695 missing-header detail string drifted in "
        f"services/session_project.py — expected {pinned!r}"
    )


def test_session_project_task_mismatch_detail_string_pinned_in_service_source() -> None:
    """Source-text-lock for the task-mismatch detail template
    (`assert_task_belongs_to_session`)."""
    from src.services import session_project as svc

    source = Path(svc.__file__).read_text(encoding="utf-8")
    # Module template form (str literal with format placeholders).
    pinned = '"task {task_id} does not belong to project_id {session_project_id}"'
    assert pinned in source, (
        f"Kanban #695 task-mismatch detail template drifted in "
        f"services/session_project.py — expected {pinned!r}"
    )


def test_session_project_body_mismatch_detail_string_pinned_in_service_source() -> None:
    """Source-text-lock for the body-mismatch detail template
    (`assert_body_matches_session`)."""
    from src.services import session_project as svc

    source = Path(svc.__file__).read_text(encoding="utf-8")
    pinned = '"X-Project-Id header {header} does not match request body project_id {body}"'
    assert pinned in source, (
        f"Kanban #695 body-mismatch detail template drifted in "
        f"services/session_project.py — expected {pinned!r}"
    )
