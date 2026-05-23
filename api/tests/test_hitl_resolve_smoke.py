"""Kanban #1452 — HITL phone-tap /decide endpoint contract smoke tests.

First-pass smokes covering the dual-contract `POST /api/tasks/{id}/decide`
endpoint's HITL-resolution path (HitlResolveRequest body shape).

Comprehensive edge-case + concurrency suite is dev-tester's domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Helpers
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


async def _make_pending_question_task(
    client,
    project_id: int,
    options: list | None = None,
) -> int:
    """Create a question task + drive it into HITL-waiting state
    (process_status=2 IN_PROGRESS + is_pending=true)."""
    headers = {"X-Project-Id": str(project_id)}
    qp: dict = {"question": "Should we proceed?", "answer_history": []}
    if options is not None:
        qp["options"] = options
    # Create as interaction_kind=question. Router server-coerces task_kind=human
    # + run_mode=manual; ps defaults to TODO.
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": project_id,
            "title": "HITL waiting task",
            "interaction_kind": "question",
            "question_payload": qp,
        },
    )
    assert resp.status_code == 201, resp.text
    tid = resp.json()["id"]

    # Drive into HITL-waiting: ps=2 + is_pending=true (cross-state invariant).
    patch_resp = await client.patch(
        f"/api/tasks/{tid}",
        headers=headers,
        json={"process_status": 2, "is_pending": True},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["is_pending"] is True
    assert body["process_status"] == 2
    return tid


# ---------------------------------------------------------------------------
# (1) Happy path — approve with valid selected_option
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_decide_approve_writes_resume_context(
    client, scaffold_cleanup
) -> None:
    """POSITIVE: approve action writes resume_context + clears is_pending.

    Locks AC1+AC2 from #1452 — endpoint records the operator decision +
    flips out of HITL-waiting state.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-a")
    options = [{"id": "opt-yes", "label": "Yes"}, {"id": "opt-no", "label": "No"}]
    tid = await _make_pending_question_task(client, pid, options=options)

    resp = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"action": "approve", "selected_option": "opt-yes"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: resume_context is materialised with all expected keys.
    assert body["task_id"] == tid
    rc = body["resume_context"]
    assert rc is not None, "resume_context must be set"
    assert rc["action"] == "approve", rc
    assert rc["selected_option"] == "opt-yes", rc
    assert rc["decided_via"] == "phone", rc
    assert rc["decided_at"] is not None
    # process_status stays at 2 — Lead resumes the in-flight task.
    assert body["process_status"] == 2, body

    # NEGATIVE: refetch via GET to confirm is_pending was actually cleared
    # (not just reported clear in the response).
    get_resp = await client.get(
        f"/api/tasks/{tid}",
        headers={"X-Project-Id": str(pid)},
    )
    assert get_resp.status_code == 200
    full = get_resp.json()
    assert full["is_pending"] is False, "is_pending must flip to false on resolve"
    assert full["resume_context"]["action"] == "approve"


# ---------------------------------------------------------------------------
# (2) Happy path — custom action with custom_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_decide_custom_writes_custom_text(
    client, scaffold_cleanup
) -> None:
    """POSITIVE: custom action stores custom_text in resume_context.

    Locks the third action branch from the spec — operator types a freeform
    answer instead of picking a preset option.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-c")
    tid = await _make_pending_question_task(client, pid)  # no options

    resp = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"action": "custom", "custom_text": "Use approach C with caveats"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rc = body["resume_context"]
    assert rc["action"] == "custom"
    assert rc["custom_text"] == "Use approach C with caveats"
    # selected_option must NOT leak in on the custom path.
    assert "selected_option" not in rc, rc


# ---------------------------------------------------------------------------
# (3) Idempotency — second tap on already-resolved task → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_decide_already_resolved_returns_409(
    client, scaffold_cleanup
) -> None:
    """POSITIVE of idempotency gate: second /decide on a task whose is_pending
    is already false returns 409. Mirrors the locked spec — re-tap returns the
    409 marker so the FE can re-poll the task state.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-i")
    options = [{"id": "opt-a", "label": "A"}]
    tid = await _make_pending_question_task(client, pid, options=options)

    # First resolve — should succeed.
    first = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"action": "approve", "selected_option": "opt-a"},
    )
    assert first.status_code == 200, first.text

    # Second tap — task is no longer pending → 409.
    second = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"action": "approve", "selected_option": "opt-a"},
    )
    assert second.status_code == 409, second.text
    assert "already resolved" in second.json()["detail"].lower(), second.text


# ---------------------------------------------------------------------------
# (4) Legacy #1007 contract still works on same endpoint (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_endpoint_legacy_chosen_id_still_works(
    client, scaffold_cleanup
) -> None:
    """REGRESSION GUARD: the new HITL routing must NOT break the existing
    Kanban #1007 contract. A body with `chosen_id` (no `action`) on a
    decision task with options must still flip the task to DONE.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-l")
    options = [{"id": "option-x", "label": "X"}]
    headers = {"X-Project-Id": str(pid)}
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": pid,
            "title": "Decision task — legacy",
            "interaction_kind": "decision",
            "question_payload": {
                "question": "Pick X",
                "options": options,
                "answer_history": [],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    tid = resp.json()["id"]

    legacy = await client.post(
        f"/api/tasks/{tid}/decide",
        headers=headers,
        json={"chosen_id": "option-x", "rationale": "Only sane choice"},
    )
    assert legacy.status_code == 200, legacy.text
    body = legacy.json()
    # Legacy path returns the full TaskRead.
    assert body["process_status"] == 5, body  # DONE
    assert body["question_payload"]["chosen_id"] == "option-x"


# ---------------------------------------------------------------------------
# (5) NEGATIVE — invalid action / mismatched selected_option / empty custom_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_decide_bad_body_returns_400(
    client, scaffold_cleanup
) -> None:
    """NEGATIVE: malformed bodies must 400 (not 5xx or silent-pass).

    Covers three Pydantic-validation paths:
      (a) unknown action value → 400 from HitlResolveRequest validation
      (b) custom action with empty custom_text → 400 from model_validator
      (c) approve with selected_option that doesn't match any option → 400
          from router-side selected_option validation
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-n")
    options = [{"id": "opt-x", "label": "X"}]
    tid = await _make_pending_question_task(client, pid, options=options)
    headers = {"X-Project-Id": str(pid)}

    # (a) Unknown action enum value → 400.
    bad_action = await client.post(
        f"/api/tasks/{tid}/decide",
        headers=headers,
        json={"action": "garbage", "selected_option": "opt-x"},
    )
    assert bad_action.status_code == 400, bad_action.text

    # (b) custom action with empty custom_text → 400.
    bad_custom = await client.post(
        f"/api/tasks/{tid}/decide",
        headers=headers,
        json={"action": "custom", "custom_text": "   "},  # whitespace-only
    )
    # min_length=1 fails before the strip check, so 400.
    assert bad_custom.status_code == 400, bad_custom.text

    # (c) selected_option not in options → 400.
    bad_option = await client.post(
        f"/api/tasks/{tid}/decide",
        headers=headers,
        json={"action": "approve", "selected_option": "does-not-exist"},
    )
    assert bad_option.status_code == 400, bad_option.text
    assert "does-not-exist" in bad_option.json()["detail"], bad_option.text


# ---------------------------------------------------------------------------
# (6) NEGATIVE — non-existent task → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_decide_nonexistent_task_returns_404(
    client, scaffold_cleanup
) -> None:
    """NEGATIVE: /decide on a non-existent task id → 404."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-z")
    # Create + delete a task to reach a known-non-existent id, then bump past.
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(pid)},
        json={"project_id": pid, "title": "Throwaway"},
    )
    assert resp.status_code == 201
    real_id = resp.json()["id"]

    nonexistent_id = real_id + 999_999
    miss = await client.post(
        f"/api/tasks/{nonexistent_id}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"action": "approve", "selected_option": "x"},
    )
    assert miss.status_code == 404, miss.text


# ---------------------------------------------------------------------------
# (7) NEGATIVE — non-HITL task (interaction_kind='work') → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_decide_on_work_task_returns_409(
    client, scaffold_cleanup
) -> None:
    """NEGATIVE: /decide on a plain work task (not question/decision) → 409.
    Caller had no business calling /decide on a non-HITL task — same gate
    that catches already-resolved tasks.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "hitl-resolve-w")
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(pid)},
        json={"project_id": pid, "title": "Plain work", "interaction_kind": "work"},
    )
    assert resp.status_code == 201
    tid = resp.json()["id"]

    miss = await client.post(
        f"/api/tasks/{tid}/decide",
        headers={"X-Project-Id": str(pid)},
        json={"action": "custom", "custom_text": "anything"},
    )
    assert miss.status_code == 409, miss.text
    assert "not awaiting HITL" in miss.json()["detail"], miss.text
