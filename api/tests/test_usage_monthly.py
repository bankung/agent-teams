"""Contract-smoke tests for GET /api/usage/monthly (Kanban #2356, read-side P3).

First-pass coverage of the billing-cycle cost rollup that combines BOTH cost
modes (Mode A = usage_events, Mode B = session_runs):

1. Billing-cycle bucketing with an explicit cut-off day — an event AT the day-D
   boundary lands in the NEW (current) cycle; the instant before lands in the
   prior cycle (the off-by-one contract).
2. ``?cycle_day=`` overrides the env/default (echoed in the response + bucketing
   shifts accordingly).
3. Mode A + Mode B split + total in one cycle (exact Decimals; total == a + b).
4. Per-task drilldown groups by task_id incl. the ``null`` unattributed bucket,
   ordered by total desc.
5. ``occurred_at`` clamp on POST /api/usage/events (now-31d → 422, now+10min →
   422, now-1d → 201, omitted → 201).
6. project_id filter excludes other projects.
7. Empty/zero window → ``months`` zero-filled cycles, top-level total "0.0000".

Regression tests added by dev-tester (Kanban #2356):
8. Year-rollover in _cycle_starts — December case + January-before-cutoff case.

The boundary timestamps are computed DYNAMICALLY from ``now()`` so the tests
pass on any calendar day. The chosen cut-off day keeps the current-cycle
boundary within the last few days, so seeded Mode-A events never trip the
30-day occurred_at clamp (Part B of this task).

The rigorous suite (DST/year-rollover matrices, FK SET NULL on task delete,
CASCADE on project delete, cross-mode race, etc.) is dev-tester's.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"usage monthly fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _current_cycle_start(now: datetime, cycle_day: int) -> datetime:
    """Mirror the router's current-cycle-start rule (00:00 UTC of day-D).

    Used by tests to seed Mode-A/Mode-B rows relative to a known boundary.
    """
    if now.day >= cycle_day:
        y, m = now.year, now.month
    else:
        y, m = _prev_month(now.year, now.month)
    return datetime(y, m, cycle_day, 0, 0, 0, tzinfo=timezone.utc)


def _safe_cycle_day(now: datetime) -> int:
    """A cut-off day whose current-cycle boundary is within the last few days.

    cycle_day is capped at 28 by the endpoint. Picking ``min(now.day, 28)``
    means the current cycle started today (or, on days 29-31, on the 28th — at
    most 3 days ago), so ``boundary - 1s`` is always well inside the 30-day
    occurred_at clamp window.
    """
    return min(now.day, 28)


async def _make_project(client, scaffold_cleanup, prefix: str) -> int:
    name = scaffold_cleanup(_unique_name(prefix))
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str) -> int:
    resp = await client.post(
        "/api/tasks",
        json={
            "title": title,
            "project_id": project_id,
            "process_status": 1,
            "priority": 3,
        },
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_mode_a(
    client,
    project_id: int,
    *,
    occurred_at: datetime,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    task_id: int | None = None,
    model: str = "claude-opus-4-8",
) -> dict:
    """POST one usage_events row (Mode A) at an explicit occurred_at."""
    payload: dict = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "occurred_at": occurred_at.isoformat(),
        "dedup_key": f"mt-{uuid.uuid4().hex}",
    }
    if task_id is not None:
        payload["task_id"] = task_id
    resp = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_mode_b(
    client,
    project_id: int,
    *,
    finished_at: datetime,
    input_tokens: int = 200_000,
    output_tokens: int = 50_000,
    task_id: int | None = None,
    provider: str = "google",
    model: str = "gemini-2.5-flash-lite",
) -> dict:
    """Create a session + run (Mode B) and PATCH it done at an explicit finished_at."""
    sess = await client.post("/api/sessions", json={"project_id": project_id})
    assert sess.status_code == 201, sess.text
    run = await client.post(
        f"/api/sessions/{sess.json()['id']}/runs",
        json={} if task_id is None else {"task_id": task_id},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    patch = await client.patch(
        f"/api/session_runs/{run_id}",
        json={
            "status": "done",
            "finished_at": finished_at.isoformat(),
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "provider": provider,
            "model": model,
        },
    )
    assert patch.status_code == 200, patch.text
    return patch.json()


# =============================================================================
# 1. Billing-cycle bucketing + off-by-one contract
# =============================================================================


@pytest.mark.asyncio
async def test_off_by_one_boundary_mode_a(client, scaffold_cleanup) -> None:
    """Event AT day-D 00:00 → current cycle; the instant before → prior cycle.

    Seeds two Mode-A events in the SAME project: one exactly at the current
    cycle's cut-off boundary, one 1 second before it. With the project filter,
    cycle[0] (current) must hold exactly the boundary event and cycle[1] (prior)
    exactly the before-boundary event.
    """
    now = datetime.now(timezone.utc)
    cycle_day = _safe_cycle_day(now)
    boundary = _current_cycle_start(now, cycle_day)

    project_id = await _make_project(client, scaffold_cleanup, "um-boundary")

    # AT the boundary → current cycle. Distinct token counts so we can tell the
    # two events apart in the aggregates.
    await _seed_mode_a(
        client, project_id, occurred_at=boundary, input_tokens=111, output_tokens=11
    )
    # The instant before → prior cycle.
    await _seed_mode_a(
        client,
        project_id,
        occurred_at=boundary - timedelta(seconds=1),
        input_tokens=222,
        output_tokens=22,
    )

    resp = await client.get(
        f"/api/usage/monthly?months=2&cycle_day={cycle_day}&project_id={project_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cycle_day"] == cycle_day
    assert len(body["cycles"]) == 2

    current_cycle, prior_cycle = body["cycles"][0], body["cycles"][1]
    # cycle_start of cycle[0] equals the boundary date (sanity on ordering).
    assert current_cycle["cycle_start"] == boundary.date().isoformat()

    # POSITIVE: the AT-boundary event landed in the CURRENT cycle.
    assert current_cycle["mode_a_input_tokens"] == 111
    assert current_cycle["mode_a_output_tokens"] == 11
    # POSITIVE: the before-boundary event landed in the PRIOR cycle.
    assert prior_cycle["mode_a_input_tokens"] == 222
    assert prior_cycle["mode_a_output_tokens"] == 22
    # NEGATIVE: the boundary event did NOT bleed into the prior cycle, and the
    # before-boundary event did NOT bleed into the current cycle.
    assert prior_cycle["mode_a_input_tokens"] != 111
    assert current_cycle["mode_a_input_tokens"] != 222


# =============================================================================
# 2. ?cycle_day= override
# =============================================================================


@pytest.mark.asyncio
async def test_cycle_day_query_overrides_default(client, scaffold_cleanup) -> None:
    """``?cycle_day=`` is echoed in the response and shifts bucket boundaries.

    POSITIVE: the response's cycle_day equals the query value (not the env
    default of 1). NEGATIVE: it is NOT the default 1 (proves the override path).
    Also asserts the cycle_start dates carry the chosen cut-off day.
    """
    now = datetime.now(timezone.utc)
    cycle_day = _safe_cycle_day(now)
    # Choose a value guaranteed != 1 so the negative assertion is meaningful even
    # when _safe_cycle_day happens to be 1 (i.e. on the 1st of the month).
    chosen = cycle_day if cycle_day != 1 else 2

    project_id = await _make_project(client, scaffold_cleanup, "um-cycleday")

    resp = await client.get(
        f"/api/usage/monthly?months=3&cycle_day={chosen}&project_id={project_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: echoed value matches the query.
    assert body["cycle_day"] == chosen
    # NEGATIVE: it is the override, not the env default.
    assert body["cycle_day"] != 1 or chosen == 1
    # Every cycle boundary carries the chosen cut-off day.
    for cyc in body["cycles"]:
        assert int(cyc["cycle_start"].split("-")[2]) == chosen


# =============================================================================
# 3. Mode A + Mode B split + total in one cycle
# =============================================================================


@pytest.mark.asyncio
async def test_mode_a_plus_mode_b_split_and_total(client, scaffold_cleanup) -> None:
    """One cycle with BOTH a Mode-A event and a Mode-B run → split + summed.

    Mode A: opus 1000 in / 500 out (cost computed server-side from the price card).
    Mode B: gemini-2.5-flash-lite 200k in / 50k out = 0.0200 + 0.0200 = $0.0400.
    Asserts mode_a_cost_usd, mode_b_cost_usd, and total == a + b as exact Decimals.
    """
    from src.services.cost_tracker import compute_cost

    now = datetime.now(timezone.utc)
    cycle_day = _safe_cycle_day(now)
    boundary = _current_cycle_start(now, cycle_day)
    # A timestamp safely inside the current cycle (1 hour after the boundary).
    in_cycle = boundary + timedelta(hours=1)

    project_id = await _make_project(client, scaffold_cleanup, "um-split")

    mode_a = await _seed_mode_a(
        client,
        project_id,
        occurred_at=in_cycle,
        input_tokens=1000,
        output_tokens=500,
    )
    expected_a = Decimal(str(mode_a["cost_usd"]))
    # Sanity: opus 1000 in @ $5/1M + 500 out @ $25/1M = 0.0050 + 0.0125 = 0.0175.
    assert expected_a == Decimal("0.0175")

    await _seed_mode_b(
        client,
        project_id,
        finished_at=in_cycle,
        input_tokens=200_000,
        output_tokens=50_000,
    )
    expected_b = compute_cost("google", "gemini-2.5-flash-lite", 200_000, 50_000)
    assert expected_b == Decimal("0.0400")

    resp = await client.get(
        f"/api/usage/monthly?months=2&cycle_day={cycle_day}&project_id={project_id}"
    )
    assert resp.status_code == 200, resp.text
    cyc = resp.json()["cycles"][0]  # current cycle

    assert Decimal(cyc["mode_a_cost_usd"]) == expected_a
    assert Decimal(cyc["mode_b_cost_usd"]) == expected_b
    # POSITIVE + NEGATIVE: total is exactly a + b — not just one mode, not vacuous.
    assert Decimal(cyc["total_cost_usd"]) == expected_a + expected_b
    assert Decimal(cyc["total_cost_usd"]) == Decimal("0.0575")


# =============================================================================
# 4. Per-task drilldown incl. unattributed bucket, ordered by total desc
# =============================================================================


@pytest.mark.asyncio
async def test_per_task_drilldown_groups_and_orders(client, scaffold_cleanup) -> None:
    """Per-cycle tasks group by task_id incl. the null bucket, ordered desc.

    Seeds in the current cycle:
      - task HIGH: Mode-A opus 4000 in (bigger cost),
      - task LOW:  Mode-A opus 1000 in (smaller cost),
      - unattributed (no task_id): Mode-B gemini run.
    Asserts three task rows, the null bucket present, and ordering by
    total_cost_usd descending.
    """
    now = datetime.now(timezone.utc)
    cycle_day = _safe_cycle_day(now)
    in_cycle = _current_cycle_start(now, cycle_day) + timedelta(hours=2)

    project_id = await _make_project(client, scaffold_cleanup, "um-tasks")
    task_high = await _make_task(client, project_id, "high-cost task")
    task_low = await _make_task(client, project_id, "low-cost task")

    await _seed_mode_a(
        client, project_id, occurred_at=in_cycle, input_tokens=4000, task_id=task_high
    )
    await _seed_mode_a(
        client, project_id, occurred_at=in_cycle, input_tokens=1000, task_id=task_low
    )
    # Unattributed Mode-B run (no task_id) — a sizeable gemini run.
    await _seed_mode_b(
        client,
        project_id,
        finished_at=in_cycle,
        input_tokens=300_000,
        output_tokens=100_000,
    )

    resp = await client.get(
        f"/api/usage/monthly?months=1&cycle_day={cycle_day}&project_id={project_id}"
    )
    assert resp.status_code == 200, resp.text
    cyc = resp.json()["cycles"][0]
    tasks = cyc["tasks"]

    # Three buckets: two real tasks + the unattributed (null) bucket.
    assert len(tasks) == 3
    by_id = {t["task_id"]: t for t in tasks}
    assert task_high in by_id
    assert task_low in by_id
    assert None in by_id, f"expected an unattributed (null) bucket: {tasks}"

    # Titles resolved for the real tasks; null for the unattributed bucket.
    assert by_id[task_high]["task_title"] == "high-cost task"
    assert by_id[None]["task_title"] is None

    # POSITIVE: ordered by total_cost_usd descending (non-increasing sequence).
    totals = [Decimal(t["total_cost_usd"]) for t in tasks]
    assert totals == sorted(totals, reverse=True), totals
    # NEGATIVE lock on grouping: the high task's cost strictly exceeds the low
    # task's (4000 vs 1000 input tokens) — proves rows grouped, not split.
    assert Decimal(by_id[task_high]["total_cost_usd"]) > Decimal(
        by_id[task_low]["total_cost_usd"]
    )


# =============================================================================
# 5. occurred_at clamp on POST /api/usage/events (Part B)
# =============================================================================


@pytest.mark.asyncio
async def test_occurred_at_clamp_rejects_old_and_future(
    client, scaffold_cleanup
) -> None:
    """now-31d → 422, now+10min → 422, now-1d → 201, omitted → 201."""
    project_id = await _make_project(client, scaffold_cleanup, "um-clamp")
    now = datetime.now(timezone.utc)

    headers = {"X-Project-Id": str(project_id)}

    # now-31d → outside [now-30d, now+5min] → 422.
    too_old = await client.post(
        "/api/usage/events",
        json={
            "model": "claude-opus-4-8",
            "occurred_at": (now - timedelta(days=31)).isoformat(),
        },
        headers=headers,
    )
    assert too_old.status_code == 422, too_old.text

    # now+10min → beyond the +5min skew bound → 422.
    too_future = await client.post(
        "/api/usage/events",
        json={
            "model": "claude-opus-4-8",
            "occurred_at": (now + timedelta(minutes=10)).isoformat(),
        },
        headers=headers,
    )
    assert too_future.status_code == 422, too_future.text

    # now-1d → inside the window → 201 (POSITIVE: the clamp is not over-tight).
    ok_recent = await client.post(
        "/api/usage/events",
        json={
            "model": "claude-opus-4-8",
            "occurred_at": (now - timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert ok_recent.status_code == 201, ok_recent.text

    # omitted → server default now() applies → 201 (None must pass the validator).
    ok_omitted = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8"},
        headers=headers,
    )
    assert ok_omitted.status_code == 201, ok_omitted.text


# =============================================================================
# 6. project_id filter excludes other projects
# =============================================================================


@pytest.mark.asyncio
async def test_project_filter_excludes_other_projects(
    client, scaffold_cleanup
) -> None:
    """A monthly query for project A must not include project B's spend."""
    now = datetime.now(timezone.utc)
    cycle_day = _safe_cycle_day(now)
    in_cycle = _current_cycle_start(now, cycle_day) + timedelta(hours=3)

    project_a = await _make_project(client, scaffold_cleanup, "um-pf-a")
    project_b = await _make_project(client, scaffold_cleanup, "um-pf-b")

    # Seed both modes in project B only.
    await _seed_mode_a(client, project_b, occurred_at=in_cycle, input_tokens=9999)
    await _seed_mode_b(client, project_b, finished_at=in_cycle, input_tokens=500_000)

    # Query project A → current cycle must be empty (B's rows excluded).
    resp = await client.get(
        f"/api/usage/monthly?months=1&cycle_day={cycle_day}&project_id={project_a}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cyc = body["cycles"][0]
    # NEGATIVE: none of B's spend leaked into A.
    assert Decimal(cyc["total_cost_usd"]) == Decimal("0")
    assert cyc["mode_a_input_tokens"] == 0
    assert cyc["mode_b_input_tokens"] == 0
    assert cyc["tasks"] == []
    assert Decimal(body["total_cost_usd"]) == Decimal("0")

    # POSITIVE control: querying project B DOES surface the seeded spend.
    resp_b = await client.get(
        f"/api/usage/monthly?months=1&cycle_day={cycle_day}&project_id={project_b}"
    )
    assert resp_b.status_code == 200, resp_b.text
    cyc_b = resp_b.json()["cycles"][0]
    assert cyc_b["mode_a_input_tokens"] == 9999
    assert cyc_b["mode_b_input_tokens"] == 500_000


# =============================================================================
# 7. Empty/zero window → zero-filled cycles, correct shape
# =============================================================================


@pytest.mark.asyncio
async def test_empty_window_zero_filled(client, scaffold_cleanup) -> None:
    """A project with no spend → ``months`` zero-filled cycles, total "0.0000"."""
    project_id = await _make_project(client, scaffold_cleanup, "um-empty")

    resp = await client.get(
        f"/api/usage/monthly?months=4&cycle_day=15&project_id={project_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Shape: top-level fields present.
    assert body["months"] == 4
    assert body["cycle_day"] == 15
    assert body["total_cost_usd"] == "0.0000"

    # Exactly `months` zero-filled cycles, most-recent first (strictly
    # descending cycle_start dates).
    cycles = body["cycles"]
    assert len(cycles) == 4
    starts = [c["cycle_start"] for c in cycles]
    assert starts == sorted(starts, reverse=True), starts
    for c in cycles:
        assert c["mode_a_cost_usd"] == "0.0000"
        assert c["mode_b_cost_usd"] == "0.0000"
        assert c["total_cost_usd"] == "0.0000"
        assert c["mode_a_input_tokens"] == 0
        assert c["mode_b_output_tokens"] == 0
        assert c["tasks"] == []
        # Cut-off day on every cycle_start.
        assert int(c["cycle_start"].split("-")[2]) == 15


# =============================================================================
# 8. Pure-unit regression: year-rollover in _cycle_starts (Kanban #2356)
#    No DB; exercises the helper directly to lock two under-tested date-math
#    branches that integration tests can't easily reach without wall-clock tricks.
# =============================================================================


def test_cycle_starts_december_no_year_rollover() -> None:
    """December case: cycle_day=1, now=Dec 20 — three cycles all in 2026/2025.

    now.day (20) >= cycle_day (1), so the current cycle starts Dec 1 2026.
    Walking back 2 more months: Nov 1, Oct 1. No year-rollover needed for the
    forward direction, but _prev_month must correctly cross Dec→Nov→Oct without
    erroneously decrementing the year.

    POSITIVE: all three cycle-start dates are correct.
    NEGATIVE: the year never goes wrong (all dates within 2026, then 2026/Oct).
    """
    from src.routers.usage import _cycle_starts

    utc = timezone.utc
    now = datetime(2026, 12, 20, 14, 0, 0, tzinfo=utc)
    result = _cycle_starts(now, cycle_day=1, months=3)

    assert result == [date(2026, 12, 1), date(2026, 11, 1), date(2026, 10, 1)]
    # NEGATIVE: none of the starts accidentally rolled into 2025 or 2027.
    assert all(d.year == 2026 for d in result)


def test_cycle_starts_january_before_cutoff_crosses_year() -> None:
    """January-before-cutoff case: cycle_day=15, now=Jan 5 2026 — year rollover.

    now.day (5) < cycle_day (15), so _prev_month is called: Jan→Dec 2025.
    Current cycle started Dec 15 2025; prior cycle started Nov 15 2025.

    This is the year-rollover branch: the helper must emit a date in 2025, not
    2026, and must NOT produce date(2026, 0, 15) (month=0 crash).

    POSITIVE: both starts are in 2025, with the correct months.
    NEGATIVE: no start is in 2026 (the current year of `now`), confirming the
    year-rollover branch fired — not the same-month branch.
    """
    from src.routers.usage import _cycle_starts

    utc = timezone.utc
    now = datetime(2026, 1, 5, 9, 0, 0, tzinfo=utc)
    result = _cycle_starts(now, cycle_day=15, months=2)

    assert result == [date(2025, 12, 15), date(2025, 11, 15)]
    # NEGATIVE: the year rolled back — neither date is in 2026.
    assert all(d.year == 2025 for d in result)
    # NEGATIVE: no invalid month (guards against a month=0 or month=13 edge).
    assert all(1 <= d.month <= 12 for d in result)


# =============================================================================
# 9. Pure-unit: _next_month helper + December newest-cycle exclusive-end
#    Mirrors the _prev_month regression above; locks the extracted helper and
#    the December→January year-rollover on the newest-cycle boundary.
# =============================================================================


def test_next_month_basic_cases() -> None:
    """_next_month(y,m) → (y,m+1) for non-December; (y+1,1) for December.

    POSITIVE: both the normal advance and the year-rollover branch produce the
    expected (year, month) tuples.
    NEGATIVE: December does NOT yield month=13.
    """
    from src.routers.usage import _next_month

    # Normal advance — mid-year.
    assert _next_month(2026, 6) == (2026, 7)
    # Normal advance — November → December (within same year).
    assert _next_month(2026, 11) == (2026, 12)
    # December year-rollover branch.
    assert _next_month(2026, 12) == (2027, 1)
    # NEGATIVE: December must not produce month 13.
    assert _next_month(2026, 12)[1] != 13


def test_newest_cycle_exclusive_end_december_rollover() -> None:
    """December newest-cycle boundary rolls the exclusive end into January next year.

    With now = Dec 20 2026 and cycle_day=1:
      - newest start  = Dec 1 2026
      - exclusive end = Jan 1 2027 (i.e. _next_month(2026,12) = (2027,1))
      - display end   = Dec 31 2026 (exclusive_end minus 1 day)

    This pins the _next_month extraction path that replaced the inline ternary
    (see Kanban #2356 M1).

    POSITIVE: cycle_start = "2026-12-01", cycle_end = "2026-12-31".
    NEGATIVE: cycle_end is NOT "2026-12-32" or "2027-01-00" (invalid date guards).
    """
    from src.routers.usage import _cycle_starts
    from datetime import time

    utc = timezone.utc
    now = datetime(2026, 12, 20, 14, 0, 0, tzinfo=utc)
    cycle_day = 1

    starts = _cycle_starts(now, cycle_day=cycle_day, months=1)
    assert len(starts) == 1
    newest_start = starts[0]
    assert newest_start == date(2026, 12, 1)

    # Replicate the router's exclusive-end computation using _next_month.
    from src.routers.usage import _next_month

    ny, nm = _next_month(newest_start.year, newest_start.month)
    newest_end_excl = datetime(ny, nm, cycle_day, 0, 0, 0, tzinfo=utc)

    # POSITIVE: exclusive end is Jan 1 2027.
    assert newest_end_excl == datetime(2027, 1, 1, 0, 0, 0, tzinfo=utc)

    # Display end = exclusive_end.date() - 1 day (mirrors router logic).
    from datetime import timedelta as _td

    display_end = (newest_end_excl - _td(days=1)).date()
    # POSITIVE: last display day of the December cycle is Dec 31 2026.
    assert display_end == date(2026, 12, 31)
    # NEGATIVE: not a nonsense date.
    assert display_end.month == 12
    assert display_end.day == 31
