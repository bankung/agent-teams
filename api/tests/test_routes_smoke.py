"""HTTP-level contract tests for projects + tasks routers.

These run against the live FastAPI app via httpx.AsyncClient + ASGITransport
(see tests/conftest.py for the `client` fixture). They share the dev Postgres
that the seed populated — the `agent-teams` project (Phase 2b verified) is
expected to exist and be is_active=True.

Scope:
- Verify success-path response shape on the read endpoints we lean on (Lead
  calls /api/projects/by-name/{name} on every bootstrap; legacy
  /api/projects/active returns 410 Gone after Kanban #694 Phase 2).
- Lock in the *exact* 404 detail strings that backend's recent refactor moved
  through `get_or_404` — drift in those strings would silently change the
  error UX the FE will eventually render.
- Verify Pydantic validator errors travel through to the HTTP layer (422 with
  the expected message).
- Exercise the process_status -> timestamp lookup-dict (the refactor's other
  behavioral surface) end-to-end.
- Cover the soft-delete contract: list default-filter, ?include_deleted opt-in,
  DELETE 204, re-create after soft-delete, detail-returns-regardless, PATCH
  silently ignores soft-delete `status`.
- Cover the multi-domain `team` contract: required on POST, rejects unknown,
  novel scaffold creates the right roster.

Tests that create rows soft-delete them on the way out so the dev DB doesn't
balloon with stale data; the partial unique index also lets re-runs reuse the
same name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from src.constants import RecordStatus


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp from JSON. Handles trailing 'Z' (UTC) by
    rewriting to '+00:00' so `datetime.fromisoformat` accepts it on 3.10.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _unique_name(prefix: str) -> str:
    """Generate a name unlikely to collide with prior test runs.

    Soft-deleted rows free the name (partial unique on status=1), but using a
    fresh suffix per run keeps the test output readable when you query the DB
    by hand.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev", is_active: bool = False) -> dict:
    """Minimal valid POST /api/projects body."""
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": is_active,
        "team": team,
    }


# -----------------------------------------------------------------------------
# Projects — read-only assertions
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_project_returns_410_gone(client) -> None:
    """Regression: Kanban #694, Phase 2 — `/api/projects/active` is deprecated.

    Wire-lock: stable status_code (410) AND stable body shape. Callers must
    migrate to `/api/projects/by-name/{name}` or `/api/projects?status=1`.
    """
    resp = await client.get("/api/projects/active")
    assert resp.status_code == 410, resp.text
    assert resp.json() == {
        "detail": (
            "Endpoint deprecated. Use /api/projects/by-name/{name} or "
            "/api/projects?status=1 instead."
        )
    }


@pytest.mark.asyncio
async def test_get_active_project_410_detail_pinned_in_router_source() -> None:
    """Regression: Kanban #694 — source-text-lock per #122 pattern.

    The 410 detail string is wire contract — drift breaks any FE / shell that
    string-matches it. Lock by scanning `routers/projects.py` source for the
    exact substring."""
    from src.routers import projects as projects_router

    source = Path(projects_router.__file__).read_text(encoding="utf-8")
    pinned = (
        '"Endpoint deprecated. Use /api/projects/by-name/{name} or "\n'
        '            "/api/projects?status=1 instead."'
    )
    assert pinned in source, (
        f"410 detail string drifted in routers/projects.py — expected {pinned!r}"
    )


@pytest.mark.asyncio
async def test_get_seeded_agent_teams_by_name(client) -> None:
    """Replacement for the legacy `/api/projects/active` smoke test.

    The bootstrap convention (Kanban #694) is to look up the seeded project by
    name. Same shape assertions, new endpoint.
    """
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "agent-teams"
    assert body["is_active"] is True
    # ProjectRead shape sanity — these fields back the Lead bootstrap.
    for field in ("id", "paths_web", "paths_api", "paths_db", "config", "team"):
        assert field in body, f"missing {field} in ProjectRead body"
    # Backfill from the soft-delete-and-lead migration sets agent-teams to team='dev'
    # (renamed from lead by 0004_rename_lead_to_team).
    assert body["team"] == "dev"


@pytest.mark.asyncio
async def test_get_project_by_name_existing(client) -> None:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "agent-teams"


@pytest.mark.asyncio
async def test_get_project_by_name_404_exact_detail(client) -> None:
    """The 404 detail string is part of the contract (api-contracts.md L59)."""
    resp = await client.get("/api/projects/by-name/does-not-exist-xyz")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Project 'does-not-exist-xyz' not found"}


@pytest.mark.asyncio
async def test_get_project_by_id_returns_active_project(client) -> None:
    """Kanban #691: GET /api/projects/{id} parity with /by-name/{name}.

    Seeded `agent-teams` is id=1, is_active=True. ProjectRead shape sanity
    matches the by-name smoke test above.
    """
    resp = await client.get("/api/projects/1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == 1
    assert body["name"] == "agent-teams"
    assert body["team"] == "dev"
    assert body["is_active"] is True
    # ProjectRead shape sanity — same fields the by-name smoke test pins.
    for field in ("paths_web", "paths_api", "paths_db", "config", "auto_run_consent_at"):
        assert field in body, f"missing {field} in ProjectRead body"


@pytest.mark.asyncio
async def test_get_project_by_id_404_exact_detail(client) -> None:
    """Kanban #691: 404 detail is source-text-locked (per #122 pattern).

    Detail string mirrors PATCH /api/projects/{id} and POST /grant-consent
    byte-for-byte (`Project id=<n> not found`).
    """
    resp = await client.get("/api/projects/9999999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Project id=9999999 not found"}


@pytest.mark.asyncio
async def test_get_project_by_id_404_for_soft_deleted(
    client, scaffold_cleanup
) -> None:
    """Kanban #691: GET /{id} returns 404 on soft-deleted rows (active-only
    parity with /by-name/{name} and /grant-consent).

    Create a throwaway project, DELETE it (flips status=0), then GET /{id}
    must 404 with the same source-text-locked detail.
    """
    name = scaffold_cleanup(_unique_name("get-by-id-deleted"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    delete = await client.delete(f"/api/projects/{project_id}")
    assert delete.status_code == 204, delete.text

    resp = await client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": f"Project id={project_id} not found"}


@pytest.mark.asyncio
async def test_patch_project_404_exact_detail(client) -> None:
    """`get_or_404` on the PATCH path must surface "Project id=<n> not found"."""
    resp = await client.patch("/api/projects/9999999", json={})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Project id=9999999 not found"}


# -----------------------------------------------------------------------------
# Tasks — 404 contract on get/patch
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_404_exact_detail(client) -> None:
    # Kanban #695: header required even for 404 — gate fires at dependency
    # injection but `get_or_404` still raises 404 before the cross-check.
    resp = await client.get(
        "/api/tasks/9999999", headers={"X-Project-Id": "1"}
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=9999999 not found"}


@pytest.mark.asyncio
async def test_patch_task_404_exact_detail(client) -> None:
    resp = await client.patch(
        "/api/tasks/9999999", json={}, headers={"X-Project-Id": "1"}
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=9999999 not found"}


# -----------------------------------------------------------------------------
# Tasks — Pydantic validator surface (422 with stable message)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_invalid_process_status_returns_422_with_validator_message(
    client,
) -> None:
    """Validator error message is part of the contract — the FE will eventually
    parse `errors[].msg` to render inline form errors.

    N8: also assert `errors[].loc` includes the field path so a future schema
    rename to `process_status_v2` cannot pass this test by accident.
    """
    # Resolve the active project id dynamically — never hardcode `1`.
    active = await client.get("/api/projects/by-name/agent-teams")
    assert active.status_code == 200, active.text
    project_id = active.json()["id"]

    resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "smoke", "process_status": 99},
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    # FastAPI 422 envelope: {"detail": [{"loc": [...], "msg": "...", ...}, ...]}
    assert "detail" in body and isinstance(body["detail"], list)
    msgs = " | ".join(err["msg"] for err in body["detail"])
    assert "process_status must be one of (1, 2, 3, 4, 5, 6), got 99" in msgs
    # N8 — pin the field path so renames break the test.
    assert any(err["loc"] == ["body", "process_status"] for err in body["detail"]), (
        f"expected loc=['body','process_status'] in detail; got "
        f"{[err['loc'] for err in body['detail']]}"
    )


# -----------------------------------------------------------------------------
# Tasks — process_status -> timestamp lifecycle (the refactor's behavioral surface)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_process_status_transitions_stamp_lifecycle_timestamps(client) -> None:
    """Create -> PATCH to in_progress -> started_at filled -> PATCH to done ->
    completed_at filled.

    Exercises `_STATUS_TIMESTAMP_FIELDS` (the lookup dict introduced by the
    refactor). Hardcoded codes 2 (in_progress) and 5 (done) are pinned by
    standards/general.md — bumping them is a breaking schema change.

    The created row is soft-deleted on the way out so the dev DB stays clean.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    assert active.status_code == 200
    project_id = active.json()["id"]

    # 1. Create
    headers = {"X-Project-Id": str(project_id)}
    create_resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-smoke-lifecycle (test row, safe to delete)",
            "description": "Created by tests/test_routes_smoke.py — verifies process_status -> timestamp transitions.",
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    task = create_resp.json()
    task_id = task["id"]
    assert task["process_status"] == 1  # default TODO
    assert task["started_at"] is None
    assert task["completed_at"] is None

    # 2. -> in_progress should stamp started_at
    in_progress = await client.patch(
        f"/api/tasks/{task_id}", json={"process_status": 2}, headers=headers,
    )
    assert in_progress.status_code == 200, in_progress.text
    body = in_progress.json()
    assert body["process_status"] == 2
    assert body["started_at"] is not None, "in_progress transition must stamp started_at"
    assert body["completed_at"] is None
    started_at_snapshot = body["started_at"]

    # 3. -> done should stamp completed_at and leave started_at intact
    done = await client.patch(
        f"/api/tasks/{task_id}", json={"process_status": 5}, headers=headers
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["process_status"] == 5
    assert body["started_at"] == started_at_snapshot, (
        "completing a task must not overwrite started_at"
    )
    assert body["completed_at"] is not None, "done transition must stamp completed_at"

    # Cleanup: soft-delete the test row.
    cleanup = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert cleanup.status_code == 204


# -----------------------------------------------------------------------------
# Soft-delete — list default filter, ?include_deleted, DELETE, re-create
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_default_filters_active_only(client) -> None:
    """Default-filter `WHERE status=1` — soft-deleted rows are invisible
    unless `?include_deleted=true` is passed.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    # Create a task, then soft-delete it.
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "soft-delete-list-filter probe"},
        headers=headers,
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    delete = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert delete.status_code == 204

    # Default list — must NOT include the soft-deleted row. Kanban #695:
    # project scoping moved from `?project_id=` to the X-Project-Id header.
    default_list = await client.get("/api/tasks?limit=500", headers=headers)
    assert default_list.status_code == 200
    ids = {t["id"] for t in default_list.json()}
    assert task_id not in ids, "default list must hide soft-deleted rows"

    # ?include_deleted=true — must include it.
    with_deleted = await client.get(
        "/api/tasks?include_deleted=true&limit=500", headers=headers
    )
    assert with_deleted.status_code == 200
    ids_with_deleted = {t["id"] for t in with_deleted.json()}
    assert task_id in ids_with_deleted, "include_deleted=true must surface soft-deleted rows"


@pytest.mark.asyncio
async def test_get_task_returns_row_regardless_of_soft_delete_status(client) -> None:
    """Detail endpoints return the row even after soft-delete (per
    standards/postgresql/soft-delete.md — withholding by status surprises
    consumers calling restore flows).
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "detail-after-soft-delete"},
        headers=headers,
    )
    task_id = create.json()["id"]

    delete = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert delete.status_code == 204

    # Detail endpoint must still return the row.
    detail = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == task_id


@pytest.mark.asyncio
async def test_delete_task_returns_204_and_is_idempotent(client) -> None:
    """DELETE flips status=0 and returns 204. A second DELETE is a no-op (still 204)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "delete-idempotent probe"},
        headers=headers,
    )
    task_id = create.json()["id"]

    first = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert first.status_code == 204

    second = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert second.status_code == 204


@pytest.mark.asyncio
async def test_patch_task_silently_ignores_soft_delete_status_field(client) -> None:
    """Decision: PATCH `{"status": 0}` is silently ignored (Pydantic default
    `extra='ignore'` on TaskUpdate, which has no `status` field). This locks
    the choice — a future switch to 422 would require setting
    `model_config = ConfigDict(extra='forbid')` on TaskUpdate, which is a
    contract change tracked by this test.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "patch-ignore-status probe"},
        headers=headers,
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    # PATCH with `status: 0` in the body — should NOT soft-delete the task.
    resp = await client.patch(
        f"/api/tasks/{task_id}", json={"status": 0}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    # Detail still appears in the default list (i.e., status=1).
    listing = await client.get("/api/tasks?limit=500", headers=headers)
    ids = {t["id"] for t in listing.json()}
    assert task_id in ids, "PATCH {status:0} must NOT soft-delete (silent-ignore contract)"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_list_projects_default_filters_active_only(client, scaffold_cleanup) -> None:
    name = scaffold_cleanup(_unique_name("proj-list-filter"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    delete = await client.delete(f"/api/projects/{project_id}")
    assert delete.status_code == 204

    default_list = await client.get("/api/projects?limit=500")
    assert default_list.status_code == 200
    ids = {p["id"] for p in default_list.json()}
    assert project_id not in ids

    with_deleted = await client.get("/api/projects?include_deleted=true&limit=500")
    assert with_deleted.status_code == 200
    ids_with_deleted = {p["id"] for p in with_deleted.json()}
    assert project_id in ids_with_deleted


@pytest.mark.asyncio
async def test_delete_project_clears_is_active_when_previously_true(
    client, scaffold_cleanup
) -> None:
    """Deleting an active project flips is_active=false in the same txn.

    Originally guarded the partial unique index `ux_projects_active_one`
    (dropped in `0006_drop_active_one`, Kanban #694 Phase 2). The DELETE
    behavior — flip is_active=false alongside status=0 — is still preserved
    by the router as a defensive cleanup; this test now locks that behavior
    standalone (the index is gone but the cleanup remains).

    M7: the seeded agent-teams row's is_active is restored in `finally` so a
    failed assertion above the restore step does NOT leak state to subsequent
    tests (which all look up agent-teams by name).
    """
    name = scaffold_cleanup(_unique_name("proj-delete-active"))
    # Create as inactive first; toggling via PATCH below exercises the post-694
    # PATCH path (no atomic-clear of the seeded agent-teams row).
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201
    project_id = create.json()["id"]

    try:
        # Flip is_active=true via PATCH. Post-694 Phase 2: this no longer
        # touches other rows' is_active.
        activate = await client.patch(
            f"/api/projects/{project_id}", json={"is_active": True}
        )
        assert activate.status_code == 200, activate.text
        assert activate.json()["is_active"] is True

        # DELETE — should soft-delete AND clear is_active.
        delete = await client.delete(f"/api/projects/{project_id}")
        assert delete.status_code == 204

        # Re-list with include_deleted to verify both flags flipped.
        listing = await client.get("/api/projects?include_deleted=true&limit=500")
        rows = [p for p in listing.json() if p["id"] == project_id]
        assert len(rows) == 1
        assert rows[0]["is_active"] is False
    finally:
        # Restore agent-teams's is_active=true NO MATTER WHAT — many tests
        # assert on it via /api/projects/by-name/agent-teams.
        seeded = await client.get("/api/projects/by-name/agent-teams")
        if seeded.status_code == 404:
            all_rows = (
                await client.get("/api/projects?include_deleted=true&limit=500")
            ).json()
            seeded_id = next(p["id"] for p in all_rows if p["name"] == "agent-teams")
        else:
            seeded_id = seeded.json()["id"]
        await client.patch(f"/api/projects/{seeded_id}", json={"is_active": True})


@pytest.mark.asyncio
async def test_recreate_project_with_name_of_soft_deleted_one(
    client, scaffold_cleanup
) -> None:
    """Partial unique on `name` (status=1) lets a name be reused after soft-delete."""
    name = scaffold_cleanup(_unique_name("proj-recreate"))
    first = await client.post("/api/projects", json=_project_create_payload(name))
    assert first.status_code == 201
    first_id = first.json()["id"]

    delete = await client.delete(f"/api/projects/{first_id}")
    assert delete.status_code == 204

    # Re-create with the same name — must succeed (partial unique frees the slot).
    second = await client.post("/api/projects", json=_project_create_payload(name))
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert second_id != first_id

    # Cleanup
    await client.delete(f"/api/projects/{second_id}")


# -----------------------------------------------------------------------------
# Kanban #694 Phase 2 — session-scoped active: PATCH does not clear other rows
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setting_is_active_true_does_not_clear_others(
    client, scaffold_cleanup
) -> None:
    """Regression: Kanban #694, Phase 2.

    Pre-694 PATCH `{"is_active": true}` atomically cleared every other row's
    is_active (load-bearing on the now-dropped `ux_projects_active_one`
    partial unique index). Post-694 the side-effect is gone — multiple rows
    may legitimately carry `is_active=true` simultaneously because each
    Claude Code session binds to a project by name.

    Seed two throwaway projects, PATCH BOTH to is_active=true, then GET each
    via /api/projects/by-name/{name} and assert both still report
    is_active=true. Soft-delete both in `finally`.
    """
    name_a = scaffold_cleanup(_unique_name("proj-694a"))
    name_b = scaffold_cleanup(_unique_name("proj-694b"))

    create_a = await client.post("/api/projects", json=_project_create_payload(name_a))
    assert create_a.status_code == 201, create_a.text
    id_a = create_a.json()["id"]

    create_b = await client.post("/api/projects", json=_project_create_payload(name_b))
    assert create_b.status_code == 201, create_b.text
    id_b = create_b.json()["id"]

    try:
        # PATCH both to is_active=true. Pre-694 the second PATCH would have
        # silently cleared the first; post-694 both stick.
        patch_a = await client.patch(f"/api/projects/{id_a}", json={"is_active": True})
        assert patch_a.status_code == 200, patch_a.text
        assert patch_a.json()["is_active"] is True

        patch_b = await client.patch(f"/api/projects/{id_b}", json={"is_active": True})
        assert patch_b.status_code == 200, patch_b.text
        assert patch_b.json()["is_active"] is True

        # Re-fetch both via by-name to confirm BOTH are still active (the key
        # post-694 invariant — pre-694, this would have failed because the
        # second PATCH atomically cleared row a).
        check_a = await client.get(f"/api/projects/by-name/{name_a}")
        assert check_a.status_code == 200, check_a.text
        assert check_a.json()["is_active"] is True, (
            f"row a (id={id_a}) was silently cleared by row b's PATCH — "
            f"atomic-clear leaked back into update_project"
        )

        check_b = await client.get(f"/api/projects/by-name/{name_b}")
        assert check_b.status_code == 200, check_b.text
        assert check_b.json()["is_active"] is True

        # Bonus assertion — the seeded agent-teams row was also untouched.
        seeded = await client.get("/api/projects/by-name/agent-teams")
        assert seeded.status_code == 200
        assert seeded.json()["is_active"] is True, (
            "seeded agent-teams row's is_active was cleared by a throwaway "
            "PATCH — atomic-clear leaked back into update_project"
        )
    finally:
        await client.delete(f"/api/projects/{id_a}")
        await client.delete(f"/api/projects/{id_b}")


# -----------------------------------------------------------------------------
# Multi-domain team — POST validation + scaffold dispatch
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_project_requires_team_field(client) -> None:
    """`team` is required on ProjectCreate; missing it -> 422.

    No scaffold_cleanup needed — request is rejected at the schema layer before
    the scaffold side-effect runs.
    """
    payload = _project_create_payload(_unique_name("proj-missing-team"))
    payload.pop("team")
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_project_rejects_unknown_team(client) -> None:
    """Unknown team value -> 422 (Pydantic Literal rejects it).

    No scaffold_cleanup needed — request is rejected at the schema layer.
    """
    payload = _project_create_payload(_unique_name("proj-bad-team"), team="manager")
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_project_with_novel_team_scaffolds_novel_roster(
    client, scaffold_cleanup
) -> None:
    """`team='novel'` creates novel-writer + novel-editor folders, NOT dev-*.

    Resolves the on-disk path via settings.repo_root (same root the router uses).
    """
    from src.settings import get_settings

    settings = get_settings()
    repo_root = Path(settings.repo_root)

    name = scaffold_cleanup(_unique_name("proj-novel"))
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name, team="novel")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["team"] == "novel"
    project_id = body["id"]

    base = repo_root / "context" / "projects" / name
    assert (base / "novel-writer").is_dir(), "novel-writer folder missing"
    assert (base / "novel-editor").is_dir(), "novel-editor folder missing"
    assert not (base / "dev-frontend").exists(), "dev-frontend leaked into novel project"
    assert not (base / "dev-backend").exists(), "dev-backend leaked into novel project"

    # Cleanup the DB row (folder cleanup is best-effort — leaves the dir).
    await client.delete(f"/api/projects/{project_id}")


# -----------------------------------------------------------------------------
# M10 — PATCH cannot reactivate a soft-deleted project (contract locked here)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_cannot_reactivate_soft_deleted_project(
    client, scaffold_cleanup
) -> None:
    """Locked contract (decision 2026-05-08): PATCH may edit non-active fields
    on a soft-deleted project (admin edit), but PATCH `{"is_active": true}` on
    a soft-deleted row returns 400 with a stable detail string. Restore is a
    deferred admin path (separate endpoint when UI demands it).
    """
    name = scaffold_cleanup(_unique_name("proj-reactivate-deleted"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201
    project_id = create.json()["id"]

    # Soft-delete it.
    delete = await client.delete(f"/api/projects/{project_id}")
    assert delete.status_code == 204

    # PATCH is_active=true on the soft-deleted row → 400 with the locked detail.
    resp = await client.patch(f"/api/projects/{project_id}", json={"is_active": True})
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "detail": "Cannot activate a soft-deleted project — restore first"
    }

    # Sanity: editing a non-status field is still fine on a soft-deleted row.
    resp = await client.patch(
        f"/api/projects/{project_id}", json={"description": "edited after soft-delete"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "edited after soft-delete"


# -----------------------------------------------------------------------------
# Detail-string lock tests — pin the wire contract for 409/400 responses
# (review M4, M5, M9). Drift here is a breaking change to the FE error UX.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_project_409_detail_string_is_stable(
    client, scaffold_cleanup
) -> None:
    """M4 lock: PATCH /api/projects/{id} that conflicts on `name` returns 409
    with the exact detail `Project name '<name>' already exists` (repr-quoted).

    Uses a fresh pair so the seeded `agent-teams` row is never the conflicting
    target. Both projects are inactive so the active-flag clearing path is not
    exercised.
    """
    name_a = scaffold_cleanup(_unique_name("proj-409-a"))
    name_b = scaffold_cleanup(_unique_name("proj-409-b"))

    a = await client.post("/api/projects", json=_project_create_payload(name_a))
    assert a.status_code == 201, a.text
    b = await client.post("/api/projects", json=_project_create_payload(name_b))
    assert b.status_code == 201, b.text
    b_id = b.json()["id"]

    # Rename b → name_a (already taken by a, status=1) → 409 with locked detail.
    resp = await client.patch(f"/api/projects/{b_id}", json={"name": name_a})
    assert resp.status_code == 409, resp.text
    assert resp.json() == {
        "detail": f"Project name '{name_a}' already exists"
    }, resp.json()

    # Cleanup DB rows (folders handled by scaffold_cleanup).
    await client.delete(f"/api/projects/{a.json()['id']}")
    await client.delete(f"/api/projects/{b_id}")


def test_patch_task_400_detail_strings_are_pinned_in_router_source() -> None:
    """M5 lock: PATCH /api/tasks/{id} translates well-known DB CHECK violations
    to stable detail strings in `routers/tasks.py`. We can't drive these branches
    from the HTTP layer because the Pydantic validators on `TaskUpdate` already
    reject the same out-of-range integers at 422 — the IntegrityError handler is
    a defense-in-depth fallback for raw-SQL bypass / schema drift.

    Locking pattern: a textual assertion on the router source. Drift in any of
    these strings (rename / wording change) breaks the test. The strings are
    part of the wire contract once a future caller reaches the 400 branch
    (e.g., a script that bypasses Pydantic via SQLAlchemy core, or a constraint
    name that the validator doesn't yet cover).

    Strings pinned (must remain byte-for-byte stable per `routers/tasks.py`):
    - `"process_status violates ck_tasks_process_status_valid"`
    - `"priority violates ck_tasks_priority_valid"`
    - `"status violates ck_tasks_status_valid"`
    - `"Task update violates a database constraint"`  (fallback)
    """
    from pathlib import Path

    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")

    pinned = [
        '"process_status violates ck_tasks_process_status_valid"',
        '"priority violates ck_tasks_priority_valid"',
        '"status violates ck_tasks_status_valid"',
        '"Task update violates a database constraint"',
    ]
    missing = [s for s in pinned if s not in source]
    assert not missing, (
        "M5-locked detail strings drifted in routers/tasks.py — "
        f"missing: {missing}"
    )


# Regression: Kanban #120
@pytest.mark.asyncio
async def test_first_delete_bumps_updated_at_redelete_does_not_for_tasks(
    client, db_session
) -> None:
    """M9 lock (tasks), strengthened for Kanban #120 — mirrors the projects-side
    canonical at `test_first_delete_bumps_updated_at_redelete_does_not`.

    Three invariants:
      1. First DELETE bumps `updated_at` strictly forward from the create
         baseline (load-bearing — the original test passed vacuously while
         `delete_task` never bumped `updated_at` at all). The baseline is
         captured BEFORE the first DELETE so the `>` check actually probes the
         router-side `task.updated_at = func.now()` write, not a vacuous tie.
      2. Re-DELETE on an already-soft-deleted task is a true no-op:
         `updated_at` is unchanged between the post-first-DELETE snapshot and
         the post-second-DELETE snapshot (the early-return skip path holds).
      3. Audit-row count stays the same on re-DELETE — the no-op skip in
         `delete_task` must not write a redundant `'U'` row to `tasks_history`
         (preserved from the prior `test_redelete_task_does_not_grow_audit_history`).

    Reads `tasks_history` directly via db_session because there is no public
    endpoint for the audit table.
    """
    from sqlalchemy import text

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "m9-redelete-task probe"},
        headers=headers,
    )
    assert create.status_code == 201
    task_id = create.json()["id"]
    # Capture the create baseline BEFORE any DELETE — this is load-bearing.
    updated_at_at_create = _parse_ts(create.json()["updated_at"])

    # First DELETE — flips status=0; audit trigger writes one 'U' row AND the
    # router explicitly sets `task.updated_at = func.now()`.
    first = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert first.status_code == 204

    # Re-fetch the row to read updated_at (detail endpoint returns soft-deleted rows).
    detail = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    updated_at_after_first_delete = _parse_ts(detail.json()["updated_at"])

    # Snapshot history count after the legitimate first DELETE.
    count_after_first = await db_session.scalar(
        text("SELECT COUNT(*) FROM tasks_history WHERE task_id = :tid"),
        {"tid": task_id},
    )
    assert count_after_first >= 1, (
        f"expected at least one audit row after first DELETE, got {count_after_first}"
    )

    # Invariant 1: first DELETE must advance updated_at past the create baseline.
    assert updated_at_after_first_delete > updated_at_at_create, (
        f"first DELETE did not bump updated_at: "
        f"create={updated_at_at_create.isoformat()} "
        f"after_first_delete={updated_at_after_first_delete.isoformat()}"
    )

    # Second DELETE — should be a no-op.
    second = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert second.status_code == 204

    detail2 = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert detail2.status_code == 200, detail2.text
    updated_at_after_second_delete = _parse_ts(detail2.json()["updated_at"])

    count_after_second = await db_session.scalar(
        text("SELECT COUNT(*) FROM tasks_history WHERE task_id = :tid"),
        {"tid": task_id},
    )

    # Invariant 2: re-DELETE is a true no-op for updated_at.
    assert updated_at_after_second_delete == updated_at_after_first_delete, (
        f"re-DELETE on a soft-deleted task mutated updated_at: "
        f"{updated_at_after_first_delete.isoformat()} → "
        f"{updated_at_after_second_delete.isoformat()} (the no-op skip is broken)"
    )

    # Invariant 3: audit row count must NOT grow on re-DELETE.
    assert count_after_second == count_after_first, (
        f"re-DELETE on a soft-deleted task wrote a redundant audit row: "
        f"{count_after_first} → {count_after_second}"
    )


# Regression: Kanban #76
@pytest.mark.asyncio
async def test_first_delete_bumps_updated_at_redelete_does_not(client, scaffold_cleanup) -> None:
    """M9 lock (projects), strengthened.

    Three invariants:
      1. First DELETE bumps `updated_at` strictly forward from the create
         baseline (load-bearing — the original test missed this and passed
         vacuously while the underlying code never bumped `updated_at` at all).
      2. Second DELETE on an already-soft-deleted project is a no-op:
         `updated_at` is unchanged between the post-first-DELETE snapshot and
         the post-second-DELETE snapshot (the early-return skip path holds).
      3. After both DELETEs the row is observable via `?include_deleted=true`
         AND absent from the default (active-only) list — proxy for
         `status == RecordStatus.DELETED` since `ProjectRead` does not surface
         the SMALLINT `status` column. (`RecordStatus.DELETED` imported so a
         future schema change that exposes `status` will be a one-line patch.)
    """
    name = scaffold_cleanup(_unique_name("proj-redelete"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201
    project_id = create.json()["id"]
    updated_at_at_create = _parse_ts(create.json()["updated_at"])

    # First DELETE.
    first = await client.delete(f"/api/projects/{project_id}")
    assert first.status_code == 204

    # Snapshot the row state after the first DELETE.
    listing = await client.get("/api/projects?include_deleted=true&limit=500")
    rows = [p for p in listing.json() if p["id"] == project_id]
    assert len(rows) == 1, (
        f"row missing from include_deleted listing after first DELETE "
        f"(expected exactly one DELETED row, got {len(rows)})"
    )
    updated_at_after_first_delete = _parse_ts(rows[0]["updated_at"])

    # Sanity: row must NOT show in the default (active-only) listing — proxy
    # for status == RecordStatus.DELETED.
    active_listing = await client.get("/api/projects?limit=500")
    assert not any(p["id"] == project_id for p in active_listing.json()), (
        f"DELETED project id={project_id} still showing in default listing "
        f"(status proxy: expected RecordStatus.DELETED={RecordStatus.DELETED})"
    )

    # Invariant 1: first DELETE must advance updated_at past the create baseline.
    assert updated_at_after_first_delete > updated_at_at_create, (
        f"first DELETE did not bump updated_at: "
        f"create={updated_at_at_create.isoformat()} "
        f"after_first_delete={updated_at_after_first_delete.isoformat()}"
    )

    # Second DELETE — must be a no-op (skip path returns 204 without UPDATE).
    second = await client.delete(f"/api/projects/{project_id}")
    assert second.status_code == 204

    # Re-fetch and assert updated_at did NOT advance further.
    listing = await client.get("/api/projects?include_deleted=true&limit=500")
    rows = [p for p in listing.json() if p["id"] == project_id]
    assert len(rows) == 1
    updated_at_after_second_delete = _parse_ts(rows[0]["updated_at"])

    # Status sanity (proxy via default listing): row still excluded post-re-DELETE.
    active_listing = await client.get("/api/projects?limit=500")
    assert not any(p["id"] == project_id for p in active_listing.json()), (
        f"row reappeared in default listing after re-DELETE (status proxy broke; "
        f"expected RecordStatus.DELETED={RecordStatus.DELETED})"
    )

    # Invariant 2: re-DELETE is a true no-op for updated_at.
    assert updated_at_after_second_delete == updated_at_after_first_delete, (
        f"re-DELETE on a soft-deleted project mutated updated_at: "
        f"{updated_at_after_first_delete.isoformat()} → "
        f"{updated_at_after_second_delete.isoformat()} (the no-op skip is broken)"
    )


# Regression: Kanban #76 — sibling positive lock for the M9 invariant;
# without this the no-op skip could silently regress.
@pytest.mark.asyncio
async def test_patch_project_updated_at_advances_on_real_change_and_no_op_skips(
    client, scaffold_cleanup
) -> None:
    """PATCH /api/projects/{id} parity with tasks:
      1. PATCH with a real change advances `updated_at` past the create baseline.
      2. PATCH with the identical body is a no-op — `updated_at` does NOT advance.
      3. PATCH with a second real change advances `updated_at` again.
      4. None of the three PATCHes mutate `created_at`.
    """
    name = scaffold_cleanup(_unique_name("proj-patch-updated-at"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201
    project_id = create.json()["id"]
    updated_at_at_create = _parse_ts(create.json()["updated_at"])
    created_at_at_create = _parse_ts(create.json()["created_at"])

    # 1) Real change — should bump updated_at.
    first_patch = await client.patch(
        f"/api/projects/{project_id}",
        json={"description": "first real change"},
    )
    assert first_patch.status_code == 200, first_patch.text
    updated_at_after_patch = _parse_ts(first_patch.json()["updated_at"])
    assert updated_at_after_patch > updated_at_at_create, (
        f"PATCH with a real change did not advance updated_at: "
        f"create={updated_at_at_create.isoformat()} "
        f"after_patch={updated_at_after_patch.isoformat()}"
    )

    # 2) Identical body — N7 no-op skip should hold updated_at steady.
    second_patch = await client.patch(
        f"/api/projects/{project_id}",
        json={"description": "first real change"},
    )
    assert second_patch.status_code == 200, second_patch.text
    updated_at_after_noop = _parse_ts(second_patch.json()["updated_at"])
    assert updated_at_after_noop == updated_at_after_patch, (
        f"PATCH with an identical body bumped updated_at (no-op skip broken): "
        f"{updated_at_after_patch.isoformat()} → {updated_at_after_noop.isoformat()}"
    )

    # 3) Second real change — should bump again.
    third_patch = await client.patch(
        f"/api/projects/{project_id}",
        json={"description": "second real change"},
    )
    assert third_patch.status_code == 200, third_patch.text
    updated_at_after_second_change = _parse_ts(third_patch.json()["updated_at"])
    assert updated_at_after_second_change > updated_at_after_noop, (
        f"second real-change PATCH did not advance updated_at: "
        f"prev={updated_at_after_noop.isoformat()} "
        f"after={updated_at_after_second_change.isoformat()}"
    )

    # 4) created_at must never move on PATCH.
    for label, resp in (
        ("first_patch", first_patch),
        ("second_patch", second_patch),
        ("third_patch", third_patch),
    ):
        created_at_seen = _parse_ts(resp.json()["created_at"])
        assert created_at_seen == created_at_at_create, (
            f"PATCH ({label}) mutated created_at: "
            f"{created_at_at_create.isoformat()} → {created_at_seen.isoformat()}"
        )


# Regression: Kanban #120 — sibling positive lock for the N7 invariant on tasks.
# Mirrors the projects-side canonical
# `test_patch_project_updated_at_advances_on_real_change_and_no_op_skips`.
@pytest.mark.asyncio
async def test_patch_task_updated_at_advances_on_real_change_and_no_op_skips(
    client,
) -> None:
    """PATCH /api/tasks/{id} parity with projects:
      1. PATCH with a real change (priority 1 → 3) advances `updated_at` past
         the create baseline.
      2. PATCH with the identical body (priority 3 again) is a no-op —
         `updated_at` does NOT advance.
      3. PATCH with a second real change (priority 3 → 4) advances
         `updated_at` again past the post-no-op snapshot.
      4. None of the three PATCHes mutate `created_at`.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "patch-updated-at-task probe",
            "priority": 1,
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    updated_at_at_create = _parse_ts(create.json()["updated_at"])
    created_at_at_create = _parse_ts(create.json()["created_at"])

    try:
        # 1) Real change — should bump updated_at.
        first_patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"priority": 3},
            headers=headers,
        )
        assert first_patch.status_code == 200, first_patch.text
        assert first_patch.json()["priority"] == 3
        updated_at_after_patch = _parse_ts(first_patch.json()["updated_at"])
        assert updated_at_after_patch > updated_at_at_create, (
            f"PATCH with a real change did not advance updated_at: "
            f"create={updated_at_at_create.isoformat()} "
            f"after_patch={updated_at_after_patch.isoformat()}"
        )

        # 2) Identical body — N7 no-op skip should hold updated_at steady.
        second_patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"priority": 3},
            headers=headers,
        )
        assert second_patch.status_code == 200, second_patch.text
        updated_at_after_noop = _parse_ts(second_patch.json()["updated_at"])
        assert updated_at_after_noop == updated_at_after_patch, (
            f"PATCH with an identical body bumped updated_at (no-op skip broken): "
            f"{updated_at_after_patch.isoformat()} → {updated_at_after_noop.isoformat()}"
        )

        # 3) Second real change — should bump again.
        third_patch = await client.patch(
            f"/api/tasks/{task_id}",
            json={"priority": 4},
            headers=headers,
        )
        assert third_patch.status_code == 200, third_patch.text
        assert third_patch.json()["priority"] == 4
        updated_at_after_second_change = _parse_ts(third_patch.json()["updated_at"])
        assert updated_at_after_second_change > updated_at_after_noop, (
            f"second real-change PATCH did not advance updated_at: "
            f"prev={updated_at_after_noop.isoformat()} "
            f"after={updated_at_after_second_change.isoformat()}"
        )

        # 4) created_at must never move on PATCH.
        for label, resp in (
            ("first_patch", first_patch),
            ("second_patch", second_patch),
            ("third_patch", third_patch),
        ):
            created_at_seen = _parse_ts(resp.json()["created_at"])
            assert created_at_seen == created_at_at_create, (
                f"PATCH ({label}) mutated created_at: "
                f"{created_at_at_create.isoformat()} → {created_at_seen.isoformat()}"
            )
    finally:
        # Soft-delete the test row.
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# Kanban #121 — projects.name path-traversal hardening
#
# Two-layer defence:
#   Layer 1 — Pydantic schema regex on `ProjectCreate.name` / `ProjectUpdate.name`
#             (api/src/schemas/project.py:53,76). HTTP requests with a malicious
#             name short-circuit at 422 before the router or scaffold runs.
#   Layer 2 — `scaffold_project_folder` defense-in-depth: forbidden-token guard
#             + `is_relative_to(projects_root)` resolved-path guard, both
#             returning False (not raising). Catches anything bypassing Pydantic.
# -----------------------------------------------------------------------------


# Regression: Kanban #121
@pytest.mark.asyncio
async def test_post_project_rejects_path_traversal_names(client) -> None:
    """Layer 1 lock — POST /api/projects with malicious `name` → 422.

    Each rejection asserts the Pydantic 422 envelope identifies the `name`
    field via `errors[].loc == ['body', 'name']` so a future schema rename
    can't pass this test by accident (mirrors the N8 pattern on
    `process_status`).

    Cases:
      - "../evil"          (parent-dir token)
      - "proj/sub"         (forward slash)
      - "proj\\sub"        (backslash)
      - "proj with space"  (disallowed character — space)
      - "proj.name"        (disallowed character — dot)
      - "a" * 65           (exceeds 64-char max from the regex)

    No scaffold_cleanup needed — request is rejected at the schema layer
    before the scaffold side-effect runs.
    """
    bad_names = [
        "../evil",
        "proj/sub",
        "proj\\sub",
        "proj with space",
        "proj.name",
        "a" * 65,
    ]
    for bad in bad_names:
        payload = _project_create_payload(bad)
        resp = await client.post("/api/projects", json=payload)
        assert resp.status_code == 422, (
            f"name={bad!r} expected 422, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "detail" in body and isinstance(body["detail"], list), (
            f"name={bad!r}: malformed 422 envelope: {body!r}"
        )
        assert any(err["loc"] == ["body", "name"] for err in body["detail"]), (
            f"name={bad!r}: expected loc=['body','name'] in detail; "
            f"got {[err['loc'] for err in body['detail']]}"
        )


# Regression: Kanban #121
@pytest.mark.asyncio
async def test_patch_project_rejects_path_traversal_names(client) -> None:
    """Layer 1 lock — PATCH /api/projects/{id} with malicious `name` → 422.

    Targets the seeded `agent-teams` project (id resolved dynamically via
    /api/projects/by-name/agent-teams). `ProjectUpdate.name` carries the same regex as
    `ProjectCreate.name` (schemas/project.py:76) — drift breaks this test.

    Only one representative malicious name is exercised here; the create-side
    test already covers the full charset matrix. The point of this test is to
    confirm the PATCH path also enforces the regex (no silent contract gap
    between POST and PATCH).
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    assert active.status_code == 200, active.text
    project_id = active.json()["id"]

    resp = await client.patch(
        f"/api/projects/{project_id}", json={"name": "../evil"}
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    assert any(err["loc"] == ["body", "name"] for err in body["detail"]), (
        f"expected loc=['body','name'] in PATCH 422 detail; "
        f"got {[err['loc'] for err in body['detail']]}"
    )


# Regression: Kanban #121
def test_scaffold_service_rejects_traversal_directly() -> None:
    """Layer 2 lock — defense-in-depth: `scaffold_project_folder` rejects
    malicious project names directly (without going through the HTTP layer).

    For each malicious case:
      - Returns False (NOT raises — caller treats the row commit as truth and
        keeps going; an exception would roll back unrelated work).
      - No directory is created at the dangerous resolved path.

    Cases pinned:
      - "../evil-bf-<uniq>"    (parent-dir token — would resolve OUTSIDE
                                <repo_root>/context/projects/, caught by both
                                the forbidden-token short-circuit and the
                                `is_relative_to` resolved-path guard)
      - "evil-<uniq>/sub"      (forward slash — caught by forbidden-token guard)
      - "evil-<uniq>\x00null"  (NUL byte — caught by forbidden-token guard;
                                also breaks Path() on most platforms but the
                                guard fires first)

    Each case uses a unique uuid suffix so this test is repeatable without
    cross-contamination from prior fail-before runs (which DO create the
    scaffold output on pre-fix code — that's the whole point of the test).
    Each dangerous path is also pre-removed via `shutil.rmtree(...,
    ignore_errors=True)` before the call so a leaked dir from a previous
    failed run cannot make this test pass vacuously.

    Mirrors the existing test convention of importing repo_root via
    `src.settings.get_settings()` (see
    `test_post_project_with_novel_team_scaffolds_novel_roster`).
    """
    import shutil
    import uuid

    from src.services.project_scaffold import scaffold_project_folder
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    projects_root = repo_root / "context" / "projects"
    uniq = uuid.uuid4().hex[:8]

    bad_cases = [
        (
            f"../evil-bf-{uniq}",
            repo_root / "context" / f"evil-bf-{uniq}",
        ),
        (
            f"evil-{uniq}/sub",
            projects_root / f"evil-{uniq}",
        ),
        (
            f"evil-{uniq}\x00null",
            projects_root / f"evil-{uniq}\x00null",
        ),
    ]
    try:
        for project_name, dangerous_path in bad_cases:
            # Pre-clean any stale dir at the dangerous path so a leaked dir
            # from a prior fail-before run can't mask a regression.
            try:
                if dangerous_path.exists():
                    shutil.rmtree(dangerous_path, ignore_errors=True)
            except (OSError, ValueError):
                pass

            result = scaffold_project_folder(
                repo_root=repo_root, project_name=project_name, team="dev"
            )
            assert result is False, (
                f"scaffold_project_folder({project_name!r}) returned {result!r}; "
                f"expected False (defense-in-depth must reject without raising)"
            )
            # Verify the dangerous path was NOT created. Path.exists() raises
            # on NUL on some platforms, so guard with try/except — the guard
            # rejecting before any mkdir is the load-bearing assertion above.
            try:
                existed = dangerous_path.exists()
            except (OSError, ValueError):
                existed = False
            assert not existed, (
                f"scaffold_project_folder({project_name!r}) created "
                f"{dangerous_path!s} despite returning False"
            )
    finally:
        # Defensive cleanup if an earlier assertion raised AFTER a scaffold
        # somehow succeeded (shouldn't happen post-fix, but keeps the working
        # tree clean if this test ever flakes).
        for _name, dangerous_path in bad_cases:
            try:
                if dangerous_path.exists():
                    shutil.rmtree(dangerous_path, ignore_errors=True)
            except (OSError, ValueError):
                pass


# Regression: Kanban #122
def test_post_task_400_detail_strings_are_pinned_in_router_source() -> None:
    """M5-style lock for POST /api/tasks: `create_task` translates well-known DB
    constraint names (FK + 3 CHECKs) to stable detail strings in
    `routers/tasks.py`. Mirrors `test_patch_task_400_detail_strings_are_pinned_in_router_source`.

    The CHECK branches are unreachable from HTTP because Pydantic validators on
    `TaskCreate` reject the same out-of-range integers at 422 first — the
    IntegrityError handler is defense-in-depth for raw-SQL bypass / future
    schema drift. The FK branch (`tasks_project_id_fkey`) IS reachable via the
    HTTP wire (any non-existent project_id passes Pydantic but fails at the SQL
    layer); test_post_task_returns_stable_detail_on_fk_violation locks that path.

    Strings pinned (must remain byte-for-byte stable per `routers/tasks.py`):
    - constraint name `tasks_project_id_fkey` AND f-string template
      `"project_id {payload.project_id} does not exist"`
    - constraint name `ck_tasks_process_status_valid` AND
      `"process_status violates ck_tasks_process_status_valid"`
    - constraint name `ck_tasks_priority_valid` AND
      `"priority violates ck_tasks_priority_valid"`
    - constraint name `ck_tasks_status_valid` AND
      `"status violates ck_tasks_status_valid"`
    - fallback `"Task creation violates a database constraint"`

    Drift in any of these strings (rename / wording change / removed branch)
    breaks the test — the wire contract stays auditable from source.
    """
    from pathlib import Path

    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")

    # Pair each constraint name with its stable detail string. Both must appear
    # verbatim in the create_task block — checking the constraint name alone
    # would miss a regression that left the `if "..." in orig_text` arm but
    # rewrote the detail string, and checking the detail alone would miss a
    # regression that dropped the `if`-branch entirely.
    pinned_pairs = [
        ('"tasks_project_id_fkey"', '"project_id {payload.project_id} does not exist"'),
        (
            '"ck_tasks_process_status_valid"',
            '"process_status violates ck_tasks_process_status_valid"',
        ),
        ('"ck_tasks_priority_valid"', '"priority violates ck_tasks_priority_valid"'),
        ('"ck_tasks_status_valid"', '"status violates ck_tasks_status_valid"'),
    ]
    fallback = '"Task creation violates a database constraint"'

    missing: list[str] = []
    for constraint, detail in pinned_pairs:
        # The `tasks_project_id_fkey` membership check is `"tasks_project_id_fkey" in orig_text`
        # — strip the surrounding quotes for the source-text scan since the source
        # has the literal string with quotes around it.
        if constraint not in source:
            missing.append(f"constraint name {constraint}")
        if detail not in source:
            missing.append(f"detail string {detail}")
    if fallback not in source:
        missing.append(f"fallback {fallback}")

    assert not missing, (
        "Kanban #122 POST /api/tasks 400 detail strings drifted in "
        f"routers/tasks.py — missing: {missing}"
    )


# Regression: Kanban #122
@pytest.mark.asyncio
async def test_post_task_returns_stable_detail_on_fk_violation(client) -> None:
    """SECURITY-WARN lock for Kanban #122: POST /api/tasks with a non-existent
    project_id surfaces a stable, hygienic 400 detail string instead of leaking
    the raw asyncpg ForeignKeyViolationError text (class name, internal DETAIL
    line, constraint name).

    Pre-fix wire shape on the FK branch was:
        "<class 'asyncpg.exceptions.ForeignKeyViolationError'>: insert or update
        on table \"tasks\" violates foreign key constraint
        \"tasks_project_id_fkey\"\\nDETAIL:  Key (project_id)=(99999999) is not
        present in table \"projects\"."
    — i.e., `detail=str(exc.orig)`. Post-fix shape is the byte-stable
    `"project_id 99999999 does not exist"` (matches the f-string template
    pinned by the source-text test above).

    Status code must be 400 (NOT 422 — Pydantic accepts any positive int as
    project_id, so the FK violation only surfaces post-Pydantic at the SQL
    commit layer).
    """
    bogus_project_id = 99999999
    # Kanban #695: header must match body to reach the FK-violation branch
    # (body-vs-header mismatch fires earlier with a different 400 detail).
    resp = await client.post(
        "/api/tasks",
        json={"project_id": bogus_project_id, "title": "kanban-122-fk-probe"},
        headers={"X-Project-Id": str(bogus_project_id)},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body == {"detail": f"project_id {bogus_project_id} does not exist"}, (
        f"Kanban #122: detail string drifted from the f-string template — "
        f"got {body!r}"
    )


# -----------------------------------------------------------------------------
# Kanban #238 — tasks.parent_task_id + subtask API support
#
# 8 contract tests for the locked design (2026-05-08):
#   (a) POST parent + child happy path
#   (b) POST cross-project parent rejection (400 stable detail)
#   (c) DB-level CHECK rejects self-parent
#   (d) PATCH parent_task_id → 422 (Pydantic model_validator)
#   (e) DELETE parent with active children → 409 (with fail-before/pass-after demo)
#   (f) DELETE parent after all children soft-deleted → 204
#   (g) GET ?parent_task_id=N filters to direct children
#   (h) GET ?top_level_only=true filters to parent_task_id IS NULL
# -----------------------------------------------------------------------------


# Regression: Kanban #238 (a)
@pytest.mark.asyncio
async def test_post_parent_and_child_round_trip(client) -> None:
    """Happy path: create a parent, then a child referring to parent.id; both
    succeed (201) and TaskRead exposes parent_task_id correctly on each."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    parent_resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-a parent probe"},
        headers=headers,
    )
    assert parent_resp.status_code == 201, parent_resp.text
    parent = parent_resp.json()
    assert parent["parent_task_id"] is None, "top-level task must have parent_task_id=None"
    parent_id = parent["id"]

    try:
        child_resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k238-a child probe",
                "parent_task_id": parent_id,
            },
            headers=headers,
        )
        assert child_resp.status_code == 201, child_resp.text
        child = child_resp.json()
        assert child["parent_task_id"] == parent_id, (
            f"parent_task_id did not round-trip: expected {parent_id} got "
            f"{child['parent_task_id']!r}"
        )
        # Cleanup child first (parent has active child otherwise → 409).
        await client.delete(f"/api/tasks/{child['id']}", headers=headers)
    finally:
        await client.delete(f"/api/tasks/{parent_id}", headers=headers)


# Regression: Kanban #238 (b)
@pytest.mark.asyncio
async def test_post_child_rejects_cross_project_parent(
    client, scaffold_cleanup
) -> None:
    """Parent in project A, child claiming project B but pointing at parent →
    400 with the locked detail string."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_a_id = active.json()["id"]

    # Create an inactive sibling project for the cross-project parent.
    name_b = scaffold_cleanup(_unique_name("k238-b-proj"))
    proj_b_resp = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    assert proj_b_resp.status_code == 201, proj_b_resp.text
    project_b_id = proj_b_resp.json()["id"]

    headers_a = {"X-Project-Id": str(project_a_id)}
    headers_b = {"X-Project-Id": str(project_b_id)}
    parent_resp = await client.post(
        "/api/tasks",
        json={"project_id": project_a_id, "title": "k238-b parent in A"},
        headers=headers_a,
    )
    assert parent_resp.status_code == 201
    parent_id = parent_resp.json()["id"]

    try:
        # Body claims B → header must also be B (Kanban #695). Parent is in A,
        # so the cross-project parent rejection still fires AFTER the header
        # gate passes (header == body == B).
        bad_child = await client.post(
            "/api/tasks",
            json={
                "project_id": project_b_id,  # B
                "title": "k238-b child claiming B but referencing parent in A",
                "parent_task_id": parent_id,
            },
            headers=headers_b,
        )
        assert bad_child.status_code == 400, bad_child.text
        assert bad_child.json() == {
            "detail": f"parent_task_id {parent_id} belongs to a different project"
        }, bad_child.json()
    finally:
        await client.delete(f"/api/tasks/{parent_id}", headers=headers_a)
        await client.delete(f"/api/projects/{project_b_id}")


# Regression: Kanban #238 (c)
@pytest.mark.asyncio
async def test_db_check_rejects_self_parent(client, db_session) -> None:
    """Defense-in-depth: the DB CHECK ck_tasks_parent_task_id_not_self rejects
    raw-SQL drift that bypasses the app's PATCH-422 guard. The app code path
    can't actually self-parent (id is autoassigned post-INSERT), so we hit the
    DB layer directly with an UPDATE."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-c self-parent probe"},
        headers=headers,
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    try:
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.execute(
                text("UPDATE tasks SET parent_task_id = :tid WHERE id = :tid"),
                {"tid": task_id},
            )
            await db_session.commit()
        await db_session.rollback()
        assert "ck_tasks_parent_task_id_not_self" in str(excinfo.value), (
            f"expected ck_tasks_parent_task_id_not_self in IntegrityError; got "
            f"{excinfo.value!s}"
        )
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# Regression: Kanban #238 (d)
@pytest.mark.asyncio
async def test_patch_parent_task_id_rejected_with_422(client) -> None:
    """Re-parenting is not supported in V1 — PATCH parent_task_id → 422 with a
    Pydantic validation error mentioning the field. We don't pin the exact
    message text (Pydantic versions tweak wording); we pin the field path +
    a substring of the locked message."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    parent_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-d parent"},
        headers=headers,
    )
    assert parent_create.status_code == 201
    parent_id = parent_create.json()["id"]

    target_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-d target"},
        headers=headers,
    )
    assert target_create.status_code == 201
    target_id = target_create.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"parent_task_id": parent_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert "detail" in body and isinstance(body["detail"], list)
        msgs = " | ".join(err["msg"] for err in body["detail"])
        assert "parent_task_id" in msgs, (
            f"expected 'parent_task_id' in 422 envelope msgs; got {msgs!r}"
        )
        # Sanity: the row was NOT mutated.
        detail = await client.get(f"/api/tasks/{target_id}", headers=headers)
        assert detail.json()["parent_task_id"] is None

        # Explicit-null PATCH must ALSO 422 — locked semantic uses
        # model_fields_set so {"parent_task_id": None} is rejected the same
        # as {"parent_task_id": <int>}. Guards against a future maintainer
        # relaxing the validator to `if self.parent_task_id is not None:`
        # which would silently let the null case through. (Kanban #238 W1.)
        resp_null = await client.patch(
            f"/api/tasks/{target_id}",
            json={"parent_task_id": None},
            headers=headers,
        )
        assert resp_null.status_code == 422, resp_null.text
        msgs_null = " | ".join(err["msg"] for err in resp_null.json()["detail"])
        assert "parent_task_id" in msgs_null, (
            f"explicit-null PATCH must also 422; got {msgs_null!r}"
        )
    finally:
        await client.delete(f"/api/tasks/{target_id}", headers=headers)
        await client.delete(f"/api/tasks/{parent_id}", headers=headers)


# Regression: Kanban #238 (e) — fail-before / pass-after demo target
@pytest.mark.asyncio
async def test_delete_parent_with_active_children_returns_409(client) -> None:
    """Locked behaviour: soft-deleting a parent with at least one active child
    is blocked with 409 + the stable detail string. Verify parent.status stays
    1 (active) and the child also stays 1.

    This is the most consequential rule from #238 — pinned with both the wire
    contract (status + detail) and the DB-state invariant. The fail-before /
    pass-after demo for this test is captured in the dev-backend session log.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    parent_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-e parent"},
        headers=headers,
    )
    assert parent_create.status_code == 201
    parent_id = parent_create.json()["id"]

    child_create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k238-e child",
            "parent_task_id": parent_id,
        },
        headers=headers,
    )
    assert child_create.status_code == 201
    child_id = child_create.json()["id"]

    try:
        block = await client.delete(f"/api/tasks/{parent_id}", headers=headers)
        assert block.status_code == 409, block.text
        assert block.json() == {
            "detail": "Cannot delete task — 1 active subtask(s) reference this task"
        }, block.json()

        # Parent still active in the default listing (status=1 proxy).
        listing = await client.get("/api/tasks?limit=500", headers=headers)
        ids = {t["id"] for t in listing.json()}
        assert parent_id in ids, "parent must still be active after blocked DELETE"
        assert child_id in ids, "child must still be active after blocked DELETE"
    finally:
        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{parent_id}", headers=headers)


# Regression: Kanban #238 (f)
@pytest.mark.asyncio
async def test_delete_parent_succeeds_after_children_soft_deleted(client) -> None:
    """Once every child is soft-deleted (status=0), DELETE parent → 204 and
    parent.status flips to 0."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    parent_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-f parent"},
        headers=headers,
    )
    parent_id = parent_create.json()["id"]
    child_create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k238-f child",
            "parent_task_id": parent_id,
        },
        headers=headers,
    )
    child_id = child_create.json()["id"]

    # Soft-delete child first.
    delete_child = await client.delete(f"/api/tasks/{child_id}", headers=headers)
    assert delete_child.status_code == 204

    # Now parent DELETE should succeed.
    delete_parent = await client.delete(f"/api/tasks/{parent_id}", headers=headers)
    assert delete_parent.status_code == 204, delete_parent.text

    # Verify parent is gone from default listing (status=1 filter).
    listing = await client.get("/api/tasks?limit=500", headers=headers)
    ids = {t["id"] for t in listing.json()}
    assert parent_id not in ids, "parent must be soft-deleted (status=0) after success"


# Regression: Kanban #238 (g)
@pytest.mark.asyncio
async def test_list_tasks_filters_by_parent_task_id(client) -> None:
    """`?parent_task_id=N` returns only direct children of N (and excludes N itself,
    plus unrelated rows in the same project)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    parent_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-g parent"},
        headers=headers,
    )
    parent_id = parent_create.json()["id"]
    other_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-g unrelated top-level"},
        headers=headers,
    )
    other_id = other_create.json()["id"]

    child_a = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k238-g child A",
            "parent_task_id": parent_id,
        },
        headers=headers,
    )
    child_a_id = child_a.json()["id"]
    child_b = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k238-g child B",
            "parent_task_id": parent_id,
        },
        headers=headers,
    )
    child_b_id = child_b.json()["id"]

    try:
        resp = await client.get(
            f"/api/tasks?parent_task_id={parent_id}&limit=500",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert ids == {child_a_id, child_b_id}, (
            f"expected exactly {{child_a={child_a_id}, child_b={child_b_id}}}; "
            f"got {ids!r}"
        )
        # Each row's parent_task_id round-trips correctly.
        for t in resp.json():
            assert t["parent_task_id"] == parent_id
    finally:
        await client.delete(f"/api/tasks/{child_a_id}", headers=headers)
        await client.delete(f"/api/tasks/{child_b_id}", headers=headers)
        await client.delete(f"/api/tasks/{parent_id}", headers=headers)
        await client.delete(f"/api/tasks/{other_id}", headers=headers)


# Regression: Kanban #238 (h)
@pytest.mark.asyncio
async def test_list_tasks_top_level_only_filter(client) -> None:
    """`?top_level_only=true` returns only rows with parent_task_id IS NULL —
    children of a parent must be excluded."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    headers = {"X-Project-Id": str(project_id)}
    parent_create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k238-h parent"},
        headers=headers,
    )
    parent_id = parent_create.json()["id"]
    child_create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k238-h child",
            "parent_task_id": parent_id,
        },
        headers=headers,
    )
    child_id = child_create.json()["id"]

    try:
        resp = await client.get(
            "/api/tasks?top_level_only=true&limit=500",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        # Parent must appear; child must not.
        ids = {t["id"] for t in rows}
        assert parent_id in ids, "parent (top-level) must appear when top_level_only=true"
        assert child_id not in ids, (
            "child (parent_task_id is set) must NOT appear when top_level_only=true"
        )
        # Every returned row genuinely has parent_task_id IS NULL.
        for t in rows:
            assert t["parent_task_id"] is None, (
                f"top_level_only=true returned a row with parent_task_id={t['parent_task_id']!r}"
            )
    finally:
        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{parent_id}", headers=headers)


# Regression: Kanban #238 — source-text pin for the new POST/DELETE detail strings
def test_kanban_238_detail_strings_are_pinned_in_router_source() -> None:
    """Mirrors the M5 / #122 source-text-pin pattern. The new POST 400 strings
    on the parent-validation branch and the DELETE 409 string for blocked
    parent-with-children are part of the wire contract; drift breaks the FE
    error UX. Pin them here so a wording change requires a deliberate test
    update.
    """
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")

    pinned = [
        '"parent_task_id {payload.parent_task_id} does not exist or is deleted"',
        '"parent_task_id {payload.parent_task_id} belongs to a different project"',
        '"Cannot delete task — {active_children_count} active subtask(s) reference this task"',
    ]
    missing = [s for s in pinned if s not in source]
    assert not missing, (
        "Kanban #238 detail strings drifted in routers/tasks.py — "
        f"missing: {missing}"
    )


# -----------------------------------------------------------------------------
# Kanban #697 — `?pending=true` shortcut for "list non-done tasks"
# -----------------------------------------------------------------------------
#
# Convenience query param for the Lead bootstrap workflow. Lead frequently
# wants "all tasks except done"; without this shortcut, each Lead session
# has to either issue 4 separate `?process_status=N` calls or post-filter in
# Python. `?pending=true` filters `process_status != 5` (TaskStatus.DONE)
# server-side. Precedence rule: when BOTH `pending=true` and
# `process_status=N` are supplied, the explicit `process_status` wins
# (more specific) — `pending` is silently ignored. The router uses
# `elif pending:` so precedence is enforced by control flow, not boolean
# arithmetic.


# Regression: Kanban #697
@pytest.mark.asyncio
async def test_list_tasks_pending_true_excludes_done(client) -> None:
    """`?pending=true` returns only rows with process_status != 5 (DONE).

    Seed one task per process_status (1..5) and confirm the shortcut returns
    exactly the four non-done rows.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    created_ids: dict[int, int] = {}
    try:
        for ps in (1, 2, 3, 4, 5):
            create = await client.post(
                "/api/tasks",
                json={
                    "project_id": project_id,
                    "title": f"k697-pending probe ps={ps}",
                    "process_status": ps,
                },
                headers=headers,
            )
            assert create.status_code == 201, create.text
            created_ids[ps] = create.json()["id"]

        resp = await client.get("/api/tasks?pending=true&limit=500", headers=headers)
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = {t["id"] for t in rows}
        # The four non-done seeded rows must be present.
        for ps in (1, 2, 3, 4):
            assert created_ids[ps] in ids, (
                f"pending=true must include process_status={ps} row "
                f"id={created_ids[ps]}"
            )
        # The done row must NOT be present.
        assert created_ids[5] not in ids, (
            f"pending=true must exclude the done row id={created_ids[5]}"
        )
        # Every returned row genuinely has process_status != 5.
        for t in rows:
            assert t["process_status"] != 5, (
                f"pending=true returned a row with process_status="
                f"{t['process_status']!r} id={t['id']}"
            )
    finally:
        for tid in created_ids.values():
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #697
@pytest.mark.asyncio
async def test_list_tasks_pending_false_default_unchanged(client) -> None:
    """Without `pending` (default false), all five seeded rows are returned —
    locks the default-behavior preservation: the new param is opt-in and
    must not change existing list semantics.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    created_ids: dict[int, int] = {}
    try:
        for ps in (1, 2, 3, 4, 5):
            create = await client.post(
                "/api/tasks",
                json={
                    "project_id": project_id,
                    "title": f"k697-default probe ps={ps}",
                    "process_status": ps,
                },
                headers=headers,
            )
            assert create.status_code == 201, create.text
            created_ids[ps] = create.json()["id"]

        # Without `pending` — all five must come back.
        resp = await client.get("/api/tasks?limit=500", headers=headers)
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        for ps in (1, 2, 3, 4, 5):
            assert created_ids[ps] in ids, (
                f"default list (no pending param) must include process_status={ps} "
                f"row id={created_ids[ps]}"
            )
    finally:
        for tid in created_ids.values():
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #697
@pytest.mark.asyncio
async def test_list_tasks_pending_and_process_status_explicit_wins(client) -> None:
    """`?pending=true&process_status=5` returns the done row — locks the
    precedence rule. Explicit `process_status` is more specific and silently
    overrides `pending`. Enforced by `elif pending:` in the router.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    created_ids: dict[int, int] = {}
    try:
        for ps in (1, 2, 3, 4, 5):
            create = await client.post(
                "/api/tasks",
                json={
                    "project_id": project_id,
                    "title": f"k697-precedence probe ps={ps}",
                    "process_status": ps,
                },
                headers=headers,
            )
            assert create.status_code == 201, create.text
            created_ids[ps] = create.json()["id"]

        # Explicit process_status=5 must win over pending=true.
        resp = await client.get(
            "/api/tasks?pending=true&process_status=5&limit=500",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = {t["id"] for t in rows}
        # The done row MUST be present (explicit wins).
        assert created_ids[5] in ids, (
            "explicit process_status=5 must win over pending=true "
            f"(done row id={created_ids[5]} missing)"
        )
        # Every returned row has process_status == 5.
        for t in rows:
            assert t["process_status"] == 5, (
                f"explicit process_status=5 returned a row with process_status="
                f"{t['process_status']!r} id={t['id']}"
            )
        # The non-done rows must NOT be present.
        for ps in (1, 2, 3, 4):
            assert created_ids[ps] not in ids, (
                f"explicit process_status=5 must exclude process_status={ps} "
                f"row id={created_ids[ps]}"
            )
    finally:
        for tid in created_ids.values():
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #697
@pytest.mark.asyncio
async def test_list_tasks_pending_composes_with_assigned_role(client) -> None:
    """`?pending=true&assigned_role=2` returns only the backend non-done rows
    — locks composability with the existing `assigned_role` filter.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    # Seed 4 rows: backend-todo, backend-done, frontend-todo, frontend-done.
    # Only backend-todo should match `pending=true&assigned_role=2`.
    created: list[tuple[str, int]] = []
    try:
        be_todo = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-compose backend todo",
                "process_status": 1,
                "assigned_role": 2,
            },
            headers=headers,
        )
        assert be_todo.status_code == 201, be_todo.text
        be_todo_id = be_todo.json()["id"]
        created.append(("be_todo", be_todo_id))

        be_done = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-compose backend done",
                "process_status": 5,
                "assigned_role": 2,
            },
            headers=headers,
        )
        assert be_done.status_code == 201, be_done.text
        be_done_id = be_done.json()["id"]
        created.append(("be_done", be_done_id))

        fe_todo = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-compose frontend todo",
                "process_status": 1,
                "assigned_role": 1,
            },
            headers=headers,
        )
        assert fe_todo.status_code == 201, fe_todo.text
        fe_todo_id = fe_todo.json()["id"]
        created.append(("fe_todo", fe_todo_id))

        fe_done = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-compose frontend done",
                "process_status": 5,
                "assigned_role": 1,
            },
            headers=headers,
        )
        assert fe_done.status_code == 201, fe_done.text
        fe_done_id = fe_done.json()["id"]
        created.append(("fe_done", fe_done_id))

        resp = await client.get(
            "/api/tasks?pending=true&assigned_role=2&limit=500",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = {t["id"] for t in rows}
        assert be_todo_id in ids, (
            f"pending=true&assigned_role=2 must include backend-todo id={be_todo_id}"
        )
        assert be_done_id not in ids, (
            f"pending=true&assigned_role=2 must EXCLUDE backend-done id={be_done_id}"
        )
        assert fe_todo_id not in ids, (
            f"pending=true&assigned_role=2 must EXCLUDE frontend-todo id={fe_todo_id}"
        )
        assert fe_done_id not in ids, (
            f"pending=true&assigned_role=2 must EXCLUDE frontend-done id={fe_done_id}"
        )
        # Every returned row has assigned_role==2 and process_status!=5.
        for t in rows:
            assert t["assigned_role"] == 2, (
                f"pending+assigned_role=2 returned a row with assigned_role="
                f"{t['assigned_role']!r} id={t['id']}"
            )
            assert t["process_status"] != 5, (
                f"pending+assigned_role=2 returned a done row id={t['id']}"
            )
    finally:
        for _label, tid in created:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #697
@pytest.mark.asyncio
async def test_list_tasks_pending_with_top_level_only(client) -> None:
    """`?pending=true&top_level_only=true` composes correctly: returns only
    parent_task_id IS NULL rows that are also non-done. One probe is enough
    to lock the `pending`-clause-doesn't-clobber-other-where-clauses guarantee.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    parent_id: int | None = None
    child_id: int | None = None
    done_top_id: int | None = None
    try:
        parent_create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-toplevel parent (todo)",
                "process_status": 1,
            },
            headers=headers,
        )
        assert parent_create.status_code == 201, parent_create.text
        parent_id = parent_create.json()["id"]

        child_create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-toplevel child (todo)",
                "process_status": 1,
                "parent_task_id": parent_id,
            },
            headers=headers,
        )
        assert child_create.status_code == 201, child_create.text
        child_id = child_create.json()["id"]

        done_top_create = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k697-toplevel done top-level",
                "process_status": 5,
            },
            headers=headers,
        )
        assert done_top_create.status_code == 201, done_top_create.text
        done_top_id = done_top_create.json()["id"]

        resp = await client.get(
            "/api/tasks?pending=true&top_level_only=true&limit=500",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = {t["id"] for t in rows}

        # parent_id (top-level + todo) MUST be present.
        assert parent_id in ids, (
            f"pending=true&top_level_only=true must include "
            f"top-level-todo id={parent_id}"
        )
        # child (has parent_task_id) MUST be excluded by top_level_only.
        assert child_id not in ids, (
            f"top_level_only=true must exclude subtask id={child_id} "
            "even when it's pending"
        )
        # done top-level MUST be excluded by pending.
        assert done_top_id not in ids, (
            f"pending=true must exclude done top-level id={done_top_id}"
        )
        # Every returned row has parent_task_id IS NULL and process_status != 5.
        for t in rows:
            assert t["parent_task_id"] is None, (
                "top_level_only=true returned a row with parent_task_id="
                f"{t['parent_task_id']!r} id={t['id']}"
            )
            assert t["process_status"] != 5, (
                f"pending=true returned a done row id={t['id']}"
            )
    finally:
        if child_id is not None:
            await client.delete(f"/api/tasks/{child_id}", headers=headers)
        if parent_id is not None:
            await client.delete(f"/api/tasks/{parent_id}", headers=headers)
        if done_top_id is not None:
            await client.delete(f"/api/tasks/{done_top_id}", headers=headers)


# =============================================================================
# Kanban #777 — working_path / working_repo / agent_overrides
# =============================================================================
# These tests cover the three new optional ProjectCreate / ProjectUpdate /
# ProjectRead fields introduced by Kanban #777:
#   - working_path:     nullable TEXT, single project-root path on host
#   - working_repo:     nullable TEXT, free-form repo URL or path
#   - agent_overrides:  JSONB DEFAULT '{}', dict[str, "haiku"|"sonnet"|"opus"]
# Values for agent_overrides are constrained by `AgentModelLiteral`; keys are
# free-form (forward-compat with #774/#775/#779/#780 roles not yet wired).


@pytest.mark.asyncio
async def test_create_project_with_working_path_and_repo(
    client, scaffold_cleanup
) -> None:
    """POST /api/projects echoes working_path + working_repo on the response."""
    name = scaffold_cleanup(_unique_name("proj-777-paths"))
    payload = _project_create_payload(name)
    payload["working_path"] = "/tmp/foo"
    payload["working_repo"] = "https://github.com/user/repo.git"

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["working_path"] == "/tmp/foo"
    assert body["working_repo"] == "https://github.com/user/repo.git"


@pytest.mark.asyncio
async def test_create_project_without_new_fields_defaults_correctly(
    client, scaffold_cleanup
) -> None:
    """POST without the 3 new fields → working_path/repo are None, overrides {}.

    Lock the DB-default contract: agent_overrides falls back to '{}'::jsonb
    via server_default when the field is absent in the request body.
    """
    name = scaffold_cleanup(_unique_name("proj-777-defaults"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["working_path"] is None
    assert body["working_repo"] is None
    assert body["agent_overrides"] == {}


@pytest.mark.asyncio
async def test_create_project_with_agent_overrides(
    client, scaffold_cleanup
) -> None:
    """POST with a populated agent_overrides dict → echoed back verbatim."""
    name = scaffold_cleanup(_unique_name("proj-777-overrides"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {
        "dev-analyst": "sonnet",
        "dev-spec-reviewer": "sonnet",
    }

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["agent_overrides"] == {
        "dev-analyst": "sonnet",
        "dev-spec-reviewer": "sonnet",
    }


@pytest.mark.asyncio
async def test_create_project_rejects_invalid_model_value(
    client, scaffold_cleanup
) -> None:
    """POST with agent_overrides value outside the haiku/sonnet/opus literal → 422.

    Locks the AgentModelLiteral Pydantic enforcement at the request boundary.
    The loc path is ["body", "agent_overrides", "<key>"] because Pydantic
    descends into dict values when validating literal constraints. The error
    type is `literal_error`.
    """
    name = scaffold_cleanup(_unique_name("proj-777-bad-model"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"dev-analyst": "claude-3"}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    matching = [
        err
        for err in body["detail"]
        if err["loc"] == ["body", "agent_overrides", "dev-analyst"]
    ]
    assert matching, (
        f"expected loc=['body','agent_overrides','dev-analyst'] in 422 detail; "
        f"got {[err['loc'] for err in body['detail']]}"
    )
    assert matching[0]["type"] == "literal_error", (
        f"expected type='literal_error'; got {matching[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_create_project_rejects_empty_working_path(
    client, scaffold_cleanup
) -> None:
    """POST with working_path="" → 422 via min_length=1 Field constraint."""
    name = scaffold_cleanup(_unique_name("proj-777-empty-path"))
    payload = _project_create_payload(name)
    payload["working_path"] = ""

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    matching = [
        err for err in body["detail"] if err["loc"] == ["body", "working_path"]
    ]
    assert matching, (
        f"expected loc=['body','working_path'] in 422 detail; "
        f"got {[err['loc'] for err in body['detail']]}"
    )
    # Pydantic v2 reports the min_length violation with type='string_too_short'.
    assert matching[0]["type"] == "string_too_short", (
        f"expected type='string_too_short'; got {matching[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_patch_project_sets_working_path(
    client, scaffold_cleanup
) -> None:
    """PATCH with {"working_path": "/new/path"} → 200 + working_path updated,
    other new fields untouched.
    """
    name = scaffold_cleanup(_unique_name("proj-777-patch-set"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    patch = await client.patch(
        f"/api/projects/{project_id}", json={"working_path": "/new/path"}
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["working_path"] == "/new/path"
    # Other new fields untouched.
    assert body["working_repo"] is None
    assert body["agent_overrides"] == {}


@pytest.mark.asyncio
async def test_patch_project_unsets_working_path_via_null(
    client, scaffold_cleanup
) -> None:
    """PATCH with {"working_path": null} → 200 + working_path becomes None.

    Verifies the null-clears-field contract on the new optional fields:
    explicit `null` in the JSON body is treated as "clear", consistent with
    description / stack_* on ProjectUpdate.
    """
    name = scaffold_cleanup(_unique_name("proj-777-patch-null"))
    payload = _project_create_payload(name)
    payload["working_path"] = "/tmp/initial"
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    assert create.json()["working_path"] == "/tmp/initial"
    project_id = create.json()["id"]

    patch = await client.patch(
        f"/api/projects/{project_id}", json={"working_path": None}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["working_path"] is None


@pytest.mark.asyncio
async def test_patch_project_unsets_working_repo_via_null(
    client, scaffold_cleanup
) -> None:
    """PATCH with {"working_repo": null} → 200 + working_repo becomes None.

    Sibling of test_patch_project_unsets_working_path_via_null (Kanban #777
    WARN-2) — pins the explicit-null clears-field contract for working_repo too.
    """
    name = scaffold_cleanup(_unique_name("proj-777-patch-null-repo"))
    payload = _project_create_payload(name)
    payload["working_repo"] = "https://example.com/initial.git"
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    assert create.json()["working_repo"] == "https://example.com/initial.git"
    project_id = create.json()["id"]

    patch = await client.patch(
        f"/api/projects/{project_id}", json={"working_repo": None}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["working_repo"] is None


@pytest.mark.asyncio
async def test_patch_project_agent_overrides_replace_semantics(
    client, scaffold_cleanup
) -> None:
    """PATCH with a new agent_overrides dict REPLACES the prior dict (no merge).

    Contract: agent_overrides PATCH is full-replace semantics, NOT deep-merge.
    Start with {"a": "haiku"}, PATCH with {"b": "sonnet"} → result is exactly
    {"b": "sonnet"} (key "a" gone). Lock this so a future "merge" refactor
    can't silently change the wire contract.
    """
    name = scaffold_cleanup(_unique_name("proj-777-patch-replace"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"a": "haiku"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    assert create.json()["agent_overrides"] == {"a": "haiku"}
    project_id = create.json()["id"]

    patch = await client.patch(
        f"/api/projects/{project_id}",
        json={"agent_overrides": {"b": "sonnet"}},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["agent_overrides"] == {"b": "sonnet"}


@pytest.mark.asyncio
async def test_patch_project_omitted_fields_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH that touches only `name` leaves working_path/repo/overrides alone.

    Locks the `exclude_unset=True` behavior — fields absent in the PATCH body
    must not be re-written (key invariant: PATCH is partial-update, never
    a full-replace).
    """
    name = scaffold_cleanup(_unique_name("proj-777-omit"))
    payload = _project_create_payload(name)
    payload["working_path"] = "/keep/me"
    payload["working_repo"] = "https://example.com/repo.git"
    payload["agent_overrides"] = {"dev-analyst": "opus"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    new_name = scaffold_cleanup(_unique_name("proj-777-omit-renamed"))
    patch = await client.patch(
        f"/api/projects/{project_id}", json={"name": new_name}
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    # Explicit safety-net: a regression to model_dump() (without exclude_unset)
    # would null-clear these because their Pydantic default is None.
    assert body["name"] == new_name
    assert body["working_path"] == "/keep/me"
    assert body["working_repo"] == "https://example.com/repo.git"
    assert body["agent_overrides"] == {"dev-analyst": "opus"}


@pytest.mark.asyncio
async def test_get_project_by_name_returns_new_fields(
    client, scaffold_cleanup
) -> None:
    """GET /api/projects/by-name/<name> response body carries all 3 new keys.

    Verifies ProjectRead exposes working_path / working_repo / agent_overrides
    on the read path used by Lead bootstrap and external integrations.
    """
    name = scaffold_cleanup(_unique_name("proj-777-by-name"))
    payload = _project_create_payload(name)
    payload["working_path"] = "/some/path"
    payload["working_repo"] = "git@github.com:user/x.git"
    payload["agent_overrides"] = {"dev-backend": "sonnet"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text

    resp = await client.get(f"/api/projects/by-name/{name}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for field in ("working_path", "working_repo", "agent_overrides"):
        assert field in body, f"missing {field!r} in ProjectRead body"
    assert body["working_path"] == "/some/path"
    assert body["working_repo"] == "git@github.com:user/x.git"
    assert body["agent_overrides"] == {"dev-backend": "sonnet"}


# -----------------------------------------------------------------------------
# Kanban #777 — tester edge-case pass
# -----------------------------------------------------------------------------
# Independent edge-case + integration coverage on top of the BE author's 10
# happy-path / contract tests above. Focus: pathological inputs, PATCH-null vs
# PATCH-empty-dict semantics, whitespace, soft-delete cross-row leakage,
# list-endpoint field surfacing, by-id parity, and scaffolding side-effect
# independence from working_path.


@pytest.mark.asyncio
async def test_777_edge_agent_overrides_rejects_empty_key(
    client, scaffold_cleanup
) -> None:
    """POST agent_overrides with empty-string key → 422.

    Kanban #777 WARN-4: keys must match ^[a-zA-Z0-9_-]{1,64}$ — empty string
    fails on min-length-1. Pydantic surfaces the failure as a value_error
    on the agent_overrides field.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-emptykey"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"": "haiku"}
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert any(
        err["loc"] == ["body", "agent_overrides"] for err in body["detail"]
    ), f"expected loc=['body','agent_overrides'] in 422 detail; got {body['detail']!r}"


@pytest.mark.asyncio
async def test_777_edge_agent_overrides_rejects_long_key(
    client, scaffold_cleanup
) -> None:
    """POST agent_overrides with a 65-char key → 422.

    Kanban #777 WARN-4: keys must match ^[a-zA-Z0-9_-]{1,64}$ — 65 chars
    exceeds the cap by one. Pydantic surfaces as a value_error whose message
    contains the regex pattern.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-longkey"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"a" * 65: "haiku"}
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matching = [
        err for err in body["detail"]
        if err["loc"] == ["body", "agent_overrides"]
    ]
    assert matching, (
        f"expected loc=['body','agent_overrides'] in 422 detail; "
        f"got {body['detail']!r}"
    )
    assert matching[0]["type"] == "value_error"
    assert "[a-zA-Z0-9_-]" in matching[0]["msg"]


@pytest.mark.asyncio
async def test_777_edge_patch_agent_overrides_empty_dict_clears(
    client, scaffold_cleanup
) -> None:
    """PATCH {"agent_overrides": {}} → result is exactly {} (cleared-to-empty).

    Distinct from PATCH null (next test). Locks the wire contract that empty
    dict and null are NOT collapsed by the server — they round-trip as the
    caller sent them.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-patch-empty"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"dev-analyst": "opus"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    assert create.json()["agent_overrides"] == {"dev-analyst": "opus"}

    patch = await client.patch(
        f"/api/projects/{project_id}", json={"agent_overrides": {}}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["agent_overrides"] == {}

    # Verify on a subsequent GET (round-trip).
    get_resp = await client.get(f"/api/projects/by-name/{name}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["agent_overrides"] == {}


@pytest.mark.asyncio
async def test_777_edge_patch_agent_overrides_null_clears_to_empty_dict(
    client, scaffold_cleanup
) -> None:
    """PATCH {"agent_overrides": null} → response is exactly {}.

    Kanban #777 WARN-1 Option A: the router transforms an explicit-null PATCH
    on agent_overrides into an empty-dict UPDATE, so the wire contract is locked
    — response (and subsequent GET) MUST be `{}`, never `None`, never a SQL-NULL
    that surfaces as `None` to Pydantic. Contract is in the test name.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-patch-null"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"dev-analyst": "opus"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    patch = await client.patch(
        f"/api/projects/{project_id}", json={"agent_overrides": None}
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["agent_overrides"] == {}, (
        f"agent_overrides after null-PATCH must be {{}} (WARN-1 Option A); "
        f"got {body['agent_overrides']!r}"
    )

    # Round-trip via GET — same value as PATCH echoed.
    get_resp = await client.get(f"/api/projects/by-name/{name}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["agent_overrides"] == {}


@pytest.mark.asyncio
async def test_777_edge_patch_omitting_agent_overrides_preserves_existing(
    client, scaffold_cleanup
) -> None:
    """PATCH that does NOT include `agent_overrides` leaves the prior value alone.

    Mirrors test_patch_project_omitted_fields_unchanged but specifically pins
    that the partial-update contract applies to agent_overrides (Pydantic
    `exclude_unset=True`). Touch `description` only; assert overrides intact.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-patch-omit"))
    payload = _project_create_payload(name)
    payload["agent_overrides"] = {"dev-analyst": "haiku", "dev-backend": "sonnet"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    patch = await client.patch(
        f"/api/projects/{project_id}", json={"description": "updated description"}
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["description"] == "updated description"
    assert body["agent_overrides"] == {
        "dev-analyst": "haiku",
        "dev-backend": "sonnet",
    }


@pytest.mark.asyncio
async def test_777_edge_whitespace_only_working_path_accepted(
    client, scaffold_cleanup
) -> None:
    """POST with working_path='   ' (3 spaces) is ACCEPTED.

    `min_length=1` on a Pydantic str field does NOT strip whitespace before
    counting, so a 3-space string passes the constraint. Document actual
    behavior — the contract is "non-empty string", NOT "non-blank string".
    Same for working_repo.

    If product later wants a non-blank contract, this test becomes the
    inflection point — flag as a test-NIT under OBSERVATION in the report.
    """
    # working_path = "   "
    name1 = scaffold_cleanup(_unique_name("proj-777-edge-ws-path"))
    payload = _project_create_payload(name1)
    payload["working_path"] = "   "
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["working_path"] == "   "

    # working_repo = "   "
    name2 = scaffold_cleanup(_unique_name("proj-777-edge-ws-repo"))
    payload = _project_create_payload(name2)
    payload["working_repo"] = "   "
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["working_repo"] == "   "


@pytest.mark.asyncio
async def test_777_edge_very_long_working_path_accepted(
    client, scaffold_cleanup
) -> None:
    """POST with working_path of 100k chars (TEXT has no length cap).

    Verifies the field is genuinely uncapped at both Pydantic AND DB layers,
    and the value round-trips intact through serialization + JSONB-adjacent
    storage paths.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-long-path"))
    big = "x" * 100000
    payload = _project_create_payload(name)
    payload["working_path"] = big
    payload["working_repo"] = big  # same big value, both fields

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["working_path"] == big
    assert body["working_repo"] == big
    assert len(body["working_path"]) == 100000
    assert len(body["working_repo"]) == 100000

    # Round-trip via GET — same intact value.
    get_resp = await client.get(f"/api/projects/by-name/{name}")
    assert get_resp.status_code == 200, get_resp.text
    assert len(get_resp.json()["working_path"]) == 100000
    assert get_resp.json()["working_path"] == big


@pytest.mark.asyncio
async def test_777_edge_soft_delete_recreate_isolates_working_path(
    client, scaffold_cleanup
) -> None:
    """Re-create after soft-delete keeps each row's working_path independent.

    Partial unique on (name) where status=1 frees the name slot after DELETE.
    The new row's working_path must be ITS payload's value; the old (status=0)
    row keeps its original working_path. No cross-row leakage via shared name.

    Verify via `/api/projects?include_deleted=1` to fetch the soft-deleted row
    and assert its working_path is unchanged.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-recreate"))

    # Project A — working_path="/a"
    payload_a = _project_create_payload(name)
    payload_a["working_path"] = "/a"
    create_a = await client.post("/api/projects", json=payload_a)
    assert create_a.status_code == 201, create_a.text
    id_a = create_a.json()["id"]
    assert create_a.json()["working_path"] == "/a"

    # Soft-delete A.
    delete_a = await client.delete(f"/api/projects/{id_a}")
    assert delete_a.status_code == 204

    # Re-create with the SAME name, working_path="/b".
    payload_b = _project_create_payload(name)
    payload_b["working_path"] = "/b"
    create_b = await client.post("/api/projects", json=payload_b)
    assert create_b.status_code == 201, create_b.text
    id_b = create_b.json()["id"]
    assert id_b != id_a
    assert create_b.json()["working_path"] == "/b"

    # Fetch via list w/ include_deleted to confirm A's working_path stayed "/a".
    list_resp = await client.get("/api/projects?include_deleted=1")
    assert list_resp.status_code == 200, list_resp.text
    by_id = {row["id"]: row for row in list_resp.json()}
    assert id_a in by_id, f"soft-deleted id={id_a} not in include_deleted list"
    assert id_b in by_id, f"recreated id={id_b} not in include_deleted list"
    assert by_id[id_a]["working_path"] == "/a", (
        f"soft-deleted row should retain working_path='/a'; "
        f"got {by_id[id_a]['working_path']!r}"
    )
    assert by_id[id_b]["working_path"] == "/b"

    # Clean up — soft-delete the live B row.
    await client.delete(f"/api/projects/{id_b}")


@pytest.mark.asyncio
async def test_777_edge_list_projects_includes_new_fields(
    client, scaffold_cleanup
) -> None:
    """GET /api/projects (list) surfaces working_path / working_repo /
    agent_overrides for every row.

    ProjectRead is the response_model on list, so the new fields propagate
    automatically — but a list-level test is the only place this is actually
    wired end-to-end. Locks that the list serializer doesn't drop the new
    keys (e.g., via a stale projection).
    """
    name_x = scaffold_cleanup(_unique_name("proj-777-edge-list-x"))
    name_y = scaffold_cleanup(_unique_name("proj-777-edge-list-y"))

    payload_x = _project_create_payload(name_x)
    payload_x["working_path"] = "/list/x"
    payload_x["agent_overrides"] = {"dev-analyst": "haiku"}
    create_x = await client.post("/api/projects", json=payload_x)
    assert create_x.status_code == 201, create_x.text
    id_x = create_x.json()["id"]

    payload_y = _project_create_payload(name_y)
    payload_y["working_path"] = "/list/y"
    payload_y["working_repo"] = "https://example.com/y.git"
    create_y = await client.post("/api/projects", json=payload_y)
    assert create_y.status_code == 201, create_y.text
    id_y = create_y.json()["id"]

    try:
        list_resp = await client.get("/api/projects?status=1")
        assert list_resp.status_code == 200, list_resp.text
        by_id = {row["id"]: row for row in list_resp.json()}

        assert id_x in by_id, f"created id={id_x} missing from active list"
        assert id_y in by_id, f"created id={id_y} missing from active list"

        # Both rows carry all three new keys.
        for field in ("working_path", "working_repo", "agent_overrides"):
            assert field in by_id[id_x], f"list row id={id_x} missing {field!r}"
            assert field in by_id[id_y], f"list row id={id_y} missing {field!r}"

        # Values match what we posted (no cross-row contamination on the list).
        assert by_id[id_x]["working_path"] == "/list/x"
        assert by_id[id_x]["working_repo"] is None
        assert by_id[id_x]["agent_overrides"] == {"dev-analyst": "haiku"}

        assert by_id[id_y]["working_path"] == "/list/y"
        assert by_id[id_y]["working_repo"] == "https://example.com/y.git"
        assert by_id[id_y]["agent_overrides"] == {}
    finally:
        await client.delete(f"/api/projects/{id_x}")
        await client.delete(f"/api/projects/{id_y}")


@pytest.mark.asyncio
async def test_777_edge_get_by_id_parity_with_by_name(
    client, scaffold_cleanup
) -> None:
    """GET /api/projects/{id} returns identical new-field values to
    GET /api/projects/by-name/{name} for the same row.

    Catches any divergence where one read path projects different columns or
    applies a different serializer.
    """
    name = scaffold_cleanup(_unique_name("proj-777-edge-byid-parity"))
    payload = _project_create_payload(name)
    payload["working_path"] = "/parity/path"
    payload["working_repo"] = "https://example.com/parity.git"
    payload["agent_overrides"] = {"dev-frontend": "sonnet", "dev-backend": "opus"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]

    try:
        by_id_resp = await client.get(f"/api/projects/{project_id}")
        assert by_id_resp.status_code == 200, by_id_resp.text
        by_id_body = by_id_resp.json()

        by_name_resp = await client.get(f"/api/projects/by-name/{name}")
        assert by_name_resp.status_code == 200, by_name_resp.text
        by_name_body = by_name_resp.json()

        # All 3 new fields present + equal across both read paths.
        for field in ("working_path", "working_repo", "agent_overrides"):
            assert field in by_id_body, f"by-id missing {field!r}"
            assert field in by_name_body, f"by-name missing {field!r}"
            assert by_id_body[field] == by_name_body[field], (
                f"{field} differs: by-id={by_id_body[field]!r} "
                f"by-name={by_name_body[field]!r}"
            )

        # Values match what we POSTed (sanity).
        assert by_id_body["working_path"] == "/parity/path"
        assert by_id_body["working_repo"] == "https://example.com/parity.git"
        assert by_id_body["agent_overrides"] == {
            "dev-frontend": "sonnet",
            "dev-backend": "opus",
        }
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_777_edge_scaffold_uses_repo_root_not_working_path(
    client, scaffold_cleanup
) -> None:
    """POST with working_path set still scaffolds under settings.repo_root.

    Regression-guard: scaffold_project_folder(repo_root, name, team) signature
    has NEVER taken working_path — it builds the on-disk role folders under
    `<repo_root>/context/projects/<name>/` regardless of working_path. A
    future refactor that re-routes scaffolding to working_path would silently
    leak directories outside the repo. Lock the current behavior.

    Assert: `<repo_root>/context/projects/<name>/` exists after POST. We don't
    assert the working_path target does NOT exist (it's a fake `/nonsense/...`
    path — `os.path.exists` is trivially False, no signal).
    """
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    name = scaffold_cleanup(_unique_name("proj-777-edge-scaffold"))

    payload = _project_create_payload(name)
    # Deliberately non-existent absolute path — proves scaffold doesn't try
    # to use it as the on-disk root (would error or no-op silently).
    payload["working_path"] = "/nonsense/scaffold-target/that/does/not/exist"
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["working_path"] == "/nonsense/scaffold-target/that/does/not/exist"

    # The canonical scaffold location must exist regardless.
    canonical = repo_root / "context" / "projects" / name
    assert canonical.exists(), (
        f"scaffold target {canonical!s} does not exist after POST — "
        f"scaffolder may have been mis-routed via working_path"
    )
    assert canonical.is_dir(), f"{canonical!s} exists but is not a directory"

    # Sanity: at least one expected role folder under the canonical scaffold
    # (dev team → role folders like dev-lead / dev-frontend / etc.).
    role_subdirs = [p.name for p in canonical.iterdir() if p.is_dir()]
    assert role_subdirs, (
        f"scaffold {canonical!s} is empty — no role folders created. "
        f"Expected dev-team roster sub-folders."
    )


# -----------------------------------------------------------------------------
# Kanban #785 — halt_reason
#
# Free-form text on `tasks.halt_reason` (text, nullable). Lead sets it at halt
# time; auto-pickup query skips rows where halt_reason IS NOT NULL. PATCH
# semantics mirror description / working_path: key-absent = unchanged,
# explicit-null = clear / unhalt, "" = 422 (min_length=1), non-empty = set.
# ORM model + migration 0013 done in the DevOps slice. This slice wires the
# field through Pydantic schemas (TaskCreate / TaskUpdate / TaskRead) and
# verifies the router's generic exclude_unset + setattr loop carries it.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_785_create_task_with_halt_reason(client) -> None:
    """POST with halt_reason set → 201 + body echoes the field.

    Rare-but-legal: a task may be filed already halted (e.g., user logs a task
    that's pending external input). Cleanup soft-deletes the row.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 halt_reason on create (test row, safe to delete)",
            "halt_reason": "Option A/B decision needed",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["halt_reason"] == "Option A/B decision needed"

    # Cleanup
    await client.delete(f"/api/tasks/{body['id']}", headers=headers)


@pytest.mark.asyncio
async def test_785_create_task_without_halt_reason_defaults_null(client) -> None:
    """POST omitting halt_reason → 201 + body has halt_reason: null.

    The DB column is nullable with no DEFAULT — the absence on POST resolves
    through Pydantic default=None and lands as NULL.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 halt_reason default null (test row, safe to delete)",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "halt_reason" in body, "TaskRead must expose halt_reason"
    assert body["halt_reason"] is None

    # Cleanup
    await client.delete(f"/api/tasks/{body['id']}", headers=headers)


@pytest.mark.asyncio
async def test_785_create_task_rejects_empty_halt_reason(client) -> None:
    """POST with halt_reason="" → 422 + type=string_too_short at
    loc=['body','halt_reason']. Mirror of working_path empty-string contract.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 halt_reason empty rejection (should not insert)",
            "halt_reason": "",
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    matching = [
        err for err in body["detail"] if err["loc"] == ["body", "halt_reason"]
    ]
    assert matching, (
        f"expected loc=['body','halt_reason'] in 422 detail; "
        f"got {[err['loc'] for err in body['detail']]}"
    )
    assert matching[0]["type"] == "string_too_short", (
        f"expected type='string_too_short'; got {matching[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_785_patch_task_sets_halt_reason(client) -> None:
    """PATCH {"halt_reason": "scope creep"} on a non-halted task → 200 with the
    new value; subsequent GET returns it. Verifies the router's generic
    exclude_unset + setattr loop carries the new field without special-casing.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 patch set halt_reason (test row, safe to delete)",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["halt_reason"] is None

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"halt_reason": "scope creep"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["halt_reason"] == "scope creep"

    # GET round-trip confirms persistence (not just response shape).
    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["halt_reason"] == "scope creep"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_785_patch_task_unsets_halt_reason_via_null(client) -> None:
    """Start halted, PATCH {"halt_reason": null} → halt_reason becomes None.

    Locks the explicit-null = unhalt semantics. No _reject_explicit_null
    validator — null IS meaningful here (parity with description).
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 patch unset halt_reason (test row, safe to delete)",
            "halt_reason": "waiting on user",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["halt_reason"] == "waiting on user"

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"halt_reason": None},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["halt_reason"] is None

    # GET round-trip
    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.json()["halt_reason"] is None

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_785_patch_task_omitting_halt_reason_unchanged(client) -> None:
    """Start halted, PATCH {"title": "new"} only → halt_reason unchanged.

    Locks the key-absent = no-touch PATCH semantics (exclude_unset=True). A
    future schema change that adds a default-None override on PATCH would
    silently clear halt_reason on every unrelated PATCH — this test traps it.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 patch omit halt_reason (test row, safe to delete)",
            "halt_reason": "pending review",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "qa-785 retitled (still halted)"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["title"] == "qa-785 retitled (still halted)"
    assert patch.json()["halt_reason"] == "pending review", (
        "halt_reason must be untouched on a PATCH that omits the key"
    )

    # GET round-trip
    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.json()["halt_reason"] == "pending review"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_785_get_task_returns_halt_reason(client) -> None:
    """POST a halted task, GET it by id, assert the field is present and
    equals what was set. Locks TaskRead exposure of halt_reason."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-785 GET returns halt_reason (test row, safe to delete)",
            "halt_reason": "blocked by upstream",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert "halt_reason" in body, "TaskRead must expose halt_reason"
    assert body["halt_reason"] == "blocked by upstream"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# Kanban #793 — auto-scaffold-on-POST
#
# POST /api/projects copies the agent-teams orchestration harness (CLAUDE.md,
# .claude/, context/standards/, context/teams/<team>/) into the project's
# `working_path` when that path is set AND exists as a directory. DB row creation
# is the source of truth: scaffold errors NEVER roll back the row or flip the
# response off 201. The settings.json substitution drops agent-teams self-
# references (project name, id=1 patterns, context/projects/agent-teams/...).
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_793_post_project_with_writable_working_path_scaffolds(
    client, scaffold_cleanup
) -> None:
    """POST with `working_path` set to an existing tempdir → 201 + harness
    files land inside the tempdir.

    Verifies the universal manifest (CLAUDE.md, .claude/settings.json, the
    dev-* agents) landed; the canonical context/projects/<name>/ scaffold is
    still created in agent-teams under repo_root (handled by scaffold_cleanup).
    """
    import tempfile

    name = scaffold_cleanup(_unique_name("proj-793-scaffold"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = _project_create_payload(name)
        payload["working_path"] = str(tmp_path)

        resp = await client.post("/api/projects", json=payload)
        assert resp.status_code == 201, resp.text

        # Universal files copied
        assert (tmp_path / "CLAUDE.md").is_file(), "CLAUDE.md should be scaffolded"
        assert (tmp_path / ".claude" / "settings.json").is_file(), (
            ".claude/settings.json should be scaffolded"
        )
        # context/standards/** is a glob — at least one file should land
        standards_dir = tmp_path / "context" / "standards"
        assert standards_dir.is_dir(), "context/standards/ should exist"
        # Dev team agent file (team='dev' is the default in _project_create_payload)
        assert (tmp_path / ".claude" / "agents" / "dev-backend.md").is_file(), (
            "dev-backend.md should be scaffolded for team=dev"
        )


@pytest.mark.asyncio
async def test_793_post_project_without_working_path_skips_scaffold(
    client, scaffold_cleanup
) -> None:
    """POST without `working_path` → 201, no filesystem side-effect outside
    the canonical context/projects/<name>/ folder under repo_root.
    """
    import tempfile

    # Use tempdir purely as a witness — we never tell the API about it, so it
    # must remain empty after POST.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        name = scaffold_cleanup(_unique_name("proj-793-noscaffold"))
        payload = _project_create_payload(name)
        # No working_path field set.

        resp = await client.post("/api/projects", json=payload)
        assert resp.status_code == 201, resp.text
        assert resp.json()["working_path"] is None

        # Tempdir is untouched — no harness leaked into a random path.
        contents = list(tmp_path.iterdir())
        assert contents == [], (
            f"expected tempdir to remain empty; got {contents!r}"
        )


@pytest.mark.asyncio
async def test_793_post_project_nonexistent_working_path_returns_201_logs_warning(
    client, scaffold_cleanup
) -> None:
    """POST with `working_path=/nonexistent/path/...` → 201 + DB row created.

    The handler EXPLICITLY skips when the path doesn't exist (we don't want to
    auto-create user-named directories). No crash, no 4xx.
    """
    name = scaffold_cleanup(_unique_name("proj-793-missing-path"))
    payload = _project_create_payload(name)
    payload["working_path"] = "/nonexistent/path/that/should/not/exist/793"

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == name
    assert body["working_path"] == "/nonexistent/path/that/should/not/exist/793"

    # Confirm the nonexistent path was NOT auto-created.
    assert not Path("/nonexistent/path/that/should/not/exist/793").exists(), (
        "handler must not auto-create user's working_path"
    )


@pytest.mark.asyncio
async def test_793_post_project_settings_json_drops_agent_teams_specific_patterns(
    client, scaffold_cleanup
) -> None:
    """The substituted settings.json in the scaffolded tempdir must NOT contain
    any agent-teams self-reference (project name `agent-teams`, id=1 hard-codes,
    or context/projects/agent-teams/ paths) in permissions.allow / permissions.ask.
    """
    import tempfile

    name = scaffold_cleanup(_unique_name("proj-793-settings"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = _project_create_payload(name)
        payload["working_path"] = str(tmp_path)

        resp = await client.post("/api/projects", json=payload)
        assert resp.status_code == 201, resp.text

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.is_file()

        import json as _json

        with settings_path.open("r", encoding="utf-8") as f:
            data = _json.load(f)

        forbidden = (
            "by-name/agent-teams",
            "/api/projects/1/",
            '/api/projects/1"',
            "/context/projects/agent-teams/",
        )
        allow = data.get("permissions", {}).get("allow", [])
        ask = data.get("permissions", {}).get("ask", [])
        for entry in allow + ask:
            if not isinstance(entry, str):
                continue
            for needle in forbidden:
                assert needle not in entry, (
                    f"forbidden substring {needle!r} found in settings entry "
                    f"{entry!r}"
                )

        # Sanity: the file is non-empty after filtering (we didn't blow away
        # everything by accident).
        assert len(allow) > 0, "filter wiped permissions.allow entirely"


@pytest.mark.asyncio
async def test_793_post_project_team_novel_only_copies_novel_agents(
    client, scaffold_cleanup
) -> None:
    """POST with `team='novel'` → tempdir has novel-* agents, NO dev-* agents."""
    import tempfile

    name = scaffold_cleanup(_unique_name("proj-793-novel"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = _project_create_payload(name, team="novel")
        payload["working_path"] = str(tmp_path)

        resp = await client.post("/api/projects", json=payload)
        assert resp.status_code == 201, resp.text
        assert resp.json()["team"] == "novel"

        agents_dir = tmp_path / ".claude" / "agents"
        assert agents_dir.is_dir(), ".claude/agents/ should exist"

        # Novel agents present
        assert (agents_dir / "novel-writer.md").is_file(), "novel-writer.md missing"
        assert (agents_dir / "novel-editor.md").is_file(), "novel-editor.md missing"

        # Dev-specific agents must NOT be present
        assert not (agents_dir / "dev-backend.md").exists(), (
            "dev-backend.md leaked into novel scaffold"
        )
        assert not (agents_dir / "dev-frontend.md").exists(), (
            "dev-frontend.md leaked into novel scaffold"
        )
        assert not (agents_dir / "dev-devops.md").exists(), (
            "dev-devops.md leaked into novel scaffold"
        )


@pytest.mark.asyncio
async def test_793_post_project_re_post_409_but_idempotent_scaffold_via_separate_path(
    client, scaffold_cleanup
) -> None:
    """Duplicate name → 409 (existing contract); scaffolding the SAME tempdir
    via a fresh second name is idempotent — already-present files are skipped
    (no overwrite, no crash). The MVP-A scaffolder records them under `skipped`
    in its report.
    """
    import tempfile

    name_a = scaffold_cleanup(_unique_name("proj-793-idem-a"))
    name_b = scaffold_cleanup(_unique_name("proj-793-idem-b"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # First POST — name_a, working_path=tmp → harness lands.
        payload_a = _project_create_payload(name_a)
        payload_a["working_path"] = str(tmp_path)
        resp_a = await client.post("/api/projects", json=payload_a)
        assert resp_a.status_code == 201, resp_a.text

        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.is_file()
        first_size = claude_md.stat().st_size

        # Re-POST same name → 409 with existing detail string.
        dup = await client.post("/api/projects", json=payload_a)
        assert dup.status_code == 409, dup.text
        assert dup.json()["detail"] == f"Project name {name_a!r} already exists"

        # Second POST — DIFFERENT name, SAME working_path → 201, and the
        # already-present CLAUDE.md is left untouched (size unchanged).
        payload_b = _project_create_payload(name_b)
        payload_b["working_path"] = str(tmp_path)
        resp_b = await client.post("/api/projects", json=payload_b)
        assert resp_b.status_code == 201, resp_b.text

        assert claude_md.is_file(), "CLAUDE.md should still exist after 2nd scaffold"
        assert claude_md.stat().st_size == first_size, (
            "CLAUDE.md size changed — second scaffold should be idempotent-skip"
        )


# -----------------------------------------------------------------------------
# Kanban #797 — acceptance_criteria
#
# Structured JSONB per-criterion exit-criteria tracker. Optional on every task;
# AcceptanceCriterion validates element shape (text required, status Literal in
# {'pending','passed','failed','na'}). PATCH semantics mirror halt_reason /
# description: key-absent = unchanged, explicit-null = clear, explicit-array =
# REPLACE the whole array (no element-merge). Soft enforce via agent prompts
# (#798) — NOT a hard API done-guard this slice.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_797_task_create_with_criteria_returns_criteria(client) -> None:
    """POST with 2 criteria → 201 + GET returns the same 2 elements verbatim.

    Locks the create-path roundtrip: structured JSONB array goes IN through
    Pydantic AcceptanceCriterion, stored in tasks.acceptance_criteria, and
    comes OUT through TaskRead with full element shape preserved.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    criteria = [
        {"text": "endpoint returns 201 on POST", "status": "pending"},
        {"text": "migration applied via alembic", "status": "passed"},
    ]
    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 criteria roundtrip (test row, safe to delete)",
            "acceptance_criteria": criteria,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["acceptance_criteria"] is not None
    assert len(body["acceptance_criteria"]) == 2
    assert body["acceptance_criteria"][0]["text"] == "endpoint returns 201 on POST"
    assert body["acceptance_criteria"][0]["status"] == "pending"
    assert body["acceptance_criteria"][1]["text"] == "migration applied via alembic"
    assert body["acceptance_criteria"][1]["status"] == "passed"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_797_task_create_without_criteria_returns_null(client) -> None:
    """POST omitting acceptance_criteria → 201 + GET returns null.

    The DB column is nullable JSONB with no DEFAULT — absence on POST resolves
    through Pydantic default=None and lands as NULL.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 criteria absent (test row, safe to delete)",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "acceptance_criteria" in body, "TaskRead must expose acceptance_criteria"
    assert body["acceptance_criteria"] is None
    # Kanban #887: subagent_models is NOT NULL DEFAULT '[]' — always present,
    # never null. Verify alongside acceptance_criteria (both are JSONB fields
    # on the same task row).
    assert "subagent_models" in body, "TaskRead must expose subagent_models"
    assert body["subagent_models"] == [], (
        f"expected [] (NOT NULL DEFAULT '[]'), got {body['subagent_models']!r}"
    )

    # Cleanup
    await client.delete(f"/api/tasks/{body['id']}", headers=headers)


@pytest.mark.asyncio
async def test_797_task_create_criteria_empty_text_rejected_422(client) -> None:
    """POST with criterion text="" → 422 + type=string_too_short at
    loc=['body','acceptance_criteria',0,'text'].

    AcceptanceCriterion.text has min_length=1 — empty strings would be
    invisible-but-counted false positives at done-time.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 criterion empty-text rejection (should not insert)",
            "acceptance_criteria": [{"text": "", "status": "pending"}],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    matching = [
        err
        for err in body["detail"]
        if err["loc"] == ["body", "acceptance_criteria", 0, "text"]
    ]
    assert matching, (
        f"expected loc=['body','acceptance_criteria',0,'text'] in 422 detail; "
        f"got {[err['loc'] for err in body['detail']]}"
    )
    assert matching[0]["type"] == "string_too_short", (
        f"expected type='string_too_short'; got {matching[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_797_task_create_criteria_invalid_status_rejected_422(client) -> None:
    """POST with criterion status='WONTFIX' → 422 + literal_error at
    loc=['body','acceptance_criteria',0,'status'].

    AcceptanceCriterion.status is Literal['pending','passed','failed','na'].
    Unknown values must fail Pydantic at the boundary — no silent coerce.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 criterion bad-status rejection (should not insert)",
            "acceptance_criteria": [{"text": "x", "status": "WONTFIX"}],
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body and isinstance(body["detail"], list)
    matching = [
        err
        for err in body["detail"]
        if err["loc"] == ["body", "acceptance_criteria", 0, "status"]
    ]
    assert matching, (
        f"expected loc=['body','acceptance_criteria',0,'status'] in 422 detail; "
        f"got {[err['loc'] for err in body['detail']]}"
    )
    assert matching[0]["type"] == "literal_error", (
        f"expected type='literal_error'; got {matching[0]['type']!r}"
    )


@pytest.mark.asyncio
async def test_797_task_patch_sets_criteria_via_array(client) -> None:
    """PATCH with acceptance_criteria=[...] on a task created without criteria
    → 200 + GET reflects the new array.

    Verifies the router's generic exclude_unset + setattr loop carries the
    JSONB field without special-casing.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 patch sets criteria (test row, safe to delete)",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["acceptance_criteria"] is None

    new_criteria = [
        {"text": "tests pass", "status": "pending"},
        {"text": "reviewer approved", "status": "pending"},
    ]
    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"acceptance_criteria": new_criteria},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert len(patch.json()["acceptance_criteria"]) == 2

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    got = fetched.json()["acceptance_criteria"]
    assert got[0]["text"] == "tests pass"
    assert got[1]["text"] == "reviewer approved"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_797_task_patch_replaces_entire_array(client) -> None:
    """Start with 5 criteria → PATCH with 2 → GET returns 2 (replacement, not
    merge).

    Locks the design-locked PATCH semantic: full array replace only, no
    element-merge. A future schema change that diffs/merges the array would
    silently change semantics — this test traps it.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    initial = [
        {"text": "criterion 1", "status": "pending"},
        {"text": "criterion 2", "status": "passed"},
        {"text": "criterion 3", "status": "failed"},
        {"text": "criterion 4", "status": "na"},
        {"text": "criterion 5", "status": "pending"},
    ]
    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 patch replaces array (test row, safe to delete)",
            "acceptance_criteria": initial,
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert len(create.json()["acceptance_criteria"]) == 5

    replacement = [
        {"text": "new criterion A", "status": "pending"},
        {"text": "new criterion B", "status": "pending"},
    ]
    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"acceptance_criteria": replacement},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    got = fetched.json()["acceptance_criteria"]
    assert len(got) == 2, (
        f"expected exactly 2 criteria after replacement-PATCH; got {len(got)} "
        f"— implies merge semantics (forbidden)"
    )
    assert got[0]["text"] == "new criterion A"
    assert got[1]["text"] == "new criterion B"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_797_task_patch_unsets_criteria_via_null(client) -> None:
    """Start with criteria, PATCH {"acceptance_criteria": null} →
    acceptance_criteria becomes None.

    Locks the explicit-null = clear semantic — null IS meaningful here, no
    _reject_explicit_null validator (parity with halt_reason).
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 patch null clears criteria (test row, safe to delete)",
            "acceptance_criteria": [{"text": "x"}],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["acceptance_criteria"] is not None

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"acceptance_criteria": None},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["acceptance_criteria"] is None

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.json()["acceptance_criteria"] is None

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_797_task_patch_omitting_keeps_criteria(client) -> None:
    """Start with criteria, PATCH {"title": "new"} only → criteria unchanged.

    Locks the key-absent = no-touch PATCH semantic (exclude_unset=True). A
    future schema change adding a default-None override would silently clear
    criteria on every unrelated PATCH — this test traps it.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    initial = [
        {"text": "stays put A", "status": "pending"},
        {"text": "stays put B", "status": "passed"},
    ]
    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 patch omit keeps criteria (test row, safe to delete)",
            "acceptance_criteria": initial,
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "qa-797 retitled (criteria untouched)"},
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["title"] == "qa-797 retitled (criteria untouched)"
    got = patch.json()["acceptance_criteria"]
    assert got is not None and len(got) == 2, (
        "acceptance_criteria must be untouched on a PATCH that omits the key"
    )
    assert got[0]["text"] == "stays put A"
    assert got[1]["text"] == "stays put B"

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    fetched_got = fetched.json()["acceptance_criteria"]
    assert fetched_got is not None and len(fetched_got) == 2

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_797_task_get_returns_criteria_status_default_pending(client) -> None:
    """POST with criterion {text: 'x'} (no status field) → GET returns the
    element with status='pending'.

    Locks AcceptanceCriterion.status default. A future change that flips the
    default to e.g. 'passed' would silently mark fresh criteria as already-
    verified — this test traps it.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-797 criterion default status (test row, safe to delete)",
            "acceptance_criteria": [{"text": "no-status-given"}],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    got = fetched.json()["acceptance_criteria"]
    assert got is not None and len(got) == 1
    assert got[0]["text"] == "no-status-given"
    assert got[0]["status"] == "pending", (
        f"expected default status='pending'; got {got[0]['status']!r}"
    )

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# Kanban #801 — AcceptanceCriterion.verified_at JSONB serialization fix
#
# Defect on #797: AcceptanceCriterion.verified_at is `datetime | None`. The
# router's `model_dump()` left datetime objects nested in the list of dicts,
# which SQLAlchemy's default JSONB json_serializer cannot encode → 500. Fix
# was to re-dump the criterion list with `mode='json'` so datetime → ISO
# string before it reaches the JSONB column. These tests pin the regression:
# any future refactor that drops the mode='json' coercion → red here.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_801_criteria_with_verified_at_isoformat_roundtrips(client) -> None:
    """POST with a criterion carrying verified_at='2026-05-12T11:30:00Z' →
    201 (NOT 500). GET returns verified_at as an ISO-format string.

    Pre-fix: SQLAlchemy's JSONB json_serializer crashes on the nested
    datetime object — server returns 500 with a TypeError traceback. Locks
    the surgical mode='json' fix in routers/tasks.py.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-801 verified_at roundtrip (test row, safe to delete)",
            "acceptance_criteria": [
                {
                    "text": "criterion with verified_at",
                    "status": "passed",
                    "verified_at": "2026-05-12T11:30:00Z",
                }
            ],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    got = fetched.json()["acceptance_criteria"]
    assert got is not None and len(got) == 1
    assert got[0]["text"] == "criterion with verified_at"
    assert got[0]["status"] == "passed"
    # ISO-format string — accept either 'Z' suffix or '+00:00' offset since
    # Pydantic + datetime.isoformat() canonicalize differently across versions.
    verified_at = got[0]["verified_at"]
    assert isinstance(verified_at, str), (
        f"verified_at should be ISO string after JSONB roundtrip; got {type(verified_at).__name__}"
    )
    assert "2026-05-12T11:30:00" in verified_at, (
        f"expected '2026-05-12T11:30:00' substring; got {verified_at!r}"
    )

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_801_criteria_with_microseconds_preserved(client) -> None:
    """POST with verified_at carrying microseconds → 201 + roundtrip preserves
    the microsecond component.

    Locks that mode='json' coercion uses datetime.isoformat() (which keeps
    microseconds when present) rather than a lossier strftime.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-801 verified_at microseconds (test row, safe to delete)",
            "acceptance_criteria": [
                {
                    "text": "criterion with microsecond precision",
                    "status": "passed",
                    "verified_at": "2026-05-12T11:30:00.123456Z",
                }
            ],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    fetched = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    got = fetched.json()["acceptance_criteria"]
    assert got is not None and len(got) == 1
    verified_at = got[0]["verified_at"]
    assert isinstance(verified_at, str)
    assert "123456" in verified_at, (
        f"expected microsecond component '123456' preserved through roundtrip; got {verified_at!r}"
    )

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_801_patch_with_datetime_returns_200(client) -> None:
    """Regression for the exact #799 PATCH reproducer shape: PATCH a task
    with acceptance_criteria=[{text, status='passed', verified_at=<iso>}] →
    200 (NOT 500).

    Pre-fix: same JSONB-encoder crash on PATCH that #799 surfaced. This test
    locks the parallel fix in update_task() — both POST and PATCH paths must
    coerce datetime → ISO string before SQLAlchemy serializes the list to
    JSONB.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-801 PATCH datetime regression (test row, safe to delete)",
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    assert create.json()["acceptance_criteria"] is None

    patch = await client.patch(
        f"/api/tasks/{task_id}",
        json={
            "acceptance_criteria": [
                {
                    "text": "x",
                    "status": "passed",
                    "verified_at": "2026-05-12T11:30:00Z",
                }
            ]
        },
        headers=headers,
    )
    assert patch.status_code == 200, patch.text  # NOT 500
    got = patch.json()["acceptance_criteria"]
    assert got is not None and len(got) == 1
    assert got[0]["text"] == "x"
    assert got[0]["status"] == "passed"
    verified_at = got[0]["verified_at"]
    assert isinstance(verified_at, str)
    assert "2026-05-12T11:30:00" in verified_at

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=headers)


# Regression: Kanban #854 — CANCELLED=6 default-exclusion + status_change_reason
@pytest.mark.asyncio
async def test_list_tasks_excludes_cancelled_by_default(client) -> None:
    """`GET /api/tasks` default response MUST omit process_status=6 rows.

    Seed two rows in the project — one TODO, one we PATCH to CANCELLED —
    and confirm default GET only returns the TODO row.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    created: list[int] = []
    try:
        t_todo = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "k854 default-excl todo"},
            headers=headers,
        )
        assert t_todo.status_code == 201, t_todo.text
        todo_id = t_todo.json()["id"]
        created.append(todo_id)

        t_cancel = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "k854 default-excl victim"},
            headers=headers,
        )
        assert t_cancel.status_code == 201, t_cancel.text
        cancel_id = t_cancel.json()["id"]
        created.append(cancel_id)

        # Flip to CANCELLED via PATCH.
        flip = await client.patch(
            f"/api/tasks/{cancel_id}",
            json={"process_status": 6, "status_change_reason": "k854 smoke cancel"},
            headers=headers,
        )
        assert flip.status_code == 200, flip.text
        assert flip.json()["process_status"] == 6
        assert flip.json()["status_change_reason"] == "k854 smoke cancel"

        # Default list — cancel_id must NOT appear; todo_id MUST.
        resp = await client.get("/api/tasks?limit=500", headers=headers)
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert todo_id in ids, "default GET must include the TODO row"
        assert cancel_id not in ids, (
            f"default GET must exclude CANCELLED row id={cancel_id}"
        )
        # Defensive: no returned row has process_status=6.
        for t in resp.json():
            assert t["process_status"] != 6, (
                f"default GET leaked a cancelled row id={t['id']}"
            )
    finally:
        for tid in created:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #854
@pytest.mark.asyncio
async def test_list_tasks_include_cancelled_opts_in(client) -> None:
    """`?include_cancelled=true` opts the cancelled rows back in. The TODO
    row stays included (default behavior is additive, not replacement).
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    created: list[int] = []
    try:
        t_todo = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "k854 incl todo"},
            headers=headers,
        )
        todo_id = t_todo.json()["id"]
        created.append(todo_id)

        t_cancel = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "k854 incl victim"},
            headers=headers,
        )
        cancel_id = t_cancel.json()["id"]
        created.append(cancel_id)
        flip = await client.patch(
            f"/api/tasks/{cancel_id}",
            json={"process_status": 6, "status_change_reason": "k854 incl"},
            headers=headers,
        )
        assert flip.status_code == 200, flip.text

        resp = await client.get(
            "/api/tasks?include_cancelled=true&limit=500", headers=headers
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert todo_id in ids
        assert cancel_id in ids, (
            f"?include_cancelled=true must surface CANCELLED row id={cancel_id}"
        )
    finally:
        for tid in created:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #854 — explicit process_status filter still works for 6
@pytest.mark.asyncio
async def test_list_tasks_explicit_process_status_6_returns_cancelled(client) -> None:
    """`?process_status=6` explicit filter SHOULD include the cancelled rows
    (precedence: explicit `process_status` wins over the default cancelled
    exclusion — same precedence pattern as `pending`).
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    created: list[int] = []
    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "k854 explicit6 victim"},
            headers=headers,
        )
        tid = t.json()["id"]
        created.append(tid)
        await client.patch(
            f"/api/tasks/{tid}",
            json={"process_status": 6, "status_change_reason": "k854 explicit6"},
            headers=headers,
        )

        resp = await client.get(
            "/api/tasks?process_status=6&limit=500", headers=headers
        )
        assert resp.status_code == 200, resp.text
        ids = {t["id"] for t in resp.json()}
        assert tid in ids, (
            f"explicit ?process_status=6 must include CANCELLED row id={tid}"
        )
        for t in resp.json():
            assert t["process_status"] == 6
    finally:
        for tid in created:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# Regression: Kanban #854 — audit-trigger captures status_change_reason
@pytest.mark.asyncio
async def test_patch_cancel_records_reason_in_tasks_history(client) -> None:
    """`tasks_history` rows snapshot the OLD row (pre-UPDATE state) — this is
    a property of the existing audit trigger and intentional. To verify that
    `status_change_reason` is captured by the trigger, we PATCH the field
    TWICE: the first PATCH sets it; the second PATCH (any column change)
    causes the trigger to emit a snapshot of the row BETWEEN those two
    PATCHes — which now carries the reason. This is the load-bearing
    audit-trail behavior the FE depends on.
    """
    from sqlalchemy import select

    from src.db import SessionLocal
    from src.models.task import TaskHistory

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k854 audit smoke"},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    try:
        reason = "user clicked cancel — k854 audit"
        flip = await client.patch(
            f"/api/tasks/{task_id}",
            json={"process_status": 6, "status_change_reason": reason},
            headers=headers,
        )
        assert flip.status_code == 200, flip.text

        # Second PATCH — any no-op-ish change. Touch the title so the audit
        # trigger fires AGAIN, this time capturing the row state set by the
        # first cancel PATCH (process_status=6 + the reason).
        second = await client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "k854 audit smoke v2"},
            headers=headers,
        )
        assert second.status_code == 200, second.text

        # Read tasks_history rows for this task_id. Trigger writes OLD state;
        # the SECOND history row (most-recent id) snapshots the row state
        # AFTER the cancel PATCH but BEFORE the title PATCH — so the snapshot
        # carries process_status=6 + the reason.
        async with SessionLocal() as s:
            rows = (
                await s.execute(
                    select(TaskHistory)
                    .where(TaskHistory.task_id == task_id)
                    .order_by(TaskHistory.id.desc())
                )
            ).scalars().all()
        assert len(rows) >= 2, (
            f"expected >=2 tasks_history rows after two PATCHes; got {len(rows)}"
        )
        snap_post_cancel = rows[0].snapshot  # OLD state at the title PATCH = post-cancel state
        assert snap_post_cancel.get("process_status") == 6, snap_post_cancel
        assert snap_post_cancel.get("status_change_reason") == reason, snap_post_cancel
        # And the earliest history row snapshots the pre-cancel state — has
        # the new column key but a NULL value (column existed pre-PATCH but
        # the row had no reason yet).
        snap_pre_cancel = rows[1].snapshot
        assert "status_change_reason" in snap_pre_cancel, snap_pre_cancel
        assert snap_pre_cancel["status_change_reason"] is None, snap_pre_cancel
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
