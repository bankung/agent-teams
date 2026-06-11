"""Tests for the Lead activity-rail dual-contract on tool_calls (Kanban #2320).

Mode A: the Lead appends report-back checkpoints onto the existing #980
`tool_calls` rail via the SAME `POST /api/tasks/{task_id}/tool-calls` URL,
dispatched by the `source` discriminator. This file covers ONLY the lead path
+ the engine-path regression guard; the full engine contract lives in
`test_tool_calls.py`.

Coverage:
  * lead 201 + GET roundtrip (source/kind/summary correct, engine-only NULL)
  * invalid kind 422
  * missing summary 422
  * summary > 2000 422
  * engine-shape POST still 201 (regression #981 — byte-compatible)
  * GET ordering intact with mixed engine+lead rows
  * 410 on soft-deleted parent (lead POST)
  * 400 cross-project header (lead POST)
"""

from __future__ import annotations

import uuid

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


def _lead_body(
    *,
    kind: str = "spawn",
    summary: str = "dev-sr-backend: build the #2320 dual-contract",
    success: bool | None = None,
    tool_name: str | None = None,
) -> dict:
    body: dict = {"source": "lead", "kind": kind, "summary": summary}
    if success is not None:
        body["success"] = success
    if tool_name is not None:
        body["tool_name"] = tool_name
    return body


def _engine_body() -> dict:
    """A valid engine-path (ToolCallCreate) body — no `source` key."""
    return {
        "tool_name": "git_status",
        "tier": "read",
        "input_args": {"path": "/repo"},
        "result": {
            "success": True,
            "error_code": None,
            "error_msg": None,
            "output": "clean",
            "duration_ms": 9,
        },
        "permission_decision": "auto_allow",
    }


async def _new_task(client, title: str) -> tuple[int, int, dict]:
    """Create a task on the seeded agent-teams project; return (project_id,
    task_id, headers)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": title},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    return project_id, create.json()["id"], headers


@pytest.mark.asyncio
async def test_lead_activity_201_and_get_roundtrip(client) -> None:
    """Lead POST → 201; GET surfaces it with source/kind/summary set and the
    engine-only columns NULL."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-lead-201"
    )
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(
                kind="ac_verified",
                summary="AC[2] verified via curl 200",
                tool_name="dev-tester",
            ),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["source"] == "lead"
        assert body["kind"] == "ac_verified"
        assert body["summary"] == "AC[2] verified via curl 200"
        assert body["success"] is True  # default
        assert body["tool_name"] == "dev-tester"
        assert body["task_id"] == task_id
        assert body["id"] > 0
        # Engine-only columns NULL on a lead row.
        assert body["tier"] is None
        assert body["input_json"] is None
        assert body["duration_ms"] is None
        assert body["permission_decision"] is None

        # GET roundtrip.
        get_resp = await client.get(
            f"/api/tasks/{task_id}/tool-calls", headers=headers
        )
        assert get_resp.status_code == 200
        rows = get_resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == body["id"]
        assert row["source"] == "lead"
        assert row["kind"] == "ac_verified"
        assert row["summary"] == "AC[2] verified via curl 200"
        assert row["tier"] is None
        assert row["input_json"] is None
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_lead_activity_422_on_invalid_kind(client) -> None:
    """kind not in the Literal enum → 422 with a `kind` loc."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-bad-kind"
    )
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(kind="not_a_real_kind"),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
        assert any("kind" in loc for loc in locs), resp.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_lead_activity_422_on_missing_summary(client) -> None:
    """summary omitted → 422 with a `summary` loc."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-no-summary"
    )
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json={"source": "lead", "kind": "note"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
        assert any("summary" in loc for loc in locs), resp.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_lead_activity_422_on_empty_summary(client) -> None:
    """summary = "" violates min_length=1 → 422."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-empty-summary"
    )
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(summary=""),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_lead_activity_422_on_summary_over_2000(client) -> None:
    """summary > 2000 chars violates max_length → 422 (validation rejects before
    the writer's defensive cap)."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-long-summary"
    )
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(summary="x" * 2001),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_engine_post_still_201_regression(client) -> None:
    """The #981 engine path (no `source`) is byte-compatible — still 201 with
    all engine columns filled and lead columns NULL."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-engine-regression"
    )
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_engine_body(),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # source defaults to 'engine' for the engine path.
        assert body["source"] == "engine"
        assert body["kind"] is None
        assert body["summary"] is None
        # Engine columns filled.
        assert body["tool_name"] == "git_status"
        assert body["tier"] == "read"
        assert body["input_json"] == {"path": "/repo"}
        assert body["duration_ms"] == 9
        assert body["permission_decision"] == "auto_allow"
        assert body["output_summary"] == "clean"
        assert body["success"] is True
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_get_ordering_intact_with_mixed_rows(client) -> None:
    """Engine + lead rows on the same task come back invoked_at DESC."""
    import asyncio

    _project_id, task_id, headers = await _new_task(
        client, "k2320-mixed-order"
    )
    try:
        # 1st: engine row.
        r1 = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_engine_body(),
            headers=headers,
        )
        assert r1.status_code == 201, r1.text
        await asyncio.sleep(0.01)
        # 2nd: lead row.
        r2 = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(kind="status_change", summary="TODO -> IN PROGRESS"),
            headers=headers,
        )
        assert r2.status_code == 201, r2.text
        await asyncio.sleep(0.01)
        # 3rd: lead row.
        r3 = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(kind="commit", summary="abc123 build dual-contract"),
            headers=headers,
        )
        assert r3.status_code == 201, r3.text

        get_resp = await client.get(
            f"/api/tasks/{task_id}/tool-calls", headers=headers
        )
        assert get_resp.status_code == 200
        rows = get_resp.json()
        assert len(rows) == 3
        # Most-recent first: commit (lead), status_change (lead), engine.
        assert rows[0]["id"] == r3.json()["id"]
        assert rows[0]["source"] == "lead"
        assert rows[0]["kind"] == "commit"
        assert rows[1]["id"] == r2.json()["id"]
        assert rows[1]["source"] == "lead"
        assert rows[2]["id"] == r1.json()["id"]
        assert rows[2]["source"] == "engine"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_lead_activity_410_on_soft_deleted_task(client) -> None:
    """Soft-deleted parent → 410 on the lead POST (rail gone with the parent)."""
    _project_id, task_id, headers = await _new_task(
        client, "k2320-lead-410"
    )
    delete = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert delete.status_code == 204

    resp = await client.post(
        f"/api/tasks/{task_id}/tool-calls",
        json=_lead_body(),
        headers=headers,
    )
    assert resp.status_code == 410, resp.text
    assert resp.json()["detail"].startswith(f"Task id={task_id} is deleted")


@pytest.mark.asyncio
async def test_lead_activity_400_on_cross_project_header(
    client, scaffold_cleanup
) -> None:
    """Task in project A; header claims B → 400 (session-project gate)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_a_id = active.json()["id"]
    headers_a = {"X-Project-Id": str(project_a_id)}

    name_b = scaffold_cleanup(_unique_name("k2320-crossproj"))
    proj_b = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    project_b_id = proj_b.json()["id"]

    create = await client.post(
        "/api/tasks",
        json={"project_id": project_a_id, "title": "k2320-crossproj-task"},
        headers=headers_a,
    )
    task_id = create.json()["id"]

    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_lead_body(),
            headers={"X-Project-Id": str(project_b_id)},
        )
        assert resp.status_code == 400, resp.text
        assert "does not belong to" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_a)
