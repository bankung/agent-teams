"""Kanban #833 — GET /api/tasks/next-autorun.

Read-only snapshot endpoint that tells the headless auto-run loop what to
do next. Eight positive cases, one fresh project per test (via
`_make_fresh_project`) to avoid cross-test lane pollution.

Note on run_mode: `auto_headless` requires `auto_run_consent_at` on the
project. We use `auto_pickup` (no consent gate) for most tests. Test (a)
uses `auto_headless` on the agent-teams project (which has consent) to
verify that run_mode is correctly included.

Coverage:
  (a) next_task returns top-priority auto_pickup task; manual task excluded
  (b) next_task skips tasks with halt_reason set
  (c) next_task skips tasks with active blocker (blocker ps != DONE)
  (d) next_task includes task whose blocker is DONE (ps=5)
  (e) resume_tasks returns halted task whose blocker is DONE
  (f) pending_questions returns question/decision tasks not yet DONE
  (g) blocked_count correct when tasks have active blockers
  (h) empty project → next_task=null, resume_tasks=[], pending_questions=[], blocked_count=0
  (i-l) scheduled_at enforcement — Kanban #1972
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror test_tasks_sort_order.py pattern)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    """Create an isolated project with no seeded tasks."""
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


async def _make_task(client, project_id: int, title: str, **extras) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(client, project_id: int, task_id: int, **fields) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(f"/api/tasks/{task_id}", json=fields, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_next_autorun(client, project_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_project_id(client) -> int:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# (a) next_task returns top-priority auto_pickup task; manual excluded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_returns_auto_pickup_task_and_excludes_manual(
    client, scaffold_cleanup
) -> None:
    """auto_pickup task is returned; manual task is excluded even if lower priority."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-a")

    # manual task — must be excluded
    await _make_task(
        client, pid, "manual task", run_mode="manual", task_kind="human", priority=3
    )
    # auto_pickup task — must be returned (auto_pickup needs no consent)
    ap = await _make_task(
        client,
        pid,
        "auto_pickup task",
        run_mode="auto_pickup",
        task_kind="ai",
        priority=2,
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == ap["id"]
    assert body["next_task"]["run_mode"] in ("auto_pickup", "auto_headless")


# ---------------------------------------------------------------------------
# (a2) priority ordering — higher priority runs first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_priority_desc_ordering(client, scaffold_cleanup) -> None:
    """URGENT(4) auto_pickup task is picked before NORMAL(2) auto_pickup task."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-a2")

    normal = await _make_task(
        client, pid, "normal task", run_mode="auto_pickup", task_kind="ai", priority=2
    )
    urgent = await _make_task(
        client, pid, "urgent task", run_mode="auto_pickup", task_kind="ai", priority=4
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == urgent["id"], (
        f"expected urgent task {urgent['id']}, got {body['next_task']['id']}"
    )
    # normal task must not be picked ahead of urgent
    assert body["next_task"]["id"] != normal["id"]


# ---------------------------------------------------------------------------
# (b) next_task skips tasks with halt_reason set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_skips_halted_task(client, scaffold_cleanup) -> None:
    """A task with halt_reason is excluded from next_task."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-b")

    halted = await _make_task(
        client,
        pid,
        "halted task",
        run_mode="auto_pickup",
        task_kind="ai",
        halt_reason="waiting for decision #999",
    )
    runnable = await _make_task(
        client, pid, "runnable task", run_mode="auto_pickup", task_kind="ai"
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == runnable["id"]
    assert body["next_task"]["id"] != halted["id"]


# ---------------------------------------------------------------------------
# (c) next_task skips tasks with active blocker (blocker ps != DONE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_skips_task_with_active_blocker(client, scaffold_cleanup) -> None:
    """A task blocked by a non-DONE task is excluded from next_task."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-c")

    # blocker is TODO (process_status=1)
    blocker = await _make_task(
        client, pid, "blocker task", run_mode="manual", task_kind="human"
    )
    blocked = await _make_task(
        client,
        pid,
        "blocked task",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=blocker["id"],
    )

    body = await _get_next_autorun(client, pid)
    returned_ids = (
        [body["next_task"]["id"]] if body["next_task"] is not None else []
    )
    assert blocked["id"] not in returned_ids, (
        f"blocked task {blocked['id']} should not appear in next_task: {body}"
    )


# ---------------------------------------------------------------------------
# (d) next_task includes task whose blocker is DONE (ps=5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_includes_task_whose_blocker_is_done(
    client, scaffold_cleanup
) -> None:
    """A task blocked by a DONE task IS eligible for next_task."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-d")

    blocker = await _make_task(
        client, pid, "blocker done", run_mode="manual", task_kind="human"
    )
    # Mark blocker DONE
    await _patch_task(client, pid, blocker["id"], process_status=5)

    unblocked = await _make_task(
        client,
        pid,
        "formerly-blocked task",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=blocker["id"],
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == unblocked["id"]


# ---------------------------------------------------------------------------
# (d2) #2422 — next_task includes task whose blocker is CANCELLED (ps=6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_includes_task_whose_blocker_is_cancelled(
    client, scaffold_cleanup
) -> None:
    """#2422: A task blocked by a CANCELLED(6) task IS eligible for next_task.
    CANCELLED is a terminal state — the dependent must not stay blocked.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2422-d2")

    blocker = await _make_task(
        client, pid, "blocker cancelled", run_mode="manual", task_kind="human"
    )
    # Mark blocker CANCELLED (ps=6)
    await _patch_task(client, pid, blocker["id"], process_status=6)

    unblocked = await _make_task(
        client,
        pid,
        "formerly-blocked by cancelled",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=blocker["id"],
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, (
        f"task {unblocked['id']} blocked by CANCELLED blocker must be eligible: {body}"
    )
    assert body["next_task"]["id"] == unblocked["id"]


# ---------------------------------------------------------------------------
# (e) resume_tasks returns halted task whose blocker is DONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_tasks_returns_halted_task_with_done_blocker(
    client, scaffold_cleanup
) -> None:
    """resume_tasks includes halted tasks whose blocker is DONE."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-e")

    blocker = await _make_task(
        client, pid, "blocker for resume", run_mode="manual", task_kind="human"
    )
    await _patch_task(client, pid, blocker["id"], process_status=5)

    halted = await _make_task(
        client,
        pid,
        "halted task waiting for blocker",
        run_mode="auto_pickup",
        task_kind="ai",
        halt_reason="blocked by Question:blocker",
        blocked_by=blocker["id"],
    )

    body = await _get_next_autorun(client, pid)
    resume_ids = [t["id"] for t in body["resume_tasks"]]
    assert halted["id"] in resume_ids, (
        f"halted task {halted['id']} should be in resume_tasks: {body['resume_tasks']}"
    )


# ---------------------------------------------------------------------------
# (e2) pre-push review revert — resume_tasks must NOT include halted task
#      whose blocker is CANCELLED (ps=6); DONE-only resume (#2422 over-broad)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_tasks_excludes_halted_task_with_cancelled_blocker(
    client, scaffold_cleanup
) -> None:
    """resume_tasks is intentionally DONE-only.  A CANCELLED blocker provides no
    answer, so a HITL-halted task whose blocker was cancelled must NOT be auto-resumed
    — it is left halted for manual attention.

    #2422 correctly broadened next-autorun readiness and blocked-count to treat
    CANCELLED as terminal; the pre-push review found that applying the same broadening
    to resume_stmt was incorrect.  This test locks the correct (reverted) behaviour.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2422-e2")

    blocker = await _make_task(
        client, pid, "blocker cancelled for resume", run_mode="manual", task_kind="human"
    )
    await _patch_task(client, pid, blocker["id"], process_status=6)

    halted = await _make_task(
        client,
        pid,
        "halted task waiting on cancelled blocker",
        run_mode="auto_pickup",
        task_kind="ai",
        halt_reason="blocked by Question:blocker",
        blocked_by=blocker["id"],
    )

    body = await _get_next_autorun(client, pid)
    resume_ids = [t["id"] for t in body["resume_tasks"]]
    assert halted["id"] not in resume_ids, (
        f"halted task {halted['id']} with CANCELLED blocker must NOT be in resume_tasks: {body['resume_tasks']}"
    )


# ---------------------------------------------------------------------------
# (f) pending_questions returns question/decision tasks not yet DONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_questions_returns_question_and_decision_tasks(
    client, scaffold_cleanup
) -> None:
    """pending_questions contains active question/decision tasks; work and DONE tasks excluded."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-f")

    # HITL interrupt sets process_status=BLOCKED(4) on question/decision tasks
    q = await _make_task(
        client,
        pid,
        "question task",
        interaction_kind="question",
        question_payload={"question": "Which option?"},
        run_mode="manual",
        task_kind="human",
    )
    await _patch_task(client, pid, q["id"], process_status=4)

    d = await _make_task(
        client,
        pid,
        "decision task",
        interaction_kind="decision",
        question_payload={"question": "Go with option A?", "options": ["A", "B"]},
        run_mode="manual",
        task_kind="human",
    )
    await _patch_task(client, pid, d["id"], process_status=4)

    # work task — must be excluded (not a question/decision interaction_kind)
    await _make_task(
        client, pid, "work task", interaction_kind="work", run_mode="manual", task_kind="human"
    )
    # DONE question task — must be excluded
    done_q = await _make_task(
        client,
        pid,
        "done question",
        interaction_kind="question",
        question_payload={"question": "Already answered?"},
        run_mode="manual",
        task_kind="human",
    )
    await _patch_task(client, pid, done_q["id"], process_status=5)

    body = await _get_next_autorun(client, pid)
    pq_ids = [t["id"] for t in body["pending_questions"]]
    assert q["id"] in pq_ids, body
    assert d["id"] in pq_ids, body
    assert done_q["id"] not in pq_ids, body


# ---------------------------------------------------------------------------
# (f2) pending_questions excludes CANCELLED tasks; includes BLOCKED ones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_questions_excludes_cancelled_includes_blocked(
    client, scaffold_cleanup
) -> None:
    """Kanban #1700: CANCELLED(6) question/decision tasks must NOT appear in
    pending_questions; only BLOCKED(4) tasks (the HITL-interrupt state) are
    genuinely resumable and must be included."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-f2")

    # CANCELLED decision task — must be excluded (process_status=6)
    cancelled_d = await _make_task(
        client,
        pid,
        "cancelled decision",
        interaction_kind="decision",
        question_payload={"question": "Pick one?", "options": ["X", "Y"]},
        run_mode="manual",
        task_kind="human",
    )
    await _patch_task(client, pid, cancelled_d["id"], process_status=6)

    # BLOCKED question task — must be included (process_status=4, HITL state)
    blocked_q = await _make_task(
        client,
        pid,
        "blocked question",
        interaction_kind="question",
        question_payload={"question": "What next?"},
        run_mode="manual",
        task_kind="human",
    )
    await _patch_task(client, pid, blocked_q["id"], process_status=4)

    body = await _get_next_autorun(client, pid)
    pq_ids = [t["id"] for t in body["pending_questions"]]

    assert cancelled_d["id"] not in pq_ids, (
        f"CANCELLED task {cancelled_d['id']} must not appear in pending_questions: {body}"
    )
    assert blocked_q["id"] in pq_ids, (
        f"BLOCKED task {blocked_q['id']} must appear in pending_questions: {body}"
    )


# ---------------------------------------------------------------------------
# (g) blocked_count correct when tasks have active blockers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_count_counts_tasks_with_active_blockers(
    client, scaffold_cleanup
) -> None:
    """blocked_count reflects the number of TODO/IN_PROGRESS tasks with a non-DONE blocker."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-g")

    # Two blockers — both TODO (not DONE)
    b1 = await _make_task(
        client, pid, "blocker 1", run_mode="manual", task_kind="human"
    )
    b2 = await _make_task(
        client, pid, "blocker 2", run_mode="manual", task_kind="human"
    )

    # Two blocked tasks pointing at active blockers → should count
    await _make_task(
        client,
        pid,
        "blocked A",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=b1["id"],
    )
    await _make_task(
        client,
        pid,
        "blocked B",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=b2["id"],
    )

    # One task whose blocker IS DONE → must NOT be counted
    b3 = await _make_task(
        client, pid, "done blocker", run_mode="manual", task_kind="human"
    )
    await _patch_task(client, pid, b3["id"], process_status=5)
    await _make_task(
        client,
        pid,
        "unblocked by done",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=b3["id"],
    )

    body = await _get_next_autorun(client, pid)
    assert body["blocked_count"] == 2, (
        f"expected 2 actively-blocked tasks, got {body['blocked_count']}: {body}"
    )


# ---------------------------------------------------------------------------
# (g2) #2422 — blocked_count excludes tasks whose blocker is CANCELLED (ps=6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_count_excludes_tasks_with_cancelled_blocker(
    client, scaffold_cleanup
) -> None:
    """#2422: blocked_count must NOT include tasks whose blocker is CANCELLED(6).
    CANCELLED is terminal — the dependent is no longer held.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2422-g2")

    # Active (TODO) blocker — should count
    active_blocker = await _make_task(
        client, pid, "active blocker", run_mode="manual", task_kind="human"
    )
    await _make_task(
        client,
        pid,
        "blocked by active",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=active_blocker["id"],
    )

    # CANCELLED blocker — must NOT count
    cancelled_blocker = await _make_task(
        client, pid, "cancelled blocker", run_mode="manual", task_kind="human"
    )
    await _patch_task(client, pid, cancelled_blocker["id"], process_status=6)
    await _make_task(
        client,
        pid,
        "blocked by cancelled",
        run_mode="auto_pickup",
        task_kind="ai",
        blocked_by=cancelled_blocker["id"],
    )

    body = await _get_next_autorun(client, pid)
    assert body["blocked_count"] == 1, (
        f"expected 1 blocked task (active blocker only), got {body['blocked_count']}: {body}"
    )


# ---------------------------------------------------------------------------
# (h) empty project → all null/zero
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_project_returns_null_and_zero(client, scaffold_cleanup) -> None:
    """An empty project returns a well-formed response with all-null/zero fields."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k833-h")

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is None, body
    assert body["resume_tasks"] == [], body
    assert body["pending_questions"] == [], body
    assert body["blocked_count"] == 0, body


# ---------------------------------------------------------------------------
# (i-l) scheduled_at enforcement — Kanban #1972
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_excludes_future_scheduled_at(client, scaffold_cleanup) -> None:
    """auto_pickup task with scheduled_at=+1h must NOT be returned as next_task (AC[0])."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1972-i")

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    future_task = await _make_task(
        client,
        pid,
        "future scheduled task",
        run_mode="auto_pickup",
        task_kind="ai",
        scheduled_at=future,
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is None, (
        f"future-scheduled task {future_task['id']} must NOT be picked up early: {body}"
    )


@pytest.mark.asyncio
async def test_next_task_includes_past_scheduled_at(client, scaffold_cleanup) -> None:
    """auto_pickup task with scheduled_at=-1h (already elapsed) IS returned."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1972-j")

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    past_task = await _make_task(
        client,
        pid,
        "past scheduled task",
        run_mode="auto_pickup",
        task_kind="ai",
        scheduled_at=past,
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == past_task["id"], (
        f"expected past-scheduled task {past_task['id']}, got {body['next_task']}"
    )


@pytest.mark.asyncio
async def test_next_task_includes_null_scheduled_at(client, scaffold_cleanup) -> None:
    """auto_pickup task with scheduled_at=NULL (default) is always eligible."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1972-k")

    null_task = await _make_task(
        client,
        pid,
        "unscheduled task",
        run_mode="auto_pickup",
        task_kind="ai",
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == null_task["id"], (
        f"expected null-scheduled task {null_task['id']}, got {body['next_task']}"
    )


@pytest.mark.asyncio
async def test_next_task_scheduled_at_ordering_unchanged(client, scaffold_cleanup) -> None:
    """With two eligible tasks, priority ordering is unaffected by scheduled_at presence.

    Arrange: urgent task (priority=4, scheduled_at=-1h) + normal task (priority=2, no
    scheduled_at). The urgent task should still win because priority DESC is the primary sort.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1972-l")

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    urgent = await _make_task(
        client,
        pid,
        "urgent past-scheduled",
        run_mode="auto_pickup",
        task_kind="ai",
        priority=4,
        scheduled_at=past,
    )
    await _make_task(
        client,
        pid,
        "normal unscheduled",
        run_mode="auto_pickup",
        task_kind="ai",
        priority=2,
    )

    body = await _get_next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == urgent["id"], (
        f"expected priority-4 task {urgent['id']}, got {body['next_task']['id']}"
    )
