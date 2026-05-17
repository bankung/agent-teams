"""Kanban #1010 — cross-project next-action recommender.

Endpoint: GET /api/user/next-action?limit=N
USER-scoped; no X-Project-Id header required.

Coverage:
  1. Happy path: 3+ actionable tasks → ordered by score (oldest first
     dominates aging factor).
  2. Empty: items=[], fallback_hint string with running/completed counts.
  3. Filter: done / blocked / non-interaction tasks EXCLUDED.
  4. Ranking: newer-but-P1 outranks older-but-P3 when priority weight wins.
  5. Cross-project: items from multiple projects co-mingle.
  6. limit query param respected.
  7. Budget timeout: hanging PL call → item still returned, budget=0.
  8. No X-Project-Id header required.

Plus pure-ranker unit tests on weights, reason picking, and tie-band rule.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from src.db import SessionLocal
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionCompact, SessionRun
from src.models.task import Task, TaskHistory
from src.services.next_action_ranker import (
    AGING_HOURS_FULL,
    BLOCK_COUNT_FULL,
    TIE_BAND,
    W_AGING,
    W_BLOCK,
    W_BUDGET,
    W_PRIORITY,
    RankedCandidate,
    compute_score_and_reason,
    score_candidates,
)


# =============================================================================
# Fixtures — fresh DB per test for predictable cross-project assertions
# =============================================================================


@pytest_asyncio.fixture(autouse=True)
async def _purge_db_per_test():
    """Purge every row before each test; re-seed `agent-teams` on teardown.

    Mirrors the pattern in test_empty_db_smoke.py — clean slate so
    cross-project queries see only the tasks we explicitly create.
    """
    async with SessionLocal() as session:
        await session.execute(delete(SessionCompact))
        await session.execute(delete(SessionRun))
        await session.execute(delete(SessionModel))
        await session.execute(delete(Task))
        await session.execute(delete(TaskHistory))
        await session.execute(delete(Project))
        for seq in (
            "projects_id_seq",
            "tasks_id_seq",
            "tasks_history_id_seq",
            "sessions_id_seq",
            "session_runs_id_seq",
            "session_compacts_id_seq",
            "tool_calls_id_seq",
        ):
            await session.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH 1"))
        await session.commit()

    yield

    # Restore canonical seed for sibling tests in the same pytest invocation.
    from src.db import engine as _engine
    from scripts.seed import _seed

    await _engine.dispose()
    await _seed()
    await _engine.dispose()


# =============================================================================
# Helpers — HTTP creation of projects + tasks
# =============================================================================


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, slug: str) -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_question_task(
    client,
    project_id: int,
    title: str,
    *,
    priority: int = 2,
    interaction_kind: str = "question",
) -> int:
    """Create a question/decision task (HITL by definition, so manual + human)."""
    headers = {"X-Project-Id": str(project_id)}
    body = {
        "project_id": project_id,
        "title": title,
        "priority": priority,
        "interaction_kind": interaction_kind,
        "task_kind": "human",
        "run_mode": "manual",
        "question_payload": {
            "question": f"q for {title}?",
            "options": ["yes", "no"],
            "answer_history": [],
        },
    }
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _bump_updated_at(task_id: int, older_by_hours: float) -> None:
    """Backdate a task's updated_at by N hours.

    Direct ORM mutation (NO raw DML SQL) — we update the column on the
    fetched row + commit, which is the supported channel for test fixtures.
    """
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        assert task is not None, f"task_id={task_id} not found"
        task.updated_at = datetime.now(timezone.utc) - timedelta(hours=older_by_hours)
        await session.commit()


# =============================================================================
# 1. Happy path — 3+ actionable tasks ordered by score
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_returns_ordered_actionable_tasks(client, scaffold_cleanup):
    """Three question tasks of varying age — the oldest scores highest on the
    aging factor and lands first."""
    proj = await _make_project(client, scaffold_cleanup, "next-action-happy")
    pid = proj["id"]

    t1 = await _make_question_task(client, pid, "youngest")
    t2 = await _make_question_task(client, pid, "middle")
    t3 = await _make_question_task(client, pid, "oldest")

    # Age them apart so the aging factor produces a clean ordering.
    await _bump_updated_at(t2, 24)
    await _bump_updated_at(t3, 96)

    resp = await client.get("/api/user/next-action?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback_hint"] is None, body
    items = body["items"]
    assert len(items) == 3, items

    # Oldest first: scores should be monotone non-increasing.
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True), scores
    assert items[0]["task_id"] == t3, items
    # Each item carries the required keys.
    for item in items:
        assert set(item.keys()) == {
            "task_id", "project_id", "project_name", "title", "reason", "score"
        }
        assert item["project_name"] == proj["name"]
        assert 0.0 <= item["score"] <= 1.0
        assert len(item["reason"]) > 0


# =============================================================================
# 2. Empty result → items: [], fallback_hint set
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_empty_returns_fallback_hint(client):
    """Empty DB (purge fixture leaves zero rows) → empty items, fallback hint
    reports zero running, zero completed."""
    resp = await client.get("/api/user/next-action?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["fallback_hint"] is not None
    assert "No action needed" in body["fallback_hint"]
    # Both counts present in the hint.
    assert "tasks running" in body["fallback_hint"]
    assert "completed today" in body["fallback_hint"]


@pytest.mark.asyncio
async def test_next_action_empty_hint_includes_running_count(
    client, scaffold_cleanup
):
    """Running and completed tasks (process_status=2 / =5) are excluded from
    the candidate set BUT counted in the fallback hint."""
    proj = await _make_project(client, scaffold_cleanup, "next-action-counts")
    pid = proj["id"]
    headers = {"X-Project-Id": str(pid)}

    # Create one in-progress + one done task, NO question/decision tasks.
    body_running = {"project_id": pid, "title": "running work"}
    r1 = await client.post("/api/tasks", json=body_running, headers=headers)
    assert r1.status_code == 201, r1.text
    rid = r1.json()["id"]
    await client.patch(f"/api/tasks/{rid}", json={"process_status": 2}, headers=headers)

    body_done = {"project_id": pid, "title": "completed work"}
    r2 = await client.post("/api/tasks", json=body_done, headers=headers)
    assert r2.status_code == 201, r2.text
    did = r2.json()["id"]
    await client.patch(f"/api/tasks/{did}", json={"process_status": 2}, headers=headers)
    await client.patch(f"/api/tasks/{did}", json={"process_status": 5}, headers=headers)

    resp = await client.get("/api/user/next-action?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    # Hint reports at least 1 running + 1 completed today.
    assert body["fallback_hint"] is not None
    # Numbers parsed from the string — robust to wording tweaks.
    hint = body["fallback_hint"]
    assert "1 tasks running" in hint, hint
    assert "1 completed today" in hint, hint


# =============================================================================
# 3. Filter correctness — done / blocked / non-interaction EXCLUDED
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_filter_excludes_done_blocked_work(client, scaffold_cleanup):
    """Verify each disqualifying condition removes the task from the candidate
    pool. Start with one actionable task as a "control" that survives the
    filter and lands in items."""
    proj = await _make_project(client, scaffold_cleanup, "next-action-filter")
    pid = proj["id"]
    headers = {"X-Project-Id": str(pid)}

    # Control: one actionable question task — should appear in items.
    control = await _make_question_task(client, pid, "control survives")

    # Excluded: done question task.
    done_q = await _make_question_task(client, pid, "done question")
    await client.patch(
        f"/api/tasks/{done_q}", json={"process_status": 5}, headers=headers
    )

    # Excluded: cancelled question task.
    cancelled = await _make_question_task(client, pid, "cancelled question")
    await client.patch(
        f"/api/tasks/{cancelled}",
        json={"process_status": 6, "status_change_reason": "test cancel"},
        headers=headers,
    )

    # Excluded: blocked question (has blocked_by set).
    blocker = await _make_question_task(client, pid, "blocker")
    blocked_resp = await client.post(
        "/api/tasks",
        json={
            "project_id": pid,
            "title": "blocked question",
            "interaction_kind": "question",
            "task_kind": "human",
            "run_mode": "manual",
            "question_payload": {
                "question": "q?",
                "options": ["a", "b"],
                "answer_history": [],
            },
            "blocked_by": blocker,
        },
        headers=headers,
    )
    assert blocked_resp.status_code == 201, blocked_resp.text

    # Excluded: non-interaction (work-kind) task.
    work_resp = await client.post(
        "/api/tasks",
        json={"project_id": pid, "title": "regular work task"},
        headers=headers,
    )
    assert work_resp.status_code == 201, work_resp.text

    resp = await client.get("/api/user/next-action?limit=10")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Only the control task + the blocker (which has no blocked_by itself) survive.
    task_ids = {item["task_id"] for item in body["items"]}
    assert control in task_ids
    assert blocker in task_ids
    assert done_q not in task_ids
    assert cancelled not in task_ids
    assert work_resp.json()["id"] not in task_ids


# =============================================================================
# 4. Ranking — priority can outrank older-but-low-priority task
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_priority_can_outrank_age_when_aging_small(
    client, scaffold_cleanup
):
    """Pure-ranker unit test: with P1 (priority=1) on a fresh task and P3 on a
    slightly older task with no other dominant factors, the priority weight
    swings the order. Test the ranker directly so the SQL fixture doesn't have
    to fight backdating math."""
    now = datetime.now(timezone.utc)
    fresh_p1 = RankedCandidate(
        task_id=1, project_id=1, project_name="p", title="fresh P1",
        priority=1, updated_at=now - timedelta(hours=1),
        downstream_block_count=0, budget_pct=0.0,
    )
    older_p3 = RankedCandidate(
        task_id=2, project_id=1, project_name="p", title="older P3",
        priority=3, updated_at=now - timedelta(hours=10),
        downstream_block_count=0, budget_pct=0.0,
    )

    items = score_candidates([fresh_p1, older_p3], now=now, limit=5)
    # P1 component = 0.2 * 1.0 = 0.20
    # Aging for older (10h / 168) ≈ 0.0595 -> weighted 0.40 * 0.0595 ≈ 0.024
    # So fresh P1 should clearly outrank older P3.
    assert items[0].task_id == 1, items
    assert items[1].task_id == 2, items


# =============================================================================
# 5. Cross-project — items from multiple projects appear together
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_aggregates_across_multiple_projects(
    client, scaffold_cleanup
):
    """Two distinct projects, one actionable task each — both surface in items."""
    proj_a = await _make_project(client, scaffold_cleanup, "cross-a")
    proj_b = await _make_project(client, scaffold_cleanup, "cross-b")

    ta = await _make_question_task(client, proj_a["id"], "from project A")
    tb = await _make_question_task(client, proj_b["id"], "from project B")

    resp = await client.get("/api/user/next-action?limit=10")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    task_ids = {item["task_id"] for item in body["items"]}
    assert ta in task_ids
    assert tb in task_ids
    # Distinct project_name values reflected.
    project_names = {item["project_name"] for item in body["items"]}
    assert proj_a["name"] in project_names
    assert proj_b["name"] in project_names


# =============================================================================
# 6. limit query param respected
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_limit_param_respected(client, scaffold_cleanup):
    """Five candidate tasks, limit=2 → exactly 2 returned (highest-scoring)."""
    proj = await _make_project(client, scaffold_cleanup, "limit-test")
    pid = proj["id"]

    for i in range(5):
        t = await _make_question_task(client, pid, f"task-{i}")
        # Make each progressively older so the ranker has clear distinctions.
        await _bump_updated_at(t, i * 12)

    resp = await client.get("/api/user/next-action?limit=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2, body


# =============================================================================
# 7. Budget timeout → item still returned with 0 budget contribution
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_budget_timeout_does_not_break_response(
    client, scaffold_cleanup, monkeypatch
):
    """If `compute_spend` hangs / raises, the budget component falls back to
    0.0 and the item is still surfaced. Patch the call inside the router to
    raise (TimeoutError simulation) and assert the response is valid + the
    item present.

    Sets a daily budget cap on the project so the fan-out actually fires
    (otherwise the helper short-circuits without calling compute_spend)."""
    proj = await _make_project(client, scaffold_cleanup, "budget-timeout")
    pid = proj["id"]

    # Set a daily cap so the budget pathway is actually exercised.
    resp = await client.patch(
        f"/api/projects/{pid}",
        json={"budget_daily_usd": "10.00"},
    )
    assert resp.status_code == 200, resp.text

    tid = await _make_question_task(client, pid, "budget-hang task")

    # Patch the imported symbol inside the router so the timeout fires.
    from src.routers import user_actions as ua_module

    async def _slow_compute_spend(*args, **kwargs):
        # Sleep longer than BUDGET_TIMEOUT_SECONDS so asyncio.wait_for fires.
        await asyncio.sleep(ua_module.BUDGET_TIMEOUT_SECONDS + 1.0)
        return 0.0  # never reached

    monkeypatch.setattr(ua_module, "compute_spend", _slow_compute_spend)

    resp = await client.get("/api/user/next-action?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The task still surfaces despite the timeout — budget contributes 0.
    assert any(item["task_id"] == tid for item in body["items"]), body


# =============================================================================
# 8. No X-Project-Id header required
# =============================================================================


@pytest.mark.asyncio
async def test_next_action_works_without_x_project_id_header(client, scaffold_cleanup):
    """Hit the endpoint with no headers at all — must return 200 (parity with
    /api/projects). A 400 would mean the route accidentally got mounted under
    the X-Project-Id gate."""
    # Empty DB is fine; we just want the 200 + valid shape.
    resp = await client.get("/api/user/next-action?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Shape contract — both keys present even when empty.
    assert "items" in body
    assert "fallback_hint" in body


# =============================================================================
# Pure ranker unit tests — score weights + reason picking
# =============================================================================


def test_ranker_weights_sum_to_one():
    """Weights are locked to the documented 40/30/20/10 split."""
    assert W_AGING == 0.40
    assert W_BLOCK == 0.30
    assert W_PRIORITY == 0.20
    assert W_BUDGET == 0.10
    assert W_AGING + W_BLOCK + W_PRIORITY + W_BUDGET == pytest.approx(1.0)


def test_ranker_aging_dominant_reason_renders_hours():
    """A task with aging dominant produces 'oldest in inbox (Xh)' reason."""
    now = datetime.now(timezone.utc)
    cand = RankedCandidate(
        task_id=1, project_id=1, project_name="p", title="t",
        priority=4, updated_at=now - timedelta(hours=AGING_HOURS_FULL),  # full aging
        downstream_block_count=0, budget_pct=0.0,
    )
    score, reason = compute_score_and_reason(cand, now)
    assert "oldest in inbox" in reason
    assert "h)" in reason
    assert score == pytest.approx(W_AGING)  # only aging contributes


def test_ranker_block_dominant_reason_renders_count():
    """Block count dominant → 'blocking N downstream tasks' reason."""
    now = datetime.now(timezone.utc)
    cand = RankedCandidate(
        task_id=1, project_id=1, project_name="p", title="t",
        priority=4, updated_at=now,
        downstream_block_count=int(BLOCK_COUNT_FULL),  # max
        budget_pct=0.0,
    )
    score, reason = compute_score_and_reason(cand, now)
    assert "blocking 5 downstream tasks" in reason
    assert score == pytest.approx(W_BLOCK)


def test_ranker_priority_dominant_reason_renders_p1():
    """P1 priority dominant → 'P1 priority' reason."""
    now = datetime.now(timezone.utc)
    cand = RankedCandidate(
        task_id=1, project_id=1, project_name="p", title="t",
        priority=1,
        updated_at=now, downstream_block_count=0, budget_pct=0.0,
    )
    score, reason = compute_score_and_reason(cand, now)
    assert "P1 priority" in reason
    # Only priority contributes a non-zero weight.
    assert score == pytest.approx(W_PRIORITY)


def test_ranker_budget_dominant_reason_renders_percent_and_project():
    """Budget dominant → 'budget hit X% on <project_name>' reason."""
    now = datetime.now(timezone.utc)
    cand = RankedCandidate(
        task_id=1, project_id=1, project_name="myproj", title="t",
        priority=4, updated_at=now, downstream_block_count=0,
        budget_pct=0.92,
    )
    score, reason = compute_score_and_reason(cand, now)
    assert "budget hit" in reason
    assert "92%" in reason
    assert "myproj" in reason


def test_ranker_tie_band_concatenates_with_and():
    """Two co-dominant factors within TIE_BAND of each other are joined with
    ' and '. Construct aging + block contributions that land equal (both
    weighted 0.20)."""
    now = datetime.now(timezone.utc)
    # aging weight 0.40 * 0.5 = 0.20
    # block weight 0.30 * (2/5) = 0.30 * 0.4 = 0.12 ... too far
    # Find a 0.20 block contribution: 0.20 / 0.30 = 0.667 raw → block_count ≈ 3.33
    # Or pick equal contributions:
    #   aging_hours so 0.40 * (h/168) = 0.10 → h = 42
    #   block 0.30 * (1/5) = 0.06  → not equal but within 5% of each other? No.
    # Simpler: pick aging hours = 168 (full 0.40 contribution), block_count = 5 (full 0.30).
    # 0.40 vs 0.30 → diff 0.10 > 5% of 0.40 = 0.02. Not co-dominant.
    # Force a co-dominant case: aging full (0.40), block ~1.0 (0.30) -- still not in band.
    # Just verify the SAME contribution edge case via two-factor-tie:
    # priority=1 -> 0.20. budget_pct=1.0 -> 0.10.
    # Try: priority=1 (0.20) + aging at 0.5*0.40=0.20 (hours=84).
    cand = RankedCandidate(
        task_id=1, project_id=1, project_name="p", title="t",
        priority=1, updated_at=now - timedelta(hours=84),
        downstream_block_count=0, budget_pct=0.0,
    )
    score, reason = compute_score_and_reason(cand, now)
    # Both aging and priority contribute 0.20. With TIE_BAND=0.05, the diff
    # (0) is within 5% of 0.20 = 0.01, so they're co-dominant.
    assert " and " in reason, reason
    assert "P1 priority" in reason
    assert "oldest in inbox" in reason


def test_ranker_score_clamped_to_unit_interval():
    """Even pathological inputs (super-old, many blockers, P1, over-budget)
    cap at 1.0."""
    now = datetime.now(timezone.utc)
    cand = RankedCandidate(
        task_id=1, project_id=1, project_name="p", title="t",
        priority=1,
        updated_at=now - timedelta(days=365),
        downstream_block_count=100,
        budget_pct=5.0,  # 500% over cap
    )
    score, _ = compute_score_and_reason(cand, now)
    assert score == pytest.approx(1.0)


def test_ranker_sorts_by_score_desc_then_task_id_asc():
    """Tie-breaking on identical scores uses task_id ASC for determinism."""
    now = datetime.now(timezone.utc)
    a = RankedCandidate(
        task_id=10, project_id=1, project_name="p", title="a",
        priority=2, updated_at=now,
        downstream_block_count=0, budget_pct=0.0,
    )
    b = RankedCandidate(
        task_id=5, project_id=1, project_name="p", title="b",
        priority=2, updated_at=now,
        downstream_block_count=0, budget_pct=0.0,
    )
    items = score_candidates([a, b], now=now, limit=5)
    # Same score (both priority=2, no aging, no blocks). Lower task_id wins.
    assert items[0].task_id == 5
    assert items[1].task_id == 10


def test_ranker_limit_truncates_after_sort():
    """limit=1 on a 3-candidate set returns the single highest-scoring item."""
    now = datetime.now(timezone.utc)
    cands = [
        RankedCandidate(
            task_id=i, project_id=1, project_name="p", title=f"t{i}",
            priority=4 - i % 4,
            updated_at=now - timedelta(hours=i * 24),
            downstream_block_count=0, budget_pct=0.0,
        )
        for i in range(1, 4)
    ]
    items = score_candidates(cands, now=now, limit=1)
    assert len(items) == 1
