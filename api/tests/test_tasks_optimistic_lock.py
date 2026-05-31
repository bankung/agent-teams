"""Kanban #1128 — optimistic locking for PATCH /api/tasks/{id}.

Coverage (4 cases):
  (a) Two PATCHes with the same stale baseline: first 200, second 409.
  (b) No header → backward-compatible 200.
  (c) Header equal to current updated_at → 200 (not-strictly-newer passes).
  (d) Unparseable header → 400.
"""

from __future__ import annotations

import pytest


async def _get_project_id(client) -> int:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str) -> dict:
    resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": title},
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _delete_task(client, task_id: int, project_id: int) -> None:
    await client.delete(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(project_id)},
    )


# ---------------------------------------------------------------------------
# (a) Stale baseline → first PATCH 200, second PATCH 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_patch_with_stale_baseline_returns_409(client) -> None:
    """Two PATCHes carrying the SAME If-Unmodified-Since:
    - PATCH 1 (baseline = created updated_at) succeeds → 200, bumps updated_at.
    - PATCH 2 (same stale baseline) → 409 with current_updated_at in body.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}
    task = await _make_task(client, project_id, "k1128-a optimistic-lock test")
    task_id = task["id"]
    baseline = task["updated_at"]

    try:
        # PATCH 1: baseline == current → passes.
        resp1 = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "k1128-a title v2"},
            headers={**headers, "If-Unmodified-Since": baseline},
        )
        assert resp1.status_code == 200, f"PATCH 1 unexpected status: {resp1.text}"
        new_updated_at = resp1.json()["updated_at"]
        assert new_updated_at != baseline, "updated_at should have advanced after PATCH 1"

        # PATCH 2: still using the original stale baseline → 409.
        resp2 = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "k1128-a title v3"},
            headers={**headers, "If-Unmodified-Since": baseline},
        )
        assert resp2.status_code == 409, f"PATCH 2 should be 409, got: {resp2.text}"
        body = resp2.json()
        assert "current_updated_at" in body["detail"], (
            f"409 body missing current_updated_at: {body}"
        )
    finally:
        await _delete_task(client, task_id, project_id)


# ---------------------------------------------------------------------------
# (b) No header → backward-compatible 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_without_header_succeeds(client) -> None:
    """Omitting If-Unmodified-Since must still return 200 (backward compat)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}
    task = await _make_task(client, project_id, "k1128-b no-header test")
    task_id = task["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "k1128-b updated"},
            headers=headers,  # no If-Unmodified-Since
        )
        assert resp.status_code == 200, f"Backward-compat PATCH failed: {resp.text}"
    finally:
        await _delete_task(client, task_id, project_id)


# ---------------------------------------------------------------------------
# (c) Header >= current updated_at → 200 (not strictly newer, no conflict)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_with_current_baseline_succeeds(client) -> None:
    """If-Unmodified-Since == task's current updated_at → 200 (no conflict)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}
    task = await _make_task(client, project_id, "k1128-c current-baseline test")
    task_id = task["id"]
    current_updated_at = task["updated_at"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "k1128-c updated"},
            headers={**headers, "If-Unmodified-Since": current_updated_at},
        )
        assert resp.status_code == 200, (
            f"PATCH with current baseline should succeed, got: {resp.text}"
        )
    finally:
        await _delete_task(client, task_id, project_id)


# ---------------------------------------------------------------------------
# (d) Unparseable header → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_with_invalid_header_returns_400(client) -> None:
    """An unparseable If-Unmodified-Since header value returns 400."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}
    task = await _make_task(client, project_id, "k1128-d bad-header test")
    task_id = task["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "k1128-d updated"},
            headers={**headers, "If-Unmodified-Since": "not-a-timestamp"},
        )
        assert resp.status_code == 400, f"Bad header should return 400, got: {resp.text}"
        assert "ISO-8601" in resp.text, f"400 body should mention ISO-8601: {resp.text}"
    finally:
        await _delete_task(client, task_id, project_id)
