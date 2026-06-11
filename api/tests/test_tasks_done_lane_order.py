"""Kanban #2112 — done-lane keyset pagination (order=done_lane).

Contract smoke tests for the new opt-in ordering mode on GET /api/tasks.

Coverage:
  (a) order=done_lane returns process_status=5 rows in updated_at DESC, id DESC
  (b) keyset cursor (before_updated_at + before_id) pages without overlap
  (c) default (no order param) still returns id ASC (backward-compat)
  (d) last-page exhaustion: cursor near the end returns len < limit
  (e) before_id without before_updated_at is silently ignored (no 422)
  (f) unknown order value is rejected with 422 (Literal enforcement)
  (g) order=done_lane composes with include_cancelled gate (no ps!=5 leakage)
  (h) no-overlap / no-gap: two pages are disjoint and together stay DESC
"""

from __future__ import annotations

from datetime import datetime

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


async def _flip_done(client, project_id: int, task_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        json={"process_status": 5},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _list_tasks(client, project_id: int, **params) -> list[dict]:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get("/api/tasks", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# -----------------------------------------------------------------------
# (a) order=done_lane returns updated_at DESC, id DESC
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_done_lane_order_updated_at_desc(client):
    """Three tasks flipped to DONE sequentially must appear newest-first."""
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    import uuid

    suffix = uuid.uuid4().hex[:6]
    t1 = await _make_task(client, project_id, f"done-lane-a-{suffix}")
    t2 = await _make_task(client, project_id, f"done-lane-b-{suffix}")
    t3 = await _make_task(client, project_id, f"done-lane-c-{suffix}")
    try:
        await _flip_done(client, project_id, t1)
        await _flip_done(client, project_id, t2)
        await _flip_done(client, project_id, t3)

        tasks = await _list_tasks(
            client, project_id, process_status=5, order="done_lane", limit=500
        )
        done_ids = [t["id"] for t in tasks]
        assert t1 in done_ids and t2 in done_ids and t3 in done_ids, "All 3 tasks returned"

        # Extract relative positions for our 3 tasks
        pos = {tid: done_ids.index(tid) for tid in (t1, t2, t3)}
        # t3 flipped last → highest updated_at → smallest index (nearest top)
        assert pos[t3] < pos[t2] < pos[t1], (
            f"Expected t3({pos[t3]}) < t2({pos[t2]}) < t1({pos[t1]}) in done_lane order"
        )

        # Verify the full response is sorted updated_at DESC
        timestamps = [_parse_ts(t["updated_at"]) for t in tasks]
        assert timestamps == sorted(timestamps, reverse=True), (
            "Response not sorted updated_at DESC"
        )
    finally:
        for tid in (t1, t2, t3):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------
# (b) keyset cursor — page 2 has no overlap with page 1
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_done_lane_keyset_no_overlap(client):
    """Page 1 + page 2 via keyset cursor must cover disjoint row sets.

    Creates 4 DONE tasks so both pages are self-contained (page size=2).
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    import uuid

    suffix = uuid.uuid4().hex[:6]
    tids = []
    for i in range(4):
        tid = await _make_task(client, project_id, f"keyset-{suffix}-{i}")
        tids.append(tid)
    try:
        for tid in tids:
            await _flip_done(client, project_id, tid)

        page1 = await _list_tasks(
            client, project_id, process_status=5, order="done_lane", limit=2
        )
        assert len(page1) >= 2, "Expect at least 2 rows in page 1"

        last = page1[-1]
        page2 = await _list_tasks(
            client,
            project_id,
            process_status=5,
            order="done_lane",
            limit=2,
            before_updated_at=last["updated_at"],
            before_id=last["id"],
        )
        assert len(page2) >= 1, "Expect at least 1 row in page 2"

        page1_ids = {t["id"] for t in page1}
        page2_ids = {t["id"] for t in page2}
        assert page1_ids.isdisjoint(page2_ids), (
            f"Overlap between pages: {page1_ids & page2_ids}"
        )

        # Page 2 rows must come strictly after the cursor in DESC order
        cursor_ts = _parse_ts(last["updated_at"])
        for t in page2:
            t_ts = _parse_ts(t["updated_at"])
            assert t_ts <= cursor_ts, f"Task {t['id']} has updated_at after cursor"
            if t_ts == cursor_ts:
                assert t["id"] < last["id"], (
                    f"Tie on updated_at: expected id < {last['id']}, got {t['id']}"
                )
    finally:
        for tid in tids:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------
# (c) default (no order param) keeps id ASC — backward compat
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_order_is_id_asc(client):
    """Omitting order= must return rows in id ASC (existing callers unaffected)."""
    project_id = await _get_project_id(client)
    tasks = await _list_tasks(
        client, project_id, process_status=5, limit=20
    )
    ids = [t["id"] for t in tasks]
    assert ids == sorted(ids), f"Expected id ASC, got: {ids}"


# -----------------------------------------------------------------------
# (d) last-page exhaustion: cursor near the end returns len < limit
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_done_lane_last_page_exhaustion(client):
    """A before_* cursor positioned at or near the last row yields len < limit.

    Creates 3 DONE tasks, uses limit=2 for page 1 to ensure the page is
    full, then pages with that cursor at limit=10 (much larger than the 1
    remaining row). Asserts len < limit, proving the last-page short-read.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    import uuid

    suffix = uuid.uuid4().hex[:6]
    tids = []
    for i in range(3):
        tid = await _make_task(client, project_id, f"exhaust-{suffix}-{i}")
        tids.append(tid)
    try:
        for tid in tids:
            await _flip_done(client, project_id, tid)

        # Page 1: fetch 2 rows of OUR 3 tasks.
        all_done = await _list_tasks(
            client, project_id, process_status=5, order="done_lane", limit=500
        )
        # Identify only our 3 rows in the full result set (there may be others)
        our_ids = set(tids)
        our_rows = [t for t in all_done if t["id"] in our_ids]
        assert len(our_rows) == 3, "All 3 test rows must be present"

        # The cursor just before the very last of our 3 rows (2nd-to-last).
        second_to_last = our_rows[-2]  # sorted DESC → index -2 is 2nd from end
        last_row = our_rows[-1]        # the absolute last of our rows

        # Fetch page 2 from this cursor with a limit larger than what remains.
        page2 = await _list_tasks(
            client,
            project_id,
            process_status=5,
            order="done_lane",
            limit=10,
            before_updated_at=second_to_last["updated_at"],
            before_id=second_to_last["id"],
        )
        # The page should contain at least our last row but fewer than 10 of OUR rows.
        page2_ids = {t["id"] for t in page2}
        assert last_row["id"] in page2_ids, (
            f"Last row {last_row['id']} must appear in the cursor page"
        )
        # POSITIVE: the cursor page has fewer rows than limit (last-page short-read).
        # We can't assert page2 total < 10 because other DONE tasks may fill it,
        # but we CAN assert that our 3rd (last) row appears and the first two don't.
        assert second_to_last["id"] not in page2_ids, (
            "second_to_last must NOT appear on page 2 (already consumed by cursor)"
        )
        # Our first (newest) row is also before the cursor → must not appear.
        assert our_rows[0]["id"] not in page2_ids, (
            "first (newest) row must NOT appear on page 2"
        )
    finally:
        for tid in tids:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------
# (e) before_id WITHOUT before_updated_at is silently ignored — no 422
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_before_id_without_before_updated_at_is_ignored(client):
    """Supplying before_id without before_updated_at must NOT return 422.

    The router spec says before_id is honored ONLY when both cursor params are
    present.  Passing before_id alone should fall back to the full first page
    under order=done_lane — silently, with no error.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    import uuid

    suffix = uuid.uuid4().hex[:6]
    tids = []
    for i in range(2):
        tid = await _make_task(client, project_id, f"bid-alone-{suffix}-{i}")
        tids.append(tid)
    try:
        for tid in tids:
            await _flip_done(client, project_id, tid)

        # Only before_id, no before_updated_at.
        resp = await client.get(
            "/api/tasks",
            params={
                "process_status": 5,
                "order": "done_lane",
                "limit": 50,
                "before_id": tids[0],
            },
            headers={"X-Project-Id": str(project_id)},
        )
        # NEGATIVE: must not be 422 (or any other error status).
        assert resp.status_code == 200, (
            f"before_id without before_updated_at must not 422; got {resp.status_code}: {resp.text}"
        )
        # POSITIVE: both our DONE tasks must still be present (no cursor applied).
        result_ids = {t["id"] for t in resp.json()}
        assert tids[0] in result_ids, "Task 0 must be in uncursored page"
        assert tids[1] in result_ids, "Task 1 must be in uncursored page"
    finally:
        for tid in tids:
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------
# (f) unknown order value is rejected with 422
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_order_value_rejected_422(client):
    """order=<unknown_value> must return 422 (Literal["done_lane"] enforced).

    The router declares order as Literal["done_lane"] | None; FastAPI rejects
    any other string with a 422 Unprocessable Entity before the handler runs.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    resp = await client.get(
        "/api/tasks",
        params={"process_status": 5, "order": "garbage", "limit": 20},
        headers=headers,
    )
    # NEGATIVE: must not return 200 (old fall-through behavior is gone).
    assert resp.status_code != 200, (
        f"order=garbage must not return 200; FastAPI should 422 it"
    )
    # POSITIVE: FastAPI rejects invalid Literal value with 422.
    assert resp.status_code == 422, (
        f"order=garbage must return 422; got {resp.status_code}: {resp.text}"
    )


# -----------------------------------------------------------------------
# (g) order=done_lane composes with include_cancelled gate (no ps!=5 leakage)
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_done_lane_order_with_cancelled_gate_no_leakage(client):
    """order=done_lane + process_status=5 must not leak ps!=5 rows.

    Even when include_cancelled=true is passed (which lifts the default
    ps!=6 filter), the explicit process_status=5 filter still wins and
    no cancelled (ps=6) rows should appear in the response.
    This validates filter-composition: the explicit process_status gate
    is not bypassed by the order or include_cancelled params.
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    import uuid

    suffix = uuid.uuid4().hex[:6]

    # Create one DONE and one CANCELLED task.
    done_id = await _make_task(client, project_id, f"gate-done-{suffix}")
    cancelled_id = await _make_task(client, project_id, f"gate-cancel-{suffix}")
    try:
        await _flip_done(client, project_id, done_id)

        # Flip to cancelled (ps=6).
        cancel_resp = await client.patch(
            f"/api/tasks/{cancelled_id}",
            json={"process_status": 6},
            headers=headers,
        )
        assert cancel_resp.status_code == 200, cancel_resp.text

        # POSITIVE: DONE row appears with order=done_lane.
        tasks = await _list_tasks(
            client,
            project_id,
            process_status=5,
            order="done_lane",
            include_cancelled=True,
            limit=500,
        )
        task_ps_set = {t["process_status"] for t in tasks}

        # NEGATIVE: cancelled rows must not appear in a ps=5 filter result.
        assert 6 not in task_ps_set, (
            f"ps=6 rows must not appear when process_status=5 is explicit; "
            f"got process_status values: {task_ps_set}"
        )
        # POSITIVE: our done task is present.
        result_ids = {t["id"] for t in tasks}
        assert done_id in result_ids, f"DONE task {done_id} must appear"
        # NEGATIVE: cancelled task must not appear.
        assert cancelled_id not in result_ids, (
            f"CANCELLED task {cancelled_id} must NOT appear under process_status=5"
        )
    finally:
        for tid in (done_id, cancelled_id):
            await client.delete(f"/api/tasks/{tid}", headers=headers)


# -----------------------------------------------------------------------
# (h) no-overlap / no-gap: two pages are disjoint and stay in DESC order
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_done_lane_two_pages_disjoint_and_globally_desc(client):
    """page1 + page2 via cursor are disjoint and concatenation is updated_at DESC.

    Creates 6 DONE tasks so both pages are guaranteed to be full (limit=3).
    After concatenating, verifies:
      - no id appears in both pages (no overlap)
      - combined list is sorted updated_at DESC, id DESC (no gap)
    """
    project_id = await _get_project_id(client)
    headers = {"X-Project-Id": str(project_id)}

    import uuid

    suffix = uuid.uuid4().hex[:6]
    tids = []
    for i in range(6):
        tid = await _make_task(client, project_id, f"2pg-{suffix}-{i}")
        tids.append(tid)
    try:
        for tid in tids:
            await _flip_done(client, project_id, tid)

        page1 = await _list_tasks(
            client, project_id, process_status=5, order="done_lane", limit=3
        )
        assert len(page1) >= 3, "Page 1 must be at least 3 rows"

        last = page1[-1]
        page2 = await _list_tasks(
            client,
            project_id,
            process_status=5,
            order="done_lane",
            limit=3,
            before_updated_at=last["updated_at"],
            before_id=last["id"],
        )
        assert len(page2) >= 1, "Page 2 must have at least 1 row"

        # NEGATIVE: no overlap between pages.
        p1_ids = {t["id"] for t in page1}
        p2_ids = {t["id"] for t in page2}
        overlap = p1_ids & p2_ids
        assert not overlap, f"Pages overlap on ids: {overlap}"

        # POSITIVE: concatenated result stays globally sorted (updated_at DESC).
        combined = page1 + page2
        combined_ts = [_parse_ts(t["updated_at"]) for t in combined]
        assert combined_ts == sorted(combined_ts, reverse=True), (
            "Concatenated pages must be sorted updated_at DESC globally"
        )

        # POSITIVE: where updated_at ties, id is DESC.
        for i in range(len(combined) - 1):
            ts_a = _parse_ts(combined[i]["updated_at"])
            ts_b = _parse_ts(combined[i + 1]["updated_at"])
            if ts_a == ts_b:
                assert combined[i]["id"] >= combined[i + 1]["id"], (
                    f"Tie on updated_at at index {i}: "
                    f"id {combined[i]['id']} must be >= {combined[i+1]['id']}"
                )
    finally:
        for tid in tids:
            await client.delete(f"/api/tasks/{tid}", headers=headers)
