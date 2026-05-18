"""Kanban #7 Section A (AC#1) — `projects.config.enabled_roles` Pydantic validator.

Storage choice locked: `config["enabled_roles"]` is a JSONB subkey (NOT a new
column). No Alembic migration needed. Semantic contract:

- key absent       → "all roles allowed" (no restriction; default behavior)
- value `[]`       → "no role enabled" (explicit empty roster — distinct from absent)
- value list[int]  → allowlist of TaskRole codes (each in 1..20)

These tests pin the API-layer contract on both POST /api/projects and
PATCH /api/projects/{id}. Cleanup uses `scaffold_cleanup` + DELETE so the
live-DB row-count invariant in conftest stays happy.
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
        "description": f"k7A enabled_roles fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


# ---- 1. POST with valid enabled_roles → 201 + GET round-trip ---------------


@pytest.mark.asyncio
async def test_project_create_enabled_roles_valid(client, scaffold_cleanup) -> None:
    name = scaffold_cleanup(_unique_name("k7a-valid"))
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": [1, 2, 6]}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["config"]["enabled_roles"] == [1, 2, 6], resp.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["config"]["enabled_roles"] == [1, 2, 6], get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. POST with out-of-range role code → 422 -----------------------------


@pytest.mark.asyncio
async def test_project_create_enabled_roles_rejects_out_of_range(client) -> None:
    name = _unique_name("k7a-oor")
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": [99]}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err for err in body["detail"]
        if err["loc"][:2] == ["body", "config"] and err["type"] == "value_error"
    ]
    assert matches, f"expected value_error on config; got {body}"
    assert "out of range" in matches[0]["msg"].lower(), matches[0]["msg"]


# ---- 3. POST with non-int element → 422 ------------------------------------


@pytest.mark.asyncio
async def test_project_create_enabled_roles_rejects_non_int(client) -> None:
    name = _unique_name("k7a-str")
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": ["frontend"]}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err for err in body["detail"]
        if err["loc"][:2] == ["body", "config"] and err["type"] == "value_error"
    ]
    assert matches, f"expected value_error on config; got {body}"
    assert "int role code" in matches[0]["msg"], matches[0]["msg"]


# ---- 4. POST with empty list [] → 201 (explicit "no role enabled") ---------


@pytest.mark.asyncio
async def test_project_create_enabled_roles_empty_allowed(client, scaffold_cleanup) -> None:
    name = scaffold_cleanup(_unique_name("k7a-empty"))
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": []}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        # Empty list MUST round-trip distinct from key-absent.
        assert resp.json()["config"]["enabled_roles"] == [], resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5. POST without the key → 201 (key is optional) -----------------------


@pytest.mark.asyncio
async def test_project_create_enabled_roles_key_optional(client, scaffold_cleanup) -> None:
    name = scaffold_cleanup(_unique_name("k7a-absent"))
    payload = _project_create_payload(name)
    # config is {} — no enabled_roles key. Default behavior = "all roles allowed".

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        # Key MUST NOT appear when caller didn't send it (no auto-fill on absent).
        assert "enabled_roles" not in resp.json()["config"], resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 6. PATCH mirrors POST — valid + invalid -------------------------------


@pytest.mark.asyncio
async def test_project_update_enabled_roles_valid(client, scaffold_cleanup) -> None:
    name = scaffold_cleanup(_unique_name("k7a-patch-ok"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"config": {"enabled_roles": [2, 5]}},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["config"]["enabled_roles"] == [2, 5], patch.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["config"]["enabled_roles"] == [2, 5], get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_project_update_enabled_roles_rejects_out_of_range(
    client, scaffold_cleanup
) -> None:
    name = scaffold_cleanup(_unique_name("k7a-patch-oor"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"config": {"enabled_roles": [0, 21]}},
        )
        assert patch.status_code == 422, patch.text
        body = patch.json()
        matches = [
            err for err in body["detail"]
            if err["loc"][:2] == ["body", "config"] and err["type"] == "value_error"
        ]
        assert matches, f"expected value_error on config; got {body}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_project_update_enabled_roles_rejects_non_int(
    client, scaffold_cleanup
) -> None:
    name = scaffold_cleanup(_unique_name("k7a-patch-str"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"config": {"enabled_roles": ["backend"]}},
        )
        assert patch.status_code == 422, patch.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 7. Boundary + adversarial inputs --------------------------------------


@pytest.mark.asyncio
async def test_project_create_enabled_roles_boundary(client, scaffold_cleanup) -> None:
    """Min (1) and max (20) of the TaskRole range MUST both be accepted."""
    name = scaffold_cleanup(_unique_name("k7a-bound"))
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": [1, 20]}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["config"]["enabled_roles"] == [1, 20], resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_project_create_enabled_roles_rejects_bool(client) -> None:
    """Python `bool` is a subclass of `int` — explicit guard rejects it.

    Without the `type(item) is bool` check, `True` (== 1) and `False` (== 0)
    would silently pass through. Defends against JS truthy/falsy bleed-through.
    """
    name = _unique_name("k7a-bool")
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": [True]}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_project_create_enabled_roles_rejects_non_list(client) -> None:
    """A scalar / dict / string value (not a list) → 422."""
    name = _unique_name("k7a-scalar")
    payload = _project_create_payload(name)
    payload["config"] = {"enabled_roles": 2}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err for err in body["detail"]
        if err["loc"][:2] == ["body", "config"] and err["type"] == "value_error"
    ]
    assert matches, f"expected value_error on config; got {body}"
    assert "list" in matches[0]["msg"].lower(), matches[0]["msg"]
