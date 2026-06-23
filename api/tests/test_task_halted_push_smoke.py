"""Kanban #1841 — task_halted push notification contract smoke tests.

First-pass smokes for the halt-notification hook added to:
  - PATCH /api/tasks/{id} (halt_reason NULL -> non-NULL transition)
  - GET  /api/tasks/next-autorun (budget hard-halt + hitl_timeout paths
    are server-side mutations; not exercised by PATCH hook tests)

Coverage:
  (a) PATCH that sets halt_reason on a previously-unhalted task calls
      deliver() with event_kind='task_halted'. POSITIVE assertion.
  (b) Opt-in / silent fallback — with no push_subscription having
      task_halted=true, no web-push adapter is called (local-file
      fallback path fires; no exception). NEGATIVE assertion.
  (c) No double-fire — a normal done-flip (no halt_reason change) does
      NOT fire task_halted. NEGATIVE assertion.

Adapter stubbing approach: mirrors test_push_event_hooks_smoke.py —
patch `notification_router._ADAPTERS["web_push"]` so deliver() resolver
runs (DB subscription lookup, kinds_enabled filter, audit row) but the
final pywebpush network call is replaced. autouse _no_scaffold suppresses
on-disk scaffold and fallback-file writes for the test run.

Rigorous suite (hitl_timeout loop multi-task, budget-gate path, project-
scoped filter, concurrent races, delivery retries) is dev-tester's domain.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Scaffold + fallback-file suppression (autouse — mirrors #955.B test file)
# ---------------------------------------------------------------------------


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


async def _create_project(client, name_suffix: str = "") -> dict:
    import uuid
    name = f"smoke-halted-{uuid.uuid4().hex[:8]}{name_suffix}"
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "team": "dev",
            "paths": {"web": "/tmp/sh/web", "api": "/tmp/sh/api", "db": "/tmp/sh/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, title: str = "Halt smoke task") -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={"title": title, "project_id": project_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _subscribe_payload(*, endpoint: str, project_id: int, kinds_enabled: dict) -> dict:
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
            "auth": "tBHItJI5svbpez7KI4CCXg",
        },
        "user_agent": "HaltedSmoke/1.0",
        "project_id": project_id,
        "kinds_enabled": kinds_enabled,
    }


# ---------------------------------------------------------------------------
# Fixture: _ADAPTERS["web_push"] recording stub (mirrors #955.B file)
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_web_push_adapter(monkeypatch):
    """Replace notification_router._ADAPTERS['web_push'] with a recording stub.

    Stub returns ok=True so deliver() marks delivered_ok (no fallback).
    Monkeypatch restores on teardown.
    """
    import os
    import src.services.notification_router as nr
    from src.services import notify_web_push as nwp

    os.environ.setdefault(nwp.VAPID_ENV_PUBLIC, "BSTUB-public-key")
    os.environ.setdefault(nwp.VAPID_ENV_PRIVATE, "STUB-private-key")
    os.environ.setdefault(nwp.VAPID_ENV_SUBJECT, "mailto:smoke@example.test")

    calls: list[dict] = []

    async def _stub(target: dict, payload: dict, **kwargs) -> dict:
        calls.append({"target": dict(target), "payload": dict(payload)})
        return {"ok": True, "detail": "stub_sent"}

    monkeypatch.setitem(nr._ADAPTERS, "web_push", _stub)
    yield calls


# ---------------------------------------------------------------------------
# (a) PATCH sets halt_reason NULL -> non-NULL: deliver fires with task_halted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_halt_reason_fires_task_halted_push(
    client, stub_web_push_adapter
) -> None:
    """A PATCH that transitions halt_reason from NULL to a non-NULL value
    fires deliver() with event_kind='task_halted' for subscriptions that
    have kinds_enabled.task_halted=true.

    Static trace:
      1. create project + task (halt_reason=NULL at creation)
      2. create push_subscription with task_halted=true
      3. PATCH task: halt_reason='waiting for decision #999'
      4. deliver() resolver runs → finds the subscription → calls stub
      5. ASSERT: stub called; chat_id matches sub_id; event_kind (in payload
         routing) matched 'task_halted'; stub payload title contains 'halted'
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-halt-a")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "Halt smoke task A")
    task_id = task["id"]
    assert task["halt_reason"] is None  # pre-condition

    # Subscribe with task_halted=true.
    resp_sub = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/halted-a-{task_id}",
            project_id=proj_id,
            kinds_enabled={"task_halted": True, "task_done": False, "task_failed": False},
        ),
    )
    assert resp_sub.status_code == 201, resp_sub.text
    sub_id = resp_sub.json()["id"]

    # PATCH halt_reason NULL -> non-NULL.
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"halt_reason": "waiting for decision #999"},
    )
    assert resp.status_code == 200, resp.text

    # POSITIVE: at least one stub call targeting our subscription.
    called_chat_ids = {c["target"]["chat_id"] for c in calls}
    assert str(sub_id) in called_chat_ids, (
        "POSITIVE: subscription with task_halted=true must receive the push"
    )
    # Payload title must mention 'halted'.
    titles = [c["payload"].get("title", "") for c in calls]
    assert any("halted" in t.lower() for t in titles), (
        "POSITIVE: push payload title must mention 'halted'"
    )


# ---------------------------------------------------------------------------
# (b) Opt-in / silent — no subscription with task_halted=true → no web-push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_reason_patch_no_subscription_no_push(
    client, stub_web_push_adapter
) -> None:
    """When no push_subscription has task_halted=true for the project, a
    halt_reason PATCH does NOT call the web_push adapter. The local-file
    fallback silently records the event (suppressed in this test by
    _no_scaffold); no exception is raised and the PATCH still returns 200.

    POSITIVE: PATCH returns 200 (push failure never crashes the response).
    NEGATIVE: stub_web_push_adapter records zero calls (no matching sub).
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-halt-b")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "Halt smoke task B")
    task_id = task["id"]

    # Intentionally NO subscription created (or create one with task_halted=false).
    resp_sub = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/halted-b-{task_id}",
            project_id=proj_id,
            kinds_enabled={"task_halted": False, "task_done": True},
        ),
    )
    assert resp_sub.status_code == 201, resp_sub.text

    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"halt_reason": "blocking on review"},
    )
    # POSITIVE: 200 regardless of push outcome.
    assert resp.status_code == 200, resp.text

    # NEGATIVE: no adapter call (no matching subscription).
    assert len(calls) == 0, (
        "NEGATIVE: stub must not be called when no subscription has task_halted=true"
    )


# ---------------------------------------------------------------------------
# (c) No double-fire — done-flip does NOT trigger task_halted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_flip_does_not_fire_task_halted(
    client, stub_web_push_adapter
) -> None:
    """A normal PATCH flipping process_status to DONE (no halt_reason change)
    must NOT fire event_kind='task_halted'.

    POSITIVE: subscribe with both task_done=true AND task_halted=true.
    POSITIVE: PATCH ps=5 fires task_done (stub called).
    NEGATIVE: no call has event_kind routed through 'task_halted' subscription
    path — confirmed by checking payload titles (task_done title says 'done',
    not 'halted'; task_halted branch requires halt_reason in updates).
    """
    calls = stub_web_push_adapter

    proj = await _create_project(client, "-halt-c")
    proj_id = proj["id"]
    task = await _create_task(client, proj_id, "Halt smoke task C")
    task_id = task["id"]
    assert task["halt_reason"] is None  # halt_reason not set

    # Subscribe with task_done=true AND task_halted=true.
    resp_sub = await client.post(
        "/api/push/subscribe",
        json=_subscribe_payload(
            endpoint=f"https://example.test/push/halted-c-{task_id}",
            project_id=proj_id,
            kinds_enabled={"task_done": True, "task_halted": True, "task_failed": False},
        ),
    )
    assert resp_sub.status_code == 201, resp_sub.text

    # PATCH: done-flip only — no halt_reason in body.
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(proj_id)},
        json={"process_status": 5, "status_change_reason": "All done"},
    )
    assert resp.status_code == 200, resp.text

    # POSITIVE: stub called (task_done fires).
    assert len(calls) >= 1, "POSITIVE: task_done push must fire on ps=5 transition"

    # NEGATIVE: no 'halted' title in any call (task_halted branch not triggered).
    halted_titles = [c["payload"].get("title", "") for c in calls if "halted" in c["payload"].get("title", "").lower()]
    assert len(halted_titles) == 0, (
        "NEGATIVE: done-flip must NOT trigger a 'task_halted' push "
        f"(got halted-titled calls: {halted_titles!r})"
    )
