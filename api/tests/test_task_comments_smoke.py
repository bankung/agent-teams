"""Kanban #1005 — task_comments append-only thread (backend contract smoke).

First-pass contract-smoke coverage (dev-sr-backend scope — the rigorous suite is
dev-tester's). Proves the new POST/GET surface is wired: status codes, response
shape, chronological ordering, and the `?before` pagination cursor. Append-only
(AC#7) is verified structurally — there is no PATCH/DELETE route to call.

Coverage:
  (a) POST /api/tasks/{id}/comments → 201 + the created row round-trips
  (b) GET  /api/tasks/{id}/comments → chronological (oldest-first) list
  (c) GET  ?before=<id> cursor → returns only comments with id < before

Cleanup: deleting the parent task is a SOFT-delete, which does NOT cascade the
comments (CASCADE is on hard-delete only). That's fine for test hygiene — each
test uses its own fresh task and asserts only on that task's thread.
"""

from __future__ import annotations

import pytest


async def _get_project_id(client) -> int:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str) -> int:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# -----------------------------------------------------------------------------
# (a) POST happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_comment_returns_201_and_created_row(client) -> None:
    """Appending a comment returns 201 + a row whose fields round-trip."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(client, project_id, "k1005-a comment thread")
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/comments",
            json={
                "author_kind": "agent",
                "author_label": "dev-backend",
                "body": "Backend foundation landed.",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_id"] == task_id, body
        assert body["author_kind"] == "agent", body
        assert body["author_label"] == "dev-backend", body
        assert body["body"] == "Backend foundation landed.", body
        # body_markdown defaults to true (matches DB DEFAULT).
        assert body["body_markdown"] is True, body
        assert isinstance(body["id"], int) and body["id"] >= 1, body
        assert body["created_at"], body
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (b) GET chronological list
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_comments_lists_chronologically(client) -> None:
    """Three comments appended in order come back oldest-first (id ASC)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(client, project_id, "k1005-b chronological thread")
    try:
        posted_ids = []
        for n in range(1, 4):
            resp = await client.post(
                f"/api/tasks/{task_id}/comments",
                json={"author_kind": "user", "body": f"note {n}"},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            posted_ids.append(resp.json()["id"])

        listing = await client.get(
            f"/api/tasks/{task_id}/comments", headers=headers
        )
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        got_ids = [r["id"] for r in rows]
        assert got_ids == posted_ids, (
            f"comments not chronological: got {got_ids}, posted {posted_ids}"
        )
        assert [r["body"] for r in rows] == ["note 1", "note 2", "note 3"], rows
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (c) GET ?before cursor
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_comments_before_cursor_paginates(client) -> None:
    """`?before=<id>` returns only comments with id strictly less than the
    cursor — and excludes the cursor row itself."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(client, project_id, "k1005-c cursor thread")
    try:
        ids = []
        for n in range(1, 4):
            resp = await client.post(
                f"/api/tasks/{task_id}/comments",
                json={"author_kind": "system", "body": f"event {n}"},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            ids.append(resp.json()["id"])

        # Cursor on the LAST comment → expect the two earlier ones only.
        resp = await client.get(
            f"/api/tasks/{task_id}/comments?before={ids[-1]}", headers=headers
        )
        assert resp.status_code == 200, resp.text
        got_ids = [r["id"] for r in resp.json()]
        assert got_ids == ids[:-1], (
            f"before-cursor page drifted: got {got_ids}, expected {ids[:-1]}"
        )
        assert ids[-1] not in got_ids, "cursor row must be excluded"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
