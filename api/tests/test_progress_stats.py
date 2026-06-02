"""HTTP-level contract tests for GET /api/projects/{id}/progress-stats (Kanban #1292).

Burndown + velocity series computed from the `tasks` table. Auth gate mirrors
the sibling GET /api/projects/{id}/pl (requires X-Project-Id == path id).

Coverage (first-pass contract-smoke per dev-sr-backend scope):
1. Seeded project with KNOWN task history → assert specific burndown + velocity
   values in specific buckets (the load-bearing positive assertion).
2. Empty project (no tasks) → zero-filled buckets, ascending `t`, no crash/NaN.
3. 404 — missing project (header matches path, project absent).
4. 422 — bad `bucket` value and out-of-range `days`.
5. 400 — missing X-Project-Id header (parity with /pl).
6. 404 — cross-project header/path mismatch (parity with /pl).
7. Shape contract — bucket boundaries ascending; window_days echoes param;
   day vs week bucketing yields date-aligned `t`.

Timestamps (`created_at` / `completed_at`) are server-stamped and have no public
POST surface, so tests create tasks via HTTP then BACKDATE the two columns
directly on the test DB via the `db_session` fixture (same pattern as
test_budget_enforcer.py::_set_task_cost). This is the test DB (`agent_teams_test`)
— the live-DB invariant in conftest excludes `tasks` from its row-count guard.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.task import Task


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k1292 progress-stats fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


async def _make_project(client, scaffold_cleanup, *, slug: str = "k1292") -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_task_http(client, project_id: int, title: str, **extras) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _backdate_task(
    db_session,
    task_id: int,
    *,
    created_at: datetime,
    completed_at: datetime | None = None,
    process_status: int | None = None,
    status: int | None = None,
) -> None:
    """Directly stamp created_at / completed_at / process_status on a task row.

    No public POST surface accepts created_at/completed_at — they are
    server-stamped. We set them directly so the burndown/velocity buckets land
    on deterministic dates. (Test DB only.)
    """
    task = await db_session.get(Task, task_id)
    assert task is not None, f"task_id={task_id} not found"
    task.created_at = created_at
    if completed_at is not None:
        task.completed_at = completed_at
    if process_status is not None:
        task.process_status = process_status
    if status is not None:
        task.status = status
    await db_session.commit()


def _series_by_t(series: list[dict], key: str) -> dict[str, int]:
    """Index a burndown/velocity series by its `t` date string → metric value."""
    return {pt["t"]: pt[key] for pt in series}


# ---- 1. seeded history: specific burndown + velocity values ----------------


@pytest.mark.asyncio
async def test_progress_stats_known_history_day_buckets(
    client, scaffold_cleanup, db_session
) -> None:
    """Seed a project with a KNOWN task timeline, backdate created_at/completed_at,
    and assert exact burndown + velocity values in specific day buckets.

    Timeline (UTC, relative to today):
      task A: created day-10, still open (TODO)            → open from day-10 on
      task B: created day-10, completed day-5 (DONE)       → open day-10..day-5, velocity@day-5
      task C: created day-3,  completed day-1 (DONE)       → open day-3..day-1, velocity@day-1

    Assertions (POSITIVE + the NEGATIVE this locks):
      - velocity at day-5 bucket == 1 (B), at day-1 bucket == 1 (C),
        and a bucket with NO completion (day-7) == 0 (negative: not vacuously
        counting unrelated completions).
      - burndown at day-7 (end-of-bucket) counts A + B (both still open) == 2,
        at day-2 counts only A == 1 (B and C closed by their bucket-ends only
        if completed_at <= bucket_end; C completes day-1 so still open at day-2).
    """
    project = await _make_project(client, scaffold_cleanup, slug="k1292-day")
    pid = project["id"]
    now = datetime.now(timezone.utc)
    today = now.date()

    def days_ago(n: int) -> datetime:
        # Anchor at mid-day UTC so the date arithmetic is unambiguous w.r.t.
        # bucket boundaries (which are start-of-day UTC).
        d = today - timedelta(days=n)
        return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)

    try:
        a = await _make_task_http(client, pid, "k1292 A still-open")
        b = await _make_task_http(client, pid, "k1292 B done-day5")
        c = await _make_task_http(client, pid, "k1292 C done-day1")

        # A: created day-10, stays TODO (process_status default 1).
        await _backdate_task(db_session, a["id"], created_at=days_ago(10))
        # B: created day-10, DONE, completed day-5.
        await _backdate_task(
            db_session,
            b["id"],
            created_at=days_ago(10),
            completed_at=days_ago(5),
            process_status=5,
        )
        # C: created day-3, DONE, completed day-1.
        await _backdate_task(
            db_session,
            c["id"],
            created_at=days_ago(3),
            completed_at=days_ago(1),
            process_status=5,
        )

        resp = await client.get(
            f"/api/projects/{pid}/progress-stats?bucket=day&days=14",
            headers={"X-Project-Id": str(pid)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["project_id"] == pid
        assert body["bucket"] == "day"
        assert body["window_days"] == 14
        assert isinstance(body["generated_at"], str)
        assert body["generated_at"].endswith("Z"), body["generated_at"]

        vel = _series_by_t(body["velocity"], "completed")
        burn = _series_by_t(body["burndown"], "remaining")

        d = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731

        # --- velocity POSITIVE: completions land in the right day buckets ---
        assert vel.get(d(5)) == 1, ("B should be counted on day-5", vel)
        assert vel.get(d(1)) == 1, ("C should be counted on day-1", vel)
        # --- velocity NEGATIVE: a day with no completion is 0, not inherited ---
        assert vel.get(d(7)) == 0, ("day-7 had no completion", vel)
        # Total completions across the whole window == 2 (B + C), never more.
        assert sum(vel.values()) == 2, ("exactly 2 completions in window", vel)

        # --- burndown POSITIVE: open-as-of-bucket-end counts ---
        # End of day-7 bucket = start of day-6 (exclusive). A (TODO) + B
        # (completes day-5, AFTER day-7 end) both open → 2.
        assert burn.get(d(7)) == 2, ("A + B open at end of day-7", burn)
        # End of day-2 bucket = start of day-1. A open; B closed (day-5);
        # C completes day-1 which is NOT < the day-1 boundary, so C still open
        # at the day-2 bucket end → A + C == 2.
        assert burn.get(d(2)) == 2, ("A + C open at end of day-2", burn)
        # --- burndown NEGATIVE: after everything but A closes, remaining == 1 ---
        assert burn.get(d(0)) == 1, ("only A open at end of today bucket", burn)
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 1b. week bucketing aligns to ISO Monday -------------------------------


@pytest.mark.asyncio
async def test_progress_stats_week_buckets_monday_aligned(
    client, scaffold_cleanup, db_session
) -> None:
    """Week buckets snap `t` to ISO Monday and zero-fill every week in the window.

    Positive: each bucket `t` is a Monday (weekday()==0). One DONE task lands its
    velocity in the week containing its completed_at. Negative: weeks with no
    completion are present with completed==0 (zero-fill, not skipped).
    """
    project = await _make_project(client, scaffold_cleanup, slug="k1292-week")
    pid = project["id"]
    today = datetime.now(timezone.utc).date()

    def days_ago_dt(n: int) -> datetime:
        d = today - timedelta(days=n)
        return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)

    try:
        t = await _make_task_http(client, pid, "k1292 week done")
        await _backdate_task(
            db_session,
            t["id"],
            created_at=days_ago_dt(20),
            completed_at=days_ago_dt(10),
            process_status=5,
        )

        resp = await client.get(
            f"/api/projects/{pid}/progress-stats?bucket=week&days=60",
            headers={"X-Project-Id": str(pid)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bucket"] == "week"

        # Every bucket start is a Monday, and the series is strictly ascending.
        starts = [pt["t"] for pt in body["velocity"]]
        burn_starts = [pt["t"] for pt in body["burndown"]]
        assert starts == burn_starts, "burndown + velocity share the bucket axis"
        assert starts == sorted(starts), "weeks must ascend by t"
        for s in starts:
            dt = datetime.fromisoformat(s).date()
            assert dt.weekday() == 0, f"week bucket {s} is not a Monday"

        # The completed task contributes exactly 1 velocity unit, in exactly
        # one week bucket (the week containing completed_at = day-10).
        vel = _series_by_t(body["velocity"], "completed")
        assert sum(vel.values()) == 1, ("exactly one completion total", vel)
        completed_week = (today - timedelta(days=10))
        completed_week_monday = (
            completed_week - timedelta(days=completed_week.weekday())
        ).isoformat()
        assert vel.get(completed_week_monday) == 1, (
            "completion lands in its ISO week",
            vel,
            completed_week_monday,
        )
        # Negative: at least one zero-filled week exists (60d window > 1 week).
        assert any(v == 0 for v in vel.values()), ("zero-filled weeks present", vel)
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 2. empty project: zero-filled, no crash -------------------------------


@pytest.mark.asyncio
async def test_progress_stats_empty_project_zero_filled(
    client, scaffold_cleanup
) -> None:
    """A project with NO tasks returns every bucket zero-filled (remaining=0,
    completed=0), both series ascending, no crash / NaN.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k1292-empty")
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/progress-stats?bucket=day&days=30",
            headers={"X-Project-Id": str(pid)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["project_id"] == pid
        assert body["window_days"] == 30
        assert len(body["burndown"]) >= 1
        assert len(body["velocity"]) == len(body["burndown"])

        # Every bucket zero, ascending t.
        burn_ts = [pt["t"] for pt in body["burndown"]]
        assert burn_ts == sorted(burn_ts)
        assert all(pt["remaining"] == 0 for pt in body["burndown"]), body["burndown"]
        assert all(pt["completed"] == 0 for pt in body["velocity"]), body["velocity"]
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 3. 404 — missing project (header matches absent path id) --------------


@pytest.mark.asyncio
async def test_progress_stats_missing_project_returns_404(client) -> None:
    resp = await client.get(
        "/api/projects/9999999/progress-stats",
        headers={"X-Project-Id": "9999999"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Project id=9999999 not found"


# ---- 3b. 404 — soft-deleted project -----------------------------------------


@pytest.mark.asyncio
async def test_progress_stats_soft_deleted_project_returns_404(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1292-softdel")
    pid = project["id"]
    # Present while active.
    pre = await client.get(
        f"/api/projects/{pid}/progress-stats", headers={"X-Project-Id": str(pid)}
    )
    assert pre.status_code == 200, pre.text
    # Soft-delete, then it must 404.
    del_resp = await client.delete(f"/api/projects/{pid}")
    assert del_resp.status_code == 204, del_resp.text
    post = await client.get(
        f"/api/projects/{pid}/progress-stats", headers={"X-Project-Id": str(pid)}
    )
    assert post.status_code == 404, post.text
    assert post.json()["detail"] == f"Project id={pid} not found"


# ---- 4. 422 — bad bucket / out-of-range days --------------------------------


@pytest.mark.asyncio
async def test_progress_stats_bad_bucket_returns_422(client, scaffold_cleanup) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1292-badbucket")
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/progress-stats?bucket=month",
            headers={"X-Project-Id": str(pid)},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_progress_stats_days_out_of_range_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1292-baddays")
    pid = project["id"]
    headers = {"X-Project-Id": str(pid)}
    try:
        # days=0 (below ge=1) and days=366 (above le=365) both 422.
        lo = await client.get(
            f"/api/projects/{pid}/progress-stats?days=0", headers=headers
        )
        assert lo.status_code == 422, lo.text
        hi = await client.get(
            f"/api/projects/{pid}/progress-stats?days=366", headers=headers
        )
        assert hi.status_code == 422, hi.text
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 5. 400 — missing X-Project-Id header (parity with /pl) -----------------


@pytest.mark.asyncio
async def test_progress_stats_missing_header_returns_400(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1292-nohdr")
    pid = project["id"]
    try:
        resp = await client.get(f"/api/projects/{pid}/progress-stats")
        assert resp.status_code == 400, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 6. 404 — cross-project header/path mismatch (parity with /pl) ----------


@pytest.mark.asyncio
async def test_progress_stats_cross_project_returns_404(
    client, scaffold_cleanup
) -> None:
    project_b = await _make_project(client, scaffold_cleanup, slug="k1292-cross")
    pid_b = project_b["id"]
    try:
        # Header bound to project=1 but path is B → 404 (B invisible from session 1).
        resp = await client.get(
            f"/api/projects/{pid_b}/progress-stats",
            headers={"X-Project-Id": "1"},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == f"Project id={pid_b} not found"
    finally:
        await client.delete(f"/api/projects/{pid_b}")


# ---- 7. default bucket is week ----------------------------------------------


@pytest.mark.asyncio
async def test_progress_stats_default_bucket_is_week(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1292-default")
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/progress-stats", headers={"X-Project-Id": str(pid)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bucket"] == "week"
        assert body["window_days"] == 90  # default days
    finally:
        await client.delete(f"/api/projects/{pid}")
