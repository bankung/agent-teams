"""Kanban #887 — tasks.subagent_models JSONB column.

Append-only audit log of subagent spawns per task. Each element:
    {"agent": str, "model": "opus"|"sonnet"|"haiku", "at": ISO-8601 datetime}

PATCH semantics: full-replace (Lead sends the full accumulated list).
Column is NOT NULL DEFAULT '[]' — always a list on the wire, never null.

Covers:
1. POST with subagent_models=[]         → 201, response has subagent_models: []
2. POST omitting subagent_models        → 201, response has subagent_models: []
3. POST with bad model value            → 422
4. PATCH with valid list                → 200, round-trip persisted
5. PATCH with bad model string          → 422
6. PATCH with missing 'at' field        → 422
7. PATCH with extra unknown key         → 422 (extra='forbid')
8. PATCH key absent                     → subagent_models left unchanged
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 1. POST with explicit empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_empty_subagent_models_returns_empty_list(
    client, scaffold_cleanup
) -> None:
    """POST task with subagent_models=[] → 201 + response body has subagent_models: []."""
    name = _unique_name("sm-empty")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "sm-empty — explicit [] on POST",
                "subagent_models": [],
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "subagent_models" in body, "TaskRead must expose subagent_models"
        assert body["subagent_models"] == [], (
            f"expected [] got {body['subagent_models']!r}"
        )
        # GET round-trip confirms DB persisted the empty list.
        got = await client.get(f"/api/tasks/{body['id']}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["subagent_models"] == []
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 2. POST omitting subagent_models → defaults to []
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_without_subagent_models_defaults_to_empty_list(
    client, scaffold_cleanup
) -> None:
    """POST omitting subagent_models → 201 + subagent_models defaults to []."""
    name = _unique_name("sm-default")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "sm-default — omit field"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "subagent_models" in body, "TaskRead must expose subagent_models"
        assert body["subagent_models"] == [], (
            f"expected [] (from NOT NULL DEFAULT '[]'), got {body['subagent_models']!r}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 3. POST with bad model value → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_bad_model_value_returns_422(
    client, scaffold_cleanup
) -> None:
    """POST subagent_models=[{..., model:'gpt-4'}] → 422 (Literal validator)."""
    name = _unique_name("sm-bad-model-post")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "sm-bad-model-post",
                "subagent_models": [
                    {
                        "agent": "dev-backend",
                        "model": "gpt-4",
                        "at": "2026-05-13T09:00:00Z",
                    }
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        # Confirm the validation error names the field path.
        detail = resp.json()["detail"]
        assert any(
            "subagent_models" in str(err.get("loc", "")) for err in detail
        ), f"expected 'subagent_models' in loc; got {detail}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 4. PATCH with valid list → round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_with_valid_subagent_models_roundtrip(
    client, scaffold_cleanup
) -> None:
    """PATCH subagent_models=[...valid entry...] → 200 + GET reflects persisted value."""
    name = _unique_name("sm-patch-valid")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "sm-patch-valid target"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]
        assert create.json()["subagent_models"] == []

        entries = [
            {"agent": "dev-backend", "model": "sonnet", "at": "2026-05-13T09:00:00Z"},
            {"agent": "dev-tester", "model": "haiku", "at": "2026-05-13T10:30:00Z"},
        ]
        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"subagent_models": entries},
            headers=headers,
        )
        assert patch.status_code == 200, patch.text
        patched_body = patch.json()
        assert len(patched_body["subagent_models"]) == 2
        assert patched_body["subagent_models"][0]["agent"] == "dev-backend"
        assert patched_body["subagent_models"][0]["model"] == "sonnet"
        assert patched_body["subagent_models"][1]["agent"] == "dev-tester"
        assert patched_body["subagent_models"][1]["model"] == "haiku"
        assert patched_body["subagent_models"][0]["at"] == "2026-05-13T09:00:00Z"
        assert patched_body["subagent_models"][1]["at"] == "2026-05-13T10:30:00Z"

        # GET to confirm DB persistence.
        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert got.status_code == 200, got.text
        stored = got.json()["subagent_models"]
        assert len(stored) == 2
        assert stored[0]["agent"] == "dev-backend"
        assert stored[1]["agent"] == "dev-tester"
        assert stored[0]["at"] == "2026-05-13T09:00:00Z"
        assert stored[1]["at"] == "2026-05-13T10:30:00Z"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 5. PATCH with bad model string → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_with_bad_model_string_returns_422(
    client, scaffold_cleanup
) -> None:
    """PATCH subagent_models=[{..., model:'gpt-4'}] → 422."""
    name = _unique_name("sm-bad-model-patch")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "sm-bad-model-patch target"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={
                "subagent_models": [
                    {
                        "agent": "dev-backend",
                        "model": "gpt-4",
                        "at": "2026-05-13T09:00:00Z",
                    }
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert any(
            "subagent_models" in str(err.get("loc", "")) for err in detail
        ), f"expected 'subagent_models' in loc; got {detail}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 6. PATCH with missing 'at' field → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_subagent_models_missing_at_returns_422(
    client, scaffold_cleanup
) -> None:
    """PATCH subagent_models entry without 'at' → 422 (required field)."""
    name = _unique_name("sm-missing-at")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "sm-missing-at target"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={
                "subagent_models": [
                    # 'at' field deliberately omitted.
                    {"agent": "dev-backend", "model": "sonnet"}
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert any(
            "subagent_models" in str(err.get("loc", "")) for err in detail
        ), f"expected 'subagent_models' in loc; got {detail}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 7. PATCH with extra unknown key → 422 (extra='forbid')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_subagent_models_extra_key_returns_422(
    client, scaffold_cleanup
) -> None:
    """PATCH subagent_models entry with unknown key → 422 (extra='forbid')."""
    name = _unique_name("sm-extra-key")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "sm-extra-key target"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={
                "subagent_models": [
                    {
                        "agent": "dev-backend",
                        "model": "opus",
                        "at": "2026-05-13T09:00:00Z",
                        "unknown_field": "should_reject",
                    }
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 8. PATCH key absent → subagent_models left unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_omit_subagent_models_leaves_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH without subagent_models key → existing value preserved (exclude_unset)."""
    name = _unique_name("sm-no-touch")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    initial_entries = [
        {"agent": "dev-backend", "model": "opus", "at": "2026-05-13T09:00:00Z"}
    ]

    try:
        create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "sm-no-touch target",
                "subagent_models": initial_entries,
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]
        assert len(create.json()["subagent_models"]) == 1

        # PATCH with only title — no subagent_models key.
        patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "sm-no-touch retitled"},
            headers=headers,
        )
        assert patch.status_code == 200, patch.text
        assert len(patch.json()["subagent_models"]) == 1, (
            "subagent_models must be unchanged when key is absent from PATCH body"
        )
        assert patch.json()["subagent_models"][0]["agent"] == "dev-backend"

        got = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert len(got.json()["subagent_models"]) == 1
        assert got.json()["subagent_models"][0]["model"] == "opus"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 9. PATCH {"subagent_models": null} → 422 (reject-explicit-null validator)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_explicit_null_subagent_models_returns_422(
    client, scaffold_cleanup
) -> None:
    """PATCH {subagent_models: null} → 422 (NOT NULL column, reject-explicit-null guard)."""
    name = _unique_name("sm-null-patch")
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = proj.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        create = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "sm-null-patch target"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"subagent_models": None},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail_str = str(resp.json()["detail"])
        assert "subagent_models" in detail_str, (
            f"expected 'subagent_models' in error detail; got {detail_str}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")
