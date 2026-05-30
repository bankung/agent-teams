"""Kanban #1007 — DecisionPayload contract smoke tests.

Covers the happy path of:
  (1) POST /api/tasks/{id}/decide on a valid decision task → 200 + task DONE
  (2) POST /api/tasks/{id}/decide with unknown chosen_id → 422
  (3) GET /api/decisions returns the decided task in the retro feed
  (4) PATCH DONE on a decision task without chosen_id → 422 (AC2 gate)

Kanban #1695 — string-option fix coverage:
  (5) validate_decision_payload: string options + matching chosen_id → no raise
  (6) validate_decision_payload: string options + non-matching chosen_id → ValueError
  (7) validate_decision_payload: string options + missing chosen_id → ValueError
  (8) validate_decision_payload: dict options + matching chosen_id → no raise (regression)

These are first-pass contract-smoke tests only.  The comprehensive edge-case
suite is delegated to dev-tester.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror pattern from test_tasks_question_interaction.py)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"smoke fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_decision_task(client, project_id: int, options: list[dict]) -> int:
    """Create a decision task with the given OptionItem-shaped options."""
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": project_id,
            "title": "Which approach to use?",
            "interaction_kind": "decision",
            "question_payload": {
                "question": "Pick one",
                "options": options,
                "answer_history": [],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


_OPTIONS = [
    {"id": "option-a", "label": "Option A", "description": "First path"},
    {"id": "option-b", "label": "Option B", "description": "Second path"},
]


# ---------------------------------------------------------------------------
# (1) Happy path: decide → 200, task DONE, payload merged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_happy_path(client, scaffold_cleanup) -> None:
    """POST /api/tasks/{id}/decide with valid chosen_id → 200 + DONE + payload merged."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "dec-smoke-a")
    tid = await _make_decision_task(client, pid, _OPTIONS)

    resp = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"chosen_id": "option-a", "rationale": "First is best", "chosen_by": "tester"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # process_status must be DONE (5)
    assert body["process_status"] == 5, body

    # question_payload must carry the decision fields
    qp = body["question_payload"]
    assert qp["chosen_id"] == "option-a"
    assert qp["rationale"] == "First is best"
    assert qp["chosen_by"] == "tester"
    assert qp["chosen_at"] is not None

    # completed_at should be stamped
    assert body["completed_at"] is not None


# ---------------------------------------------------------------------------
# (2) Unknown chosen_id → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_unknown_chosen_id_422(client, scaffold_cleanup) -> None:
    """POST /api/tasks/{id}/decide with chosen_id not in options → 422."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "dec-smoke-b")
    tid = await _make_decision_task(client, pid, _OPTIONS)

    resp = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"chosen_id": "does-not-exist"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "does-not-exist" in detail, detail


# ---------------------------------------------------------------------------
# (3) GET /api/decisions includes the decided task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decisions_retro_feed_includes_decided_task(client, scaffold_cleanup) -> None:
    """After deciding a task, GET /api/decisions returns it in the feed."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "dec-smoke-c")
    tid = await _make_decision_task(client, pid, _OPTIONS)

    # Decide
    decide_resp = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"chosen_id": "option-b", "rationale": "Better fit"},
    )
    assert decide_resp.status_code == 200, decide_resp.text

    # Fetch decisions feed
    feed_resp = await client.get(
        "/api/decisions",
        headers={"X-Project-Id": str(pid)},
    )
    assert feed_resp.status_code == 200, feed_resp.text
    items = feed_resp.json()

    assert len(items) >= 1, items
    match = next((i for i in items if i["task_id"] == tid), None)
    assert match is not None, f"task {tid} not found in decisions feed: {items}"
    assert match["chosen_id"] == "option-b"
    assert match["rationale"] == "Better fit"
    assert match["chosen_at"] is not None
    # options are typed
    assert len(match["options"]) == 2
    assert match["options"][0]["id"] == "option-a"
    assert match["options"][1]["id"] == "option-b"


# ---------------------------------------------------------------------------
# (4) PATCH DONE on decision task without chosen_id → 422 (AC2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_done_without_chosen_id_raises_422(client, scaffold_cleanup) -> None:
    """PATCH process_status=5 on a decision task that has no chosen_id → 422.

    This locks AC2: the validator in task_interaction.validate_decision_payload
    must fire on the PATCH path, not only on /decide.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "dec-smoke-d")
    tid = await _make_decision_task(client, pid, _OPTIONS)

    # Attempt to mark DONE without setting chosen_id — should be rejected.
    resp = await client.patch(
        f"/api/tasks/{tid}",
        headers={"X-Project-Id": str(pid)},
        json={"process_status": 5},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    # Error must mention chosen_id so the operator understands what to fix.
    assert "chosen_id" in detail, detail


# ---------------------------------------------------------------------------
# (5-8) Unit tests for validate_decision_payload — Kanban #1695 string-option fix
# ---------------------------------------------------------------------------

from src.services.task_interaction import validate_decision_payload  # noqa: E402


def test_validate_decision_payload_string_options_matching_chosen_id() -> None:
    """String options + chosen_id that IS in the list → no exception (#1695)."""
    payload = {
        "question": "Pick one",
        "options": ["accept", "retry_with_operator_input", "reject"],
        "chosen_id": "accept",
    }
    # Must not raise.
    validate_decision_payload(payload)


def test_validate_decision_payload_string_options_mismatched_chosen_id() -> None:
    """String options + chosen_id NOT in the list → ValueError (mismatch guard intact)."""
    payload = {
        "question": "Pick one",
        "options": ["accept", "retry_with_operator_input", "reject"],
        "chosen_id": "nope",
    }
    with pytest.raises(ValueError, match="nope"):
        validate_decision_payload(payload)


def test_validate_decision_payload_string_options_missing_chosen_id() -> None:
    """String options + no chosen_id → ValueError (non-null invariant intact)."""
    payload = {
        "question": "Pick one",
        "options": ["accept", "retry_with_operator_input", "reject"],
    }
    with pytest.raises(ValueError, match="chosen_id"):
        validate_decision_payload(payload)


def test_validate_decision_payload_dict_options_regression() -> None:
    """Dict options [{'id': ...}] + matching chosen_id → no exception (regression guard)."""
    payload = {
        "question": "Pick one",
        "options": [
            {"id": "a", "label": "Option A"},
            {"id": "b", "label": "Option B"},
        ],
        "chosen_id": "a",
    }
    # Must not raise.
    validate_decision_payload(payload)
