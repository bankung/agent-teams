"""Kanban #955.B — push event hooks contract smoke tests.

First-pass smokes covering the happy path of:
  (1) HITL needed → push fires for a subscription with
      kinds_enabled.hitl_needed=true; does NOT fire when that flag is false.
  (2) Task done (ps=5) → push fires; idempotent re-PATCH does NOT re-fire.
  (3) Task failed (ps=6) → push fires.
  (4) PATCH /api/push/subscribe/{id} flips kinds_enabled.task_done from true
      to false → subsequent task-done PATCH does NOT fire push for that sub.
  (5) Budget warn hook fires deliver() with event_kind='budget_warn'.

Adapter stubbing approach: we patch `notification_router._ADAPTERS["web_push"]`
directly — this is the dict bound at module import time that deliver() uses to
dispatch. Patching the module-level name `send_web_push` in `notify_web_push`
alone would NOT intercept calls because _ADAPTERS holds a direct reference.
The stub still runs through the deliver() resolver path (DB subscription
lookup, kinds_enabled filter, audit row logic) — only the final pywebpush
call is replaced.

Rigorous suite (negative paths, edge cases, concurrent races, project-scoped
vs global subscription filtering matrix, etc.) is dev-tester's domain.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


# ---------------------------------------------------------------------------
# Kanban #1796 — prevent on-disk scaffold pollution.
# Two code paths create context/projects/<name>/ on the shared /repo tree
# during tests:
#   1. scaffold_project_folder — called by POST /api/projects for
#      working_path=null projects.
#   2. _write_local_fallback in notification_router — writes a fallback
#      .txt file to context/projects/<name>/notifications/ when all push
#      adapters report ok=False (no_targets_configured / all_adapters_failed).
# Both are no-op patched here. Tests run against agent_teams_test DB;
# on-disk side effects must not land in the shared /repo working tree.
# autouse=True so every test in this module gets the guard without touching
# individual test signatures.
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
# Helper factories
# ---------------------------------------------------------------------------


def _subscribe_payload(
    *,
    endpoint: str,
    project_id: int | None = None,
    kinds_enabled: dict | None = None,
) -> dict:
    body: dict = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
            "auth": "tBHItJI5svbpez7KI4CCXg",
        },
        "user_agent": "HookSmoke/1.0",
    }
    if project_id is not None:
        body["project_id"] = project_id
    if kinds_enabled is not None:
        body["kinds_enabled"] = kinds_enabled
    return body


async def _create_project(client, name_suffix: str = "") -> dict:
    """Create a minimal project, return the JSON dict."""
    import uuid
    name = f"smoke-push-{uuid.uuid4().hex[:8]}{name_suffix}"
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "team": "dev",
            "paths": {"web": "/tmp/smoke/web", "api": "/tmp/smoke/api", "db": "/tmp/smoke/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, title: str = "Smoke task") -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={"title": title, "project_id": project_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakeWebPushResponse:
    """Duck-type a pywebpush success Response (status 201)."""

    status_code = 201
    text = ""


# ---------------------------------------------------------------------------
# Fixture: VAPID env + _ADAPTERS stub
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_web_push_adapter(monkeypatch):
    """Replace notification_router._ADAPTERS['web_push'] with a recording stub.

    Returns (calls, stub) where calls accumulates {target, payload} dicts for
    each invocation. The stub returns ok=True so deliver() marks delivered_ok
    and exits the loop (no fallback fire).
    """
    import src.services.notification_router as nr
    from src.services import notify_web_push as nwp

    # VAPID env so the adapter env-gate passes when NOT using this fixture
    # alongside vapid_env — defensive: set them here unconditionally.
    import os
    os.environ.setdefault(nwp.VAPID_ENV_PUBLIC, "BSTUB-public-key")
    os.environ.setdefault(nwp.VAPID_ENV_PRIVATE, "STUB-private-key")
    os.environ.setdefault(nwp.VAPID_ENV_SUBJECT, "mailto:smoke@example.test")

    calls: list[dict] = []

    async def _stub(target: dict, payload: dict, **kwargs) -> dict:
        calls.append({"target": dict(target), "payload": dict(payload)})
        return {"ok": True, "detail": "stub_sent"}

    # monkeypatch.setitem auto-restores on teardown.
    monkeypatch.setitem(nr._ADAPTERS, "web_push", _stub)
    yield calls


# ---------------------------------------------------------------------------
# (1) HITL needed — fires when sub.kinds_enabled.hitl_needed=true;
#     does NOT fire when the flag is false.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_needed_push_fires_when_enabled(
    client, stub_web_push_adapter
) -> None:
    """HITL-needed push fires for a subscription with hitl_needed=true, and
    does NOT fire for one with hitl_needed=false.

    POSITIVE: stub records a call with sub_a's chat_id.
    NEGATIVE: sub_b's chat_id NOT in any stub call.
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-hitl")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "HITL smoke task")
    task_id = task["id"]

    # Subscription A — hitl_needed=true (should fire)
    resp_a = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/hitl-enabled-{task_id}",
            project_id=proj_id,
            kinds_enabled={
                "hitl_needed": True,
                "task_done": True,
                "task_failed": True,
                "budget_warn": True,
            },
        ),
    )
    assert resp_a.status_code == 201, resp_a.text
    sub_a_id = resp_a.json()["id"]

    # Subscription B — hitl_needed=false (must NOT fire)
    resp_b = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/hitl-disabled-{task_id}",
            project_id=proj_id,
            kinds_enabled={
                "hitl_needed": False,
                "task_done": True,
                "task_failed": True,
                "budget_warn": True,
            },
        ),
    )
    assert resp_b.status_code == 201, resp_b.text
    sub_b_id = resp_b.json()["id"]

    # Patch interaction_kind → 'question' (HITL transition from 'work').
    # question_payload is required when interaction_kind='question'.
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={
            "interaction_kind": "question",
            "question_payload": {
                "question": "What should we do?",
                "answer_type": "free_text",
            },
        },
    )
    assert resp.status_code == 200, resp.text

    # POSITIVE: sub_a's chat_id must appear in stub calls.
    called_chat_ids = {c["target"]["chat_id"] for c in calls}
    assert str(sub_a_id) in called_chat_ids, (
        "POSITIVE: sub with hitl_needed=true must receive the push"
    )

    # NEGATIVE: sub_b's chat_id must NOT appear.
    assert str(sub_b_id) not in called_chat_ids, (
        "NEGATIVE: sub with hitl_needed=false must NOT receive the push"
    )


# ---------------------------------------------------------------------------
# (2) Task done — fires on ps=5 transition; idempotent re-PATCH does NOT re-fire.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_done_push_fires_and_idempotent_repatch_does_not(
    client, stub_web_push_adapter
) -> None:
    """Task-done push fires on process_status → 5 transition.

    POSITIVE: stub called at least once when ps transitions from <5 to 5.
    NEGATIVE (idempotent): re-PATCHing an already-DONE task without changing
    process_status does NOT call the stub again.
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-done")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "Done hook smoke")
    task_id = task["id"]

    # Subscribe with task_done=true
    await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/done-{task_id}",
            project_id=proj_id,
            kinds_enabled={
                "hitl_needed": True,
                "task_done": True,
                "task_failed": True,
                "budget_warn": True,
            },
        ),
    )

    # POSITIVE: flip to DONE
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"process_status": 5, "status_change_reason": "All done"},
    )
    assert resp.status_code == 200, resp.text
    first_call_count = len(calls)
    assert first_call_count >= 1, "POSITIVE: push must fire on done transition"

    # NEGATIVE: re-PATCH the already-DONE task without changing process_status
    resp2 = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"status_change_reason": "Updated reason only"},
    )
    assert resp2.status_code == 200, resp2.text
    second_call_count = len(calls)
    assert second_call_count == first_call_count, (
        "NEGATIVE: idempotent re-PATCH must NOT re-fire push"
    )


# ---------------------------------------------------------------------------
# (3) Task failed — fires on ps=6 transition.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_failed_push_fires(client, stub_web_push_adapter) -> None:
    """Task-failed push fires when process_status transitions to 6.

    POSITIVE: stub called at least once and payload title contains 'failed'.
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-fail")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "Failed hook smoke")
    task_id = task["id"]

    await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/fail-{task_id}",
            project_id=proj_id,
            kinds_enabled={
                "hitl_needed": True,
                "task_done": True,
                "task_failed": True,
                "budget_warn": True,
            },
        ),
    )

    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"process_status": 6, "status_change_reason": "Cancelled by operator"},
    )
    assert resp.status_code == 200, resp.text

    assert len(calls) >= 1, "POSITIVE: push must fire when task transitions to CANCELLED(6)"
    titles = [c["payload"].get("title", "") for c in calls]
    assert any("failed" in t.lower() for t in titles), (
        "POSITIVE: push payload title must mention 'failed'"
    )


# ---------------------------------------------------------------------------
# (4) PATCH /api/push/subscribe/{id} flips kinds_enabled.task_done=false →
#     subsequent task-done PATCH does NOT fire push for that sub.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_subscription_disables_task_done_push(
    client, stub_web_push_adapter
) -> None:
    """After PATCH flips kinds_enabled.task_done=false, a subsequent task-done
    PATCH must NOT attempt push delivery to that subscription.

    POSITIVE of PATCH: the response shows task_done=false.
    NEGATIVE of push: the sub's chat_id does NOT appear in stub calls.
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-patch-disable")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "Disabled sub smoke")
    task_id = task["id"]

    # Subscribe with task_done=true initially.
    resp = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/disable-done-{task_id}",
            project_id=proj_id,
            kinds_enabled={
                "hitl_needed": True,
                "task_done": True,
                "task_failed": True,
                "budget_warn": True,
            },
        ),
    )
    assert resp.status_code == 201, resp.text
    sub_id = resp.json()["id"]

    # POSITIVE of PATCH: flip task_done to false via PATCH endpoint.
    patch_resp = await client.patch(
        f"/api/push/subscribe/{sub_id}",
        json={
            "kinds_enabled": {
                "hitl_needed": True,
                "task_done": False,
                "task_failed": True,
                "budget_warn": True,
            }
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated = patch_resp.json()
    assert updated["kinds_enabled"]["task_done"] is False, (
        "POSITIVE: PATCH must have persisted task_done=false"
    )

    # Now flip the task to DONE — the subscription should NOT be targeted.
    resp2 = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"process_status": 5},
    )
    assert resp2.status_code == 200, resp2.text

    # NEGATIVE: sub_id's chat_id must not appear in any call.
    called_chat_ids = {c["target"]["chat_id"] for c in calls}
    assert str(sub_id) not in called_chat_ids, (
        "NEGATIVE: subscription with task_done=false must NOT be targeted by push"
    )


# ---------------------------------------------------------------------------
# (5) Budget warn hook — deliver() called with event_kind='budget_warn' when
#     threshold is crossed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_warn_push_fires_on_threshold(client, monkeypatch) -> None:
    """Budget threshold alert fires deliver() with event_kind='budget_warn'.

    We stub deliver() at the notification_router module level so we can
    inspect the call arguments without hitting adapters or committing
    audit rows.
    """
    from src.services import budget_gate as bg
    from src.services import notification_router as nr

    proj = await _create_project(client, "-budget")
    proj_id = proj["id"]
    # Seed a task so the anchor query returns a row.
    await _create_task(client, proj_id, "Budget warn anchor task")

    deliver_calls: list[dict] = []

    async def _mock_deliver(**kwargs):
        deliver_calls.append(dict(kwargs))
        return {"task_id": kwargs.get("task_id"), "attempts": []}

    monkeypatch.setattr(nr, "deliver", _mock_deliver)

    # Reset the de-dupe cache so this test run is not affected by others.
    # (Direct module-state mutation — see feedback_test_surface_pollution.)
    bg._ALERT_SENT.clear()

    from src.models.project import Project
    from sqlalchemy import select
    from src.db import SessionLocal

    async with SessionLocal() as db:
        project_row = (
            await db.execute(select(Project).where(Project.id == proj_id))
        ).scalar_one()
        # Force a non-None cap so the threshold computation runs.
        project_row.budget_daily_usd = Decimal("10.00")
        await db.commit()
        await db.refresh(project_row)

        from datetime import datetime, timezone
        from src.services.budget_gate import BudgetCheckResult

        mock_result = BudgetCheckResult(
            allowed=True,
            used_today_usd=Decimal("8.50"),
            cap_daily_usd=Decimal("10.00"),
            projected_usd=Decimal("8.50"),
            pct_used=Decimal("85.0000"),
            reason="ok",
        )

        await bg._maybe_fire_threshold_alert(
            db, project_row, proj_id, mock_result, datetime.now(timezone.utc)
        )

    # POSITIVE: at least one deliver() call with event_kind='budget_warn'.
    budget_warn_calls = [c for c in deliver_calls if c.get("event_kind") == "budget_warn"]
    assert len(budget_warn_calls) >= 1, (
        "POSITIVE: deliver() must be called with event_kind='budget_warn' on threshold cross"
    )
    # Payload shape (D4): title/body/url present.
    call_payload = budget_warn_calls[0]["payload"]
    assert "title" in call_payload and "budget" in call_payload["title"].lower(), (
        "POSITIVE: budget warn push payload must have a 'Budget alert' title"
    )
    assert "url" in call_payload and f"/projects/{proj_id}" in call_payload["url"], (
        "POSITIVE: budget warn push url must reference the project"
    )
