"""Tests for tasks.run_mode + projects.auto_run_consent_at — Kanban #481/#483.

Covers four surfaces:

1. Schema-level (Pydantic): TaskCreate / TaskUpdate / ProjectGrantConsent —
   accepted values, defaults, extra-field rejection, lockstep guard.
2. POST /api/projects/{id}/grant-consent — happy path, mismatch, 404,
   idempotent re-grant, extra-field rejection, source-text-lock.
3. POST /api/tasks cross-table consent gate.
4. PATCH /api/tasks/{id} cross-table consent gate.
5. TaskRead / ProjectRead shape — run_mode + auto_run_consent_at exposure.

Each test that creates rows soft-deletes them on exit (mirroring the
test_routes_smoke.py convention). Consent on the seeded `agent-teams`
project is restored to NULL via raw db_session at the end of any test
that grants it — keeps cross-test pollution bounded.
"""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.constants import RecordStatus, TaskRunMode


# -----------------------------------------------------------------------------
# Helpers (mirrored from test_routes_smoke.py — kept local to avoid import churn)
# -----------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(
    name: str, *, team: str = "dev", is_active: bool = False
) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": is_active,
        "team": team,
    }


async def _reset_consent(db_session, project_id: int) -> None:
    """Restore agent-teams's consent to NULL — call in `finally` after a grant
    test against the seeded row. Uses raw db_session because the API doesn't
    yet expose a revoke endpoint (Kanban #481 follow-up)."""
    from sqlalchemy import update

    from src.models.project import Project

    await db_session.execute(
        update(Project).where(Project.id == project_id).values(auto_run_consent_at=None)
    )
    await db_session.commit()


# =============================================================================
# 1. Schema-level (Pydantic) tests
# =============================================================================


def test_task_create_run_mode_default_is_manual() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(project_id=1, title="x")
    assert task.run_mode == TaskRunMode.MANUAL


def test_task_create_run_mode_accepts_each_valid_value() -> None:
    from src.schemas.task import TaskCreate

    for mode in TaskRunMode.ALL:
        task = TaskCreate(project_id=1, title="x", run_mode=mode)
        assert task.run_mode == mode


def test_task_create_run_mode_rejects_unknown() -> None:
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", run_mode="auto_invalid")
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    # Pydantic Literal default error mentions "Input should be" + the allowed args.
    assert "manual" in msg and "auto_pickup" in msg and "auto_headless" in msg


def test_task_update_run_mode_accepts_each_valid_value() -> None:
    from src.schemas.task import TaskUpdate

    for mode in TaskRunMode.ALL:
        upd = TaskUpdate(run_mode=mode)
        assert upd.run_mode == mode


def test_task_update_run_mode_omitted_is_none() -> None:
    """PATCH semantics: omitted run_mode means 'no change'."""
    from src.schemas.task import TaskUpdate

    upd = TaskUpdate()
    assert upd.run_mode is None
    assert "run_mode" not in upd.model_fields_set


def test_task_update_run_mode_is_not_in_rejected_set() -> None:
    """Unlike parent_task_id, run_mode IS modifiable via PATCH."""
    from src.schemas.task import TaskUpdate

    upd = TaskUpdate(run_mode="auto_pickup")
    assert upd.run_mode == "auto_pickup"
    assert "run_mode" in upd.model_fields_set


def test_project_grant_consent_rejects_extra_fields() -> None:
    """`extra='forbid'` — typed-acknowledgment must fail loud on smuggled fields."""
    from src.schemas.project import ProjectGrantConsent

    with pytest.raises(ValidationError) as ei:
        ProjectGrantConsent(confirm_name="agent-teams", extra="x")  # type: ignore[call-arg]
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "Extra inputs are not permitted" in msg or "extra_forbidden" in msg


def test_project_grant_consent_requires_min_length_1() -> None:
    from src.schemas.project import ProjectGrantConsent

    with pytest.raises(ValidationError):
        ProjectGrantConsent(confirm_name="")


def test_task_run_mode_literal_matches_constants_all() -> None:
    """The Literal stays in lockstep with TaskRunMode.ALL via an import-time
    guard at the bottom of schemas/task.py. Verify the guard's positive case:
    no drift means import succeeds + sets are equal."""
    from src.schemas.task import TaskRunModeLiteral

    assert set(TaskRunModeLiteral.__args__) == set(TaskRunMode.ALL)  # type: ignore[attr-defined]


def test_task_run_mode_literal_drift_raises_at_import(monkeypatch) -> None:
    """Force drift between TaskRunMode.ALL and the Literal — the guard at the
    bottom of schemas/task.py must raise RuntimeError. Mirrors the
    TeamCode <-> ProjectTeam.ALL guard for project schemas."""
    import src.constants as constants_mod
    import src.schemas.task as task_schema_mod

    # Patch ALL to a wrong value, then re-import the schema module.
    monkeypatch.setattr(constants_mod.TaskRunMode, "ALL", ("manual", "wrong_extra"))
    with pytest.raises(RuntimeError, match="drifted"):
        importlib.reload(task_schema_mod)
    # Re-restore original module state so other tests aren't affected.
    monkeypatch.undo()
    importlib.reload(task_schema_mod)


# =============================================================================
# 2. POST /api/projects/{id}/grant-consent
# =============================================================================


@pytest.mark.asyncio
async def test_grant_consent_happy_path_stamps_timestamp(
    client, db_session, scaffold_cleanup
) -> None:
    """confirm_name matches → 200 + auto_run_consent_at non-null + updated_at bumped."""
    name = _unique_name("consent-happy")
    scaffold_cleanup(name)

    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    pre_updated_at = create.json()["updated_at"]
    assert create.json()["auto_run_consent_at"] is None

    try:
        resp = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_run_consent_at"] is not None
        # updated_at must advance — server-side func.now() bump.
        assert body["updated_at"] != pre_updated_at
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_grant_consent_mismatch_returns_400_stable_detail(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("consent-mismatch")
    scaffold_cleanup(name)

    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        resp = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": "wrong-name"},
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "detail": "confirm_name must match project name exactly"
        }
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_grant_consent_404_on_missing_project(client) -> None:
    resp = await client.post(
        "/api/projects/999999999/grant-consent",
        json={"confirm_name": "anything"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_grant_consent_404_on_soft_deleted_project(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("consent-deleted")
    scaffold_cleanup(name)

    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    await client.delete(f"/api/projects/{project_id}")

    resp = await client.post(
        f"/api/projects/{project_id}/grant-consent",
        json={"confirm_name": name},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_grant_consent_idempotent_no_restamp(
    client, scaffold_cleanup
) -> None:
    """Re-grant on already-consented project: 200 + auto_run_consent_at and
    updated_at UNCHANGED (the first consent is the legally-significant one)."""
    name = _unique_name("consent-idempotent")
    scaffold_cleanup(name)

    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        first = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        assert first.status_code == 200
        first_consent_at = first.json()["auto_run_consent_at"]
        first_updated_at = first.json()["updated_at"]
        assert first_consent_at is not None

        second = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        assert second.status_code == 200
        # Idempotent: timestamps must NOT advance on re-grant.
        assert second.json()["auto_run_consent_at"] == first_consent_at
        assert second.json()["updated_at"] == first_updated_at
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_grant_consent_rejects_extra_fields_422(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("consent-extra")
    scaffold_cleanup(name)

    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        resp = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name, "extra": "x"},
        )
        assert resp.status_code == 422
    finally:
        await client.delete(f"/api/projects/{project_id}")


def test_grant_consent_400_detail_string_pinned_in_router_source() -> None:
    """Source-text-lock per Kanban #122 pattern: the 400 detail string for
    confirm_name mismatch is wire contract — drift breaks any FE that string-
    matches it."""
    from src.routers import projects as projects_router

    source = Path(projects_router.__file__).read_text(encoding="utf-8")
    pinned = '"confirm_name must match project name exactly"'
    assert pinned in source, (
        f"confirm_name mismatch detail string drifted in routers/projects.py — "
        f"expected {pinned!r}"
    )


# =============================================================================
# 3. Cross-table validator on POST /api/tasks
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_auto_headless_no_consent_400(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("nohead-noconsent")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "headless without consent",
                "run_mode": "auto_headless",
            },
            headers={"X-Project-Id": str(project_id)},
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "detail": f"project {project_id} has not granted auto-headless consent"
        }
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_auto_headless_after_consent_201(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("head-consented")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        grant = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        assert grant.status_code == 200

        headers = {"X-Project-Id": str(project_id)}
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "headless with consent",
                "run_mode": "auto_headless",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["run_mode"] == "auto_headless"
        await client.delete(f"/api/tasks/{resp.json()['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_manual_no_consent_201(
    client, scaffold_cleanup
) -> None:
    """Default run_mode='manual' on non-consented project → 201 (validator no-op)."""
    name = _unique_name("manual-noconsent")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        headers = {"X-Project-Id": str(project_id)}
        resp = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "manual default"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["run_mode"] == "manual"
        await client.delete(f"/api/tasks/{resp.json()['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_auto_pickup_no_consent_201(
    client, scaffold_cleanup
) -> None:
    """Mode A2 (auto_pickup) doesn't need consent — only auto_headless does."""
    name = _unique_name("pickup-noconsent")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        headers = {"X-Project-Id": str(project_id)}
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "pickup without consent",
                "run_mode": "auto_pickup",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["run_mode"] == "auto_pickup"
        await client.delete(f"/api/tasks/{resp.json()['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


def test_consent_required_detail_string_pinned_in_router_and_service() -> None:
    """Source-text-lock for the cross-table validator's 400 detail.
    The string is generated by services/run_mode.py with f-string interpolation
    of project_id; we lock the surrounding template text."""
    from src.services import run_mode as run_mode_service

    source = Path(run_mode_service.__file__).read_text(encoding="utf-8")
    # The f-string template is: f"project {project_id} has not granted auto-headless consent"
    pinned = '"project {project_id} has not granted auto-headless consent"'
    # Match the pin substring with curly braces present (f-string template form).
    assert pinned in source.replace("f\"", "\""), (
        "consent-required detail string template drifted in services/run_mode.py"
    )


# -----------------------------------------------------------------------------
# 3a. #690 — disambiguate "no active row" from "row exists with NULL consent"
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_auto_headless_with_missing_project_returns_project_does_not_exist(
    client,
) -> None:
    """#690: validator must disambiguate 'no row' from 'row exists with NULL consent'.

    Bogus project_id + run_mode='auto_headless' must surface the same FK-style
    detail string that run_mode='manual' would surface via the IntegrityError
    handler in routers/tasks.py — not the consent-required string."""
    bogus_id = 999_999
    # Kanban #695: header must match body to reach the consent/FK branch
    # (body-vs-header mismatch fires earlier with a different 400 detail).
    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": bogus_id,
            "title": "smoke-690-missing-project",
            "run_mode": "auto_headless",
        },
        headers={"X-Project-Id": str(bogus_id)},
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": f"project_id {bogus_id} does not exist"}


@pytest.mark.asyncio
async def test_post_task_auto_headless_with_softdeleted_project_returns_project_does_not_exist(
    client, scaffold_cleanup
) -> None:
    """#690: soft-deleted projects are invisible to the consent validator's
    `status == ACTIVE` filter — same FK-style detail string as a missing row."""
    name = _unique_name("proj-690-softdeleted")
    scaffold_cleanup(name)

    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 204

    # Attempt auto_headless task creation against the now-soft-deleted project.
    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "smoke-690-softdeleted-project",
            "run_mode": "auto_headless",
        },
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": f"project_id {project_id} does not exist"}


def test_missing_project_detail_string_pinned_in_service_source() -> None:
    """#690 source-text-lock: both 400 detail templates produced by
    services/run_mode.py are wire contract — drift breaks any FE that string-
    matches them. Lock the FK-style template alongside the consent template."""
    from src.services import run_mode as run_mode_service

    source = Path(run_mode_service.__file__).read_text(encoding="utf-8")
    # Strip the f"" prefix so the pin matches the f-string template form.
    normalized = source.replace("f\"", "\"")
    consent_pinned = '"project {project_id} has not granted auto-headless consent"'
    missing_pinned = '"project_id {project_id} does not exist"'
    assert consent_pinned in normalized, (
        "consent-required detail string template drifted in services/run_mode.py"
    )
    assert missing_pinned in normalized, (
        "missing-project (FK-style) detail string template drifted in "
        "services/run_mode.py — must mirror routers/tasks.py "
        "tasks_project_id_fkey IntegrityError translation (Kanban #690)"
    )


# =============================================================================
# 4. Cross-table validator on PATCH /api/tasks/{id}
# =============================================================================


@pytest.mark.asyncio
async def test_patch_task_to_auto_headless_no_consent_400(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("patch-headless-noconsent")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "manual task"},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"run_mode": "auto_headless"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "detail": f"project {project_id} has not granted auto-headless consent"
        }
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_to_auto_headless_after_consent_200(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("patch-headless-consented")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "manual task"},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        grant = await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        assert grant.status_code == 200

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"run_mode": "auto_headless"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["run_mode"] == "auto_headless"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_no_run_mode_change_works_without_consent(
    client, scaffold_cleanup
) -> None:
    """PATCHing other fields on a non-consented project still works — validator
    only fires when the resolved final run_mode is auto_headless."""
    name = _unique_name("patch-other-fields")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "manual task"},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "renamed manual task"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "renamed manual task"
        assert resp.json()["run_mode"] == "manual"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_downgrade_from_auto_headless_to_manual_allowed(
    client, db_session, scaffold_cleanup
) -> None:
    """Downgrading auto_headless → manual must always work — even after consent
    was nominally revoked. Validator only asserts when resolved mode is auto_headless."""
    name = _unique_name("downgrade-headless")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    try:
        # Grant + create headless task.
        await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        task = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "headless task",
                "run_mode": "auto_headless",
            },
            headers=headers,
        )
        task_id = task.json()["id"]
        assert task.json()["run_mode"] == "auto_headless"

        # Revoke consent at the DB level (no public revoke endpoint yet).
        await _reset_consent(db_session, project_id)

        # Downgrade to manual — must succeed despite the revoked consent.
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"run_mode": "manual"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["run_mode"] == "manual"
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 5. TaskRead / ProjectRead shape
# =============================================================================


@pytest.mark.asyncio
async def test_task_read_includes_run_mode_default_manual(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("read-shape-task")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "default mode task"},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "run_mode" in body
        assert body["run_mode"] == "manual"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_project_read_includes_auto_run_consent_at_default_null(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("read-shape-project")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]

    try:
        # Detail by-name path — exercises the most common read.
        resp = await client.get(f"/api/projects/by-name/{name}")
        assert resp.status_code == 200
        body = resp.json()
        assert "auto_run_consent_at" in body
        assert body["auto_run_consent_at"] is None
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_seeded_agent_teams_task_has_run_mode_manual(client) -> None:
    """Migration 0005's DEFAULT 'manual' applied to existing rows — seeded
    task #3 (Phase 3 — kanban UI scaffold) should expose run_mode='manual'.
    Kanban #695: header required (seeded task #3 belongs to project_id=1)."""
    resp = await client.get("/api/tasks/3", headers={"X-Project-Id": "1"})
    assert resp.status_code == 200
    assert resp.json().get("run_mode") == "manual"


@pytest.mark.asyncio
async def test_seeded_agent_teams_project_consent_is_null(client) -> None:
    """The seeded agent-teams row has not granted consent (NULL by default).
    If a previous flaky test left consent stamped, this surfaces the leak."""
    resp = await client.get("/api/projects/active")
    assert resp.status_code == 200
    assert resp.json().get("auto_run_consent_at") is None


# Sentinel: ensure the active project is still agent-teams (defense-in-depth
# against a previous test failing to clean up its is_active swap).
@pytest.mark.asyncio
async def test_active_project_is_still_agent_teams(client) -> None:
    resp = await client.get("/api/projects/active")
    assert resp.status_code == 200
    assert resp.json()["name"] == "agent-teams"
