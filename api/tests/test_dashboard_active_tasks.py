"""Contract tests for GET /api/dashboard/active-tasks — Kanban #945.

Operator-level cross-project endpoint: NO X-Project-Id header required.
Returns tasks with `process_status IN (2, 3, 4)` across active projects,
with project info denormalized into each row.

Coverage:
1. Single project with one in-progress + one review + one blocked + one done
   → 3 rows returned (done excluded); shape contract verified.
2. Soft-deleted project → its tasks excluded from the response.
3. TODO (1) and CANCELLED (6) tasks excluded.
4. Default sort: (project_name ASC, updated_at DESC) within group.
5. Denorm shape: project_name + team present on every row.
6. total_count matches rows length.

The seeded `agent-teams` project (id=1) carries its own live tasks; tests
don't pin those exact numbers — each test creates its own scaffold project
and asserts behavior on THAT project's rows only (filter on project_id).
"""

from __future__ import annotations

import uuid

import pytest


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k945 fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


async def _make_project(
    client, scaffold_cleanup, *, slug: str = "k945", team: str = "dev"
) -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name, team=team)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_task(
    client, project_id: int, title: str, **extras
) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(
    client, project_id: int, task_id: int, body: dict
) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(
        f"/api/tasks/{task_id}", json=body, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _rows_for_project(client, project_id: int) -> list[dict]:
    """Filter the cross-project response to a single project's rows."""
    resp = await client.get("/api/dashboard/active-tasks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "rows" in body and "total_count" in body, body
    return [r for r in body["rows"] if r["project_id"] == project_id]


# ---- tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_tasks_endpoint_no_header_required(
    client, scaffold_cleanup
) -> None:
    """Operator-level: the endpoint must respond 200 with no headers."""
    # Fresh project just so we know the endpoint isn't dependent on any
    # particular row being present.
    await _make_project(client, scaffold_cleanup, slug="k945-noheader")
    resp = await client.get("/api/dashboard/active-tasks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict), body
    assert isinstance(body["rows"], list), body
    assert isinstance(body["total_count"], int), body
    # total_count must match len(rows).
    assert body["total_count"] == len(body["rows"])


@pytest.mark.asyncio
async def test_active_tasks_filters_to_in_progress_review_blocked(
    client, scaffold_cleanup
) -> None:
    """Tasks at IN_PROGRESS (2), REVIEW (3), BLOCKED (4) included; TODO (1),
    DONE (5), CANCELLED (6) excluded.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k945-filter")
    pid = project["id"]

    # Create one of each lifecycle state.
    t_todo = await _make_task(client, pid, "k945 todo (excluded)")
    t_inprog = await _make_task(client, pid, "k945 in-progress")
    await _patch_task(client, pid, t_inprog["id"], {"process_status": 2})
    t_review = await _make_task(client, pid, "k945 review")
    await _patch_task(client, pid, t_review["id"], {"process_status": 3})
    t_blocked = await _make_task(client, pid, "k945 blocked")
    await _patch_task(client, pid, t_blocked["id"], {"process_status": 4})
    t_done = await _make_task(client, pid, "k945 done (excluded)")
    await _patch_task(client, pid, t_done["id"], {"process_status": 5})
    t_cancelled = await _make_task(client, pid, "k945 cancelled (excluded)")
    await _patch_task(
        client,
        pid,
        t_cancelled["id"],
        {"process_status": 6, "status_change_reason": "test cleanup"},
    )

    our_rows = await _rows_for_project(client, pid)
    task_ids = {r["task_id"] for r in our_rows}

    # Included: in_progress / review / blocked
    assert t_inprog["id"] in task_ids
    assert t_review["id"] in task_ids
    assert t_blocked["id"] in task_ids
    # Excluded: todo / done / cancelled
    assert t_todo["id"] not in task_ids
    assert t_done["id"] not in task_ids
    assert t_cancelled["id"] not in task_ids
    assert len(our_rows) == 3, our_rows


@pytest.mark.asyncio
async def test_active_tasks_denormalizes_project_fields(
    client, scaffold_cleanup
) -> None:
    """Each row carries project_name + team (denorm contract)."""
    project = await _make_project(
        client, scaffold_cleanup, slug="k945-denorm", team="dev"
    )
    pid = project["id"]
    pname = project["name"]

    t = await _make_task(client, pid, "k945 denorm task")
    await _patch_task(client, pid, t["id"], {"process_status": 2})

    our_rows = await _rows_for_project(client, pid)
    assert len(our_rows) == 1
    row = our_rows[0]
    assert row["project_id"] == pid
    assert row["project_name"] == pname
    assert row["team"] == "dev"
    # Shape: all locked fields present on the row.
    for key in (
        "task_id",
        "title",
        "project_id",
        "project_name",
        "team",
        "process_status",
        "run_mode",
        "task_kind",
        "assigned_role",
        "priority",
        "updated_at",
        "blocked_by",
    ):
        assert key in row, (key, row)


@pytest.mark.asyncio
async def test_active_tasks_excludes_soft_deleted_projects(
    client, scaffold_cleanup
) -> None:
    """Soft-deleted project (status=0) → its tasks NOT in the response."""
    project = await _make_project(client, scaffold_cleanup, slug="k945-sd")
    pid = project["id"]

    t = await _make_task(client, pid, "k945 task on doomed project")
    await _patch_task(client, pid, t["id"], {"process_status": 2})

    # Sanity check: row visible before delete.
    pre = await _rows_for_project(client, pid)
    assert len(pre) == 1, pre

    # Soft-delete the project.
    resp = await client.delete(f"/api/projects/{pid}")
    assert resp.status_code in (200, 204), resp.text

    # Row must now be absent from the cross-project list.
    post = await _rows_for_project(client, pid)
    assert post == [], post


@pytest.mark.asyncio
async def test_active_tasks_excludes_soft_deleted_tasks(
    client, scaffold_cleanup
) -> None:
    """Soft-deleted task (status=0) → excluded from the response."""
    project = await _make_project(client, scaffold_cleanup, slug="k945-tsd")
    pid = project["id"]

    t_kept = await _make_task(client, pid, "k945 kept in-progress")
    await _patch_task(client, pid, t_kept["id"], {"process_status": 2})
    t_doomed = await _make_task(client, pid, "k945 doomed in-progress")
    await _patch_task(client, pid, t_doomed["id"], {"process_status": 2})

    pre = await _rows_for_project(client, pid)
    assert {r["task_id"] for r in pre} == {t_kept["id"], t_doomed["id"]}

    headers = {"X-Project-Id": str(pid)}
    del_resp = await client.delete(
        f"/api/tasks/{t_doomed['id']}", headers=headers
    )
    assert del_resp.status_code == 204, del_resp.text

    post = await _rows_for_project(client, pid)
    assert {r["task_id"] for r in post} == {t_kept["id"]}


@pytest.mark.asyncio
async def test_active_tasks_sort_project_name_then_updated_desc(
    client, scaffold_cleanup
) -> None:
    """Default sort: project_name ASC, then updated_at DESC within project."""
    # Two projects with deterministic name ordering.
    a_name = scaffold_cleanup(f"k945-aaa-{uuid.uuid4().hex[:6]}")
    b_name = scaffold_cleanup(f"k945-zzz-{uuid.uuid4().hex[:6]}")
    a_resp = await client.post(
        "/api/projects", json=_project_create_payload(a_name)
    )
    assert a_resp.status_code == 201, a_resp.text
    b_resp = await client.post(
        "/api/projects", json=_project_create_payload(b_name)
    )
    assert b_resp.status_code == 201, b_resp.text
    a_id = a_resp.json()["id"]
    b_id = b_resp.json()["id"]

    # Two in-progress tasks on each project; the second-created on each is
    # more recent → must sort first within the project group.
    a_t1 = await _make_task(client, a_id, "k945 aaa task1")
    await _patch_task(client, a_id, a_t1["id"], {"process_status": 2})
    a_t2 = await _make_task(client, a_id, "k945 aaa task2")
    await _patch_task(client, a_id, a_t2["id"], {"process_status": 2})

    b_t1 = await _make_task(client, b_id, "k945 zzz task1")
    await _patch_task(client, b_id, b_t1["id"], {"process_status": 2})
    b_t2 = await _make_task(client, b_id, "k945 zzz task2")
    await _patch_task(client, b_id, b_t2["id"], {"process_status": 2})

    resp = await client.get("/api/dashboard/active-tasks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = body["rows"]

    # Filter to our two projects + assert the order.
    our_rows = [r for r in rows if r["project_id"] in (a_id, b_id)]
    # Should be 4 rows total (2 per project).
    assert len(our_rows) == 4, our_rows

    # Indices of our rows in the global list should preserve project ordering.
    a_idx = [i for i, r in enumerate(our_rows) if r["project_id"] == a_id]
    b_idx = [i for i, r in enumerate(our_rows) if r["project_id"] == b_id]
    # a_name < b_name lexically → all a rows come before all b rows.
    assert max(a_idx) < min(b_idx), (a_idx, b_idx, [r["project_name"] for r in our_rows])

    # Within each project: newer task first (updated_at DESC).
    a_rows = [r for r in our_rows if r["project_id"] == a_id]
    assert a_rows[0]["updated_at"] >= a_rows[1]["updated_at"], a_rows
    b_rows = [r for r in our_rows if r["project_id"] == b_id]
    assert b_rows[0]["updated_at"] >= b_rows[1]["updated_at"], b_rows


@pytest.mark.asyncio
async def test_active_tasks_blocked_carries_blocked_by(
    client, scaffold_cleanup
) -> None:
    """A blocked task must surface its `blocked_by` upstream id."""
    project = await _make_project(client, scaffold_cleanup, slug="k945-blocked")
    pid = project["id"]

    upstream = await _make_task(client, pid, "k945 upstream")
    blocked = await _make_task(client, pid, "k945 downstream blocked")
    # Set blocked_by then flip to BLOCKED (process_status=4).
    await _patch_task(
        client, pid, blocked["id"], {"blocked_by": upstream["id"]}
    )
    await _patch_task(client, pid, blocked["id"], {"process_status": 4})

    our_rows = await _rows_for_project(client, pid)
    blocked_row = next(r for r in our_rows if r["task_id"] == blocked["id"])
    assert blocked_row["process_status"] == 4
    assert blocked_row["blocked_by"] == upstream["id"]
    # The upstream task itself is TODO (process_status=1) → NOT in the
    # response.
    assert all(r["task_id"] != upstream["id"] for r in our_rows), our_rows
