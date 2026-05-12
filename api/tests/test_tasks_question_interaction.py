"""Kanban #832 — question/decision task interaction API.

Tests for answer append, invalidate, and auto-unblock-on-DONE semantics.

Coverage:
  (a) POSITIVE: POST question task + PATCH new_answer appends to history
  (b) POSITIVE: PATCH second new_answer → history has 2 entries, both is_valid=True
  (c) POSITIVE: PATCH invalidate_last_answer=True + reason → last entry is_valid=False,
                task NOT done
  (d) POSITIVE: mark question task DONE → parent's blocked_by cleared
  (e) POSITIVE: mark question task DONE → parent's halt_reason='Question: ...' cleared
  (f) NEGATIVE: new_answer on a 'work' task → 422
  (g) NEGATIVE: invalidate_last_answer=True without invalidated_reason → 422
  (h) NEGATIVE: invalidate when no valid answer exists → 422
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers
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


async def _patch_task(client, project_id: int, task_id: int, body: dict) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(f"/api/tasks/{task_id}", json=body, headers=headers)
    return resp


_QUESTION_PAYLOAD = {
    "question": "Which database should we use?",
    "options": ["postgres", "mysql"],
    "answer_history": [],
}


# ---------------------------------------------------------------------------
# (a) POSITIVE: first new_answer appends to history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_answer_appends_to_history(client, scaffold_cleanup) -> None:
    """POST question task then PATCH new_answer → history has 1 entry is_valid=True."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-a")
    tid = await _make_task(
        client, pid, "k832-a question",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )

    resp = await _patch_task(client, pid, tid, {"new_answer": "postgres"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    history = body["question_payload"]["answer_history"]
    assert len(history) == 1, history
    assert history[0]["value"] == "postgres"
    assert history[0]["is_valid"] is True
    assert history[0]["answered_by"] == "user"
    assert history[0]["answered_at"] is not None
    # Original question text preserved
    assert body["question_payload"]["question"] == _QUESTION_PAYLOAD["question"]


# ---------------------------------------------------------------------------
# (b) POSITIVE: second new_answer → 2 entries, both is_valid=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_new_answer_appends_without_overwrite(client, scaffold_cleanup) -> None:
    """Two sequential new_answer PATCHes → history has 2 entries, both valid."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-b")
    tid = await _make_task(
        client, pid, "k832-b question",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )

    resp1 = await _patch_task(client, pid, tid, {"new_answer": "first answer"})
    assert resp1.status_code == 200, resp1.text

    resp2 = await _patch_task(
        client, pid, tid, {"new_answer": "second answer", "new_answer_by": "lead"}
    )
    assert resp2.status_code == 200, resp2.text
    history = resp2.json()["question_payload"]["answer_history"]

    assert len(history) == 2, history
    assert history[0]["value"] == "first answer"
    assert history[0]["is_valid"] is True
    assert history[1]["value"] == "second answer"
    assert history[1]["is_valid"] is True
    assert history[1]["answered_by"] == "lead"


# ---------------------------------------------------------------------------
# (c) POSITIVE: invalidate_last_answer flips last valid entry; task NOT done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_last_answer_flips_is_valid(client, scaffold_cleanup) -> None:
    """PATCH invalidate_last_answer=True + reason → last entry is_valid=False;
    task process_status stays unchanged (NOT auto-done)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-c")
    tid = await _make_task(
        client, pid, "k832-c question",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )

    # Append an answer first
    r = await _patch_task(client, pid, tid, {"new_answer": "initial answer"})
    assert r.status_code == 200, r.text

    # Invalidate it
    r2 = await _patch_task(
        client, pid, tid,
        {
            "invalidate_last_answer": True,
            "invalidated_reason": "User changed requirements",
        },
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()

    history = body["question_payload"]["answer_history"]
    assert len(history) == 1, history
    assert history[0]["is_valid"] is False
    assert history[0]["invalidated_reason"] == "User changed requirements"

    # Task must NOT be marked done
    assert body["process_status"] != 5, body


# ---------------------------------------------------------------------------
# (d) POSITIVE: mark question task DONE → parent's blocked_by cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_question_task_clears_blocked_by_on_parent(
    client, scaffold_cleanup
) -> None:
    """When a question task is marked DONE, any task blocked_by it gets cleared."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-d")

    q_tid = await _make_task(
        client, pid, "k832-d question gate",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )
    parent_tid = await _make_task(
        client, pid, "k832-d parent work task",
        blocked_by=q_tid,
    )

    # Verify the parent is blocked
    parent_before = await _get_task(client, pid, parent_tid)
    assert parent_before["blocked_by"] == q_tid

    # Mark the question task DONE
    r = await _patch_task(client, pid, q_tid, {"process_status": 5})
    assert r.status_code == 200, r.text

    # Parent should now be unblocked
    parent_after = await _get_task(client, pid, parent_tid)
    assert parent_after["blocked_by"] is None, parent_after


# ---------------------------------------------------------------------------
# (e) POSITIVE: mark question task DONE → parent's halt_reason='Question: ...' cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_question_task_clears_question_halt_reason(
    client, scaffold_cleanup
) -> None:
    """When a question task is DONE, dependents whose halt_reason starts with
    'Question:' have that reason cleared."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-e")

    q_tid = await _make_task(
        client, pid, "k832-e question gate",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )
    parent_tid = await _make_task(
        client, pid, "k832-e parent halted",
        blocked_by=q_tid,
        halt_reason="Question: awaiting design decision",
    )

    # Verify setup
    parent_before = await _get_task(client, pid, parent_tid)
    assert parent_before["halt_reason"] == "Question: awaiting design decision"

    # Mark question task DONE
    r = await _patch_task(client, pid, q_tid, {"process_status": 5})
    assert r.status_code == 200, r.text

    parent_after = await _get_task(client, pid, parent_tid)
    assert parent_after["blocked_by"] is None, parent_after
    assert parent_after["halt_reason"] is None, parent_after


# ---------------------------------------------------------------------------
# (f) NEGATIVE: new_answer on a 'work' task → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_answer_on_work_task_returns_422(client, scaffold_cleanup) -> None:
    """Sending new_answer for a task with interaction_kind='work' returns 422."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-f")
    tid = await _make_task(client, pid, "k832-f work task")

    resp = await _patch_task(client, pid, tid, {"new_answer": "some answer"})
    assert resp.status_code == 422, resp.text
    assert "new_answer" in resp.text or "question" in resp.text.lower()


# ---------------------------------------------------------------------------
# (g) NEGATIVE: invalidate_last_answer=True without invalidated_reason → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_without_reason_returns_422(client, scaffold_cleanup) -> None:
    """invalidate_last_answer=True without invalidated_reason → 422 at schema layer."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-g")
    tid = await _make_task(
        client, pid, "k832-g question",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )

    resp = await _patch_task(client, pid, tid, {"invalidate_last_answer": True})
    assert resp.status_code == 422, resp.text
    assert "invalidated_reason" in resp.text


# ---------------------------------------------------------------------------
# (h) NEGATIVE: invalidate when no valid answer exists → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalidate_when_no_valid_answer_returns_422(
    client, scaffold_cleanup
) -> None:
    """Calling invalidate when answer_history is empty returns 422."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k832-h")
    tid = await _make_task(
        client, pid, "k832-h question no answers yet",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )

    resp = await _patch_task(
        client, pid, tid,
        {
            "invalidate_last_answer": True,
            "invalidated_reason": "nothing to invalidate",
        },
    )
    assert resp.status_code == 422, resp.text
    assert "no valid answer" in resp.text.lower()
