"""Kanban #832 — question/decision task interaction API.
Kanban #987 — PATCH answer validation gate (Q3=A) + invalid-attempt audit (Q6=A).

Tests for answer append, invalidate, auto-unblock-on-DONE, and strict
validation against question_payload.options for decision tasks.

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
  (i) #987 POSITIVE: decision task valid answer in options → 200 + is_valid=True
  (j) #987 NEGATIVE: decision task invalid answer → 422 + appended is_valid=False,
                     task stays BLOCKED
  (k) #987 POSITIVE: question task free-text accepted (no options enforcement)
  (l) #987 NEGATIVE: whitespace-only answer → 422
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


# ---------------------------------------------------------------------------
# #987 fixtures
# ---------------------------------------------------------------------------

_DECISION_PAYLOAD = {
    "question": "Deploy target?",
    "options": ["staging", "prod"],
    "answer_history": [],
}

_FREETEXT_PAYLOAD = {
    "question": "What changed in the last release?",
    "options": None,
    "answer_history": [],
}


# ---------------------------------------------------------------------------
# (i) #987 POSITIVE: decision task valid option accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_task_valid_answer_in_options_returns_200_and_is_valid_true(
    client, scaffold_cleanup
) -> None:
    """Decision task with options=['staging','prod']; PATCH new_answer='prod'
    → 200; answer_history has the entry with is_valid=True."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k987-i")
    tid = await _make_task(
        client, pid, "k987-i decision",
        interaction_kind="decision",
        question_payload=_DECISION_PAYLOAD,
    )

    resp = await _patch_task(client, pid, tid, {"new_answer": "prod"})
    assert resp.status_code == 200, resp.text
    history = resp.json()["question_payload"]["answer_history"]
    assert len(history) == 1, history
    assert history[0]["value"] == "prod"
    assert history[0]["is_valid"] is True
    assert history[0]["invalidated_reason"] is None


# ---------------------------------------------------------------------------
# (j) #987 NEGATIVE: decision task invalid option rejected + audit trail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_task_invalid_answer_returns_422_and_appends_is_valid_false(
    client, scaffold_cleanup
) -> None:
    """Decision task with options=['staging','prod']; PATCH new_answer='banana'
    → 422 with detail starting 'invalid_answer:'; subsequent GET shows
    answer_history grew with is_valid=False + invalidated_reason set; task
    still BLOCKED (process_status unchanged at TODO=1)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k987-j")
    tid = await _make_task(
        client, pid, "k987-j decision",
        interaction_kind="decision",
        question_payload=_DECISION_PAYLOAD,
    )

    before = await _get_task(client, pid, tid)
    ps_before = before["process_status"]

    resp = await _patch_task(client, pid, tid, {"new_answer": "banana"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"].startswith("invalid_answer:"), resp.text

    # GET shows the invalid attempt was recorded
    after = await _get_task(client, pid, tid)
    history = after["question_payload"]["answer_history"]
    assert len(history) == 1, history
    assert history[0]["value"] == "banana"
    assert history[0]["is_valid"] is False
    assert history[0]["invalidated_reason"] is not None
    assert "banana" in history[0]["invalidated_reason"]
    # Task stays BLOCKED — process_status unchanged
    assert after["process_status"] == ps_before, after


# ---------------------------------------------------------------------------
# (k) #987 POSITIVE: question task free-text accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_question_task_free_text_accepted(client, scaffold_cleanup) -> None:
    """Question task (options=None) accepts any non-empty string."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k987-k")
    tid = await _make_task(
        client, pid, "k987-k free text",
        interaction_kind="question",
        question_payload=_FREETEXT_PAYLOAD,
    )

    resp = await _patch_task(
        client, pid, tid, {"new_answer": "any string the user wants"}
    )
    assert resp.status_code == 200, resp.text
    history = resp.json()["question_payload"]["answer_history"]
    assert len(history) == 1
    assert history[0]["value"] == "any string the user wants"
    assert history[0]["is_valid"] is True


# ---------------------------------------------------------------------------
# (l) #987 NEGATIVE: whitespace-only answer rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_answer_returns_422(client, scaffold_cleanup) -> None:
    """Whitespace-only new_answer → 422 detail starts 'invalid_answer:'."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k987-l")
    tid = await _make_task(
        client, pid, "k987-l decision",
        interaction_kind="decision",
        question_payload=_DECISION_PAYLOAD,
    )

    resp = await _patch_task(client, pid, tid, {"new_answer": "   "})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"].startswith("invalid_answer:"), resp.text


# ---------------------------------------------------------------------------
# #2427 regression: independent halt_reason survives question-DONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_unblock_clears_only_question_halt_reason(
    client, scaffold_cleanup
) -> None:
    """auto_unblock_dependents clears blocked_by for ALL dependents but clears
    halt_reason ONLY for dependents halted with 'Question:...' prefix.
    Independent halts (e.g. 'budget_exceeded:monthly') must survive. (#2427)

    Setup:
      Q   -- question task (interaction_kind='question')
      A   -- dependent; halt_reason='Question: awaiting X'; blocked_by=Q
      B   -- dependent; halt_reason='budget_exceeded:monthly'; blocked_by=Q

    Mark Q DONE (process_status=5). auto_unblock_dependents runs.

    Expected state:
      A -> blocked_by=NULL, halt_reason=NULL  (resumes -- HITL halt cleared)
      B -> blocked_by=NULL, halt_reason='budget_exceeded:monthly'
                                              (stays halted for independent reason)

    Static trace:
      _make_task Q with interaction_kind='question'          -> q_id
      _make_task A with blocked_by=q_id, halt_reason='Question: awaiting X' -> a_id
      _make_task B with blocked_by=q_id, halt_reason='budget_exceeded:monthly' -> b_id
      PATCH Q to process_status=5 -> 200
      GET A -> blocked_by=None, halt_reason=None
      GET B -> blocked_by=None, halt_reason='budget_exceeded:monthly'
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2427")

    q_id = await _make_task(
        client, pid, "k2427 question gate",
        interaction_kind="question",
        question_payload=_QUESTION_PAYLOAD,
    )
    a_id = await _make_task(
        client, pid, "k2427 dependent A -- question halt",
        blocked_by=q_id,
        halt_reason="Question: awaiting design decision",
    )
    b_id = await _make_task(
        client, pid, "k2427 dependent B -- independent halt",
        blocked_by=q_id,
        halt_reason="budget_exceeded:monthly",
    )

    # Verify setup: both dependents are blocked by Q.
    a_before = await _get_task(client, pid, a_id)
    b_before = await _get_task(client, pid, b_id)
    assert a_before["blocked_by"] == q_id, "setup: A must be blocked by Q"
    assert a_before["halt_reason"] == "Question: awaiting design decision"
    assert b_before["blocked_by"] == q_id, "setup: B must be blocked by Q"
    assert b_before["halt_reason"] == "budget_exceeded:monthly"

    # Mark Q DONE -- triggers auto_unblock_dependents.
    r = await _patch_task(client, pid, q_id, {"process_status": 5})
    assert r.status_code == 200, r.text

    # --- Assert A: HITL halt fully cleared ---
    a_after = await _get_task(client, pid, a_id)
    assert a_after["blocked_by"] is None, (
        "A: blocked_by must be cleared after Q is DONE"
    )
    assert a_after["halt_reason"] is None, (
        "A: halt_reason 'Question:...' must be cleared when Q is DONE"
    )

    # --- Assert B: blocked_by cleared, but independent halt_reason survives ---
    b_after = await _get_task(client, pid, b_id)
    assert b_after["blocked_by"] is None, (
        "B: blocked_by must be cleared after Q is DONE (blocker resolved)"
    )
    assert b_after["halt_reason"] == "budget_exceeded:monthly", (
        "B: independent halt_reason must NOT be cleared by question-DONE -- "
        "it persists until the budget condition resets"
    )
