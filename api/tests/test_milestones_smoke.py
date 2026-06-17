"""Kanban #1868 — per-project Milestones contract smoke tests (Phase 1).

First-pass contract-smoke coverage for the happy paths of the new milestones
surface + task wiring:
  (1) Milestone CRUD round-trip (POST + GET detail w/ rollup + PATCH + DELETE,
      soft-delete-aware list).
  (2) Rollup math: mixed process_status (incl cancelled EXCLUDED from the
      progress denominator), div-by-zero guard.
  (3) DELETE milestone → child tasks.milestone_id set to NULL (same txn).
  (4) Same-project validation rejects a cross-project milestone_id (POST + PATCH).
  (5) Task list `?milestone_id` filter.
  (6) Soft-deleted milestone hidden from the default list; include_deleted brings
      it back.

The rigorous suite (edge cases — milestone_status `?status` filter combinatorics,
start_date>target_date 422 negatives, resolved-final PATCH date check, cross-
project 404 on GET detail, source-text-lock pins on detail strings, idempotent
re-DELETE) is dev-tester's domain.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror handoff_templates_smoke / action_templates_smoke)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"smoke fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _milestone_payload(project_id: int, title: str, **overrides) -> dict:
    base = {
        "project_id": project_id,
        "title": title,
        "description": "smoke fixture milestone",
        "milestone_status": "planned",
    }
    base.update(overrides)
    return base


async def _make_task(
    client, headers: dict, project_id: int, title: str, **overrides
) -> dict:
    body = {"project_id": project_id, "title": title}
    body.update(overrides)
    resp = await client.post("/api/tasks", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# (1) CRUD round-trip + (6) soft-delete-aware list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_crud_happy_round_trip(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "ms-crud")
    headers = {"X-Project-Id": str(pid)}

    title = f"Release v1 {uuid.uuid4().hex[:6]}"

    # POST
    resp = await client.post(
        "/api/milestones",
        headers=headers,
        json=_milestone_payload(
            pid, title, start_date="2026-06-01", target_date="2026-06-30"
        ),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    ms_id = body["id"]
    assert body["project_id"] == pid
    assert body["title"] == title
    assert body["milestone_status"] == "planned"
    assert body["start_date"] == "2026-06-01"
    assert body["target_date"] == "2026-06-30"
    assert body["released_at"] is None
    # POSITIVE: the soft-delete flag is NOT leaked on the wire.
    assert "status" not in body, body

    # GET detail WITH rollup (no tasks yet → empty rollup, progress 0.0).
    resp = await client.get(f"/api/milestones/{ms_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["id"] == ms_id
    assert "rollup" in detail, detail
    assert detail["rollup"]["total"] == 0
    assert detail["rollup"]["done"] == 0
    assert detail["rollup"]["progress_pct"] == 0.0
    assert detail["rollup"]["by_process_status"] == {
        "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "8": 0
    }

    # GET list — includes our milestone.
    resp = await client.get("/api/milestones", headers=headers)
    assert resp.status_code == 200, resp.text
    assert any(m["id"] == ms_id for m in resp.json()), resp.json()

    # PATCH lifecycle status + released_at.
    resp = await client.patch(
        f"/api/milestones/{ms_id}",
        headers=headers,
        json={"milestone_status": "released", "released_at": "2026-06-30T12:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    assert patched["milestone_status"] == "released"
    assert patched["released_at"] is not None

    # DELETE soft-deletes (204).
    resp = await client.delete(f"/api/milestones/{ms_id}", headers=headers)
    assert resp.status_code == 204, resp.text

    # (6) Default list EXCLUDES the soft-deleted row...
    resp = await client.get("/api/milestones", headers=headers)
    assert resp.status_code == 200, resp.text
    assert not any(m["id"] == ms_id for m in resp.json()), resp.json()

    # ...and include_deleted=true brings it back.
    resp = await client.get("/api/milestones?include_deleted=true", headers=headers)
    assert resp.status_code == 200, resp.text
    assert any(m["id"] == ms_id for m in resp.json()), resp.json()


# ---------------------------------------------------------------------------
# (2) Rollup math — mixed process_status, cancelled excluded from denominator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_rollup_excludes_cancelled_from_progress(
    client, scaffold_cleanup
) -> None:
    """5 tasks: 2 DONE(5), 1 TODO(1), 1 IN_PROGRESS(2), 1 CANCELLED(6).

    total = 5 (includes cancelled). progress denominator = 5 - 1 = 4.
    progress_pct = 2/4 * 100 = 50.0.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "ms-rollup")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/milestones", headers=headers,
        json=_milestone_payload(pid, "Rollup milestone"),
    )
    assert resp.status_code == 201, resp.text
    ms_id = resp.json()["id"]

    # Create 5 tasks all assigned to the milestone, in varied process_status.
    for ps in (5, 5, 1, 2, 6):
        await _make_task(
            client, headers, pid,
            f"task ps={ps} {uuid.uuid4().hex[:4]}",
            milestone_id=ms_id,
            process_status=ps,
        )

    resp = await client.get(f"/api/milestones/{ms_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    rollup = resp.json()["rollup"]
    assert rollup["total"] == 5, rollup
    assert rollup["done"] == 2, rollup
    assert rollup["by_process_status"]["5"] == 2, rollup
    assert rollup["by_process_status"]["6"] == 1, rollup
    assert rollup["by_process_status"]["1"] == 1, rollup
    assert rollup["by_process_status"]["2"] == 1, rollup
    # POSITIVE assert: progress reflects the done count over non-cancelled total.
    assert rollup["progress_pct"] == 50.0, rollup


@pytest.mark.asyncio
async def test_milestone_rollup_all_cancelled_progress_zero_no_div_by_zero(
    client, scaffold_cleanup
) -> None:
    """All tasks cancelled → non-cancelled denominator is 0 → progress 0.0
    (div-by-zero guard), NOT a 500."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "ms-allcancel")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/milestones", headers=headers,
        json=_milestone_payload(pid, "All-cancelled milestone"),
    )
    ms_id = resp.json()["id"]

    for _ in range(2):
        await _make_task(
            client, headers, pid, f"cancelled {uuid.uuid4().hex[:4]}",
            milestone_id=ms_id, process_status=6,
        )

    resp = await client.get(f"/api/milestones/{ms_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    rollup = resp.json()["rollup"]
    assert rollup["total"] == 2, rollup
    assert rollup["progress_pct"] == 0.0, rollup


# ---------------------------------------------------------------------------
# (3) DELETE milestone → child tasks.milestone_id NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_milestone_nulls_child_task_milestone_id(
    client, scaffold_cleanup
) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "ms-detach")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/milestones", headers=headers,
        json=_milestone_payload(pid, "Detach milestone"),
    )
    ms_id = resp.json()["id"]

    task = await _make_task(
        client, headers, pid, "child task", milestone_id=ms_id
    )
    task_id = task["id"]
    # POSITIVE: the task IS attached before the delete.
    assert task["milestone_id"] == ms_id, task

    # DELETE the milestone.
    resp = await client.delete(f"/api/milestones/{ms_id}", headers=headers)
    assert resp.status_code == 204, resp.text

    # NEGATIVE (the lock): the child task's milestone_id is now NULL — and the
    # task itself still exists (SET NULL, not cascade-delete).
    resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["milestone_id"] is None, resp.json()


# ---------------------------------------------------------------------------
# (4) Same-project validation rejects a cross-project milestone_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_create_rejects_cross_project_milestone(
    client, scaffold_cleanup
) -> None:
    pid_a = await _make_fresh_project(client, scaffold_cleanup, "ms-xproj-a")
    pid_b = await _make_fresh_project(client, scaffold_cleanup, "ms-xproj-b")
    headers_a = {"X-Project-Id": str(pid_a)}
    headers_b = {"X-Project-Id": str(pid_b)}

    # Milestone lives in project B.
    resp = await client.post(
        "/api/milestones", headers=headers_b,
        json=_milestone_payload(pid_b, "B milestone"),
    )
    ms_b = resp.json()["id"]

    # POST a task in project A referencing project B's milestone → 422.
    resp = await client.post(
        "/api/tasks", headers=headers_a,
        json={"project_id": pid_a, "title": "x-proj task", "milestone_id": ms_b},
    )
    assert resp.status_code == 422, resp.text
    assert "different project" in resp.json()["detail"], resp.json()

    # POSITIVE: a task in project A with a SAME-project milestone succeeds.
    resp = await client.post(
        "/api/milestones", headers=headers_a,
        json=_milestone_payload(pid_a, "A milestone"),
    )
    ms_a = resp.json()["id"]
    task = await _make_task(
        client, headers_a, pid_a, "same-proj task", milestone_id=ms_a
    )
    assert task["milestone_id"] == ms_a, task

    # PATCH that same task to point at project B's milestone → 422.
    resp = await client.patch(
        f"/api/tasks/{task['id']}", headers=headers_a,
        json={"milestone_id": ms_b},
    )
    assert resp.status_code == 422, resp.text
    assert "different project" in resp.json()["detail"], resp.json()


@pytest.mark.asyncio
async def test_task_create_rejects_missing_milestone(
    client, scaffold_cleanup
) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "ms-missing")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks", headers=headers,
        json={"project_id": pid, "title": "bad ms", "milestone_id": 999_999_999},
    )
    assert resp.status_code == 422, resp.text
    assert "does not exist" in resp.json()["detail"], resp.json()


# ---------------------------------------------------------------------------
# due_date round-trip (Kanban #1868 follow-up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_due_date_round_trip(client, scaffold_cleanup) -> None:
    """due_date: set on POST, readable on GET, updatable via PATCH, clearable;
    never touches scheduled_at."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "dd-smoke")
    headers = {"X-Project-Id": str(pid)}

    # POST with due_date set.
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={"project_id": pid, "title": "due_date task", "due_date": "2026-07-15"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    task_id = body["id"]
    # POSITIVE: due_date round-trips.
    assert body["due_date"] == "2026-07-15", body
    # NEGATIVE (decoupling lock): setting due_date must NOT touch scheduled_at.
    assert body["scheduled_at"] is None, body

    # GET read confirms due_date persisted.
    resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["due_date"] == "2026-07-15", resp.json()

    # PATCH — change the date.
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers=headers,
        json={"due_date": "2026-08-01"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["due_date"] == "2026-08-01", resp.json()

    # PATCH — clear via explicit null.
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers=headers,
        json={"due_date": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["due_date"] is None, resp.json()

    # POST without due_date — defaults to NULL.
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={"project_id": pid, "title": "no due_date task"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["due_date"] is None, resp.json()


@pytest.mark.asyncio
async def test_task_list_milestone_id_filter(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "ms-filter")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/milestones", headers=headers,
        json=_milestone_payload(pid, "Filter milestone"),
    )
    ms_id = resp.json()["id"]

    in_ms = await _make_task(
        client, headers, pid, "in milestone", milestone_id=ms_id
    )
    out_ms = await _make_task(client, headers, pid, "no milestone")

    resp = await client.get(f"/api/tasks?milestone_id={ms_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = [t["id"] for t in resp.json()]
    # POSITIVE: the assigned task is returned.
    assert in_ms["id"] in ids, ids
    # NEGATIVE (the lock): the unassigned task is NOT returned by the filter.
    assert out_ms["id"] not in ids, ids
