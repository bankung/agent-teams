"""Kanban #772 — tasks.sort_order + reorder endpoint + blocker-order constraint.

Locked design (2026-05-12): each task carries an optional sparse-float
`sort_order` for within-lane manual ordering. The reorder endpoint
`POST /api/tasks/{id}/reorder` is the user-facing API; direct PATCH of
`sort_order` is the escape hatch. Cross-row constraint: T.sort_order >=
B.sort_order when T.blocked_by transitively walks to B, both in same lane
(process_status=TODO) with non-null sort_orders. Wire-contract detail
strings pinned by `test_reorder_detail_strings_pinned_in_router_source`.

Coverage:
  (a) POST happy path with sort_order
  (b) PATCH happy path with sort_order (direct set)
  (c) PATCH clear sort_order to null
  (d) PATCH no-op skip (sort_order equals existing → updated_at unchanged)
  (e) reorder with both anchors → average
  (f) reorder with only before_id
  (g) reorder with only after_id
  (h) reorder with no anchors → 422 (at-least-one rule)
  (i) reorder with same id for both anchors → 422
  (j) reorder with anchor in different project → 422
  (k) reorder with anchor in different lane → 422
  (l) reorder with soft-deleted anchor → 422
  (m) reorder with nonexistent anchor → 422
  (n) reorder materializes NULL sort_orders in lane
  (o) reorder rejected by blocker-order constraint (direct)
  (p) reorder rejected by blocker-order constraint (transitive, depth ≥ 3)
  (q) PATCH sort_order rejected by blocker-order constraint
  (r) PATCH blocked_by rejected by blocker-order constraint when both
       sort_orders set
  (s) sort_order history captured in tasks_history.snapshot
  (t) reorder atomicity — pre-existing rows unchanged on validator failure
  (u) source-text-lock for the four reorder detail templates
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
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str, **extras) -> int:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _get_task(client, project_id: int, task_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    """Build a dedicated project with NO seeded tasks so sort_order math
    can be asserted without lane pollution from the agent-teams seed (which
    carries ~55 leftover tasks in TODO; the reorder materializer densifies
    those NULL rows to floor floats and contaminates expected math).
    """
    import uuid

    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"test fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# -----------------------------------------------------------------------------
# (a) POST happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_sort_order_succeeds(client) -> None:
    """POST with explicit sort_order=5.0 succeeds; TaskRead round-trips
    the value."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "k772-a explicit sort_order",
            "sort_order": 5.0,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sort_order"] == 5.0, body
    await client.delete(f"/api/tasks/{body['id']}", headers=headers)


# -----------------------------------------------------------------------------
# (b) PATCH happy path (direct set)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_sort_order_direct_set_succeeds(client) -> None:
    """Direct PATCH sort_order on a row that had NULL → 200, round-trips."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(client, project_id, "k772-b initial NULL sort_order")
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"sort_order": 12.5},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sort_order"] == 12.5, resp.json()
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (c) PATCH clear sort_order to null
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_sort_order_clear_to_null_succeeds(client) -> None:
    """PATCH sort_order=null clears the column → 200, null in response."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(
        client, project_id, "k772-c row with sort_order set", sort_order=7.0
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"sort_order": None},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sort_order"] is None, resp.json()
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (d) PATCH no-op skip
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_sort_order_noop_skip_does_not_bump_updated_at(
    client,
) -> None:
    """PATCH sort_order with the SAME value already on the row → no-op:
    updated_at unchanged (N7 parity)."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(
        client, project_id, "k772-d noop probe", sort_order=3.0
    )
    try:
        before = await _get_task(client, project_id, task_id)
        updated_at_before = _parse_ts(before["updated_at"])

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"sort_order": 3.0},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        updated_at_after = _parse_ts(resp.json()["updated_at"])
        assert updated_at_after == updated_at_before, (
            f"updated_at bumped on no-op sort_order PATCH: "
            f"{updated_at_before!s} -> {updated_at_after!s}"
        )
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (e) Reorder with both anchors → average
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_both_anchors_places_between(
    client, scaffold_cleanup
) -> None:
    """Build three rows in the same lane (TODO) with sort_order 1.0, 2.0,
    3.0. Reorder moves a 4th row to land BETWEEN row-at-1.0 (after_id) and
    row-at-3.0 (before_id) → new sort_order is the average = 2.0.

    Uses a fresh project to avoid the seeded agent-teams lane pollution
    (where the materializer would densify ~55 NULL rows and break the
    averaging assertion)."""
    project_id = await _make_fresh_project(client, scaffold_cleanup, "k772-e")
    headers = {"X-Project-Id": str(project_id)}

    a_id = await _make_task(client, project_id, "k772-e A", sort_order=1.0)
    b_id = await _make_task(client, project_id, "k772-e B", sort_order=3.0)
    moved_id = await _make_task(client, project_id, "k772-e moved")
    try:
        resp = await client.post(
            f"/api/tasks/{moved_id}/reorder",
            json={"before_id": b_id, "after_id": a_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        new_sort = resp.json()["sort_order"]
        # average of 1.0 and 3.0 is 2.0
        assert new_sort == 2.0, f"expected 2.0, got {new_sort!r}"
    finally:
        for tid in (moved_id, b_id, a_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# -----------------------------------------------------------------------------
# (f) Reorder with only before_id
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_only_before_id_places_just_above(
    client, scaffold_cleanup
) -> None:
    """Lane has one row at sort_order=5.0 with no smaller sibling. Reorder
    a moved row with before_id=that → new sort_order = 5.0 - 1.0 = 4.0.

    Uses a fresh project so the agent-teams seed's ~55 TODO rows (NULL
    sort_order) don't contaminate the lane via the densifier."""
    project_id = await _make_fresh_project(client, scaffold_cleanup, "k772-f")
    headers = {"X-Project-Id": str(project_id)}

    anchor_id = await _make_task(
        client, project_id, "k772-f anchor", sort_order=5.0
    )
    moved_id = await _make_task(client, project_id, "k772-f moved")
    try:
        resp = await client.post(
            f"/api/tasks/{moved_id}/reorder",
            json={"before_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # No smaller sibling → before_id.sort_order - 1.0 = 4.0
        assert resp.json()["sort_order"] == 4.0, resp.json()
    finally:
        for tid in (moved_id, anchor_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# -----------------------------------------------------------------------------
# (g) Reorder with only after_id
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_only_after_id_places_just_below(
    client, scaffold_cleanup
) -> None:
    """Lane has one row at sort_order=5.0 with no larger sibling. Reorder
    a moved row with after_id=that → new sort_order = 5.0 + 1.0 = 6.0.

    Uses a fresh project (see test_reorder_with_only_before_id docstring)."""
    project_id = await _make_fresh_project(client, scaffold_cleanup, "k772-g")
    headers = {"X-Project-Id": str(project_id)}

    anchor_id = await _make_task(
        client, project_id, "k772-g anchor", sort_order=5.0
    )
    moved_id = await _make_task(client, project_id, "k772-g moved")
    try:
        resp = await client.post(
            f"/api/tasks/{moved_id}/reorder",
            json={"after_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sort_order"] == 6.0, resp.json()
    finally:
        for tid in (moved_id, anchor_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# -----------------------------------------------------------------------------
# (h) Reorder with no anchors → 422
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_no_anchors_returns_422(client) -> None:
    """Body `{}` violates the at-least-one rule → 422."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    task_id = await _make_task(client, project_id, "k772-h moved")
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/reorder",
            json={},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        # Pydantic-level validator detail surfaces under detail[0].msg.
        body = resp.json()
        assert "before_id" in str(body) and "after_id" in str(body), body
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# -----------------------------------------------------------------------------
# (i) Reorder with same id for both anchors → 422
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_same_id_for_both_anchors_returns_422(
    client,
) -> None:
    """Body `{before_id: X, after_id: X}` violates the not-same rule → 422."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    anchor_id = await _make_task(
        client, project_id, "k772-i anchor", sort_order=1.0
    )
    task_id = await _make_task(client, project_id, "k772-i moved")
    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/reorder",
            json={"before_id": anchor_id, "after_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert "cannot reference the same task" in str(body), body
    finally:
        for tid in (task_id, anchor_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (j) Reorder with anchor in different project → 422
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_anchor_in_different_project_returns_422(
    client, scaffold_cleanup
) -> None:
    """Anchor row is in project B; reorder request scoped to project A → 422."""
    import uuid

    project_a_id = await _get_project_id(client)
    name_b = scaffold_cleanup(f"k772-j-proj-{uuid.uuid4().hex[:8]}")
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

    anchor_in_b = await _make_task(client, project_b_id, "k772-j anchor in B")
    moved_in_a = await _make_task(client, project_a_id, "k772-j moved in A")
    try:
        resp = await client.post(
            f"/api/tasks/{moved_in_a}/reorder",
            json={"before_id": anchor_in_b},
            headers=headers_a,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "detail": f"reorder anchor #{anchor_in_b} not found in project"
        }, resp.json()
    finally:
        await client.delete(f"/api/tasks/{moved_in_a}", headers=headers_a)
        await client.delete(f"/api/tasks/{anchor_in_b}", headers=headers_b)
        await client.delete(f"/api/projects/{project_b_id}")


# -----------------------------------------------------------------------------
# (k) Reorder with anchor in different lane → 422
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_anchor_in_different_lane_returns_422(
    client,
) -> None:
    """Moved task is in TODO (process_status=1), anchor in IN_PROGRESS
    (process_status=2) → 422 with the lane-mismatch detail."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    moved_id = await _make_task(client, project_id, "k772-k moved in TODO")
    anchor_id = await _make_task(client, project_id, "k772-k anchor (to bump)")
    # Bump anchor to in_progress.
    bump = await client.patch(
        f"/api/tasks/{anchor_id}",
        json={"process_status": 2},
        headers=headers,
    )
    assert bump.status_code == 200, bump.text

    try:
        resp = await client.post(
            f"/api/tasks/{moved_id}/reorder",
            json={"before_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert "share the same process_status" in body["detail"], body
        assert "moved=1" in body["detail"], body
        assert "before_id_status=2" in body["detail"], body
    finally:
        for tid in (moved_id, anchor_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (l) Reorder with soft-deleted anchor → 422
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_soft_deleted_anchor_returns_422(client) -> None:
    """Anchor is soft-deleted (status=0) → 422."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    anchor_id = await _make_task(client, project_id, "k772-l doomed anchor")
    moved_id = await _make_task(client, project_id, "k772-l moved")
    del_resp = await client.delete(
        f"/api/tasks/{anchor_id}", headers=headers
    )
    assert del_resp.status_code == 204

    try:
        resp = await client.post(
            f"/api/tasks/{moved_id}/reorder",
            json={"before_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "detail": f"reorder anchor #{anchor_id} is deleted"
        }, resp.json()
    finally:
        await client.delete(f"/api/tasks/{moved_id}", headers=headers)


# -----------------------------------------------------------------------------
# (m) Reorder with nonexistent anchor → 422
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_with_nonexistent_anchor_returns_422(client) -> None:
    """Anchor id doesn't exist → 422."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    moved_id = await _make_task(client, project_id, "k772-m moved")
    bogus = 999_999_999
    try:
        resp = await client.post(
            f"/api/tasks/{moved_id}/reorder",
            json={"before_id": bogus},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "detail": f"reorder anchor #{bogus} not found in project"
        }, resp.json()
    finally:
        await client.delete(f"/api/tasks/{moved_id}", headers=headers)


# -----------------------------------------------------------------------------
# (n) Reorder materializes NULL sort_orders in same lane
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_materializes_null_sort_orders_in_same_lane(
    client, scaffold_cleanup
) -> None:
    """In a fresh project with three NEW rows (all sort_order=NULL), the
    first reorder densifies the lane: pre-existing NULL anchors land at
    floor floats 1.0, 2.0; the moved row's sort_order is computed against
    those non-null anchors.

    Use a dedicated project so prior-test rows don't contaminate the lane
    state (the live test DB has 50+ leftover tasks in TODO from other
    suites — densifying that lane is fine but harder to assert about).
    """
    import uuid

    name_p = scaffold_cleanup(f"k772-n-proj-{uuid.uuid4().hex[:8]}")
    proj_resp = await client.post(
        "/api/projects",
        json={
            "name": name_p,
            "description": f"test fixture for {name_p}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert proj_resp.status_code == 201, proj_resp.text
    project_id = proj_resp.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    # Three rows, all sort_order=NULL (default). Created in order A, B, C
    # so the materializer's (NULLS LAST, created_at ASC) walks A → B → C
    # and assigns 1.0, 2.0, 3.0 respectively.
    a_id = await _make_task(client, project_id, "k772-n A")
    b_id = await _make_task(client, project_id, "k772-n B")
    c_id = await _make_task(client, project_id, "k772-n C (will move)")
    try:
        # Reorder C between A and B. Materialization assigns A=1.0, B=2.0
        # (C is excluded from materialization since it's the moved task);
        # then average = 1.5.
        resp = await client.post(
            f"/api/tasks/{c_id}/reorder",
            json={"before_id": b_id, "after_id": a_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sort_order"] == 1.5, resp.json()
        # And A / B should now reflect the floor floats.
        a_after = await _get_task(client, project_id, a_id)
        b_after = await _get_task(client, project_id, b_id)
        assert a_after["sort_order"] == 1.0, a_after
        assert b_after["sort_order"] == 2.0, b_after
    finally:
        for tid in (c_id, b_id, a_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# -----------------------------------------------------------------------------
# (o) Reorder rejected by blocker-order constraint (direct)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_blocker_constraint_direct_rejection(client) -> None:
    """T.blocked_by=B both in TODO with sort_orders set. Reorder T to a
    position BEFORE B (smaller sort_order) → 422 with the locked detail."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(
        client, project_id, "k772-o blocker B", sort_order=10.0
    )
    target_id = await _make_task(
        client,
        project_id,
        "k772-o target T (blocked_by B)",
        sort_order=20.0,
        blocked_by=blocker_id,
    )
    # An anchor at sort_order=5.0 — to attempt to move T to ~ 4.0 (below B).
    anchor_id = await _make_task(
        client, project_id, "k772-o anchor at 5.0", sort_order=5.0
    )
    try:
        resp = await client.post(
            f"/api/tasks/{target_id}/reorder",
            json={"before_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert (
            body["detail"]
            == f"task #{target_id} cannot be ordered before its blocker #{blocker_id}"
        ), body
    finally:
        for tid in (target_id, anchor_id, blocker_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (p) Reorder rejected — transitive blocker chain depth 3+
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_blocker_constraint_transitive_rejection_depth_3_or_more(
    client,
) -> None:
    """Build a chain T → A → B → C where each blocked_by the next, all in
    TODO lane, sort_orders ascending 1, 2, 3, 4. Try to reorder T to below
    C (which is at sort_order=1.0) → walker hits C at depth 3, finds
    T.sort_order < C.sort_order → 422 with (T, C) pair.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    # Build in chain order: C is the deepest blocker (largest sort_order
    # so T can validly be above it initially). Actually for the test we
    # want C.sort_order > T's-NEW-target so the violation fires.
    # Lay out: C=10, B=8, A=6, T=4. Each blocks the next: T.blocked_by=A,
    # A.blocked_by=B, B.blocked_by=C. Walk: T → A → B → C (depth 3).
    # All in same TODO lane. Then try reorder T below an anchor at 9.0
    # via before_id=9-anchor. T's new sort_order < C.sort_order (10) →
    # blocker-order violation on C.
    c_id = await _make_task(client, project_id, "k772-p C", sort_order=10.0)
    b_id = await _make_task(
        client, project_id, "k772-p B", sort_order=8.0, blocked_by=c_id
    )
    a_id = await _make_task(
        client, project_id, "k772-p A", sort_order=6.0, blocked_by=b_id
    )
    t_id = await _make_task(
        client, project_id, "k772-p T", sort_order=4.0, blocked_by=a_id
    )
    anchor9 = await _make_task(
        client, project_id, "k772-p anchor at 9.0", sort_order=9.0
    )
    try:
        # Move T to before anchor9 → ~ 9.5 (no larger sibling in lane).
        # Wait — anchor9.sort_order=9.0, and C=10 > 9.0 so there IS a
        # larger sibling. Largest sort_order < 9.0 in lane (excluding T):
        # B=8.0. So new = average(8.0, 9.0) = 8.5. We need this to be
        # < C=10.0 → yes → expected violation against C at depth 3.
        resp = await client.post(
            f"/api/tasks/{t_id}/reorder",
            json={"after_id": anchor9},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert (
            body["detail"]
            == f"task #{t_id} cannot be ordered before its blocker #{c_id}"
        ), body
    finally:
        # Unwire chain so deletes are cycle-free.
        for tid in (t_id, a_id, b_id):
            await client.patch(
                f"/api/tasks/{tid}",
                json={"blocked_by": None},
                headers=headers,
            )
        for tid in (t_id, anchor9, a_id, b_id, c_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (q) PATCH sort_order rejected by blocker-order constraint
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_sort_order_blocker_constraint_rejection(client) -> None:
    """Direct PATCH sort_order on T that puts T below its blocker B → 422."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(
        client, project_id, "k772-q blocker B", sort_order=10.0
    )
    target_id = await _make_task(
        client,
        project_id,
        "k772-q target T",
        sort_order=20.0,
        blocked_by=blocker_id,
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"sort_order": 5.0},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "detail": (
                f"task #{target_id} cannot be ordered before its blocker "
                f"#{blocker_id}"
            )
        }, resp.json()
    finally:
        for tid in (target_id, blocker_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (r) PATCH blocked_by rejected by blocker-order check when sort_orders set
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_blocked_by_blocker_order_check_when_sort_orders_set(
    client,
) -> None:
    """Two TODO rows: target T at sort_order=5.0, candidate B at
    sort_order=10.0. PATCH T.blocked_by=B → resolved T.sort_order=5.0 < B's
    10.0 → 422 with the blocker-order detail.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    target_id = await _make_task(
        client, project_id, "k772-r target T", sort_order=5.0
    )
    blocker_id = await _make_task(
        client, project_id, "k772-r blocker B", sort_order=10.0
    )
    try:
        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": blocker_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "detail": (
                f"task #{target_id} cannot be ordered before its blocker "
                f"#{blocker_id}"
            )
        }, resp.json()
    finally:
        for tid in (target_id, blocker_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (s) sort_order history captured in tasks_history.snapshot
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sort_order_history_captured_in_tasks_history(
    client, db_session
) -> None:
    """The audit trigger uses `to_jsonb(OLD)` — set sort_order via PATCH,
    query the most-recent 'U' row, verify the snapshot carries the OLD
    value (NULL for the first set; non-null for a subsequent change)."""
    from sqlalchemy import text

    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    target_id = await _make_task(
        client, project_id, "k772-s target (no sort_order yet)"
    )
    try:
        # PATCH 1: NULL → 7.0. Snapshot.sort_order should be NULL.
        resp = await client.patch(
            f"/api/tasks/{target_id}",
            json={"sort_order": 7.0},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

        result = await db_session.execute(
            text(
                "SELECT snapshot FROM tasks_history "
                "WHERE task_id = :tid AND operation = 'U' "
                "ORDER BY changed_at DESC, id DESC LIMIT 1"
            ),
            {"tid": target_id},
        )
        row = result.first()
        assert row is not None, "expected a 'U' audit row"
        snapshot = row[0]
        assert "sort_order" in snapshot, (
            f"snapshot missing sort_order column; keys: {sorted(snapshot.keys())}"
        )
        assert snapshot["sort_order"] is None, (
            f"OLD snapshot sort_order should be NULL; got {snapshot['sort_order']!r}"
        )

        # PATCH 2: 7.0 → 11.0. Snapshot.sort_order should be 7.0.
        resp2 = await client.patch(
            f"/api/tasks/{target_id}",
            json={"sort_order": 11.0},
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
        assert snapshot2["sort_order"] == 7.0, (
            f"OLD snapshot for the second PATCH must capture 7.0; "
            f"got {snapshot2['sort_order']!r}"
        )
    finally:
        await client.delete(f"/api/tasks/{target_id}", headers=headers)


# -----------------------------------------------------------------------------
# (t) Reorder atomicity — pre-existing rows unchanged on validator failure
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_atomicity_on_validator_failure(client) -> None:
    """When the blocker-order constraint rejects a reorder, NO pre-existing
    row's sort_order is mutated (densification rollback). Snapshot two
    rows pre/post a failing reorder; both should be byte-identical."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    blocker_id = await _make_task(
        client, project_id, "k772-t blocker B", sort_order=10.0
    )
    target_id = await _make_task(
        client,
        project_id,
        "k772-t target T",
        sort_order=20.0,
        blocked_by=blocker_id,
    )
    anchor_id = await _make_task(
        client, project_id, "k772-t anchor at 3.0", sort_order=3.0
    )
    try:
        # Snapshot pre.
        pre_blocker = await _get_task(client, project_id, blocker_id)
        pre_anchor = await _get_task(client, project_id, anchor_id)

        # Try to reorder T below anchor (sort_order ~ 2.0) → violates
        # T.sort_order >= B.sort_order(10.0) → 422.
        resp = await client.post(
            f"/api/tasks/{target_id}/reorder",
            json={"before_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

        # Snapshot post.
        post_blocker = await _get_task(client, project_id, blocker_id)
        post_anchor = await _get_task(client, project_id, anchor_id)
        # Pre-existing rows MUST NOT have been mutated.
        assert post_blocker["sort_order"] == pre_blocker["sort_order"] == 10.0
        assert post_anchor["sort_order"] == pre_anchor["sort_order"] == 3.0
        # updated_at should also be unchanged (no audit row written).
        assert post_blocker["updated_at"] == pre_blocker["updated_at"]
        assert post_anchor["updated_at"] == pre_anchor["updated_at"]
    finally:
        for tid in (target_id, anchor_id, blocker_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (t2) Reorder densification reverts on validator failure (WARN-1)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_densification_reverts_on_validator_failure(
    client, scaffold_cleanup
) -> None:
    """When a reorder triggers densification of NULL lane-mates AND the
    blocker-order validator then rejects, the densified-but-not-yet-committed
    sort_order values MUST revert to NULL on rollback.

    Strengthens test_reorder_atomicity_on_validator_failure (which only
    exercised the no-mutation path on already-densified rows). Here every
    lane row starts NULL, so the materializer ACTUALLY runs and assigns
    floor floats — the rollback must un-do those assignments.

    Uses a fresh project to keep the lane to exactly 4 rows; the seeded
    `agent-teams` project has ~55 TODO rows which would still densify
    correctly but make the failure mode harder to read.
    """
    project_id = await _make_fresh_project(client, scaffold_cleanup, "k772-t2")
    headers = {"X-Project-Id": str(project_id)}

    # All 4 rows in TODO with sort_order=NULL (default on POST). Creation
    # order matters: materializer sorts by (sort_order NULLS LAST, created_at
    # ASC), so anchor1 / anchor2 / blocker get 1.0 / 2.0 / 3.0 respectively;
    # target is excluded from densification by the reorder endpoint.
    anchor1_id = await _make_task(client, project_id, "k772-t2 anchor1")
    anchor2_id = await _make_task(client, project_id, "k772-t2 anchor2")
    blocker_id = await _make_task(client, project_id, "k772-t2 blocker B")
    target_id = await _make_task(
        client, project_id, "k772-t2 target T", blocked_by=blocker_id
    )
    try:
        # Pre-check: every row has sort_order=NULL.
        for tid in (anchor1_id, anchor2_id, blocker_id, target_id):
            row = await _get_task(client, project_id, tid)
            assert row["sort_order"] is None, (tid, row)

        # Reorder target to land BEFORE anchor1. After densification:
        # anchor1=1.0, anchor2=2.0, blocker=3.0. target lands at
        # anchor1.sort_order - 1.0 = 0.0 (no smaller sibling). Then the
        # blocker-order constraint walks T.blocked_by → blocker (3.0) and
        # finds 0.0 < 3.0 → 422 → rollback.
        resp = await client.post(
            f"/api/tasks/{target_id}/reorder",
            json={"before_id": anchor1_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "detail": (
                f"task #{target_id} cannot be ordered before its blocker "
                f"#{blocker_id}"
            )
        }, resp.json()

        # Post-check: every row STILL has sort_order=NULL. The densification
        # was rolled back along with the failed reorder.
        for tid in (anchor1_id, anchor2_id, blocker_id, target_id):
            row = await _get_task(client, project_id, tid)
            assert row["sort_order"] is None, (
                f"task #{tid} sort_order should be NULL after rollback; "
                f"got {row['sort_order']!r}"
            )
    finally:
        # Unwire blocker chain so deletes are cycle-free.
        await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": None},
            headers=headers,
        )
        for tid in (target_id, blocker_id, anchor2_id, anchor1_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (t3) Same-lane mismatch detail renders `null` for absent anchor (NIT-4)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_same_lane_mismatch_detail_renders_null_for_missing_anchor(
    client,
) -> None:
    """WARN-2 fix: the 422 detail for same-lane mismatch must render
    Optional[int]=None as JSON-conformant `null` (not Python's `"None"`).
    Trigger with ONLY before_id supplied (after_id absent) — assert the
    detail substring `after_id_status=null` appears verbatim.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    # target in TODO (process_status=1), anchor in IN_PROGRESS (=2) → cross-lane.
    target_id = await _make_task(client, project_id, "k772-t3 target")
    anchor_id = await _make_task(
        client, project_id, "k772-t3 anchor in-progress", process_status=2
    )
    try:
        resp = await client.post(
            f"/api/tasks/{target_id}/reorder",
            json={"before_id": anchor_id},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # WARN-2 wire contract: `null` (lowercase) appears verbatim for the
        # absent after_id_status side; before_id_status renders the integer.
        assert "before_id_status=2" in detail, detail
        assert "after_id_status=null" in detail, detail
        assert "moved=1" in detail, detail
        # And the leading template fragment is present too.
        assert (
            f"reorder requires moved task #{target_id} and anchor(s) to "
            "share the same process_status" in detail
        ), detail
    finally:
        for tid in (target_id, anchor_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (t4) Blocker chain exactly at depth budget does not falsely overflow (WARN-3)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_blocker_order_chain_exactly_10_deep_does_not_overflow(
    client, scaffold_cleanup
) -> None:
    """WARN-3 fix: chain of exactly _REORDER_BLOCKER_CHAIN_DEPTH (=10)
    blockers ending in NULL must NOT raise 'exceeds maximum depth'.

    Builds T → B1 → B2 → ... → B10 → NULL with sort_orders such that T's
    new position is strictly greater than every B's sort_order (no
    blocker-order violation). Reorders T to a position above all blockers
    and asserts 200.

    Fresh project so the chain dominates the lane (avoids seed pollution
    of the agent-teams project's ~55 TODO rows).
    """
    project_id = await _make_fresh_project(client, scaffold_cleanup, "k772-t4")
    headers = {"X-Project-Id": str(project_id)}

    # Build from the tail: B10 (no blocker), then B9 (blocked_by=B10), …,
    # B1 (blocked_by=B2), T (blocked_by=B1). Sort_orders ascending so T's
    # new position above the largest blocker can be validated without
    # tripping the sort_order rule on any link.
    blocker_ids: list[int] = []
    prev_blocker: int | None = None
    # Create B10..B1 in reverse so each one references the previously-created
    # deeper blocker. Sort_orders: B10=11, B9=10, ..., B1=2.
    for hop in range(10, 0, -1):
        kwargs = {"sort_order": float(hop + 1)}
        if prev_blocker is not None:
            kwargs["blocked_by"] = prev_blocker
        bid = await _make_task(
            client, project_id, f"k772-t4 B{hop}", **kwargs
        )
        blocker_ids.append(bid)
        prev_blocker = bid
    # blocker_ids is now [B10, B9, ..., B1]; B1 is the last element.
    b1_id = blocker_ids[-1]
    # T starts at sort_order=1.0 (below all blockers) — valid initial state.
    target_id = await _make_task(
        client, project_id, "k772-t4 target T", sort_order=1.0, blocked_by=b1_id
    )
    # Anchor at sort_order=100.0 so reordering T after it gives T a position
    # strictly greater than every B (B10=11 is the max). No violation.
    anchor_id = await _make_task(
        client, project_id, "k772-t4 anchor at 100", sort_order=100.0
    )
    try:
        resp = await client.post(
            f"/api/tasks/{target_id}/reorder",
            json={"after_id": anchor_id},
            headers=headers,
        )
        # Before the WARN-3 fix, this 422'd with
        # "reorder blocker chain exceeds maximum depth of 10" because the
        # for-else fired after iterating depth 1..10 without break.
        # After the fix (range +2, return→break), the depth-11 iteration
        # detects cursor=None and breaks cleanly.
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # No smaller sibling above anchor → new = anchor.sort_order + 1.0.
        assert body["sort_order"] == 101.0, body
    finally:
        # Unwire chain links so deletes are cycle-free. Order matters —
        # unwire T first, then walk B1..B10.
        await client.patch(
            f"/api/tasks/{target_id}",
            json={"blocked_by": None},
            headers=headers,
        )
        for bid in blocker_ids:  # B10, B9, ..., B1 → patch each blocked_by=None
            await client.patch(
                f"/api/tasks/{bid}",
                json={"blocked_by": None},
                headers=headers,
            )
        for tid in (target_id, anchor_id, *blocker_ids):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------------
# (u) Source-text-lock for reorder detail strings
# -----------------------------------------------------------------------------


def test_reorder_detail_strings_pinned_in_router_source() -> None:
    """Wire-contract pin for the Kanban #772 detail string templates in
    routers/tasks.py. Drift in any of these (wording / quoting / branch
    removal) breaks the test. Mirrors the M5 / #122 / #238 / #771 pattern.

    Pinned (byte-for-byte stable per routers/tasks.py):
      - "task #{target_id} cannot be ordered before its blocker #{blocker.id}"
      - "reorder blocker chain exceeds maximum depth of {_REORDER_BLOCKER_CHAIN_DEPTH}"
      - "reorder anchor #{anchor_id} not found in project"
      - "reorder anchor #{anchor_id} is deleted"
      - full same-lane mismatch f-string template (incl. `before_id_status=...`
        / `after_id_status=...` and the `_opt_int_str(...)` calls that render
        Optional[int] as JSON-conformant `null` — WARN-2 fix).
    """
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")

    pinned = [
        '"task #{target_id} cannot be ordered before its blocker #{blocker.id}"',
        '"reorder blocker chain exceeds maximum depth of {_REORDER_BLOCKER_CHAIN_DEPTH}"',
        '"reorder anchor #{anchor_id} not found in project"',
        '"reorder anchor #{anchor_id} is deleted"',
        # Full same-lane mismatch template — verify every fragment of the
        # f-string (NIT-4 tightening). The four f-string lines below appear
        # consecutively in router source; we pin each fragment.
        'f"reorder requires moved task #{task_id} and anchor(s) to "',
        'f"share the same process_status; moved={task.process_status} "',
        'f"before_id_status={_opt_int_str(before_status)} "',
        'f"after_id_status={_opt_int_str(after_status)}"',
    ]
    missing = [s for s in pinned if s not in source]
    assert not missing, (
        "Kanban #772 detail strings drifted in routers/tasks.py — "
        f"missing: {missing}"
    )
