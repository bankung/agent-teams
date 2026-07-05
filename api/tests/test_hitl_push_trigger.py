"""Kanban #1450 — HITL task-creation and transition smoke tests.

Covers:
  1. POST task with interaction_kind='question' → 201.
  2. POST task with interaction_kind='work' → 201 (non-HITL still succeeds).
  3. PATCH task setting interaction_kind='decision' (transition from 'work') → 200.
  4. PATCH task with interaction_kind already 'question' + changing description → 200.

Telegram/web_push notification coverage lives in test_notify_telegram.py and
test_notifications_optout.py.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Kanban #1796 — prevent on-disk scaffold pollution.
@pytest.fixture(autouse=True)
def _no_scaffold(monkeypatch):
    import src.routers.projects as _proj_router
    import src.services.project_scaffold as _scaffold_svc
    import src.services.notification_router as _notif_router

    monkeypatch.setattr(_proj_router, "scaffold_project_folder", lambda *a, **kw: None)
    monkeypatch.setattr(_scaffold_svc, "scaffold_project_folder", lambda *a, **kw: None)
    monkeypatch.setattr(
        _notif_router,
        "_write_local_fallback",
        lambda *a, **kw: {"ok": False, "detail": "suppressed_in_test", "path": None},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name(prefix: str = "hitl-push") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _create_project(client) -> dict:
    resp = await client.post(
        "/api/projects",
        json={
            "name": _unique_name("hitl-push-proj"),
            "team": "dev",
            "paths": {
                "web": "/tmp/hitl/web",
                "api": "/tmp/hitl/api",
                "db": "/tmp/hitl/db",
            },
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(
    client,
    project_id: int,
    *,
    title: str = "Smoke task",
    interaction_kind: str | None = None,
    question_payload: dict | None = None,
) -> dict:
    body: dict = {"title": title, "project_id": project_id}
    if interaction_kind is not None:
        body["interaction_kind"] = interaction_kind
    if question_payload is not None:
        body["question_payload"] = question_payload
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# (1) POST with interaction_kind='question' → 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_hitl_question_returns_201(client) -> None:
    """POST task with interaction_kind='question' returns 201.

    POSITIVE: creation succeeds and returned task has correct interaction_kind.
    """
    proj = await _create_project(client)
    proj_id = proj["id"]

    task = await _create_task(
        client,
        proj_id,
        title="HITL question task",
        interaction_kind="question",
        question_payload={"question": "Should we proceed?", "answer_type": "free_text"},
    )

    assert task["interaction_kind"] == "question", (
        f"POSITIVE: task must have interaction_kind='question', got {task['interaction_kind']!r}"
    )
    assert task["id"] is not None, "POSITIVE: task must have an id"


# ---------------------------------------------------------------------------
# (2) POST with interaction_kind='work' → 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_work_task_returns_201(client) -> None:
    """POST task with interaction_kind='work' returns 201.

    NEGATIVE contract: non-HITL task creation also succeeds (no regression).
    """
    proj = await _create_project(client)
    proj_id = proj["id"]

    task = await _create_task(client, proj_id, title="Work task", interaction_kind="work")

    assert task["interaction_kind"] == "work", (
        f"NEGATIVE: task must have interaction_kind='work', got {task['interaction_kind']!r}"
    )


# ---------------------------------------------------------------------------
# (3) PATCH setting interaction_kind='decision' (transition) → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_transition_to_decision_returns_200(client) -> None:
    """PATCH that transitions interaction_kind from 'work' -> 'decision' returns 200.

    POSITIVE: status 200 and returned task reflects the new interaction_kind.
    """
    proj = await _create_project(client)
    proj_id = proj["id"]

    task = await _create_task(client, proj_id, title="Transition task", interaction_kind="work")
    task_id = task["id"]

    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={
            "interaction_kind": "decision",
            "question_payload": {
                "question": "Pick one",
                "answer_type": "single_choice",
                "options": [{"id": "a", "label": "Option A"}, {"id": "b", "label": "Option B"}],
            },
        },
    )
    assert resp.status_code == 200, resp.text

    updated = resp.json()
    assert updated["interaction_kind"] == "decision", (
        f"POSITIVE: task must have interaction_kind='decision' after PATCH, "
        f"got {updated['interaction_kind']!r}"
    )


# ---------------------------------------------------------------------------
# (4) PATCH description-only on already-HITL task → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_non_interaction_kind_field_returns_200(client) -> None:
    """PATCH that does NOT include interaction_kind (description-only) returns 200.

    POSITIVE: description-only PATCH succeeds; interaction_kind unchanged.
    """
    proj = await _create_project(client)
    proj_id = proj["id"]

    task = await _create_task(
        client,
        proj_id,
        title="Already HITL task",
        interaction_kind="question",
        question_payload={"question": "What?", "answer_type": "free_text"},
    )
    task_id = task["id"]

    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"description": "Updated description only"},
    )
    assert resp.status_code == 200, resp.text

    updated = resp.json()
    assert updated["interaction_kind"] == "question", (
        "POSITIVE: interaction_kind must remain 'question' after description-only PATCH"
    )
