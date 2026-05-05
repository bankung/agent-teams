"""HTTP-level contract tests for projects + tasks routers.

These run against the live FastAPI app via httpx.AsyncClient + ASGITransport
(see tests/conftest.py for the `client` fixture). They share the dev Postgres
that the seed populated — the `agent-teams` project (Phase 2b verified) is
expected to exist and be is_active=True.

Scope:
- Verify success-path response shape on the read endpoints we lean on (Lead
  calls /api/projects/active every turn).
- Lock in the *exact* 404 detail strings that backend's recent refactor moved
  through `get_or_404` — drift in those strings would silently change the
  error UX the FE will eventually render.
- Verify Pydantic validator errors travel through to the HTTP layer (422 with
  the expected message).
- Exercise the status→timestamp lookup-dict (the refactor's other behavioral
  surface) end-to-end.

Non-destructive: the lifecycle test creates a task but cannot DELETE (no
endpoint, by design — audit trail relies on UPDATE/DELETE triggers, and there
is no CASCADE-from-test path). Created rows leak. Acceptable for now per QA
spec; revisit when DELETE /api/tasks/{id} lands.
"""

from __future__ import annotations

import pytest


# -----------------------------------------------------------------------------
# Projects — read-only assertions
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_project_returns_seeded_agent_teams(client) -> None:
    resp = await client.get("/api/projects/active")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "agent-teams"
    assert body["is_active"] is True
    # ProjectRead shape sanity — these fields back the Lead bootstrap.
    for field in ("id", "paths_web", "paths_api", "paths_db", "config"):
        assert field in body, f"missing {field} in ProjectRead body"


@pytest.mark.asyncio
async def test_get_project_by_name_existing(client) -> None:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "agent-teams"


@pytest.mark.asyncio
async def test_get_project_by_name_404_exact_detail(client) -> None:
    """The 404 detail string is part of the contract (api-contracts.md L59)."""
    resp = await client.get("/api/projects/by-name/does-not-exist-xyz")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Project 'does-not-exist-xyz' not found"}


@pytest.mark.asyncio
async def test_patch_project_404_exact_detail(client) -> None:
    """`get_or_404` on the PATCH path must surface "Project id=<n> not found"."""
    resp = await client.patch("/api/projects/9999999", json={})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Project id=9999999 not found"}


# -----------------------------------------------------------------------------
# Tasks — 404 contract on get/patch
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_404_exact_detail(client) -> None:
    resp = await client.get("/api/tasks/9999999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=9999999 not found"}


@pytest.mark.asyncio
async def test_patch_task_404_exact_detail(client) -> None:
    resp = await client.patch("/api/tasks/9999999", json={})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=9999999 not found"}


# -----------------------------------------------------------------------------
# Tasks — Pydantic validator surface (422 with stable message)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_invalid_status_returns_422_with_validator_message(
    client,
) -> None:
    """Validator error message is part of the contract — the FE will eventually
    parse `errors[].msg` to render inline form errors.
    """
    # Resolve the active project id dynamically — never hardcode `1`.
    active = await client.get("/api/projects/active")
    assert active.status_code == 200, active.text
    project_id = active.json()["id"]

    resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "smoke", "status": 99},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # FastAPI 422 envelope: {"detail": [{"loc": [...], "msg": "...", ...}, ...]}
    assert "detail" in body and isinstance(body["detail"], list)
    msgs = " | ".join(err["msg"] for err in body["detail"])
    assert "status must be one of (1, 2, 3, 4, 5), got 99" in msgs


# -----------------------------------------------------------------------------
# Tasks — status→timestamp lifecycle (the refactor's behavioral surface)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_status_transitions_stamp_lifecycle_timestamps(client) -> None:
    """Create → PATCH to in_progress → started_at filled → PATCH to done →
    completed_at filled.

    This exercises `_STATUS_TIMESTAMP_FIELDS` (the lookup dict introduced by the
    refactor). Hardcoded codes 2 (in_progress) and 5 (done) are pinned by
    standards/general.md — bumping them is a breaking schema change.

    The created row is NOT cleaned up — there's no DELETE endpoint by design.
    Acceptable for the dev DB; flagged in qa current-state.
    """
    active = await client.get("/api/projects/active")
    assert active.status_code == 200
    project_id = active.json()["id"]

    # 1. Create
    create_resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-smoke-lifecycle (test row, safe to delete)",
            "description": "Created by tests/test_routes_smoke.py — verifies status→timestamp transitions.",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    task = create_resp.json()
    task_id = task["id"]
    assert task["status"] == 1  # default TODO
    assert task["started_at"] is None
    assert task["completed_at"] is None

    # 2. → in_progress should stamp started_at
    in_progress = await client.patch(
        f"/api/tasks/{task_id}", json={"status": 2}
    )
    assert in_progress.status_code == 200, in_progress.text
    body = in_progress.json()
    assert body["status"] == 2
    assert body["started_at"] is not None, "in_progress transition must stamp started_at"
    assert body["completed_at"] is None
    started_at_snapshot = body["started_at"]

    # 3. → done should stamp completed_at and leave started_at intact
    done = await client.patch(f"/api/tasks/{task_id}", json={"status": 5})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == 5
    assert body["started_at"] == started_at_snapshot, (
        "completing a task must not overwrite started_at"
    )
    assert body["completed_at"] is not None, "done transition must stamp completed_at"
