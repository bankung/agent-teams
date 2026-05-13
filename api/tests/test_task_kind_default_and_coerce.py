"""Kanban #858 — task_kind default flip ('human' → 'ai') + server-side coerce
based on interaction_kind.

Migration 0023_tasks_task_kind_default_ai flips the column DEFAULT. The router
extends services/task_kind.coerce_task_kind_for_interaction(...) so that POST /
PATCH calls with interaction_kind IN ('question','decision') silently force
task_kind='human' AND run_mode='manual' (Option A — atomic coerce per the spawn
brief). Reverse PATCH (question/decision → work) does NOT auto-revert task_kind
(edge case #3) — the caller must explicitly PATCH task_kind='ai' if desired.

Coverage:
  1. Default `task_kind='ai'` on a vanilla POST (no interaction_kind / work).
  2. POST with task_kind='ai' AND interaction_kind='question' → coerced 'human'.
  3. POST with task_kind='ai' AND interaction_kind='decision' → coerced 'human'.
  4. PATCH interaction_kind='work' → 'question' on a work task → coerced 'human'.
  5. PATCH interaction_kind='question' → 'work' → task_kind STAYS 'human' (no
     auto-revert). Caller must PATCH task_kind='ai' explicitly.
  6. POST with run_mode='auto_pickup' AND interaction_kind='question' → coerced
     to ('human', 'manual') atomically (Option A).
  7. Migration is non-destructive: existing rows keep their task_kind value.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror of test_tasks_question_interaction.py — kept local to avoid
# import churn and keep this slice self-contained).
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    """Build a dedicated project with no seeded tasks for test isolation."""
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


async def _make_task(client, project_id: int, **fields) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": "k858 task", **fields}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(client, project_id: int, task_id: int, body: dict):
    headers = {"X-Project-Id": str(project_id)}
    return await client.patch(f"/api/tasks/{task_id}", json=body, headers=headers)


_QUESTION_PAYLOAD = {
    "question": "Which DB should we use?",
    "options": ["postgres", "mysql"],
    "answer_history": [],
}

_DECISION_PAYLOAD = {
    "question": "Option A or Option B for this redesign?",
    "options": ["A", "B"],
    "answer_history": [],
}


# ---------------------------------------------------------------------------
# AC1: default task_kind='ai' on a vanilla POST (no fields supplied).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_kind_default_ai_on_create_without_interaction_kind(
    client, scaffold_cleanup
) -> None:
    """POST with no task_kind / no interaction_kind → task_kind='ai' (the new
    schema + DB default). Kanban #858 AC2."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-ac1")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks",
        json={"project_id": pid, "title": "vanilla post"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_kind"] == "ai"
    assert body["interaction_kind"] == "work"
    assert body["run_mode"] == "manual"


# ---------------------------------------------------------------------------
# AC2 / Edge #1: explicit task_kind='ai' + interaction_kind='question' →
# server forces task_kind='human'.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_kind_coerced_human_on_question_interaction(
    client, scaffold_cleanup
) -> None:
    """POST `task_kind='ai'` (explicit) + interaction_kind='question' → server
    silently coerces task_kind='human'. Kanban #858 AC3."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-ac2")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": pid,
            "title": "question with ai requested",
            "task_kind": "ai",
            "interaction_kind": "question",
            "question_payload": _QUESTION_PAYLOAD,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_kind"] == "human", body
    assert body["interaction_kind"] == "question"
    assert body["run_mode"] == "manual"


# ---------------------------------------------------------------------------
# AC3: same with interaction_kind='decision'.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_kind_coerced_human_on_decision_interaction(
    client, scaffold_cleanup
) -> None:
    """POST `task_kind='ai'` + interaction_kind='decision' → coerced 'human'.
    Kanban #858 AC4."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-ac3")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": pid,
            "title": "decision with ai requested",
            "task_kind": "ai",
            "interaction_kind": "decision",
            "question_payload": _DECISION_PAYLOAD,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_kind"] == "human", body
    assert body["interaction_kind"] == "decision"
    assert body["run_mode"] == "manual"


# ---------------------------------------------------------------------------
# AC5 / Edge #2: PATCH a work task to interaction_kind='question' → coerced.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_to_question_coerces_task_kind(
    client, scaffold_cleanup
) -> None:
    """Start with a work task (task_kind='ai'). PATCH interaction_kind='question'
    + question_payload → task_kind flips to 'human' atomically."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-ac5")
    task = await _make_task(
        client, pid,
        title="work task being escalated",
        task_kind="ai",
        run_mode="auto_pickup",
        interaction_kind="work",
    )
    assert task["task_kind"] == "ai"
    assert task["interaction_kind"] == "work"

    resp = await _patch_task(
        client, pid, task["id"],
        {
            "interaction_kind": "question",
            "question_payload": _QUESTION_PAYLOAD,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_kind"] == "human", body
    assert body["interaction_kind"] == "question"
    # Option A: run_mode atomically coerced to 'manual'.
    assert body["run_mode"] == "manual", body


# ---------------------------------------------------------------------------
# Edge #3: reverse PATCH (question → work) does NOT auto-revert task_kind.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_back_to_work_does_not_revert_task_kind(
    client, scaffold_cleanup
) -> None:
    """A task at task_kind='human' + interaction_kind='question'. PATCH
    interaction_kind='work' → task_kind STAYS 'human'. Spawn brief edge case #3
    — conservative semantics; caller may explicitly send task_kind='ai' in the
    same PATCH if desired."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-edge3")
    task = await _make_task(
        client, pid,
        title="question that became work",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )
    assert task["task_kind"] == "human"

    resp = await _patch_task(
        client, pid, task["id"],
        {"interaction_kind": "work"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["interaction_kind"] == "work"
    # No auto-revert — still 'human'.
    assert body["task_kind"] == "human", body


# ---------------------------------------------------------------------------
# Edge #6 / Option A: POST run_mode='auto_pickup' + interaction_kind='question'
# → coerced atomically to ('human', 'manual'). The HUMAN ↔ MANUAL invariant
# never fires because the coerce runs first.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_mode_coerced_manual_when_task_kind_forced_human(
    client, scaffold_cleanup
) -> None:
    """POST `run_mode='auto_pickup'` (incompatible with 'human') AND
    interaction_kind='question' → 201 with (task_kind='human', run_mode='manual')
    — atomic Option A coerce. The HUMAN↔MANUAL assertion does NOT fire because
    the coerce runs first and the post-coerce values satisfy it."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-edge6")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks",
        json={
            "project_id": pid,
            "title": "auto_pickup question",
            "task_kind": "ai",
            "run_mode": "auto_pickup",
            "interaction_kind": "question",
            "question_payload": _QUESTION_PAYLOAD,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_kind"] == "human", body
    assert body["run_mode"] == "manual", body
    assert body["interaction_kind"] == "question"


# ---------------------------------------------------------------------------
# AC6: migration is non-destructive — existing rows preserve task_kind.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_rows_keep_task_kind_after_migration(
    client, scaffold_cleanup
) -> None:
    """Create a task with explicit task_kind='human' BEFORE re-running anything.
    The flip-to-'ai'-default migration changes only server_default — existing
    rows are NOT backfilled. Kanban #858 AC6.

    Meta-test: we can't re-run migrations inside a single pytest session, so
    instead we exercise the invariant by writing an explicit value, reading it
    back, and confirming that the COLUMN DEFAULT (revealed by a no-arg POST) is
    different from the row's stored value. Two distinct mechanisms — the
    migration touched only the former."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k858-ac6")
    headers = {"X-Project-Id": str(pid)}

    # Explicit human row — the path users would have taken pre-migration when
    # the column DEFAULT was 'human'. We're emulating an "existing row".
    explicit_human = await _make_task(
        client, pid,
        title="legacy human row",
        task_kind="human",
        run_mode="manual",
    )
    assert explicit_human["task_kind"] == "human"

    # Re-read to confirm storage; THIS specific row never got rewritten.
    resp = await client.get(
        f"/api/tasks/{explicit_human['id']}", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["task_kind"] == "human"

    # The new column DEFAULT is 'ai' — a vanilla POST on the same project should
    # land at 'ai', proving the two rows now diverge despite sharing a project.
    fresh = await _make_task(client, pid, title="new vanilla row")
    assert fresh["task_kind"] == "ai", fresh
