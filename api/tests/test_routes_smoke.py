"""HTTP-level contract tests for projects + tasks routers.

These run against the live FastAPI app via httpx.AsyncClient + ASGITransport
(see tests/conftest.py for the `client` fixture). They share the dev Postgres
that the seed populated — the `agent-teams` project (Phase 2b verified) is
expected to exist and be is_active=True.

Scope:
- Verify success-path response shape on the read endpoints we lean on (Lead
  calls /api/projects/active every turn).
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
- Cover the multi-domain `lead` contract: required on POST, rejects unknown,
  novel scaffold creates the right roster.

Tests that create rows soft-delete them on the way out so the dev DB doesn't
balloon with stale data; the partial unique index also lets re-runs reuse the
same name.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


def _unique_name(prefix: str) -> str:
    """Generate a name unlikely to collide with prior test runs.

    Soft-deleted rows free the name (partial unique on status=1), but using a
    fresh suffix per run keeps the test output readable when you query the DB
    by hand.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, lead: str = "dev", is_active: bool = False) -> dict:
    """Minimal valid POST /api/projects body."""
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": is_active,
        "lead": lead,
    }


# -----------------------------------------------------------------------------
# Projects — read-only assertions
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_project_returns_seeded_agent_teams(client) -> None:
    resp = await client.get("/api/projects/active")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "agent-teams"
    assert body["is_active"] is True
    # ProjectRead shape sanity — these fields back the Lead bootstrap.
    for field in ("id", "paths_web", "paths_api", "paths_db", "config", "lead"):
        assert field in body, f"missing {field} in ProjectRead body"
    # Backfill from the soft-delete-and-lead migration sets agent-teams to lead='dev'.
    assert body["lead"] == "dev"


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
    resp = await client.get("/api/tasks/9999999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=9999999 not found"}


@pytest.mark.asyncio
async def test_patch_task_404_exact_detail(client) -> None:
    resp = await client.patch("/api/tasks/9999999", json={})
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
    active = await client.get("/api/projects/active")
    assert active.status_code == 200, active.text
    project_id = active.json()["id"]

    resp = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "smoke", "process_status": 99},
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
    active = await client.get("/api/projects/active")
    assert active.status_code == 200
    project_id = active.json()["id"]

    # 1. Create
    create_resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "qa-smoke-lifecycle (test row, safe to delete)",
            "description": "Created by tests/test_routes_smoke.py — verifies process_status -> timestamp transitions.",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    task = create_resp.json()
    task_id = task["id"]
    assert task["process_status"] == 1  # default TODO
    assert task["started_at"] is None
    assert task["completed_at"] is None

    # 2. -> in_progress should stamp started_at
    in_progress = await client.patch(
        f"/api/tasks/{task_id}", json={"process_status": 2}
    )
    assert in_progress.status_code == 200, in_progress.text
    body = in_progress.json()
    assert body["process_status"] == 2
    assert body["started_at"] is not None, "in_progress transition must stamp started_at"
    assert body["completed_at"] is None
    started_at_snapshot = body["started_at"]

    # 3. -> done should stamp completed_at and leave started_at intact
    done = await client.patch(f"/api/tasks/{task_id}", json={"process_status": 5})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["process_status"] == 5
    assert body["started_at"] == started_at_snapshot, (
        "completing a task must not overwrite started_at"
    )
    assert body["completed_at"] is not None, "done transition must stamp completed_at"

    # Cleanup: soft-delete the test row.
    cleanup = await client.delete(f"/api/tasks/{task_id}")
    assert cleanup.status_code == 204


# -----------------------------------------------------------------------------
# Soft-delete — list default filter, ?include_deleted, DELETE, re-create
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_default_filters_active_only(client) -> None:
    """Default-filter `WHERE status=1` — soft-deleted rows are invisible
    unless `?include_deleted=true` is passed.
    """
    active = await client.get("/api/projects/active")
    project_id = active.json()["id"]

    # Create a task, then soft-delete it.
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "soft-delete-list-filter probe"},
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    delete = await client.delete(f"/api/tasks/{task_id}")
    assert delete.status_code == 204

    # Default list — must NOT include the soft-deleted row.
    default_list = await client.get(f"/api/tasks?project_id={project_id}&limit=500")
    assert default_list.status_code == 200
    ids = {t["id"] for t in default_list.json()}
    assert task_id not in ids, "default list must hide soft-deleted rows"

    # ?include_deleted=true — must include it.
    with_deleted = await client.get(
        f"/api/tasks?project_id={project_id}&include_deleted=true&limit=500"
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
    active = await client.get("/api/projects/active")
    project_id = active.json()["id"]

    create = await client.post(
        "/api/tasks", json={"project_id": project_id, "title": "detail-after-soft-delete"}
    )
    task_id = create.json()["id"]

    delete = await client.delete(f"/api/tasks/{task_id}")
    assert delete.status_code == 204

    # Detail endpoint must still return the row.
    detail = await client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == task_id


@pytest.mark.asyncio
async def test_delete_task_returns_204_and_is_idempotent(client) -> None:
    """DELETE flips status=0 and returns 204. A second DELETE is a no-op (still 204)."""
    active = await client.get("/api/projects/active")
    project_id = active.json()["id"]

    create = await client.post(
        "/api/tasks", json={"project_id": project_id, "title": "delete-idempotent probe"}
    )
    task_id = create.json()["id"]

    first = await client.delete(f"/api/tasks/{task_id}")
    assert first.status_code == 204

    second = await client.delete(f"/api/tasks/{task_id}")
    assert second.status_code == 204


@pytest.mark.asyncio
async def test_patch_task_silently_ignores_soft_delete_status_field(client) -> None:
    """Decision: PATCH `{"status": 0}` is silently ignored (Pydantic default
    `extra='ignore'` on TaskUpdate, which has no `status` field). This locks
    the choice — a future switch to 422 would require setting
    `model_config = ConfigDict(extra='forbid')` on TaskUpdate, which is a
    contract change tracked by this test.
    """
    active = await client.get("/api/projects/active")
    project_id = active.json()["id"]

    create = await client.post(
        "/api/tasks", json={"project_id": project_id, "title": "patch-ignore-status probe"}
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    # PATCH with `status: 0` in the body — should NOT soft-delete the task.
    resp = await client.patch(f"/api/tasks/{task_id}", json={"status": 0})
    assert resp.status_code == 200, resp.text

    # Detail still appears in the default list (i.e., status=1).
    listing = await client.get(f"/api/tasks?project_id={project_id}&limit=500")
    ids = {t["id"] for t in listing.json()}
    assert task_id in ids, "PATCH {status:0} must NOT soft-delete (silent-ignore contract)"

    # Cleanup
    await client.delete(f"/api/tasks/{task_id}")


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
    """Deleting an active project must also flip is_active=false (same txn) so
    the partial unique index `ux_projects_active_one` doesn't block a new
    active project.

    M7: the seeded agent-teams row's is_active is restored in `finally` so a
    failed assertion above the restore step does NOT leak state to subsequent
    tests (which all depend on /api/projects/active returning agent-teams).
    """
    name = scaffold_cleanup(_unique_name("proj-delete-active"))
    # Create as inactive first to avoid fighting the seeded agent-teams active row.
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201
    project_id = create.json()["id"]

    try:
        # Flip is_active=true via PATCH (uses _clear_other_active to free the slot).
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
        # Restore agent-teams as the active project NO MATTER WHAT — every
        # other test depends on it. Without this, an assertion failure above
        # cascades to the rest of the suite via 404 on /api/projects/active.
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
# Multi-domain lead — POST validation + scaffold dispatch
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_project_requires_lead_field(client) -> None:
    """`lead` is required on ProjectCreate; missing it -> 422.

    No scaffold_cleanup needed — request is rejected at the schema layer before
    the scaffold side-effect runs.
    """
    payload = _project_create_payload(_unique_name("proj-missing-lead"))
    payload.pop("lead")
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_project_rejects_unknown_lead(client) -> None:
    """Unknown lead value -> 422 (Pydantic Literal rejects it).

    No scaffold_cleanup needed — request is rejected at the schema layer.
    """
    payload = _project_create_payload(_unique_name("proj-bad-lead"), lead="manager")
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_project_with_novel_lead_scaffolds_novel_roster(
    client, scaffold_cleanup
) -> None:
    """`lead='novel'` creates novel-writer + novel-editor folders, NOT dev-*.

    Resolves the on-disk path via settings.repo_root (same root the router uses).
    """
    from src.settings import get_settings

    settings = get_settings()
    repo_root = Path(settings.repo_root)

    name = scaffold_cleanup(_unique_name("proj-novel"))
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name, lead="novel")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["lead"] == "novel"
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


@pytest.mark.asyncio
async def test_redelete_task_does_not_grow_audit_history(client, db_session) -> None:
    """M9 lock (tasks): A second DELETE on an already-soft-deleted task is a
    no-op — it must NOT write a redundant `'U'` row to `tasks_history`.

    Reads `tasks_history` directly via db_session because there is no public
    endpoint for the audit table.
    """
    from sqlalchemy import text

    active = await client.get("/api/projects/active")
    project_id = active.json()["id"]

    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "m9-redelete-task probe"},
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    # First DELETE — flips status=0; audit trigger writes one 'U' row.
    first = await client.delete(f"/api/tasks/{task_id}")
    assert first.status_code == 204

    # Snapshot history count after the legitimate first DELETE.
    count_after_first = await db_session.scalar(
        text("SELECT COUNT(*) FROM tasks_history WHERE task_id = :tid"),
        {"tid": task_id},
    )
    assert count_after_first >= 1, (
        f"expected at least one audit row after first DELETE, got {count_after_first}"
    )

    # Second DELETE — should be a no-op.
    second = await client.delete(f"/api/tasks/{task_id}")
    assert second.status_code == 204

    count_after_second = await db_session.scalar(
        text("SELECT COUNT(*) FROM tasks_history WHERE task_id = :tid"),
        {"tid": task_id},
    )
    assert count_after_second == count_after_first, (
        f"re-DELETE on a soft-deleted task wrote a redundant audit row: "
        f"{count_after_first} → {count_after_second}"
    )


@pytest.mark.asyncio
async def test_redelete_project_is_observable_noop(client, scaffold_cleanup) -> None:
    """M9 lock (projects): A second DELETE on an already-soft-deleted project
    is a no-op. Projects don't have an audit table — the observable signal is
    that `updated_at` does NOT change between the two DELETEs (the no-op skip
    in `delete_project` returns early before any UPDATE fires).
    """
    name = scaffold_cleanup(_unique_name("proj-redelete"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201
    project_id = create.json()["id"]

    # First DELETE.
    first = await client.delete(f"/api/projects/{project_id}")
    assert first.status_code == 204

    # Snapshot the row state after the first DELETE.
    listing = await client.get("/api/projects?include_deleted=true&limit=500")
    rows = [p for p in listing.json() if p["id"] == project_id]
    assert len(rows) == 1
    updated_at_before = rows[0]["updated_at"]

    # Second DELETE — must be a no-op (skip path returns 204 without UPDATE).
    second = await client.delete(f"/api/projects/{project_id}")
    assert second.status_code == 204

    # Re-fetch and assert updated_at did NOT advance.
    listing = await client.get("/api/projects?include_deleted=true&limit=500")
    rows = [p for p in listing.json() if p["id"] == project_id]
    assert len(rows) == 1
    updated_at_after = rows[0]["updated_at"]

    assert updated_at_after == updated_at_before, (
        f"re-DELETE on a soft-deleted project mutated updated_at: "
        f"{updated_at_before} → {updated_at_after} (the no-op skip is broken)"
    )
