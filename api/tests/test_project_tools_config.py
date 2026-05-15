"""Kanban #979 — `projects.tools_config` JSONB column wire-up.

Migration `0027_projects_tools_config` adds the column with a locked default
(Q2 Option B, design lock #949):

    {
      "tools_enabled": false,
      "auto_allow_tiers": ["read"],
      "halt_tiers": ["write", "network", "destructive"],
      "http_hosts": []
    }

This file pins the API-layer contract:

- POST /api/projects without `tools_config` → server fills the locked default
- POST /api/projects with explicit `tools_config` → server stores it verbatim
- PATCH /api/projects/{id} with a valid `tools_config` → 200 + GET reflects
- PATCH with unknown tier (`auto_allow_tiers=["readd"]`) → 422
- PATCH with tier overlap (`["read","write"]` vs `["read"]`) → 422

Cleanup uses scaffold_cleanup + DELETE /api/projects/{id} on the way out so the
live-DB row-count invariant in conftest stays happy.
"""

from __future__ import annotations

import uuid

import pytest


# ---- helpers ---------------------------------------------------------------


# The locked default JSON — must match migration 0027's
# `_DEFAULT_TOOLS_CONFIG_JSON` byte-for-byte (modulo whitespace). Tested
# implicitly by `test_project_create_default_tools_config_via_pydantic_or_db`
# below — if migration drifts from this, the assertion fails immediately.
_LOCKED_DEFAULT = {
    "tools_enabled": False,
    "auto_allow_tiers": ["read"],
    "halt_tiers": ["write", "network", "destructive"],
    "http_hosts": [],
}


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k979 tools_config fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


# ---- 1. Locked default on POST without explicit tools_config ---------------


@pytest.mark.asyncio
async def test_project_create_default_tools_config_via_pydantic_or_db(
    client, scaffold_cleanup
) -> None:
    """POST without `tools_config` → DB server_default fires → GET returns the locked default.

    Pins the migration 0027 server_default + the router's "OMIT when None"
    branch in create_project. If either drifts, this fires.
    """
    name = scaffold_cleanup(_unique_name("k979-default"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        # Response from POST already carries the column (refresh after commit).
        assert resp.json()["tools_config"] == _LOCKED_DEFAULT, resp.json()

        # Belt-and-suspenders GET round-trip.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools_config"] == _LOCKED_DEFAULT, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. Explicit tools_config on POST overrides default --------------------


@pytest.mark.asyncio
async def test_project_create_explicit_tools_config_overrides_default(
    client, scaffold_cleanup
) -> None:
    """POST with explicit `tools_config` → server stores it verbatim, not the default."""
    name = scaffold_cleanup(_unique_name("k979-explicit"))
    payload = _project_create_payload(name)
    payload["tools_config"] = {
        "tools_enabled": True,
        "auto_allow_tiers": ["read", "write"],
        "halt_tiers": ["network", "destructive"],
        "http_hosts": ["api.example.com"],
    }

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        body = resp.json()
        assert body["tools_config"]["tools_enabled"] is True, body
        assert sorted(body["tools_config"]["auto_allow_tiers"]) == ["read", "write"], body
        assert sorted(body["tools_config"]["halt_tiers"]) == ["destructive", "network"], body
        assert body["tools_config"]["http_hosts"] == ["api.example.com"], body
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 3. PATCH with valid tools_config → 200 + GET reflects -----------------


@pytest.mark.asyncio
async def test_project_update_tools_config_accepts_valid(
    client, scaffold_cleanup
) -> None:
    """PATCH with a valid `tools_config` → 200, then GET returns the new value."""
    name = scaffold_cleanup(_unique_name("k979-patch-ok"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        new_config = {
            "tools_enabled": True,
            "auto_allow_tiers": ["read"],
            "halt_tiers": ["write", "network", "destructive"],
            "http_hosts": ["docs.python.org", "github.com"],
        }
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"tools_config": new_config}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["tools_config"] == new_config, patch.json()

        # GET round-trip confirms persistence.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools_config"] == new_config, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 4. PATCH with unknown tier → 422 --------------------------------------


@pytest.mark.asyncio
async def test_project_update_tools_config_rejects_unknown_tier(
    client, scaffold_cleanup
) -> None:
    """PATCH with `auto_allow_tiers=["readd"]` (typo) → 422 literal_error.

    The `ToolTier` Literal at the Pydantic boundary catches a misspelled
    tier before the row reaches the DB.
    """
    name = scaffold_cleanup(_unique_name("k979-patch-typo"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        bad_config = {
            "tools_enabled": True,
            "auto_allow_tiers": ["readd"],  # typo
            "halt_tiers": ["write", "network", "destructive"],
            "http_hosts": [],
        }
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"tools_config": bad_config}
        )
        assert patch.status_code == 422, patch.text
        body = patch.json()
        # Pydantic places the literal_error at body.tools_config.auto_allow_tiers.0
        matches = [
            err
            for err in body["detail"]
            if err["loc"][:2] == ["body", "tools_config"]
            and "auto_allow_tiers" in err["loc"]
        ]
        assert matches, f"expected auto_allow_tiers literal_error; got {body}"
        assert matches[0]["type"] == "literal_error", (
            f"expected type='literal_error'; got {matches[0]['type']!r}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5. PATCH with tier overlap → 422 --------------------------------------


@pytest.mark.asyncio
async def test_project_update_tools_config_rejects_tier_overlap(
    client, scaffold_cleanup
) -> None:
    """PATCH with `auto_allow_tiers=["read","write"], halt_tiers=["read"]` → 422.

    The model_validator on `ToolsConfig` rejects overlap with a value_error;
    the overlapping tier ("read") appears in the error message.
    """
    name = scaffold_cleanup(_unique_name("k979-patch-overlap"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        bad_config = {
            "tools_enabled": True,
            "auto_allow_tiers": ["read", "write"],
            "halt_tiers": ["read"],  # overlap on 'read'
            "http_hosts": [],
        }
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"tools_config": bad_config}
        )
        assert patch.status_code == 422, patch.text
        body = patch.json()
        matches = [
            err
            for err in body["detail"]
            if err["loc"][:2] == ["body", "tools_config"]
            and err["type"] == "value_error"
        ]
        assert matches, f"expected disjoint-tiers value_error; got {body}"
        # The error message names the offending tier.
        assert "disjoint" in matches[0]["msg"].lower() or "overlap" in matches[0]["msg"].lower(), (
            f"expected message to mention disjoint/overlap; got {matches[0]['msg']!r}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 6. extra-forbid on tools_config keys → 422 ----------------------------


@pytest.mark.asyncio
async def test_project_update_tools_config_rejects_extra_key(
    client, scaffold_cleanup
) -> None:
    """`ToolsConfig` has `extra='forbid'` — typo'd `tool_enabled` (no `s`) → 422.

    Defends against silent persist-under-garbage-key. Mirrors `SourceEntry`
    extra-forbid precedent.
    """
    name = scaffold_cleanup(_unique_name("k979-patch-extra"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        bad_config = {
            "tools_enabled": True,
            "tool_enabled": False,  # typo'd extra key
            "auto_allow_tiers": ["read"],
            "halt_tiers": ["write", "network", "destructive"],
            "http_hosts": [],
        }
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"tools_config": bad_config}
        )
        assert patch.status_code == 422, patch.text
        body = patch.json()
        matches = [
            err
            for err in body["detail"]
            if err["loc"][:2] == ["body", "tools_config"]
            and err["type"] == "extra_forbidden"
        ]
        assert matches, f"expected extra_forbidden error; got {body}"
    finally:
        await client.delete(f"/api/projects/{project_id}")
