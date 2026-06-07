"""Kanban #1244 — description_annotation support in adjust_continue (Path A).

Contract-smoke tests (first-pass, BE only):
1. Annotation appends to project.description with the correct marker format.
2. Empty / whitespace-only annotation is a no-op (description unchanged).
3. Multiple adjust_continue calls accumulate (append, don't overwrite).
4. annotation > 1000 chars → 422 at the Pydantic boundary.
5. description_annotation is NOT leaked as a bogus project column
   (GET /api/projects/{id} must not expose a 'description_annotation' key).

All tests run against `agent_teams_test` via conftest.py — NOT the live DB.
"""

from __future__ import annotations

import re
import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "k1244 fixture",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1244"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_audit_task_with_pause(client, project_id: int) -> dict:
    """Create an audit flag task via the GOV3 pipeline (pause recommendation)
    and return the resulting flag task dict.

    Mirrors the _seed_flag helper in test_gov3_pause_flag.py.
    """
    # Create an audit task with is_audit_flag=true + recommendation=pause.
    audit_resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={
            "project_id": project_id,
            "title": "k1244 audit task",
            "description": "k1244 audit fixture",
            "process_status": 1,
            "task_type": "feature",
            "interaction_kind": "question",
            "question_payload": {
                "is_audit_flag": True,
                "options": ["continue", "adjust_continue", "keep_paused", "terminate"],
                "recommendation": "pause",
                "question": "Audit flag for k1244",
                "breach_streak_days": 1,
            },
        },
    )
    assert audit_resp.status_code == 201, audit_resp.text
    audit_task = audit_resp.json()

    # Pause the project first (resolve-flag expects is_paused=true).
    pause_resp = await client.post(
        f"/api/projects/{project_id}/pause",
        json={"reason": "k1244 test pause — annotation smoke test"},
    )
    assert pause_resp.status_code == 200, pause_resp.text

    return audit_task


# ---------------------------------------------------------------------------
# 1. Annotation appends with correct format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_annotation_appends_to_description(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    original_description = project["description"]
    try:
        flag = await _create_audit_task_with_pause(client, project_id)

        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {
                    "description_annotation": "Bumped daily budget for sprint",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "adjust_continue"
        assert body["is_paused"] is False

        # Verify description was updated on the project.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        description = get_resp.json()["description"]

        # Original text must still be present (prepend preserved).
        assert original_description in description

        # Marker must match the expected format:
        # \n\n-- YYYY-MM-DD operator adjustment: <text>
        pattern = r"\n\n-- \d{4}-\d{2}-\d{2} operator adjustment: Bumped daily budget for sprint"
        assert re.search(pattern, description), (
            f"Expected marker not found in description: {description!r}"
        )

        # Confirm annotation appears in adjustments_applied.
        applied = body.get("adjustments_applied") or {}
        assert applied.get("description_annotation") == "Bumped daily budget for sprint"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 2. Empty / whitespace-only annotation is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_annotation_is_noop(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    original_description = project["description"]
    try:
        flag = await _create_audit_task_with_pause(client, project_id)

        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {
                    "description_annotation": "   ",  # whitespace only
                    "audit_enabled": True,  # a real key so filtered is non-empty
                },
            },
        )
        assert resp.status_code == 200, resp.text

        get_resp = await client.get(f"/api/projects/{project_id}")
        description = get_resp.json()["description"]

        # No marker should have been appended — description must equal original.
        assert description == original_description, (
            f"Expected description unchanged, got: {description!r}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 3. Multiple calls accumulate (append, not overwrite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_annotations_accumulate(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        # First flag + annotation.
        flag1 = await _create_audit_task_with_pause(client, project_id)
        resp1 = await client.post(
            f"/api/tasks/{flag1['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {"description_annotation": "First annotation"},
            },
        )
        assert resp1.status_code == 200, resp1.text

        # Re-pause + second flag + second annotation.
        await client.post(
            f"/api/projects/{project_id}/pause",
            json={"reason": "k1244 second pause for accumulation test"},
        )
        # Create a second flag task directly (project already paused).
        flag2_resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json={
                "project_id": project_id,
                "title": "k1244 second flag",
                "description": "k1244 second audit fixture",
                "process_status": 1,
                "task_type": "feature",
                "interaction_kind": "question",
                "question_payload": {
                    "is_audit_flag": True,
                    "options": ["continue", "adjust_continue", "keep_paused", "terminate"],
                    "recommendation": "pause",
                    "question": "Second flag k1244",
                    "breach_streak_days": 2,
                },
                "allow_during_pause": True,
                "allow_during_pause_reason": "k1244 seeding second flag",
            },
        )
        assert flag2_resp.status_code == 201, flag2_resp.text
        flag2 = flag2_resp.json()

        resp2 = await client.post(
            f"/api/tasks/{flag2['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {"description_annotation": "Second annotation"},
            },
        )
        assert resp2.status_code == 200, resp2.text

        get_resp = await client.get(f"/api/projects/{project_id}")
        description = get_resp.json()["description"]

        # Both annotations must be present — second did not overwrite first.
        assert "First annotation" in description, description
        assert "Second annotation" in description, description
        # First annotation must appear before second (append order).
        assert description.index("First annotation") < description.index(
            "Second annotation"
        ), "Expected first annotation to precede second"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 4. annotation > 1000 chars → 422 at Pydantic boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_annotation_over_1000_chars_returns_422(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _create_audit_task_with_pause(client, project_id)

        too_long = "x" * 1001
        resp = await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {"description_annotation": too_long},
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()
        # Pydantic 422 — error mentions the field.
        assert "description_annotation" in str(detail).lower() or "1000" in str(detail)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 5. description_annotation NOT leaked as a bogus column on GET response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_annotation_not_leaked_as_column(client, scaffold_cleanup) -> None:
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        flag = await _create_audit_task_with_pause(client, project_id)

        await client.post(
            f"/api/tasks/{flag['id']}/resolve-flag",
            headers={"X-Project-Id": str(project_id)},
            json={
                "action": "adjust_continue",
                "adjustments": {
                    "description_annotation": "Should not appear as column",
                },
            },
        )

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        project_body = get_resp.json()

        # The annotation must NOT appear as a top-level key on the project.
        assert "description_annotation" not in project_body, (
            f"description_annotation leaked as project column: {list(project_body.keys())}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")
