"""Kanban #2300 (Slice 1) — per-project Anthropic effort/thinking cost lever.

Migration `0065_effort_mode` adds three nullable TEXT columns, each gated by a
Pydantic Literal at the API boundary (422 on any other value); NO DB CHECK
(#1677 / model_override posture):

  - projects.effort_mode    ∈ {off,low,medium,high,extra,auto} | NULL
  - tasks.effort_override   ∈ {off,low,medium,high,extra,max}   | NULL
  - session_runs.effort     (resolved level)                    | NULL

First-pass contract-smoke (dev-sr-backend scope — the comprehensive suite is
dev-tester's domain):

  PROJECT effort_mode:
    - POST without effort_mode → column NULL (= global default off).
    - POST + PATCH with each valid mode (incl. 'auto') → 200/201 + round-trip.
    - POST / PATCH with an invalid value → 422 (loc points at the field).
    - PATCH explicit-null → CLEARS to NULL (POSITIVE: was set; NEGATIVE: now None,
      not still the prior value).
    - PATCH absent key → leaves the value unchanged (exclude_unset).

  TASK effort_override:
    - POST without → NULL; each valid value (incl. 'max') round-trips; invalid 422.
    - 'max' IS accepted as a per-task carrier (manual-only lever) — the project
      ladder excludes it but the task carrier allows it.

  SESSION-RUN effort:
    - PATCH /api/session_runs/{id} accepts + persists `effort`; GET reflects.
    - Invalid effort → 422.

Runs against `agent_teams_test` per conftest.py. Live `agent_teams` row count
MUST NOT drift across the session; cleanup is DELETE /api/projects/{id} on the
way out (cascades child tasks / sessions).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k2300 effort lever fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k2300"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, **extra):
    body: dict = {
        "project_id": project_id,
        "title": "k2300 fixture task",
        "description": "k2300 effort_override test task",
    }
    body.update(extra)
    return await client.post(
        "/api/tasks", headers={"X-Project-Id": str(project_id)}, json=body
    )


@pytest.fixture
def session_fs_cleanup():
    """Remove `_sessions/<id>/` dirs created during a test (mirrors test_sessions)."""
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    ids: list[int] = []

    def register(session_id: int) -> int:
        ids.append(session_id)
        return session_id

    yield register

    for sid in ids:
        target = repo_root / "_sessions" / str(sid)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


# =============================================================================
# PROJECT effort_mode
# =============================================================================


@pytest.mark.asyncio
async def test_project_create_omits_effort_mode_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST without effort_mode → column NULL (= global default off)."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        assert project["effort_mode"] is None, project
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["effort_mode"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.parametrize("mode", ["off", "low", "medium", "high", "extra", "auto"])
@pytest.mark.asyncio
async def test_project_create_accepts_each_valid_mode(
    client, scaffold_cleanup, mode
) -> None:
    """POST with each valid effort_mode (incl. 'auto') → 201 + round-trip."""
    name = scaffold_cleanup(_unique_name("k2300-mode"))
    payload = _project_create_payload(name)
    payload["effort_mode"] = mode
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["effort_mode"] == mode, resp.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["effort_mode"] == mode, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_project_patch_effort_mode_round_trip(client, scaffold_cleanup) -> None:
    """PATCH 'auto' onto a project created without a mode → 200 + GET reflects."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"effort_mode": "auto"}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["effort_mode"] == "auto", patch.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["effort_mode"] == "auto", get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.parametrize("bad", ["max", "MEDIUM", "xhigh", "adaptive", "", "off "])
@pytest.mark.asyncio
async def test_project_effort_mode_invalid_returns_422(
    client, scaffold_cleanup, bad
) -> None:
    """Invalid effort_mode (incl. 'max' — NOT a project ladder value) → 422.

    'max' is deliberately excluded from the PROJECT ladder (it's manual-only via
    the per-task carrier); a project effort_mode='max' must 422.
    """
    name = scaffold_cleanup(_unique_name("k2300-bad"))
    payload = _project_create_payload(name)
    payload["effort_mode"] = bad
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    matches = [
        err
        for err in resp.json()["detail"]
        if err["loc"][:2] == ["body", "effort_mode"]
    ]
    assert matches, f"expected loc=['body','effort_mode',...]; got {resp.json()}"


@pytest.mark.asyncio
async def test_project_patch_effort_mode_null_clears(client, scaffold_cleanup) -> None:
    """PATCH explicit null → CLEARS to NULL (= back to global default off).

    POSITIVE lock: 'high' really lands first.
    NEGATIVE lock: explicit-null wipes it to None — NOT left at 'high'.
    """
    name = scaffold_cleanup(_unique_name("k2300-clear"))
    payload = _project_create_payload(name)
    payload["effort_mode"] = "high"
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # POSITIVE: it really landed.
        assert create.json()["effort_mode"] == "high", create.json()

        patch = await client.patch(
            f"/api/projects/{project_id}", json={"effort_mode": None}
        )
        assert patch.status_code == 200, patch.text
        # NEGATIVE/lock: cleared to None, not still 'high'.
        assert patch.json()["effort_mode"] is None, patch.json()
        assert patch.json()["effort_mode"] != "high", patch.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["effort_mode"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_project_patch_effort_mode_absent_key_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH not mentioning effort_mode leaves the prior mode intact (exclude_unset).

    POSITIVE (description changed) + NEGATIVE (effort_mode unchanged, still
    'medium', NOT None).
    """
    name = scaffold_cleanup(_unique_name("k2300-keep"))
    payload = _project_create_payload(name)
    payload["effort_mode"] = "medium"
    create = await client.post("/api/projects", json=payload)
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"description": "edited, no effort_mode key"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["description"] == "edited, no effort_mode key"
        # NEGATIVE/lock: effort_mode unchanged — still 'medium', NOT None.
        assert patch.json()["effort_mode"] == "medium", patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# TASK effort_override
# =============================================================================


@pytest.mark.asyncio
async def test_task_create_omits_effort_override_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST without effort_override → column NULL (= inherit)."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id)
        assert resp.status_code == 201, resp.text
        assert resp.json()["effort_override"] is None, resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.parametrize(
    "level", ["off", "low", "medium", "high", "extra", "max"]
)
@pytest.mark.asyncio
async def test_task_create_accepts_each_carrier_value(
    client, scaffold_cleanup, level
) -> None:
    """Each carrier value (incl. 'max' — manual-only lever) round-trips.

    'max' is the load-bearing case: the project ladder excludes it but the
    per-task carrier MUST accept it (Slice-2 manual override).
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id, effort_override=level)
        assert resp.status_code == 201, resp.text
        assert resp.json()["effort_override"] == level, resp.json()
        get_resp = await client.get(
            f"/api/tasks/{resp.json()['id']}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert get_resp.json()["effort_override"] == level, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.parametrize("bad", ["auto", "xhigh", "MAX", "", "high "])
@pytest.mark.asyncio
async def test_task_create_rejects_bad_effort_override(
    client, scaffold_cleanup, bad
) -> None:
    """Invalid effort_override (incl. 'auto' — a PROJECT-only value) → 422.

    'auto' is a project-mode value, NOT a per-task carrier value; a task
    effort_override='auto' must 422.
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id, effort_override=bad)
        assert resp.status_code == 422, resp.text
        matches = [
            err
            for err in resp.json()["detail"]
            if err["loc"][:2] == ["body", "effort_override"]
        ]
        assert matches, f"expected loc=['body','effort_override',...]; got {resp.json()}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_task_patch_effort_override_null_clears(
    client, scaffold_cleanup
) -> None:
    """PATCH explicit null → CLEARS to NULL (= inherit).

    POSITIVE: 'extra' really lands. NEGATIVE: explicit-null wipes to None, not
    left at 'extra'.
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id, effort_override="extra")
        task_id = create.json()["id"]
        assert create.json()["effort_override"] == "extra", create.json()

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={"effort_override": None},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["effort_override"] is None, patch.json()
        assert patch.json()["effort_override"] != "extra", patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# SESSION-RUN effort
# =============================================================================


@pytest.mark.asyncio
async def test_session_run_patch_accepts_effort(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """PATCH /api/session_runs/{id} with `effort` persists it; GET reflects."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        s = await client.post("/api/sessions", json={"project_id": project_id})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        assert run.status_code == 201, run.text
        rid = run.json()["id"]
        # Fresh run carries NULL effort.
        assert run.json()["effort"] is None, run.json()

        patch = await client.patch(
            f"/api/session_runs/{rid}",
            json={"status": "done", "effort": "high"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["effort"] == "high", patch.json()

        runs = await client.get(f"/api/sessions/{sid}/runs")
        assert runs.status_code == 200, runs.text
        row = next(r for r in runs.json() if r["id"] == rid)
        assert row["effort"] == "high", row
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_session_run_patch_rejects_bad_effort(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """PATCH with an out-of-set `effort` → 422 (the typed Literal validates even
    under SessionRunUpdate's extra='ignore')."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        s = await client.post("/api/sessions", json={"project_id": project_id})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        patch = await client.patch(
            f"/api/session_runs/{rid}",
            json={"status": "done", "effort": "xhigh"},
        )
        assert patch.status_code == 422, patch.text
        matches = [
            err
            for err in patch.json()["detail"]
            if err["loc"][:2] == ["body", "effort"]
        ]
        assert matches, f"expected loc=['body','effort',...]; got {patch.json()}"
    finally:
        await client.delete(f"/api/projects/{project_id}")
