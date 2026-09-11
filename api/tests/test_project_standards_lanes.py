"""Kanban #2871 — `projects.config.standards` lane declaration contract.

The `mobile` lane (Angular/Ionic/Capacitor) was added alongside the mobile
team. This file locks the two SILENT-DROP traps that surfaced while adding it,
because both return a success status while discarding the caller's write:

1. `schemas/project.py::_Standards` is a closed model with Pydantic's default
   `extra="ignore"`. A lane that is not declared as a field is dropped with no
   422 and no warning — the caller POSTs `standards.mobile` and reads back a
   payload with no `mobile` key. Adding a lane MUST add the field here.

2. `ProjectUpdate` has NO `standards` field at all (only `config: dict`) and is
   ALSO `extra="ignore"`. So `PATCH {"standards": {...}}` is discarded WHOLESALE
   and still returns 200 — the `payload.standards` merge in
   `routers/projects.py` is on the CREATE path only. A standards PATCH must send
   the full `config` object (REPLACE semantics).

Trap 2 is intentionally locked as CURRENT BEHAVIOUR, not endorsed. If someone
adds `standards` to `ProjectUpdate`, `test_patch_standards_key_is_ignored` is
the test that will fail and point at this docstring.
"""

from __future__ import annotations

import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _payload(name: str, standards: dict | None = None) -> dict:
    body = {
        "name": name,
        "description": f"#2871 standards-lane fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "angular", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "mobile",
    }
    if standards is not None:
        body["standards"] = standards
    return body


# ---- trap 1: the declared lane survives POST -------------------------------


@pytest.mark.asyncio
async def test_create_declares_mobile_lane(client, scaffold_cleanup) -> None:
    """POST standards.mobile round-trips — the lane is a declared field.

    Before #2871 this silently returned a project whose config.standards had
    no `mobile` key at all, with a 201 status.
    """
    name = scaffold_cleanup(_unique_name("proj-2871-lane"))
    lanes = {
        "web": ["web", "typescript"],
        "mobile": ["angular", "ionic", "capacitor"],
        "api": ["fastapi"],
        "db": ["postgresql"],
    }
    resp = await client.post("/api/projects", json=_payload(name, lanes))
    assert resp.status_code == 201, resp.text

    stored = resp.json()["config"]["standards"]
    assert stored["mobile"] == ["angular", "ionic", "capacitor"]
    # the sibling lanes must not be clobbered by the new one
    assert stored["web"] == ["web", "typescript"]
    assert stored["api"] == ["fastapi"]
    assert stored["db"] == ["postgresql"]


@pytest.mark.asyncio
async def test_create_undeclared_lane_is_dropped(client, scaffold_cleanup) -> None:
    """An UNDECLARED lane is still silently dropped — 201, no 422, key absent.

    This is the trap itself, pinned so the behaviour is a known quantity rather
    than a surprise: adding a lane is a code change, not a config change.
    """
    name = scaffold_cleanup(_unique_name("proj-2871-undeclared"))
    resp = await client.post(
        "/api/projects", json=_payload(name, {"desktop": ["electron"]})
    )
    assert resp.status_code == 201, resp.text
    assert "desktop" not in resp.json()["config"]["standards"]


# ---- trap 2: PATCH must go through `config` --------------------------------


@pytest.mark.asyncio
async def test_patch_standards_key_is_ignored(client, scaffold_cleanup) -> None:
    """PATCH {"standards": ...} is discarded WHOLESALE and still returns 200.

    ProjectUpdate has no `standards` field; `extra="ignore"` eats the key. If
    this test starts failing, someone added the field — update this module's
    docstring and `context/standards/README.md` along with it.
    """
    name = scaffold_cleanup(_unique_name("proj-2871-patchdrop"))
    created = await client.post("/api/projects", json=_payload(name, {"mobile": ["angular"]}))
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    resp = await client.patch(
        f"/api/projects/{pid}", json={"standards": {"mobile": ["ionic"]}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["standards"]["mobile"] == ["angular"]


@pytest.mark.asyncio
async def test_patch_via_config_updates_the_lane(client, scaffold_cleanup) -> None:
    """The WORKING path: send the whole `config` object (REPLACE semantics)."""
    name = scaffold_cleanup(_unique_name("proj-2871-patchconfig"))
    created = await client.post("/api/projects", json=_payload(name, {"mobile": ["angular"]}))
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    resp = await client.patch(
        f"/api/projects/{pid}",
        json={"config": {"standards": {"mobile": ["angular", "ionic", "capacitor"]}}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["standards"]["mobile"] == [
        "angular",
        "ionic",
        "capacitor",
    ]
