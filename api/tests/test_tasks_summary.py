"""Contract-smoke tests for GET /api/tasks/summary — Kanban #2345.

Slim task projection for list/ordering consumers. The endpoint mirrors a
subset of list_tasks' filter semantics but returns TaskSummaryRead, which
OMITS the heavy description / acceptance_criteria / subagent_models / JSONB
fields.

Covers:
  (a) 200 + list; each row HAS the slim keys and does NOT carry the heavy
      fields (description / acceptance_criteria / subagent_models).
  (b) filter parity vs list_tasks — milestone_id, process_status, pending,
      include_cancelled behave the same on both endpoints.
  (c) project scoping — missing X-Project-Id header → 400 (same as list).
  (d) limit / offset work.
"""

from __future__ import annotations

import uuid

import pytest

# The exact slim field set TaskSummaryRead must expose (Kanban #2345 design lock).
# BE-m1 (round-1 review): is_active added to match TaskRead parity.
_SLIM_KEYS = {
    "id",
    "project_id",
    "parent_task_id",
    "title",
    "process_status",
    "priority",
    "assigned_role",
    "task_type",
    "task_kind",
    "milestone_id",
    "blocked_by",
    "sort_order",
    "due_date",
    "operator_gate",
    "is_pending",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    # Kanban #1839: halted_at added to TaskSummaryRead — stamped on →ps=8 transition.
    "halted_at",
    "is_active",
}

# Heavy fields that MUST be absent from the slim projection.
_HEAVY_KEYS = {"description", "acceptance_criteria", "subagent_models"}


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


async def _make_project(client, scaffold_cleanup, slug: str) -> int:
    name = _unique_name(slug)
    scaffold_cleanup(name)
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(client, headers: dict, project_id: int, title: str, **extras) -> dict:
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(client, headers: dict, task_id: int, **fields) -> dict:
    resp = await client.patch(f"/api/tasks/{task_id}", json=fields, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# (a) 200 + slim shape: keys present, heavy fields absent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_returns_slim_shape(client, scaffold_cleanup) -> None:
    """Every row has exactly the slim keys and none of the heavy fields."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-shape")
    headers = {"X-Project-Id": str(project_id)}
    try:
        # Create a task WITH the heavy fields populated so their absence in the
        # summary projection is a meaningful (non-vacuous) negative assertion.
        created = await _make_task(
            client,
            headers,
            project_id,
            "shape-task",
            description="a long description that would bloat the payload",
            acceptance_criteria=[{"text": "AC one"}, {"text": "AC two"}],
        )
        # POSITIVE LOCK: the heavy fields ARE present on the full TaskRead — so
        # the summary's omission below is a real projection, not a missing-data
        # artifact.
        assert created["description"] == (
            "a long description that would bloat the payload"
        )
        assert len(created["acceptance_criteria"]) == 2
        assert isinstance(created["subagent_models"], list)

        resp = await client.get("/api/tasks/summary?limit=500", headers=headers)
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert isinstance(rows, list)
        assert len(rows) >= 1

        row = next(r for r in rows if r["id"] == created["id"])
        # Slim keys all present.
        assert set(row.keys()) == _SLIM_KEYS, (
            f"summary row keys drifted from the slim contract; "
            f"missing={_SLIM_KEYS - set(row.keys())} extra={set(row.keys()) - _SLIM_KEYS}"
        )
        # NEGATIVE LOCK: heavy fields absent on every row.
        for r in rows:
            for heavy in _HEAVY_KEYS:
                assert heavy not in r, f"heavy field {heavy!r} leaked into summary row"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# (b) filter parity — milestone_id / process_status / pending / include_cancelled
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_milestone_filter_parity(client, scaffold_cleanup) -> None:
    """?milestone_id=N returns the same id set as list_tasks."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-ms")
    headers = {"X-Project-Id": str(project_id)}
    try:
        ms = await client.post(
            "/api/milestones",
            headers=headers,
            json={
                "project_id": project_id,
                "title": _unique_name("ms"),
                "description": "fixture",
                "milestone_status": "planned",
            },
        )
        assert ms.status_code == 201, ms.text
        ms_id = ms.json()["id"]

        in_ms = await _make_task(
            client, headers, project_id, "in-milestone", milestone_id=ms_id
        )
        await _make_task(client, headers, project_id, "no-milestone")

        full = await client.get(
            f"/api/tasks?milestone_id={ms_id}&limit=500", headers=headers
        )
        slim = await client.get(
            f"/api/tasks/summary?milestone_id={ms_id}&limit=500", headers=headers
        )
        assert full.status_code == 200 and slim.status_code == 200
        full_ids = {t["id"] for t in full.json()}
        slim_ids = {t["id"] for t in slim.json()}
        assert slim_ids == full_ids
        assert slim_ids == {in_ms["id"]}
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_summary_process_status_and_pending_parity(
    client, scaffold_cleanup
) -> None:
    """?process_status=5 and ?pending=true return the same id sets as list_tasks."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-ps")
    headers = {"X-Project-Id": str(project_id)}
    try:
        todo = await _make_task(client, headers, project_id, "todo-task")
        done = await _make_task(client, headers, project_id, "done-task")
        await _patch_task(client, headers, done["id"], process_status=5)

        # process_status=5 parity
        full_done = await client.get(
            "/api/tasks?process_status=5&limit=500", headers=headers
        )
        slim_done = await client.get(
            "/api/tasks/summary?process_status=5&limit=500", headers=headers
        )
        assert full_done.status_code == 200 and slim_done.status_code == 200
        assert {t["id"] for t in slim_done.json()} == {t["id"] for t in full_done.json()}
        assert {t["id"] for t in slim_done.json()} == {done["id"]}

        # pending=true parity (process_status != 5)
        full_pending = await client.get(
            "/api/tasks?pending=true&limit=500", headers=headers
        )
        slim_pending = await client.get(
            "/api/tasks/summary?pending=true&limit=500", headers=headers
        )
        assert full_pending.status_code == 200 and slim_pending.status_code == 200
        slim_pending_ids = {t["id"] for t in slim_pending.json()}
        assert slim_pending_ids == {t["id"] for t in full_pending.json()}
        assert todo["id"] in slim_pending_ids
        assert done["id"] not in slim_pending_ids
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_summary_include_cancelled_parity(client, scaffold_cleanup) -> None:
    """Cancelled (ps=6) excluded by default; ?include_cancelled mirrors list_tasks."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-cancel")
    headers = {"X-Project-Id": str(project_id)}
    try:
        live = await _make_task(client, headers, project_id, "live-task")
        cancelled = await _make_task(client, headers, project_id, "cancelled-task")
        await _patch_task(client, headers, cancelled["id"], process_status=6)

        # Default: cancelled excluded (parity).
        slim_default = await client.get("/api/tasks/summary?limit=500", headers=headers)
        full_default = await client.get("/api/tasks?limit=500", headers=headers)
        assert slim_default.status_code == 200 and full_default.status_code == 200
        slim_default_ids = {t["id"] for t in slim_default.json()}
        assert slim_default_ids == {t["id"] for t in full_default.json()}
        assert live["id"] in slim_default_ids
        assert cancelled["id"] not in slim_default_ids

        # include_cancelled=true: cancelled appears (parity).
        slim_inc = await client.get(
            "/api/tasks/summary?include_cancelled=true&limit=500", headers=headers
        )
        full_inc = await client.get(
            "/api/tasks?include_cancelled=true&limit=500", headers=headers
        )
        assert slim_inc.status_code == 200 and full_inc.status_code == 200
        slim_inc_ids = {t["id"] for t in slim_inc.json()}
        assert slim_inc_ids == {t["id"] for t in full_inc.json()}
        assert cancelled["id"] in slim_inc_ids
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# (c) project scoping — missing header → 400 (same as list_tasks)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_missing_project_header_400(client) -> None:
    """No X-Project-Id header → 400 (wire contract parity with list_tasks)."""
    resp = await client.get("/api/tasks/summary")
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# (d) limit / offset
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_limit_and_offset(client, scaffold_cleanup) -> None:
    """limit caps the page; offset skips id-ASC rows (same ordering as list)."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-page")
    headers = {"X-Project-Id": str(project_id)}
    try:
        created = [
            await _make_task(client, headers, project_id, f"page-task-{i}")
            for i in range(3)
        ]
        ids_asc = sorted(t["id"] for t in created)

        # limit=2 → first two by id ASC.
        page1 = await client.get("/api/tasks/summary?limit=2", headers=headers)
        assert page1.status_code == 200, page1.text
        page1_ids = [t["id"] for t in page1.json()]
        assert page1_ids == ids_asc[:2]

        # offset=2 → skip the first two.
        page2 = await client.get(
            "/api/tasks/summary?limit=2&offset=2", headers=headers
        )
        assert page2.status_code == 200, page2.text
        page2_ids = [t["id"] for t in page2.json()]
        assert page2_ids == ids_asc[2:]

        # Out-of-range guards (parity with list_tasks Query bounds).
        assert (
            await client.get("/api/tasks/summary?limit=0", headers=headers)
        ).status_code == 422
        assert (
            await client.get("/api/tasks/summary?limit=501", headers=headers)
        ).status_code == 422
        assert (
            await client.get("/api/tasks/summary?offset=-1", headers=headers)
        ).status_code == 422
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# Round-1 review fixes (Kanban #2345 BE-m1 + BE-n1/SEC-NIT-1)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summary_row_has_is_active(client, scaffold_cleanup) -> None:
    """BE-m1: every summary row exposes `is_active` (parity with TaskRead)."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-isactive")
    headers = {"X-Project-Id": str(project_id)}
    try:
        await _make_task(client, headers, project_id, "is-active-check-task")
        resp = await client.get("/api/tasks/summary?limit=500", headers=headers)
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) >= 1
        for row in rows:
            assert "is_active" in row, "is_active must be present on every summary row"
            assert isinstance(row["is_active"], bool), "is_active must be a bool"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_summary_process_status_out_of_range_422(client, scaffold_cleanup) -> None:
    """BE-n1/SEC-NIT-1: process_status=99 (out of 1..6) returns 422."""
    project_id = await _make_project(client, scaffold_cleanup, "summary-ps-bounds")
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = await client.get("/api/tasks/summary?process_status=99", headers=headers)
        assert resp.status_code == 422, (
            f"Expected 422 for out-of-range process_status=99, got {resp.status_code}: {resp.text}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")
