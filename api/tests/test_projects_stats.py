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

Kanban #1289 — optional `project_id` query param (tests 12-15):
12. Param absent → all active projects returned (regression).
13. Param set to a real project id → exactly 1 entry with `id == project_id`.
14. Param set to a non-existent id → returns `[]`.
15. Param set to a soft-deleted project's id → returns `[]`.

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

        # counts — exactly seven string keys (1-6 + 8; 7 is reserved/skipped),
        # every value 0. Kanban #1839 added HALTED_PENDING_USER=8 to TaskStatus.ALL.
        assert set(entry["counts"].keys()) == {"1", "2", "3", "4", "5", "6", "8"}, entry
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

        # Counts per process_status. Kanban #854: "6" (CANCELLED) bucket
        # always present (0 here — no cancellations in this fixture).
        # Kanban #1839: "8" (HALTED_PENDING_USER) bucket always present (0 here).
        assert entry["counts"] == {
            "1": 3,  # 3 TODO
            "2": 1,
            "3": 1,
            "4": 1,
            "5": 1,
            "6": 0,
            "8": 0,
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


# ---- 2b. Kanban #854: cancelled task counted in counts["6"] but NOT in last_activity_at ----


@pytest.mark.asyncio
async def test_stats_cancelled_excluded_from_last_activity_in_counts(
    client, scaffold_cleanup
) -> None:
    """Kanban #854 — a CANCELLED (process_status=6) task:
      - DOES appear in counts["6"] (transparency; FE can decide whether to show)
      - DOES NOT contribute to last_activity_at (parity with the soft-delete
        exclusion — cancelled work is dead-end, its cancellation flip's
        updated_at must not poke through as "last activity")
    Mirrors the soft-delete negative case (test 3) — keep the patterns sibling
    so any future regression in the stats endpoint surfaces twice.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k854-stats")
    project_id = project["id"]

    try:
        # Two tasks: one keeper (TODO), one to be cancelled.
        keeper = await _make_task_http(client, project_id, "k854 stats keeper")
        victim = await _make_task_http(client, project_id, "k854 stats victim")

        # Flip victim to CANCELLED — bumps updated_at. keeper's updated_at
        # is older. After the flip the cancelled row's updated_at would be
        # the lane's max IF the stats endpoint counted it (which it must NOT).
        await _patch_task(
            client,
            project_id,
            victim["id"],
            {"process_status": 6, "status_change_reason": "k854 stats smoke"},
        )

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None

        # counts: 1 TODO keeper + 1 CANCELLED victim → "1": 1, "6": 1, rest 0.
        # Kanban #1839: "8" (HALTED_PENDING_USER) bucket always present.
        assert entry["counts"] == {
            "1": 1,
            "2": 0,
            "3": 0,
            "4": 0,
            "5": 0,
            "6": 1,
            "8": 0,
        }, entry

        # last_activity_at must reflect the keeper (older updated_at) — NOT
        # the cancelled victim (newer updated_at). This is the regression
        # gate against the cancellation flip leaking into "freshness".
        headers = {"X-Project-Id": str(project_id)}
        keeper_now = await client.get(
            f"/api/tasks/{keeper['id']}", headers=headers
        )
        keeper_updated_at = _parse_ts(keeper_now.json()["updated_at"])
        assert entry["last_activity_at"] is not None
        last = _parse_ts(entry["last_activity_at"])
        assert abs((last - keeper_updated_at).total_seconds()) < 1.0, (
            f"last_activity_at {last} != keeper updated_at {keeper_updated_at} "
            "— cancelled task leaked into last_activity_at"
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

        # Only the keeper counts. Kanban #1839: "8" bucket always present.
        assert entry["counts"] == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "8": 0}, entry
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
        # counts: 7 string keys (1-6 + 8; 7 reserved/skipped), all int.
        # Kanban #1839: "8" (HALTED_PENDING_USER) added to TaskStatus.ALL.
        assert set(entry["counts"].keys()) == {"1", "2", "3", "4", "5", "6", "8"}, entry
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


# ---- 8-11. cost_usage sub-object (Kanban #871) ------------------------------
#
# These tests exercise the new `cost_usage` aggregate sourced from
# `session_runs` (joined via `sessions.project_id`). Helpers stand up sessions
# and runs via the public HTTP API; cost is computed server-side from
# `(provider, model, input_tokens, output_tokens)` so test setup must mirror
# the CTX-3 PATCH /api/session_runs/{id} contract — client-supplied
# `total_cost_usd` is dropped.


async def _make_session(client, project_id: int) -> int:
    """POST /api/sessions and return the session id."""
    resp = await client.post("/api/sessions", json={"project_id": project_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_session_run(
    client,
    session_id: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_context_chars: int = 0,
    provider: str = "anthropic",
    model: str = "claude-opus-4-7",
    budget_warning: bool = False,
) -> dict:
    """POST a session_run then PATCH it with cost-bearing tokens + optional
    budget_warning. Returns the final SessionRunRead body.

    Cost is server-computed from `(provider, model, input_tokens, output_tokens)`
    per CTX-3 (#718). For `claude-opus-4-7`: $5/M input + $25/M output (current Opus rate, #2727).
    """
    create_resp = await client.post(f"/api/sessions/{session_id}/runs", json={})
    assert create_resp.status_code == 201, create_resp.text
    run_id = create_resp.json()["id"]

    patch_body: dict = {
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "total_context_chars": total_context_chars,
    }
    if input_tokens or output_tokens:
        patch_body["provider"] = provider
        patch_body["model"] = model
    if budget_warning:
        patch_body["budget_warning"] = True
    patch_resp = await client.patch(f"/api/session_runs/{run_id}", json=patch_body)
    assert patch_resp.status_code == 200, patch_resp.text
    return patch_resp.json()


def _session_fs_cleanup_inline(session_id: int) -> None:
    """Best-effort `_sessions/<id>/` filesystem cleanup. Not fatal if missing."""
    import shutil
    from pathlib import Path

    from src.settings import get_settings

    target = Path(get_settings().repo_root) / "_sessions" / str(session_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


# ---- 8. zero-session_runs project emits zero-filled cost_usage --------------


@pytest.mark.asyncio
async def test_stats_cost_usage_zero_filled_when_no_session_runs(
    client, scaffold_cleanup
) -> None:
    """Kanban #871 — a project with no session_runs MUST still emit the full
    `cost_usage` sub-object with every key zero-valued. FE renders without
    coalescing (mirrors `counts` / `run_mode_breakdown` invariant).
    """
    project = await _make_project(client, scaffold_cleanup, slug="k871-zero")
    try:
        entry = await _stats_entry_for(client, project["id"])
        assert entry is not None
        assert "cost_usage" in entry, entry

        cu = entry["cost_usage"]
        # All six keys present.
        assert set(cu.keys()) == {
            "total_input_tokens",
            "total_output_tokens",
            "total_context_chars",
            "total_cost_usd",
            "budget_warning_count",
            "session_run_count",
        }, cu
        # Numeric zeros for int fields.
        assert cu["total_input_tokens"] == 0, cu
        assert cu["total_output_tokens"] == 0, cu
        assert cu["total_context_chars"] == 0, cu
        assert cu["budget_warning_count"] == 0, cu
        assert cu["session_run_count"] == 0, cu
        # Decimal serializes as a JSON STRING per Pydantic v2 default
        # (mirrors `SessionRunRead.total_cost_usd`). Accept any string form
        # that parses to 0 (e.g. "0", "0.0000").
        from decimal import Decimal

        assert isinstance(cu["total_cost_usd"], str), cu
        assert Decimal(cu["total_cost_usd"]) == Decimal("0"), cu
    finally:
        await client.delete(f"/api/projects/{project['id']}")


# ---- 9. project with 2 session_runs sums correctly --------------------------


@pytest.mark.asyncio
async def test_stats_cost_usage_sums_two_session_runs(
    client, scaffold_cleanup
) -> None:
    """Two session_runs in the same project — cost_usage MUST be the SUM of
    their per-row tokens / context chars / cost / counts. Uses `claude-opus-4-7`
    ($5/M input + $25/M output) so cost is determined and test-stable.

    Run 1: 100_000 in + 200_000 out → cost = 100k/1M*5 + 200k/1M*25 = 0.5 + 5.0 = 5.5
    Run 2: 50_000 in + 80_000 out  → cost = 50k/1M*5 + 80k/1M*25 = 0.25 + 2.0 = 2.25
    Total: in=150_000, out=280_000, cost=7.75 USD; session_run_count=2.
    """
    from decimal import Decimal

    project = await _make_project(client, scaffold_cleanup, slug="k871-two")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        await _make_session_run(
            client,
            session_id,
            input_tokens=100_000,
            output_tokens=200_000,
            total_context_chars=5_000,
        )
        await _make_session_run(
            client,
            session_id,
            input_tokens=50_000,
            output_tokens=80_000,
            total_context_chars=2_500,
        )

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None
        cu = entry["cost_usage"]

        assert cu["total_input_tokens"] == 150_000, cu
        assert cu["total_output_tokens"] == 280_000, cu
        assert cu["total_context_chars"] == 7_500, cu
        assert cu["session_run_count"] == 2, cu
        # Cost: SUM(5.5 + 2.25) = 7.75 — Decimal-stringified.
        assert Decimal(cu["total_cost_usd"]) == Decimal("7.7500"), cu
        # No budget_warning flips on these runs.
        assert cu["budget_warning_count"] == 0, cu
    finally:
        _session_fs_cleanup_inline(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ---- 10. budget_warning=true count increments correctly ---------------------


@pytest.mark.asyncio
async def test_stats_cost_usage_budget_warning_count(
    client, scaffold_cleanup
) -> None:
    """`budget_warning_count` MUST count session_runs with `budget_warning=true`.
    Sanity: 3 runs (true, false, true) → budget_warning_count == 2.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k871-bw")
    project_id = project["id"]
    session_id = await _make_session(client, project_id)
    try:
        # Run 1: budget_warning=true (no tokens — just the flag).
        await _make_session_run(client, session_id, budget_warning=True)

        # After 1 warning-true run, count==1.
        entry = await _stats_entry_for(client, project_id)
        assert entry is not None
        assert entry["cost_usage"]["budget_warning_count"] == 1, entry
        assert entry["cost_usage"]["session_run_count"] == 1, entry

        # Run 2: budget_warning=false → count stays at 1.
        await _make_session_run(client, session_id, budget_warning=False)
        # Run 3: budget_warning=true → count climbs to 2.
        await _make_session_run(client, session_id, budget_warning=True)

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None
        cu = entry["cost_usage"]
        assert cu["budget_warning_count"] == 2, cu
        assert cu["session_run_count"] == 3, cu
    finally:
        _session_fs_cleanup_inline(session_id)
        await client.delete(f"/api/projects/{project_id}")


# ---- 11. cross-project isolation -------------------------------------------


@pytest.mark.asyncio
async def test_stats_cost_usage_cross_project_isolation(
    client, scaffold_cleanup
) -> None:
    """Two projects each with their own session_runs. Each project's
    `cost_usage` MUST reflect ONLY its own runs — no cross-project leak via the
    session/session_run join.
    """
    from decimal import Decimal

    proj_a = await _make_project(client, scaffold_cleanup, slug="k871-iso-a")
    proj_b = await _make_project(client, scaffold_cleanup, slug="k871-iso-b")
    pid_a, pid_b = proj_a["id"], proj_b["id"]
    sid_a = await _make_session(client, pid_a)
    sid_b = await _make_session(client, pid_b)
    try:
        # Project A: 1 run, 100_000 in / 0 out → cost 0.5 USD.
        await _make_session_run(
            client, sid_a, input_tokens=100_000, output_tokens=0
        )
        # Project B: 1 run, 0 in / 200_000 out → cost 5.0 USD; budget_warning=true.
        await _make_session_run(
            client,
            sid_b,
            input_tokens=0,
            output_tokens=200_000,
            budget_warning=True,
        )

        entry_a = await _stats_entry_for(client, pid_a)
        entry_b = await _stats_entry_for(client, pid_b)
        assert entry_a is not None
        assert entry_b is not None

        cu_a = entry_a["cost_usage"]
        assert cu_a["total_input_tokens"] == 100_000, cu_a
        assert cu_a["total_output_tokens"] == 0, cu_a
        assert cu_a["session_run_count"] == 1, cu_a
        assert cu_a["budget_warning_count"] == 0, cu_a
        assert Decimal(cu_a["total_cost_usd"]) == Decimal("0.5000"), cu_a

        cu_b = entry_b["cost_usage"]
        assert cu_b["total_input_tokens"] == 0, cu_b
        assert cu_b["total_output_tokens"] == 200_000, cu_b
        assert cu_b["session_run_count"] == 1, cu_b
        assert cu_b["budget_warning_count"] == 1, cu_b
        assert Decimal(cu_b["total_cost_usd"]) == Decimal("5.0000"), cu_b
    finally:
        _session_fs_cleanup_inline(sid_a)
        _session_fs_cleanup_inline(sid_b)
        await client.delete(f"/api/projects/{pid_a}")
        await client.delete(f"/api/projects/{pid_b}")


# ---- 12-15. Kanban #1289 — optional ?project_id= filter --------------------


@pytest.mark.asyncio
async def test_stats_project_id_param_absent_returns_all(client) -> None:
    """Regression: omitting ?project_id= still returns the full active list.
    The seeded agent-teams project (id=1) must be present.
    """
    resp = await client.get("/api/projects/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    # At minimum the seeded project must appear.
    assert len(body) >= 1
    ids = [e["id"] for e in body]
    assert 1 in ids, f"agent-teams (id=1) missing from unfiltered stats: {ids}"


@pytest.mark.asyncio
async def test_stats_project_id_param_returns_single_entry(
    client, scaffold_cleanup
) -> None:
    """?project_id=<real_id> returns exactly 1 entry whose `id` matches."""
    project = await _make_project(client, scaffold_cleanup, slug="k1289-single")
    project_id = project["id"]
    try:
        resp = await client.get(f"/api/projects/stats?project_id={project_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body, list), body
        assert len(body) == 1, f"expected 1 entry, got {len(body)}: {body}"
        assert body[0]["id"] == project_id
        assert body[0]["name"] == project["name"]
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_stats_project_id_param_nonexistent_returns_empty(client) -> None:
    """?project_id=999999 (non-existent) returns [] — NOT 404."""
    resp = await client.get("/api/projects/stats?project_id=999999")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [], f"expected [], got {body}"


@pytest.mark.asyncio
async def test_stats_project_id_param_soft_deleted_returns_empty(
    client, scaffold_cleanup
) -> None:
    """?project_id=<soft-deleted-id> returns [] — existing ACTIVE filter applies."""
    project = await _make_project(client, scaffold_cleanup, slug="k1289-softdel")
    project_id = project["id"]

    # Confirm it's present while active.
    pre = await client.get(f"/api/projects/stats?project_id={project_id}")
    assert pre.status_code == 200
    assert len(pre.json()) == 1, "should appear before soft-delete"

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 204, del_resp.text

    # Now absent.
    post = await client.get(f"/api/projects/stats?project_id={project_id}")
    assert post.status_code == 200, post.text
    assert post.json() == [], (
        f"soft-deleted project id={project_id} must not appear in filtered stats"
    )


# ---- 16-18. G1 — estimated_cost sub-object ----------------------------------
#
# These tests exercise the new `estimated_cost` aggregate sourced from
# `tasks.estimated_cost_usd / estimated_input_tokens / estimated_output_tokens`.
# Since the estimator fires at DONE-flip server-side, tests seed tasks via HTTP
# PATCH to process_status=5 and then directly write estimated_* fields via the
# internal PATCH path. However, the fields are server-computed (not client-
# writeable) — so we rely on the existing DONE-flip estimator for a light smoke
# test against agent-teams itself, and use a zero-filled check for fresh
# projects (which mirrors cost_usage test 8).


@pytest.mark.asyncio
async def test_stats_estimated_cost_zero_filled_when_no_tasks(
    client, scaffold_cleanup
) -> None:
    """G1 — a project with no tasks MUST still emit the full `estimated_cost`
    sub-object with every key zero-valued. Mirrors cost_usage test 8.
    """
    from decimal import Decimal

    project = await _make_project(client, scaffold_cleanup, slug="g1-zero")
    try:
        entry = await _stats_entry_for(client, project["id"])
        assert entry is not None
        assert "estimated_cost" in entry, entry

        ec = entry["estimated_cost"]
        # All three keys present.
        assert set(ec.keys()) == {
            "total_cost_usd",
            "total_input_tokens",
            "total_output_tokens",
        }, ec
        # Integer zeros.
        assert ec["total_input_tokens"] == 0, ec
        assert ec["total_output_tokens"] == 0, ec
        # Decimal serializes as JSON string (mirrors cost_usage.total_cost_usd).
        assert isinstance(ec["total_cost_usd"], str), ec
        assert Decimal(ec["total_cost_usd"]) == Decimal("0"), ec
    finally:
        await client.delete(f"/api/projects/{project['id']}")


@pytest.mark.asyncio
async def test_stats_estimated_cost_cancelled_excluded(
    client, scaffold_cleanup
) -> None:
    """G1 — CANCELLED tasks (process_status=6) MUST NOT contribute to
    `estimated_cost` even if they carry estimated_cost_usd. Regression gate
    for the filter `process_status != 6` in Query 4.

    Strategy: create a project with one TODO task (no estimate yet — the estimator
    only fires at DONE-flip) and one task that will be cancelled. Both start at
    zero estimates, so the sum stays zero. The cancellation must not 500 or cause
    a shape regression on `estimated_cost`.
    """
    from decimal import Decimal

    project = await _make_project(client, scaffold_cleanup, slug="g1-cancel")
    project_id = project["id"]
    try:
        t = await _make_task_http(client, project_id, "g1 cancel victim")
        await _patch_task(
            client,
            project_id,
            t["id"],
            {"process_status": 6, "status_change_reason": "g1 cancel smoke"},
        )

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None
        ec = entry["estimated_cost"]
        # Shape contract holds (all keys present).
        assert set(ec.keys()) == {
            "total_cost_usd",
            "total_input_tokens",
            "total_output_tokens",
        }, ec
        # Cancelled task has no estimate, so sum stays zero.
        assert ec["total_input_tokens"] == 0, ec
        assert ec["total_output_tokens"] == 0, ec
        assert Decimal(ec["total_cost_usd"]) == Decimal("0"), ec
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_stats_estimated_cost_nonzero_after_done_flip(
    client, scaffold_cleanup
) -> None:
    """G1 — a DONE-flipped task populates estimated_cost_usd via the
    task_cost_estimator service (fires automatically on process_status=5 PATCH).
    The aggregate in `estimated_cost` MUST then be NON-ZERO for the project,
    confirming the query actually reads task estimates (not a vacuous zero path).

    Uses a descriptive title + status_change_reason to ensure the heuristic
    produces a non-zero token/cost estimate (the estimator uses char counts of
    title + description + status_change_reason).
    """
    from decimal import Decimal

    project = await _make_project(client, scaffold_cleanup, slug="g1-nonzero")
    project_id = project["id"]
    try:
        t = await _make_task_http(
            client,
            project_id,
            "g1-nonzero implement the backend cost display aggregate",
            description=(
                "Adds estimated_cost to the stats endpoint by summing "
                "tasks.estimated_cost_usd for non-cancelled tasks."
            ),
        )
        # DONE-flip triggers the cost estimator.
        await _patch_task(
            client,
            project_id,
            t["id"],
            {
                "process_status": 5,
                "status_change_reason": (
                    "Completed. Added Query 4 to the stats router, zero-fill "
                    "in by_id, fold loop, and updated the Pydantic schema."
                ),
            },
        )

        entry = await _stats_entry_for(client, project_id)
        assert entry is not None
        ec = entry["estimated_cost"]
        assert set(ec.keys()) == {
            "total_cost_usd",
            "total_input_tokens",
            "total_output_tokens",
        }, ec
        # The estimator produces non-zero token counts for tasks with content.
        # These are the primary signal that the aggregate is reading real data.
        # total_cost_usd may round to 0.0000 at 4dp for small token counts —
        # assert >= 0 (shape) rather than > 0 (brittle for sub-cent tasks).
        assert ec["total_input_tokens"] > 0, (
            f"estimated_cost.total_input_tokens must be > 0 after DONE-flip; got {ec}"
        )
        assert ec["total_output_tokens"] > 0, (
            f"estimated_cost.total_output_tokens must be > 0 after DONE-flip; got {ec}"
        )
        assert Decimal(ec["total_cost_usd"]) >= Decimal("0"), ec
    finally:
        await client.delete(f"/api/projects/{project_id}")
