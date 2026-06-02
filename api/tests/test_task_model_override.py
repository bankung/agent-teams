"""Kanban #1677 — `tasks.model_override` per-task model-tier override wire-up.

Migration `0056_task_model_override` adds a nullable TEXT column storing an
optional per-task model-tier override — one of 'haiku'/'sonnet'/'opus' or NULL
(=inherit). NULL = no per-task override; the orchestrator falls through to
`project.agent_overrides`, then the role default (precedence:
`task.model_override > project.agent_overrides > role default`). Precedence
ENFORCEMENT is an orchestrator convention, NOT code — this slice only STORES
the column and surfaces it on TaskRead so the Lead/orchestrator can read it.

First-pass contract-smoke (dev-sr-backend scope — the comprehensive suite is
dev-tester's domain):
- POST without model_override → column lands NULL (= inherit).
- POST with 'haiku' → stored verbatim; GET reflects (persistence round-trip).
- POST with a bad tier ('gpt5') → 422 (Pydantic Literal at the boundary).
- PATCH 'sonnet' → 200 + GET reflects (round-trip).
- PATCH explicit-null → CLEARS to NULL (back to inherit; null IS meaningful),
  with a POSITIVE lock (the value was really set first) paired with the
  NEGATIVE lock (null clears it, NOT leaving the prior tier in place).
- PATCH absent key → leaves the existing value unchanged (exclude_unset).
- PATCH bad tier → 422.
- Precedence DATA-layer matrix (no runtime resolver is added this slice — it
  is an orchestrator convention; testing a fabricated helper would be
  test-surface pollution). Instead we pin the three INPUTS the orchestrator
  reads, proving the stored/readable data is correct in each case:
    (a) task override SET + project agent_overrides SET → both surface
        independently (task wins per the documented precedence; we assert the
        task column carries the winning value AND the project override is
        readable as the documented fallback).
    (b) ONLY project agent_overrides SET (task override NULL) → task surfaces
        NULL (= inherit) and the project override is the readable fallback.
    (c) NEITHER set → both NULL (role default would apply — the orchestrator's
        concern, no data on either row).

Runs against `agent_teams_test` per conftest.py. Live `agent_teams` row count
MUST NOT drift across the session (the conftest invariant asserts it); cleanup
is DELETE /api/projects/{id} on the way out (cascades child tasks).
"""

from __future__ import annotations

import uuid

import pytest


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(
    name: str,
    *,
    team: str = "dev",
    agent_overrides: dict | None = None,
) -> dict:
    body: dict = {
        "name": name,
        "description": f"k1677 model_override fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }
    if agent_overrides is not None:
        body["agent_overrides"] = agent_overrides
    return body


async def _create_project(client, scaffold_cleanup, **kwargs) -> dict:
    name = scaffold_cleanup(_unique_name("k1677"))
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name, **kwargs)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _task_create_payload(project_id: int, **extra) -> dict:
    body: dict = {
        "project_id": project_id,
        "title": "k1677 fixture task",
        "description": "k1677 model_override test task",
    }
    body.update(extra)
    return body


async def _create_task(client, project_id: int, **extra):
    return await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=_task_create_payload(project_id, **extra),
    )


# ---- 1. POST without model_override → NULL (inherit) -----------------------


@pytest.mark.asyncio
async def test_create_task_omits_model_override_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST without `model_override` → column NULL (= inherit)."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id)
        assert resp.status_code == 201, resp.text
        assert resp.json()["model_override"] is None, resp.json()

        get_resp = await client.get(
            f"/api/tasks/{resp.json()['id']}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["model_override"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. POST with 'haiku' → stored verbatim (persistence round-trip) -------


@pytest.mark.asyncio
async def test_create_task_explicit_model_override_round_trip(
    client, scaffold_cleanup
) -> None:
    """POST with `model_override='haiku'` → server stores it verbatim; GET back."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id, model_override="haiku")
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]
        assert resp.json()["model_override"] == "haiku", resp.json()

        get_resp = await client.get(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["model_override"] == "haiku", get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.parametrize("tier", ["haiku", "sonnet", "opus"])
@pytest.mark.asyncio
async def test_create_task_accepts_all_three_tiers(
    client, scaffold_cleanup, tier
) -> None:
    """Each of the three Claude tiers is accepted + round-trips."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id, model_override=tier)
        assert resp.status_code == 201, resp.text
        assert resp.json()["model_override"] == tier, resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 3. POST with a bad tier → 422 -----------------------------------------


@pytest.mark.parametrize("bad_tier", ["gpt5", "GPT-4", "claude", "haiku ", "", "nano"])
@pytest.mark.asyncio
async def test_create_task_rejects_bad_tier(
    client, scaffold_cleanup, bad_tier
) -> None:
    """POST with a non-tier `model_override` value → 422; loc points at the field."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        resp = await _create_task(client, project_id, model_override=bad_tier)
        assert resp.status_code == 422, resp.text
        matches = [
            err
            for err in resp.json()["detail"]
            if err["loc"][:2] == ["body", "model_override"]
        ]
        assert matches, f"expected loc=['body','model_override',...]; got {resp.json()}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 4. PATCH round-trip + 422 ---------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_model_override_round_trip(
    client, scaffold_cleanup
) -> None:
    """PATCH 'sonnet' onto a task created without an override → 200 + GET reflects."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id)
        assert create.status_code == 201, create.text
        task_id = create.json()["id"]
        assert create.json()["model_override"] is None, create.json()

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={"model_override": "sonnet"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["model_override"] == "sonnet", patch.json()

        get_resp = await client.get(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )
        assert get_resp.json()["model_override"] == "sonnet", get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_model_override_bad_tier_returns_422(
    client, scaffold_cleanup
) -> None:
    """PATCH with a bad tier → 422; the existing value is left untouched."""
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id, model_override="opus")
        task_id = create.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={"model_override": "turbo"},
        )
        assert patch.status_code == 422, patch.text

        # Unchanged — still 'opus'.
        get_resp = await client.get(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )
        assert get_resp.json()["model_override"] == "opus", get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5. PATCH explicit-null CLEARS (back to inherit) -----------------------


@pytest.mark.asyncio
async def test_patch_task_model_override_null_clears_to_inherit(
    client, scaffold_cleanup
) -> None:
    """PATCH explicit `null` → CLEARS to NULL (= inherit; null IS meaningful).

    POSITIVE lock: the tier is really set on the row first ('opus').
    NEGATIVE lock: explicit-null wipes it back to None — NOT left at 'opus',
    NOT coerced to any other sentinel. Mirrors halt_reason / status_change_reason.
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id, model_override="opus")
        task_id = create.json()["id"]
        # POSITIVE: it really landed.
        assert create.json()["model_override"] == "opus", create.json()

        # NEGATIVE/lock: explicit-null clears to None — NOT still 'opus'.
        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={"model_override": None},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["model_override"] is None, patch.json()
        assert patch.json()["model_override"] != "opus", patch.json()

        get_resp = await client.get(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )
        assert get_resp.json()["model_override"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 6. PATCH absent key leaves the value unchanged ------------------------


@pytest.mark.asyncio
async def test_patch_task_model_override_absent_key_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH that does NOT mention model_override leaves the prior tier intact.

    Pins exclude_unset key-absent semantics: a PATCH touching only `title`
    must not wipe a previously-set model_override. POSITIVE (title changed) +
    NEGATIVE (model_override NOT changed, still 'haiku', NOT None).
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id, model_override="haiku")
        task_id = create.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={"title": "edited title, no model_override key"},
        )
        assert patch.status_code == 200, patch.text
        # POSITIVE: the title DID change.
        assert patch.json()["title"] == "edited title, no model_override key"
        # NEGATIVE/lock: model_override unchanged — still 'haiku', NOT None.
        assert patch.json()["model_override"] == "haiku", patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 7. Precedence DATA-layer matrix (inputs the orchestrator reads) -------


@pytest.mark.asyncio
async def test_precedence_inputs_task_set_and_project_set(
    client, scaffold_cleanup
) -> None:
    """(a) Task override SET + project agent_overrides SET.

    No runtime resolver is added this slice (precedence is an orchestrator
    convention). This pins the two INPUTS the orchestrator reads — proving the
    higher-precedence value (task.model_override) and the documented fallback
    (project.agent_overrides) are BOTH independently readable, with the task
    column carrying the winning tier. The orchestrator picks task over project.
    """
    project = await _create_project(
        client,
        scaffold_cleanup,
        agent_overrides={"dev-backend": "sonnet"},
    )
    project_id = project["id"]
    try:
        # Project-level fallback is readable.
        assert project["agent_overrides"] == {"dev-backend": "sonnet"}, project

        # Task-level override (higher precedence) carries 'opus'.
        create = await _create_task(client, project_id, model_override="opus")
        assert create.status_code == 201, create.text
        assert create.json()["model_override"] == "opus", create.json()

        # Both inputs visible on read; task value distinct from project fallback.
        task_get = await client.get(
            f"/api/tasks/{create.json()['id']}",
            headers={"X-Project-Id": str(project_id)},
        )
        proj_get = await client.get(f"/api/projects/{project_id}")
        assert task_get.json()["model_override"] == "opus", task_get.json()
        assert proj_get.json()["agent_overrides"] == {"dev-backend": "sonnet"}, (
            proj_get.json()
        )
        # The winning (task) tier is NOT the project fallback's value.
        assert task_get.json()["model_override"] != "sonnet"
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_precedence_inputs_only_project_set(
    client, scaffold_cleanup
) -> None:
    """(b) ONLY project agent_overrides SET — task override NULL (= inherit).

    The task surfaces NULL (no per-task override) so the orchestrator falls
    through to the project-level agent_overrides, which is the readable fallback.
    """
    project = await _create_project(
        client,
        scaffold_cleanup,
        agent_overrides={"dev-backend": "haiku"},
    )
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id)  # no model_override
        assert create.status_code == 201, create.text
        # Task input is NULL → orchestrator inherits.
        assert create.json()["model_override"] is None, create.json()
        # Project fallback present + readable.
        proj_get = await client.get(f"/api/projects/{project_id}")
        assert proj_get.json()["agent_overrides"] == {"dev-backend": "haiku"}, (
            proj_get.json()
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_precedence_inputs_neither_set(
    client, scaffold_cleanup
) -> None:
    """(c) NEITHER set — task override NULL AND project agent_overrides NULL.

    The orchestrator falls all the way through to the role default (its own
    concern; no data on either row). We pin that both inputs are absent.
    """
    project = await _create_project(client, scaffold_cleanup)  # no agent_overrides
    project_id = project["id"]
    try:
        create = await _create_task(client, project_id)  # no model_override
        assert create.status_code == 201, create.text
        # Load-bearing: task input is NULL → orchestrator inherits.
        assert create.json()["model_override"] is None, create.json()
        # Project agent_overrides is the documented fallback. Omitting it on
        # create lands the DB default `{}` (existing projects behavior — NOT a
        # #1677 surface); both `{}` and `None` mean "no project-level override
        # → fall through to the role default". Assert FALSY (no override present).
        proj_get = await client.get(f"/api/projects/{project_id}")
        assert not proj_get.json()["agent_overrides"], proj_get.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")
