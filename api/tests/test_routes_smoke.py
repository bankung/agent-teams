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
    assert "process_status must be one of (1, 2, 3, 4, 5), got 99" in msgs
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
