"""Contract-smoke tests for GET /api/tools/directory (Kanban #1854).

Three first-pass tests:
  1. Happy path — no X-Agent-Role, no capability query: all registered tools
     returned, no suggestion.
  2. Restricted role — only granted tools appear; unlisted tools absent.
  3. AC2 — ?capability= that matches nothing returns a suggestion block;
     ?capability= that matches something has suggestion=null.

All tests use the seeded project (id=1) and PATCH config.tool_grants via the
public API, restoring it in a fixture (mirrors test_tool_grants_endpoint.py).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.services.tool_registry import TOOL_REGISTRY

_SEED_PROJ = 1
_HDR = {"X-Project-Id": str(_SEED_PROJ)}
_BASE = "/api/tools/directory"


@pytest_asyncio.fixture
async def restore_config(client):
    """Snapshot and restore the seeded project's config around each test."""
    resp = await client.get(f"/api/projects/{_SEED_PROJ}", headers=_HDR)
    assert resp.status_code == 200, resp.text
    original_config = resp.json()["config"]

    yield

    restore = await client.patch(
        f"/api/projects/{_SEED_PROJ}",
        headers=_HDR,
        json={"config": original_config},
    )
    assert restore.status_code == 200, restore.text


async def _set_tool_grants(client, grants: dict) -> None:
    resp = await client.get(f"/api/projects/{_SEED_PROJ}", headers=_HDR)
    assert resp.status_code == 200
    config = dict(resp.json()["config"])
    config["tool_grants"] = grants
    patch = await client.patch(
        f"/api/projects/{_SEED_PROJ}",
        headers=_HDR,
        json={"config": config},
    )
    assert patch.status_code == 200, patch.text


# ===========================================================================
# Test 1: Happy path — no role header, all tools returned
# ===========================================================================


@pytest.mark.asyncio
async def test_directory_no_role_returns_all_tools(client) -> None:
    """AC1: no X-Agent-Role -> unrestricted -> all TOOL_REGISTRY entries returned.

    POSITIVE: all registered tool names appear in allowed_tools.
    NEGATIVE locked: allowed_tools is NOT empty (the endpoint is wired).
    """
    resp = await client.get(_BASE, headers=_HDR)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["project_id"] == _SEED_PROJ
    assert body["role"] is None
    assert body["suggestion"] is None

    returned_names = {t["name"] for t in body["allowed_tools"]}
    registered_names = set(TOOL_REGISTRY.keys())

    # POSITIVE: every registered tool is in the response.
    assert returned_names == registered_names, (
        f"Expected all tools {registered_names}, got {returned_names}"
    )
    # NEGATIVE: response is not empty (endpoint actually wired).
    assert len(body["allowed_tools"]) > 0

    # Each entry has purpose, tier, version from the registry.
    for entry in body["allowed_tools"]:
        reg = TOOL_REGISTRY[entry["name"]]
        assert entry["tier"] == reg["tier"]
        assert entry["version"] == reg["version"]
        assert entry["purpose"] == reg["purpose"]
        assert len(entry["purpose"]) > 0


# ===========================================================================
# Test 2: Restricted role — only granted tools in response
# ===========================================================================


@pytest.mark.asyncio
async def test_directory_restricted_role_only_granted_tools(client, restore_config) -> None:
    """AC1: restricted role sees only its granted tools; others absent.

    POSITIVE: gmail.trash appears (granted).
    NEGATIVE locked: outlook.trash does NOT appear (not granted to secretary).
    """
    await _set_tool_grants(client, {"secretary": ["gmail.trash"]})

    resp = await client.get(
        _BASE,
        headers={**_HDR, "X-Agent-Role": "secretary"},
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["role"] == "secretary"
    returned_names = {t["name"] for t in body["allowed_tools"]}

    # POSITIVE: the one granted tool is present.
    assert "gmail.trash" in returned_names

    # NEGATIVE: the non-granted tool is absent.
    assert "outlook.trash" not in returned_names, (
        "outlook.trash should NOT be in the directory for restricted secretary"
    )


# ===========================================================================
# Test 3: AC2 — capability query → suggestion on no-match; null on match
# ===========================================================================


@pytest.mark.asyncio
async def test_directory_capability_suggestion(client) -> None:
    """AC2: ?capability=<text> with no tool match -> suggestion block returned.

    POSITIVE: suggestion.no_tool_for == the capability text.
    NEGATIVE locked: suggestion is NOT null when no tool matches.

    Also verifies that a capability that DOES match -> suggestion is null.
    """
    # No-match case: "calendar" is not in any tool name or purpose.
    resp = await client.get(_BASE, headers=_HDR, params={"capability": "calendar"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: suggestion present with correct no_tool_for.
    assert body["suggestion"] is not None, (
        "Expected a suggestion block when no tool matches 'calendar'"
    )
    assert body["suggestion"]["no_tool_for"] == "calendar"
    assert len(body["suggestion"]["hint"]) > 0

    # Match case: "trash" IS in both tool names and purposes.
    resp2 = await client.get(_BASE, headers=_HDR, params={"capability": "trash"})
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()

    # NEGATIVE (match path): suggestion IS null (a tool covers this capability).
    assert body2["suggestion"] is None, (
        "Expected suggestion=null when a tool matches 'trash'"
    )
