"""Tests for tasks.is_pending — Kanban #750.

Backend slice for the "in-flight and stuck" semantics (user clarification
2026-05-11 on #748). is_pending is orthogonal to process_status at the DB
level; the cross-state rule (is_pending=true REQUIRES process_status=2)
is APP-LAYER only — enforced by `services/is_pending.py` at POST and PATCH.

Covers:
- POST default case (is_pending omitted → false → 201).
- PATCH happy paths (set + clear, bundled with process_status).
- POST + PATCH negative cases — cross-state validator at 400 with the
  source-text-locked detail string.
- PATCH asymmetric drift (PATCH'ing AWAY from ps=2 with is_pending=true
  still active fails).
- PATCH bundled clear (downgrade ps + clear flag in one call succeeds).

Source-text-locked detail (mirror task_kind / run_mode pattern):
    "is_pending=true requires process_status=2 (in_progress)"
"""

from __future__ import annotations

import uuid

import pytest


# Regression: Kanban #750
PENDING_DETAIL = "is_pending=true requires process_status=2 (in_progress)"


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


# =============================================================================
# POSITIVE — default + happy path
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_default_is_pending_false_201(client, scaffold_cleanup) -> None:
    """Regression: Kanban #750 — omitted is_pending → false → 201."""
    name = _unique_name("pending-default")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "default pending task"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["is_pending"] is False
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_is_pending_true_on_in_progress_row_200(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — flip is_pending=true on a ps=2 row → 200."""
    name = _unique_name("pending-set")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "x", "process_status": 2},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"is_pending": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_pending"] is True
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_is_pending_false_clears_200(client, scaffold_cleanup) -> None:
    """Regression: Kanban #750 — PATCH is_pending=false clears the flag on a
    ps=2+is_pending=true row → 200."""
    name = _unique_name("pending-clear")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "x",
            "process_status": 2,
            "is_pending": True,
        },
        headers=headers,
    )
    task_id = task.json()["id"]
    assert task.status_code == 201, task.text
    assert task.json()["is_pending"] is True

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"is_pending": False},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_pending"] is False
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_bundled_is_pending_and_process_status_to_2_200(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — bundled transition on a ps=1 row:
    {is_pending: true, process_status: 2} → 200 (both land together, resolved
    pair is valid)."""
    name = _unique_name("pending-bundle-up")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "x"},
        headers=headers,
    )
    task_id = task.json()["id"]
    assert task.json()["process_status"] == 1
    assert task.json()["is_pending"] is False

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"is_pending": True, "process_status": 2},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["process_status"] == 2
        assert body["is_pending"] is True
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# NEGATIVE — cross-state validator at 400 with source-text-locked detail
# =============================================================================


@pytest.mark.asyncio
async def test_post_is_pending_true_with_process_status_1_400(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — POST is_pending=true with ps=1 (explicit) →
    400 with locked detail."""
    name = _unique_name("pending-post-ps1")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "stuck on todo",
                "is_pending": True,
                "process_status": 1,
            },
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {"detail": PENDING_DETAIL}
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_is_pending_true_default_process_status_400(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — POST is_pending=true with default process_status
    (TODO=1) → 400 with locked detail. Validates the omitted-process_status
    default path."""
    name = _unique_name("pending-post-default-ps")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "stuck without ps",
                "is_pending": True,
            },
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {"detail": PENDING_DETAIL}
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_is_pending_true_on_ps_3_row_400(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — PATCH is_pending=true on a ps=3 (review) row
    → 400 (resolved pair = ps=3 + is_pending=true, invalid)."""
    name = _unique_name("pending-patch-onto-ps3")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "x", "process_status": 3},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"is_pending": True},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {"detail": PENDING_DETAIL}
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_process_status_away_from_2_with_pending_active_400(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — PATCH only `process_status=3` on a ps=2 +
    is_pending=true row → 400 (resolved drift: ps=3 + is_pending=true is
    invalid). Asymmetric — the user must clear is_pending in the SAME PATCH
    if they want to transition away from in_progress while pending=true."""
    name = _unique_name("pending-patch-drift")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "x",
            "process_status": 2,
            "is_pending": True,
        },
        headers=headers,
    )
    task_id = task.json()["id"]
    assert task.json()["is_pending"] is True

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 3},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {"detail": PENDING_DETAIL}
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_bundled_clear_and_status_change_200(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #750 — PATCH bundled {is_pending: false,
    process_status: 3} on a ps=2 + is_pending=true row → 200 (resolved =
    ps=3 + is_pending=false, valid). Bundled clear is the supported escape
    hatch for transitioning away from in_progress while pending was active."""
    name = _unique_name("pending-bundle-clear")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "x",
            "process_status": 2,
            "is_pending": True,
        },
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"is_pending": False, "process_status": 3},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_pending"] is False
        assert body["process_status"] == 3
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")
