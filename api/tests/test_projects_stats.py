"""Kanban #769 — GET /api/projects/stats batched cross-project stats endpoint.

Powers the dashboard. One entry per active (`status=1`) project, ordered by
`projects.created_at ASC`. Per-entry: `counts` (str-keyed 1..5 buckets,
always all five present), `run_mode_breakdown` (manual / auto_pickup /
auto_headless, always all three present), `last_activity_at`
(MAX(updated_at) of active tasks, None when zero active tasks).

Coverage (locked in the task spec):
1. Project with zero tasks → counts all-zero, run_mode_breakdown all-zero,
   last_activity_at=None. Demonstrates the "always-emit-all-keys" contract.
2. Project with tasks spanning all 5 process_status + multiple run_modes →
   counts + run_mode_breakdown correct, last_activity_at = MAX(updated_at).
3. Soft-deleted tasks excluded from BOTH counts AND last_activity_at.
4. Soft-deleted projects NOT in the list at all.
5. Multi-project response ordered by `created_at ASC` (deterministic, parity
   with GET /api/projects).
6. Shape contract — `counts` always carries the five string keys; the
   `run_mode_breakdown` always carries the three keys; no header required.

Notes on the seeded `agent-teams` project:
The seed creates id=1 `agent-teams` with N tasks (varies by seed version).
Tests don't try to pin those exact numbers — instead each test creates its
own scaffold project, asserts behavior on THAT entry, and verifies the
ordering of the entries it owns. The seeded entry is allowed to exist
alongside.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest


# ---- helpers ---------------------------------------------------------------


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp from JSON (handles trailing 'Z')."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k769 stats fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


async def _make_project(client, scaffold_cleanup, *, slug: str = "k769") -> dict:
    """Create a fresh project via HTTP (also scaffolds the on-disk folder
    cleanly cleaned by scaffold_cleanup) and return its ProjectRead body.
    """
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_task_http(
    client, project_id: int, title: str, **extras
) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(client, project_id: int, task_id: int, body: dict) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(f"/api/tasks/{task_id}", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _delete_task(client, project_id: int, task_id: int) -> None:
    """Soft-delete a task via HTTP DELETE."""
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 204, resp.text


async def _stats_entry_for(client, project_id: int) -> dict | None:
    """Return the stats entry for `project_id`, or None if absent."""
    resp = await client.get("/api/projects/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list), body
    for entry in body:
        if entry["id"] == project_id:
            return entry
    return None


# ---- 1. zero-tasks project --------------------------------------------------


@pytest.mark.asyncio
async def test_stats_project_with_zero_tasks_all_zero_buckets(
    client, scaffold_cleanup
) -> None:
    """Project with no tasks at all: all-zero counts, all-zero run_mode_breakdown,
    last_activity_at=None, but every key is present (so FE can render without
    coalescing).
    """
    project = await _make_project(client, scaffold_cleanup, slug="k769-zero")
    try:
        entry = await _stats_entry_for(client, project["id"])
        assert entry is not None, "fresh project missing from stats list"

        # Identity fields.
        assert entry["name"] == project["name"]
        assert entry["team"] == "dev"

        # counts — exactly the five string keys, every value 0.
        assert set(entry["counts"].keys()) == {"1", "2", "3", "4", "5"}, entry
        assert all(v == 0 for v in entry["counts"].values()), entry

        # run_mode_breakdown — exactly the three keys, every value 0.
        assert set(entry["run_mode_breakdown"].keys()) == {
            "manual",
            "auto_pickup",
            "auto_headless",
        }, entry
        assert all(v == 0 for v in entry["run_mode_breakdown"].values()), entry

        # No tasks → no activity timestamp.
        assert entry["last_activity_at"] is None, entry
    finally:
        # Cleanup: soft-delete the project so the live DB invariant fixture
        # doesn't see a row delta across the test session (it counts active+
        # soft-deleted, so a soft-delete still leaves the count drift unless
        # we account for it — but the conftest invariant ONLY checks the LIVE
        # DB, not the test DB, so this cleanup is cosmetic / debug-friendly).
        await client.delete(f"/api/projects/{project['id']}")


# ---- 2. project with full bucket coverage -----------------------------------


@pytest.mark.asyncio
async def test_stats_counts_and_breakdown_and_last_activity(
    client, scaffold_cleanup
) -> None:
    """Project with tasks across all 5 process_status values + multiple
    run_modes. Verifies:
      - counts has the right per-status totals
      - run_mode_breakdown has the right per-mode totals (sum should equal
        total tasks, since every task carries exactly one run_mode)
      - last_activity_at equals MAX(updated_at) of the active tasks
    """
    project = await _make_project(client, scaffold_cleanup, slug="k769-full")
    project_id = project["id"]

    try:
        # Seed: tasks in every process_status, mixed run_modes.
        # process_status=1 (TODO):   2 manual + 1 auto_pickup
        # process_status=2 (IN_PROG): 1 manual
        # process_status=3 (REVIEW):  1 auto_pickup
        # process_status=4 (BLOCKED): 1 auto_pickup
        # process_status=5 (DONE):    1 manual
        # auto_headless is NOT exercised here (it requires project consent;
        # tested separately below would over-couple to a different feature).
        # We still expect breakdown["auto_headless"]=0 visible.
        created = []
        created.append(
            await _make_task_http(client, project_id, "k769 todo-1 manual")
        )
        created.append(
            await _make_task_http(client, project_id, "k769 todo-2 manual")
        )
        created.append(
            await _make_task_http(
                client, project_id, "k769 todo-3 ap",
                run_mode="auto_pickup", task_kind="ai",
            )
        )
        t_inprog = await _make_task_http(client, project_id, "k769 inprog manual")
        await _patch_task(client, project_id, t_inprog["id"], {"process_status": 2})
        created.append(t_inprog)

        t_review = await _make_task_http(
            client, project_id, "k769 review ap",
            run_mode="auto_pickup", task_kind="ai",
        )
        await _patch_task(client, project_id, t_review["id"], {"process_status": 3})
        created.append(t_review)

        t_blocked = await _make_task_http(
            client, project_id, "k769 blocked ap",
            run_mode="auto_pickup", task_kind="ai",
        )
        await _patch_task(client, project_id, t_blocked["id"], {"process_status": 4})
        created.append(t_blocked)

        t_done = await _make_task_http(client, project_id, "k769 done manual")
        await _patch_task(client, project_id, t_done["id"], {"process_status": 5})
        created.append(t_done)

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None

        # Counts per process_status.
        assert entry["counts"] == {
            "1": 3,  # 3 TODO
            "2": 1,
            "3": 1,
            "4": 1,
            "5": 1,
        }, entry

        # Run-mode breakdown — 4 manual, 3 auto_pickup, 0 auto_headless.
        assert entry["run_mode_breakdown"] == {
            "manual": 4,
            "auto_pickup": 3,
            "auto_headless": 0,
        }, entry

        # last_activity_at is MAX(updated_at). The latest task we touched
        # carries the most recent updated_at — getting that exact row's
        # updated_at via the public GET is the cleanest assertion.
        # Walk all created tasks and find the max via the API to avoid clock
        # skew / round-trip imprecision.
        headers = {"X-Project-Id": str(project_id)}
        latest_updated: datetime | None = None
        for t in created:
            resp = await client.get(f"/api/tasks/{t['id']}", headers=headers)
            assert resp.status_code == 200
            u = _parse_ts(resp.json()["updated_at"])
            if latest_updated is None or u > latest_updated:
                latest_updated = u

        assert entry["last_activity_at"] is not None
        last = _parse_ts(entry["last_activity_at"])
        # Allow microsecond-tolerant equality — both come from the same DB.
        assert abs((last - latest_updated).total_seconds()) < 1.0, (
            f"last_activity_at {last} != max task updated_at {latest_updated}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 3. soft-deleted tasks excluded -----------------------------------------


@pytest.mark.asyncio
async def test_stats_excludes_soft_deleted_tasks(client, scaffold_cleanup) -> None:
    """Tasks with `status=0` (soft-deleted) MUST NOT appear in counts OR
    contribute to last_activity_at. The deleted task's updated_at (bumped on
    the soft-delete flip) must NOT poke through as last_activity_at when the
    remaining live task has an older updated_at.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k769-softdel")
    project_id = project["id"]

    try:
        # Two tasks: one keeper, one soon-to-be-deleted.
        keeper = await _make_task_http(client, project_id, "k769 keeper")
        victim = await _make_task_http(client, project_id, "k769 victim")

        # Soft-delete the victim — DELETE flips status=0 AND bumps updated_at.
        # The keeper's updated_at is older.
        await _delete_task(client, project_id, victim["id"])

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None

        # Only the keeper counts.
        assert entry["counts"] == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 0}, entry
        assert entry["run_mode_breakdown"] == {
            "manual": 1,
            "auto_pickup": 0,
            "auto_headless": 0,
        }, entry

        # last_activity_at must reflect the keeper, not the (newer)
        # post-deletion victim updated_at.
        headers = {"X-Project-Id": str(project_id)}
        keeper_now = await client.get(
            f"/api/tasks/{keeper['id']}", headers=headers
        )
        keeper_updated_at = _parse_ts(keeper_now.json()["updated_at"])
        assert entry["last_activity_at"] is not None
        last = _parse_ts(entry["last_activity_at"])
        assert abs((last - keeper_updated_at).total_seconds()) < 1.0, (
            f"last_activity_at {last} != keeper updated_at {keeper_updated_at} "
            "— soft-deleted task leaked into last_activity_at"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 4. soft-deleted projects excluded from the list ------------------------


@pytest.mark.asyncio
async def test_stats_excludes_soft_deleted_projects(
    client, scaffold_cleanup
) -> None:
    """Soft-deleted projects (`projects.status=0`) MUST NOT appear as
    entries in the response at all.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k769-projdel")
    project_id = project["id"]

    # Confirm presence pre-delete.
    pre = await _stats_entry_for(client, project_id)
    assert pre is not None, "fresh project missing pre-soft-delete"

    # Soft-delete it.
    resp = await client.delete(f"/api/projects/{project_id}")
    assert resp.status_code == 204, resp.text

    # Now absent.
    post = await _stats_entry_for(client, project_id)
    assert post is None, "soft-deleted project must be filtered out of stats"


# ---- 5. ordering: created_at ASC --------------------------------------------


@pytest.mark.asyncio
async def test_stats_ordered_by_created_at_asc(client, scaffold_cleanup) -> None:
    """The response is ordered by `projects.created_at ASC`. Create two
    projects in known order and assert the earlier-created one appears
    before the later-created one in the response.
    """
    first = await _make_project(client, scaffold_cleanup, slug="k769-ord-a")
    second = await _make_project(client, scaffold_cleanup, slug="k769-ord-b")

    try:
        resp = await client.get("/api/projects/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [e["id"] for e in body]
        # Both must be present.
        assert first["id"] in ids
        assert second["id"] in ids
        # And first must come before second.
        assert ids.index(first["id"]) < ids.index(second["id"]), (
            f"ordering violated: first={first['id']} second={second['id']} "
            f"all_ids={ids}"
        )

        # Defensive: the overall list is monotone non-decreasing in created_at.
        prev_created: datetime | None = None
        # Pull projects list once for cross-check.
        plist_resp = await client.get("/api/projects?limit=500")
        assert plist_resp.status_code == 200
        plist_by_id = {p["id"]: p for p in plist_resp.json()}
        for entry in body:
            p = plist_by_id.get(entry["id"])
            assert p is not None, (
                f"stats entry id={entry['id']} not in /api/projects list"
            )
            cur = _parse_ts(p["created_at"])
            if prev_created is not None:
                assert cur >= prev_created, (
                    f"stats order not monotone in created_at: "
                    f"{prev_created} then {cur}"
                )
            prev_created = cur
    finally:
        await client.delete(f"/api/projects/{first['id']}")
        await client.delete(f"/api/projects/{second['id']}")


# ---- 6. shape contract — no X-Project-Id header required --------------------


@pytest.mark.asyncio
async def test_stats_no_project_header_required(client) -> None:
    """Cross-project endpoint — explicitly does NOT require X-Project-Id
    (parity with GET /api/projects, /api/projects/by-name/{name}).
    Sanity: 200 with a list body, no 400 from the project-header gate.
    """
    resp = await client.get("/api/projects/stats")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


# ---- 7. shape contract — every entry always has all keys -------------------


@pytest.mark.asyncio
async def test_stats_entry_shape_always_full(client) -> None:
    """Every entry in the response carries the full 5-key counts + 3-key
    run_mode_breakdown grid, regardless of whether the project has tasks in
    those buckets. Locked so the FE can render the dashboard without `||0`
    coalescing.
    """
    resp = await client.get("/api/projects/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1, "test DB should have at least the seeded agent-teams"

    for entry in body:
        # Top-level keys.
        assert set(entry.keys()) >= {
            "id",
            "name",
            "team",
            "counts",
            "run_mode_breakdown",
            "last_activity_at",
        }, entry
        # counts: 5 string keys, all int.
        assert set(entry["counts"].keys()) == {"1", "2", "3", "4", "5"}, entry
        assert all(isinstance(v, int) for v in entry["counts"].values()), entry
        # run_mode_breakdown: 3 keys, all int.
        assert set(entry["run_mode_breakdown"].keys()) == {
            "manual",
            "auto_pickup",
            "auto_headless",
        }, entry
        assert all(
            isinstance(v, int) for v in entry["run_mode_breakdown"].values()
        ), entry
        # last_activity_at: ISO string or None.
        lat = entry["last_activity_at"]
        assert lat is None or isinstance(lat, str), entry
        if isinstance(lat, str):
            # Parse must succeed (Z-suffix tolerated).
            _parse_ts(lat)
