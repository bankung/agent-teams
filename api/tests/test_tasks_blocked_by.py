"""Kanban #771 — tasks.blocked_by FK + API + history (single-blocker dependency).

Locked design (2026-05-12): each task carries at most one `blocked_by` pointer
to another task in the SAME project. Re-blocking IS allowed in V1 (unlike
parent_task_id / spawned_from_task_id). Detail strings are wire-contract pinned
by `test_blocked_by_detail_strings_pinned_in_router_source`.

Coverage:
  (a) POST happy path (same-project blocker)
  (b) POST cross-project blocker → 422 (locked detail)
  (c) POST soft-deleted blocker → 422
  (d) POST non-existent blocker id → 422
  (e) PATCH blocked_by == task_id → 422 (self-blocker)
  (f) PATCH creates direct cycle (A blocks B; PATCH A.blocked_by = B) → 422
  (g) PATCH creates transitive cycle at depth 4 → 422
  (h) PATCH clear to null → 200 (lifts blocker)
  (i) PATCH no-op skip → updated_at unchanged
  (j) GET /api/tasks/{id}/blocks reverse-lookup happy path
  (k) GET /api/tasks/{id}/blocks empty
  (l) GET /api/tasks/{nonexistent}/blocks → 404
  (m) tasks_history captures blocked_by snapshot via to_jsonb(OLD)
  (n) Source-text-lock for the four detail string templates
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp from JSON (handles trailing 'Z')."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


async def _get_project_id(client) -> int:
    """Resolve the `agent-teams` project id used by all tests in this file."""
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str, **extras) -> int:
    """POST a minimal task; return its id. Caller is responsible for cleanup."""
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# -----------------------------------------------------------------------------
# (a) POST happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_valid_blocked_by_succeeds_same_project(client) -> None:
    """A blocker in the same project + a child pointing at it both succeed
    (201); TaskRead exposes blocked_by correctly on the child."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(client, project_id, "k771-a blocker")
    try:
        child = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "k771-a blocked task",
                "blocked_by": blocker_id,
            },
            headers=headers,
        )
        assert child.status_code == 201, child.text
        body = child.json()
        assert body["blocked_by"] == blocker_id, (
            f"blocked_by did not round-trip: expected {blocker_id} got "
            f"{body['blocked_by']!r}"
        )
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/tasks/{blocker_id}", headers=headers)


# -----------------------------------------------------------------------------
# (b) POST cross-project rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_blocked_by_referencing_different_project_returns_422(
    client, scaffold_cleanup
) -> None:
    """Blocker in project A, child claiming project B → 422 with locked detail."""
    import uuid

    project_a_id = await _get_project_id(client)
    name_b = scaffold_cleanup(f"k771-b-proj-{uuid.uuid4().hex[:8]}")
    proj_b_resp = await client.post(
        "/api/projects",
        json={
            "name": name_b,
            "description": f"test fixture for {name_b}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert proj_b_resp.status_code == 201, proj_b_resp.text
    project_b_id = proj_b_resp.json()["id"]

    headers_a = {"X-Project-Id": str(project_a_id)}
    headers_b = {"X-Project-Id": str(project_b_id)}

    blocker_id = await _make_task(client, project_a_id, "k771-b blocker in A")
    try:
        bad = await client.post(
            "/api/tasks",
            json={
                "project_id": project_b_id,
                "title": "k771-b child claiming B but blocked_by in A",
                "blocked_by": blocker_id,
            },
            headers=headers_b,
        )
        assert bad.status_code == 422, bad.text
        assert bad.json() == {
            "detail": f"blocked_by {blocker_id} belongs to a different project"
        }, bad.json()
    finally:
        await client.delete(f"/api/tasks/{blocker_id}", headers=headers_a)
        await client.delete(f"/api/projects/{project_b_id}")


# -----------------------------------------------------------------------------
# (c) POST soft-deleted blocker rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_blocked_by_referencing_deleted_task_returns_422(
    client,
) -> None:
    """Blocker exists but is soft-deleted (status=0) → 422 with the
    "does not exist or is deleted" detail."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(client, project_id, "k771-c blocker to delete")
    # Soft-delete the blocker first.
    delete_resp = await client.delete(f"/api/tasks/{blocker_id}", headers=headers)
    assert delete_resp.status_code == 204

    bad = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k771-c child referencing soft-deleted blocker",
            "blocked_by": blocker_id,
        },
        headers=headers,
    )
    assert bad.status_code == 422, bad.text
    assert bad.json() == {
        "detail": f"blocked_by {blocker_id} does not exist or is deleted"
    }, bad.json()


# -----------------------------------------------------------------------------
# (d) POST non-existent blocker rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_blocked_by_referencing_nonexistent_id_returns_422(
    client,
) -> None:
    """Blocker id doesn't exist in the DB → 422."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    bogus_id = 999_999_999
    bad = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k771-d child referencing nonexistent blocker",
            "blocked_by": bogus_id,
        },
        headers=headers,
    )
    assert bad.status_code == 422, bad.text
    assert bad.json() == {
        "detail": f"blocked_by {bogus_id} does not exist or is deleted"
    }, bad.json()


# -----------------------------------------------------------------------------
# (e) PATCH self-blocker rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_blocked_by_to_self_id_returns_422(client) -> None:
    """PATCH blocked_by = task.id → 422 with locked detail. The DB CHECK
    ck_tasks_blocked_by_not_self is a backstop — the app rejects first."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(client, project_id, "k771-e self-blocker probe")
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"blocked_by": task_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {"detail": "blocked_by cannot reference self"}, resp.json()
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (f) PATCH direct cycle rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_blocked_by_creates_direct_cycle_returns_422(client) -> None:
    """A blocks B (B.blocked_by = A). PATCH A.blocked_by = B → would form
    A↔B cycle → 422 (cycle detected at depth 1)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    a_id = await _make_task(client, project_id, "k771-f task A")
    b_id = await _make_task(
        client, project_id, "k771-f task B (blocked_by A)", blocked_by=a_id
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{a_id}",
            json={"blocked_by": b_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body == {
            "detail": f"blocked_by {b_id} would create a cycle (depth 1)"
        }, body
    finally:
        # Clean up the B→A reference first so we can delete in order.
        await client.patch(
            f"/api/tasks/{b_id}", json={"blocked_by": None}, headers=headers
        )
        await client.delete(f"/api/tasks/{b_id}", headers=headers)
        await client.delete(f"/api/tasks/{a_id}", headers=headers)


# -----------------------------------------------------------------------------
# (g) PATCH transitive cycle rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_blocked_by_creates_transitive_cycle_returns_422(
    client,
) -> None:
    """Build a chain B←C←D←E (each blocked_by the previous). PATCH B.blocked_by
    = E → would form B→E→D→C→B cycle → 422 at depth 3 (B is reached after
    walking E→D→C→B)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    b_id = await _make_task(client, project_id, "k771-g B")
    c_id = await _make_task(client, project_id, "k771-g C", blocked_by=b_id)
    d_id = await _make_task(client, project_id, "k771-g D", blocked_by=c_id)
    e_id = await _make_task(client, project_id, "k771-g E", blocked_by=d_id)
    try:
        # PATCH B.blocked_by = E. Walk from E: cursor=E.blocked_by=D (depth 1),
        # D→C (depth 2), C→B (depth 3) → match → cycle.
        resp = await client.patch(
            f"/api/tasks/{b_id}",
            json={"blocked_by": e_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert "would create a cycle" in body["detail"], body
        assert str(e_id) in body["detail"], body
    finally:
        # Unwire in reverse so deletes can succeed.
        await client.patch(
            f"/api/tasks/{e_id}", json={"blocked_by": None}, headers=headers
        )
        await client.patch(
            f"/api/tasks/{d_id}", json={"blocked_by": None}, headers=headers
        )
        await client.patch(
            f"/api/tasks/{c_id}", json={"blocked_by": None}, headers=headers
        )
        for tid in (e_id, d_id, c_id, b_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (h) PATCH clear to null
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_blocked_by_clear_to_null_succeeds(client) -> None:
    """Setting blocked_by = None lifts the blocker (200) and the field
    round-trips as null."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(client, project_id, "k771-h blocker")
    target_id = await _make_task(
        client, project_id, "k771-h target", blocked_by=blocker_id
    )
    try:
        # Sanity: blocker is set.
        before = await client.get(f"/api/tasks/{target_id}", headers=headers)
        assert before.json()["blocked_by"] == blocker_id

        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": None},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["blocked_by"] is None, resp.json()
    finally:
        await client.delete(f"/api/tasks/{target_id}", headers=headers)
        await client.delete(f"/api/tasks/{blocker_id}", headers=headers)


# -----------------------------------------------------------------------------
# (i) PATCH no-op skip
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_blocked_by_noop_skip_does_not_bump_updated_at(client) -> None:
    """PATCH blocked_by with the SAME value already on the row is a no-op —
    `updated_at` must NOT change (N7 no-op skip parity)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(client, project_id, "k771-i blocker")
    target_id = await _make_task(
        client, project_id, "k771-i target", blocked_by=blocker_id
    )
    try:
        before = await client.get(f"/api/tasks/{target_id}", headers=headers)
        updated_at_before = _parse_ts(before.json()["updated_at"])

        # PATCH with the same blocked_by value → no-op.
        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": blocker_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        updated_at_after = _parse_ts(resp.json()["updated_at"])
        assert updated_at_after == updated_at_before, (
            f"updated_at bumped on no-op blocked_by PATCH: "
            f"{updated_at_before!s} -> {updated_at_after!s}"
        )
    finally:
        await client.delete(f"/api/tasks/{target_id}", headers=headers)
        await client.delete(f"/api/tasks/{blocker_id}", headers=headers)


# -----------------------------------------------------------------------------
# (j) Reverse-lookup endpoint — happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_blocks_returns_blocked_dependents(client) -> None:
    """GET /api/tasks/{id}/blocks returns the set of tasks whose blocked_by
    equals id. Two dependents → 2-element list, sorted by id ascending."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(client, project_id, "k771-j blocker")
    dep1_id = await _make_task(
        client, project_id, "k771-j dep1", blocked_by=blocker_id
    )
    dep2_id = await _make_task(
        client, project_id, "k771-j dep2", blocked_by=blocker_id
    )
    try:
        resp = await client.get(
            f"/api/tasks/{blocker_id}/blocks", headers=headers
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = [t["id"] for t in rows]
        assert ids == sorted([dep1_id, dep2_id]), (
            f"reverse-lookup ids drifted: got {ids}; expected sorted "
            f"{[dep1_id, dep2_id]}"
        )
        for t in rows:
            assert t["blocked_by"] == blocker_id
    finally:
        for tid in (dep1_id, dep2_id, blocker_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (k) Reverse-lookup — empty
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_blocks_empty_when_no_dependents(client) -> None:
    """No tasks point at this one → []."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    solo_id = await _make_task(client, project_id, "k771-k solo (no dependents)")
    try:
        resp = await client.get(f"/api/tasks/{solo_id}/blocks", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == [], resp.json()
    finally:
        await client.delete(f"/api/tasks/{solo_id}", headers=headers)


# -----------------------------------------------------------------------------
# (l) Reverse-lookup — 404 on missing task
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_blocks_404_for_nonexistent_task(client) -> None:
    """GET /api/tasks/{bogus}/blocks → 404 (mirrors /api/tasks/{id} convention)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    bogus = 999_999_999
    resp = await client.get(f"/api/tasks/{bogus}/blocks", headers=headers)
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": f"Task id={bogus} not found"}, resp.json()


# -----------------------------------------------------------------------------
# (m) History capture via to_jsonb(OLD)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_by_history_captured_in_tasks_history(
    client, db_session
) -> None:
    """The existing tasks_audit_trg uses to_jsonb(OLD), so adding a column
    auto-captures in tasks_history.snapshot. Set blocked_by on a task; query
    the most recent 'U' row for that task; assert snapshot.blocked_by reflects
    the PRIOR value (NULL since this is the first set)."""
    from sqlalchemy import text

    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(client, project_id, "k771-m blocker")
    target_id = await _make_task(client, project_id, "k771-m target (no blocker yet)")
    try:
        # PATCH to set blocked_by — fires the audit trigger.
        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": blocker_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

        # Query the most recent 'U' history row for target_id. The snapshot
        # contains the OLD row state (per the trigger fn), so blocked_by in
        # the snapshot should be NULL (the value BEFORE this PATCH).
        result = await db_session.execute(
            text(
                "SELECT snapshot FROM tasks_history "
                "WHERE task_id = :tid AND operation = 'U' "
                "ORDER BY changed_at DESC, id DESC LIMIT 1"
            ),
            {"tid": target_id},
        )
        row = result.first()
        assert row is not None, "expected at least one 'U' audit row for the PATCH"
        snapshot = row[0]
        assert "blocked_by" in snapshot, (
            f"snapshot must include blocked_by column; keys: {sorted(snapshot.keys())}"
        )
        assert snapshot["blocked_by"] is None, (
            f"OLD snapshot blocked_by should be NULL (prior value); got {snapshot['blocked_by']!r}"
        )

        # PATCH a second time to set blocked_by → None. The new 'U' row
        # snapshot should capture the prior non-null value.
        resp2 = await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": None},
            headers=headers,
        )
        assert resp2.status_code == 200, resp2.text

        result2 = await db_session.execute(
            text(
                "SELECT snapshot FROM tasks_history "
                "WHERE task_id = :tid AND operation = 'U' "
                "ORDER BY changed_at DESC, id DESC LIMIT 1"
            ),
            {"tid": target_id},
        )
        row2 = result2.first()
        assert row2 is not None
        snapshot2 = row2[0]
        assert snapshot2["blocked_by"] == blocker_id, (
            f"OLD snapshot for the clear-PATCH must capture the prior "
            f"blocker id {blocker_id}; got {snapshot2['blocked_by']!r}"
        )
    finally:
        await client.delete(f"/api/tasks/{target_id}", headers=headers)
        await client.delete(f"/api/tasks/{blocker_id}", headers=headers)


# -----------------------------------------------------------------------------
# (n) Source-text-lock for detail strings
# -----------------------------------------------------------------------------


def test_blocked_by_detail_strings_pinned_in_router_source() -> None:
    """Wire-contract pin for the Kanban #771 detail string templates that
    appear in routers/tasks.py. Drift in any of these (wording / quoting /
    branch removal) breaks the test. Mirrors the M5 / #122 / #238 pattern.

    Pinned (byte-for-byte stable per routers/tasks.py):
      - "blocked_by {payload.blocked_by} does not exist or is deleted"  (POST)
      - "blocked_by {payload.blocked_by} belongs to a different project" (POST)
      - "blocked_by {new_blocked_by} does not exist or is deleted"      (PATCH)
      - "blocked_by {new_blocked_by} belongs to a different project"    (PATCH)
      - "blocked_by cannot reference self"                              (PATCH)
      - cycle-detection template substring                              (PATCH)
      - chain-depth template substring                                  (PATCH defensive)
    """
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")

    pinned = [
        '"blocked_by {payload.blocked_by} does not exist or is deleted"',
        '"blocked_by {payload.blocked_by} belongs to a different project"',
        '"blocked_by {new_blocked_by} does not exist or is deleted"',
        '"blocked_by {new_blocked_by} belongs to a different project"',
        '"blocked_by cannot reference self"',
        "would create a cycle (depth {depth})",
        "blocked_by chain exceeds maximum depth of {_BLOCKED_BY_MAX_CHAIN_DEPTH}",
    ]
    missing = [s for s in pinned if s not in source]
    assert not missing, (
        "Kanban #771 detail strings drifted in routers/tasks.py — "
        f"missing: {missing}"
    )
