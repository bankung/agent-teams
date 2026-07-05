"""Kanban #2769 — GET /api/projects/{id}/spawn-check (read-only spawn-gate
authority endpoint).

Backend counterpart to a Lead-side PreToolUse hook (built separately) that
decides allow/deny for an `Agent`-tool spawn BEFORE it fires. This suite
covers the two gates the endpoint evaluates:

- `config.agent_settings[agent].enabled == False` (#1018 per-agent toggle) —
  the AGENT gate, checked first.
- `config.enabled_roles` (#7 int[] TaskRole whitelist) — the ROLE gate,
  checked second (only applies when the agent maps to a known `TaskRole`
  code via `constants.AGENT_ROLE_CODE`).

Tests run against `agent_teams_test`. Live `agent_teams` row count must NOT
drift across the session — the `_live_db_row_count_invariant` fixture in
conftest.py asserts that (this suite makes zero live-DB writes: every project
created below targets the test DB via the `client`/`scaffold_cleanup`
fixtures, mirroring `test_agent_overrides_audit.py`).
"""

from __future__ import annotations

import uuid

import pytest

# Real, currently-valid agent names (per `.claude/agents/*.md` + the
# AGENT_ROLE_CODE map in constants.py) — mirrors test_agent_overrides_audit.py's
# convention so any name-existence gate elsewhere always passes.
_BACKEND = "dev-backend"  # role-coded: TaskRole.BACKEND (2)
_FRONTEND = "dev-frontend"  # role-coded: TaskRole.FRONTEND (1)
_UNMAPPED = "general-researcher"  # intentionally unmapped — never role-gated


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, config: dict | None = None) -> dict:
    return {
        "name": name,
        "description": f"k2769 spawn-check fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": config or {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(
    client, scaffold_cleanup, *, slug: str, config: dict | None = None
) -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name, config=config)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- 1. no config -> allowed for both a role-coded and an unmapped agent ---


@pytest.mark.asyncio
async def test_no_config_allows_role_coded_and_unmapped_agent(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2769-noconfig")
    pid = project["id"]
    try:
        backend_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _BACKEND}
        )
        assert backend_resp.status_code == 200, backend_resp.text
        body = backend_resp.json()
        assert body["allowed"] is True, body  # POSITIVE: backfill-safe default
        assert body["role_code"] == 2, body
        assert body["agent"] == _BACKEND, body

        researcher_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _UNMAPPED}
        )
        assert researcher_resp.status_code == 200, researcher_resp.text
        researcher_body = researcher_resp.json()
        assert researcher_body["allowed"] is True, researcher_body
        assert researcher_body["role_code"] is None, researcher_body
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 2. enabled_roles=[2,5] -> dev-backend allowed, dev-frontend denied,
#         general-researcher allowed (unmapped) ----------------------------


@pytest.mark.asyncio
async def test_enabled_roles_whitelist_denies_non_listed_role(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(
        client, scaffold_cleanup, slug="k2769-roles", config={"enabled_roles": [2, 5]}
    )
    pid = project["id"]
    try:
        backend_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _BACKEND}
        )
        assert backend_resp.status_code == 200, backend_resp.text
        assert backend_resp.json()["allowed"] is True, backend_resp.json()  # POSITIVE: 2 is listed

        frontend_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _FRONTEND}
        )
        assert frontend_resp.status_code == 200, frontend_resp.text
        frontend_body = frontend_resp.json()
        assert frontend_body["allowed"] is False, frontend_body  # NEGATIVE: 1 is not listed
        assert frontend_body["role_code"] == 1, frontend_body
        assert "enabled_roles" in frontend_body["reason"], frontend_body

        researcher_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _UNMAPPED}
        )
        assert researcher_resp.status_code == 200, researcher_resp.text
        assert researcher_resp.json()["allowed"] is True, researcher_resp.json()  # POSITIVE: unmapped bypasses role gate
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 3. enabled_roles=[] -> dev-backend denied, general-researcher allowed --


@pytest.mark.asyncio
async def test_enabled_roles_empty_denies_every_role_coded_agent(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(
        client, scaffold_cleanup, slug="k2769-noroles", config={"enabled_roles": []}
    )
    pid = project["id"]
    try:
        backend_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _BACKEND}
        )
        assert backend_resp.status_code == 200, backend_resp.text
        backend_body = backend_resp.json()
        assert backend_body["allowed"] is False, backend_body  # NEGATIVE: empty whitelist
        assert backend_body["role_code"] == 2, backend_body

        researcher_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _UNMAPPED}
        )
        assert researcher_resp.status_code == 200, researcher_resp.text
        assert researcher_resp.json()["allowed"] is True, researcher_resp.json()  # POSITIVE: unmapped survives an empty whitelist
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 4. agent_settings.enabled=false -> dev-backend denied (agent gate),
#         dev-frontend unaffected ------------------------------------------


@pytest.mark.asyncio
async def test_agent_settings_disabled_denies_only_that_agent(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(
        client,
        scaffold_cleanup,
        slug="k2769-disabled",
        config={"agent_settings": {_BACKEND: {"enabled": False}}},
    )
    pid = project["id"]
    try:
        backend_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _BACKEND}
        )
        assert backend_resp.status_code == 200, backend_resp.text
        backend_body = backend_resp.json()
        assert backend_body["allowed"] is False, backend_body  # NEGATIVE: explicit disable
        assert "agent_settings.enabled=false" in backend_body["reason"], backend_body

        frontend_resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _FRONTEND}
        )
        assert frontend_resp.status_code == 200, frontend_resp.text
        assert frontend_resp.json()["allowed"] is True, frontend_resp.json()  # POSITIVE: unaffected sibling
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 5. agent_settings.enabled=true -> dev-backend allowed ------------------


@pytest.mark.asyncio
async def test_agent_settings_explicit_enabled_true_allows(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(
        client,
        scaffold_cleanup,
        slug="k2769-enabled",
        config={"agent_settings": {_BACKEND: {"enabled": True}}},
    )
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _BACKEND}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["allowed"] is True, resp.json()  # POSITIVE: explicit true passes
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 6. both gates would fire -> AGENT-gate reason wins (checked first) ----


@pytest.mark.asyncio
async def test_agent_gate_reason_wins_when_both_gates_would_deny(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(
        client,
        scaffold_cleanup,
        slug="k2769-bothgates",
        config={
            "enabled_roles": [5],  # dev-backend's role_code=2 is NOT listed
            "agent_settings": {_BACKEND: {"enabled": False}},
        },
    )
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/spawn-check", params={"agent": _BACKEND}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is False, body
        # POSITIVE: the AGENT-gate reason string is present.
        assert "agent_settings.enabled=false" in body["reason"], body
        # NEGATIVE: the ROLE-gate reason string is NOT what fired (agent gate wins).
        assert "enabled_roles" not in body["reason"], body
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 7. malformed agent name -> 422 -----------------------------------------


@pytest.mark.asyncio
async def test_malformed_agent_name_returns_422(client, scaffold_cleanup) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2769-malformed")
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/spawn-check",
            params={"agent": "../etc/passwd"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 8. unknown project_id -> 404 -------------------------------------------


@pytest.mark.asyncio
async def test_unknown_project_id_returns_404(client) -> None:
    resp = await client.get(
        "/api/projects/99999999/spawn-check", params={"agent": _BACKEND}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Project id=99999999 not found"
