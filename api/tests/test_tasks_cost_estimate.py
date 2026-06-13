"""Contract-smoke tests for PUT /api/tasks/{task_id}/cost-estimate — Kanban #2357.

Three cases:
  (a) Happy path: sets all 3 columns; estimated_cost_usd matches hand-calc.
      opus-4-8 rates: input=5.0/M, output=25.0/M
      input=200000, output=10000
      cost = (5.0 * 200000 / 1_000_000) + (25.0 * 10000 / 1_000_000)
           = 1.0000 + 0.2500 = 1.2500
  (b) 404 when task belongs to a different project.
  (c) Unknown model → cost stored as 0, response 200 (NOT 422).
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


async def _make_project(client, scaffold_cleanup, slug: str) -> int:
    name = _unique_name(slug)
    scaffold_cleanup(name)
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": title},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# (a) Happy path — sets 3 columns, cost matches hand-calc for opus-4-8
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_estimate_sets_columns_and_cost(client, scaffold_cleanup) -> None:
    """PUT sets estimated_input_tokens, estimated_output_tokens, estimated_cost_usd.

    opus-4-8 rates (from PRICING): input=5.0/M, output=25.0/M.
    With input=200000, output=10000:
      cost = (5.0 * 200000 / 1_000_000) + (25.0 * 10000 / 1_000_000)
           = 1.0000 + 0.2500 = 1.2500
    """
    project_id = await _make_project(client, scaffold_cleanup, "cost-est-happy")
    headers = {"X-Project-Id": str(project_id)}
    task = await _make_task(client, project_id, "cost-estimate-test-task")
    task_id = task["id"]

    try:
        # POSITIVE LOCK: columns start at NULL (not yet set).
        assert task["estimated_input_tokens"] is None
        assert task["estimated_output_tokens"] is None
        assert task["estimated_cost_usd"] is None

        resp = await client.put(
            f"/api/tasks/{task_id}/cost-estimate",
            json={
                "estimated_input_tokens": 200000,
                "estimated_output_tokens": 10000,
                "provider": "anthropic",
                "model": "claude-opus-4-8",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["estimated_input_tokens"] == 200000
        assert data["estimated_output_tokens"] == 10000
        # Decimal stored as string in JSON; compare as float with tolerance OR
        # convert to float. The column is numeric(10,4) so "1.2500" is exact.
        assert float(data["estimated_cost_usd"]) == pytest.approx(1.25, abs=1e-4)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# (b) 404 when task belongs to a different project
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_estimate_404_wrong_project(client, scaffold_cleanup) -> None:
    """PUT with an X-Project-Id that does not own the task yields 400 (project mismatch)."""
    project_a_id = await _make_project(client, scaffold_cleanup, "cost-est-proj-a")
    project_b_id = await _make_project(client, scaffold_cleanup, "cost-est-proj-b")

    try:
        task = await _make_task(client, project_a_id, "task-in-proj-a")
        task_id = task["id"]

        # Use project B's header to access a task that belongs to project A.
        resp = await client.put(
            f"/api/tasks/{task_id}/cost-estimate",
            json={
                "estimated_input_tokens": 100,
                "estimated_output_tokens": 50,
            },
            headers={"X-Project-Id": str(project_b_id)},
        )
        # assert_task_belongs_to_session raises 400 on mismatch.
        assert resp.status_code == 400, resp.text
    finally:
        await client.delete(f"/api/projects/{project_a_id}")
        await client.delete(f"/api/projects/{project_b_id}")


# ---------------------------------------------------------------------------
# (c) Unknown model → cost stored as 0, response 200 (not 422)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_estimate_unknown_model_stores_zero(client, scaffold_cleanup) -> None:
    """Unknown provider/model does NOT raise 422; cost is stored as 0, tokens preserved."""
    project_id = await _make_project(client, scaffold_cleanup, "cost-est-unknown")
    headers = {"X-Project-Id": str(project_id)}
    task = await _make_task(client, project_id, "unknown-model-task")
    task_id = task["id"]

    try:
        resp = await client.put(
            f"/api/tasks/{task_id}/cost-estimate",
            json={
                "estimated_input_tokens": 5000,
                "estimated_output_tokens": 1000,
                "provider": "totally-unknown-provider",
                "model": "no-such-model-v999",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Token counts written.
        assert data["estimated_input_tokens"] == 5000
        assert data["estimated_output_tokens"] == 1000
        # Cost zeroed — NEGATIVE LOCK: must be 0, not some non-zero heuristic.
        assert float(data["estimated_cost_usd"]) == 0.0
    finally:
        await client.delete(f"/api/projects/{project_id}")
