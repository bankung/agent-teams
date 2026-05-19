"""Kanban #1224 — notification_router service tests.

Covers:
- Priority resolution: task override beats project default; both beat
  local-file fallback.
- Adapter failure: first target ok=False → falls through to next priority.
- Local-file fallback: writes to projects.working_path/notifications/<task>-<ts>.txt
  with header + JSON payload. Fires when (a) no targets configured or (b)
  every adapter failed.
- tasks_history audit: one row per delivery attempt with operation='N'.
- POST /api/notifications/deliver endpoint smoke (404 on missing task,
  X-Project-Id mismatch on wrong header).

Mocks the Telegram adapter via monkeypatch on the _ADAPTERS dispatch dict.
Live curl smoke against the real Telegram API deferred — see test_notify_telegram.py
for mocked-httpx adapter unit tests.

Tests run against `agent_teams_test` per conftest.py rewrite; live-DB
row-count invariant guards against any drift.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(
    name: str,
    *,
    team: str = "dev",
    working_path: str | None = None,
    notification_targets: list[dict] | None = None,
) -> dict:
    body: dict[str, Any] = {
        "name": name,
        "description": f"k1224 fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }
    if working_path is not None:
        body["working_path"] = working_path
    if notification_targets is not None:
        body["notification_targets"] = notification_targets
    return body


def _task_create_payload(
    project_id: int,
    *,
    title: str = "k1224 fixture task",
    notification_targets: list[dict] | None = None,
) -> dict:
    body: dict[str, Any] = {
        "project_id": project_id,
        "title": title,
        "description": "k1224 test task",
        "process_status": 1,
    }
    if notification_targets is not None:
        body["notification_targets"] = notification_targets
    return body


async def _create_project(client, scaffold_cleanup, **kw) -> dict:
    name = scaffold_cleanup(_unique_name("k1224"))
    resp = await client.post(
        "/api/projects", json=_project_create_payload(name, **kw)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, **kw) -> dict:
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=_task_create_payload(project_id, **kw),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Pydantic schema validation
# ---------------------------------------------------------------------------


def test_notification_target_schema_rejects_unknown_kind() -> None:
    from pydantic import ValidationError
    from src.schemas.notification import NotificationTarget

    with pytest.raises(ValidationError):
        NotificationTarget(
            kind="discord",  # not in v1 Literal
            chat_id="123",
            priority=1,
            label="x",
        )


def test_notification_target_schema_rejects_zero_priority() -> None:
    from pydantic import ValidationError
    from src.schemas.notification import NotificationTarget

    with pytest.raises(ValidationError):
        NotificationTarget(
            kind="telegram",
            chat_id="123",
            priority=0,  # ge=1
            label="x",
        )


def test_notification_target_schema_rejects_extra_keys() -> None:
    from pydantic import ValidationError
    from src.schemas.notification import NotificationTarget

    with pytest.raises(ValidationError):
        NotificationTarget(
            kind="telegram",
            chat_id="123",
            priority=1,
            label="x",
            typo_extra="should_fail",  # extra='forbid'
        )


# ---------------------------------------------------------------------------
# Priority resolution: task override > project default > fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_task_override_beats_project_default(
    client, scaffold_cleanup, monkeypatch, tmp_path
) -> None:
    """Task-level notification_targets shadow the project default. Verifies
    the resolution priority in services/notification_router._resolve_targets."""
    from src.services import notification_router

    proj_target = {
        "kind": "telegram",
        "chat_id": "PROJ",
        "priority": 1,
        "label": "proj-default",
    }
    task_target = {
        "kind": "telegram",
        "chat_id": "TASK",
        "priority": 1,
        "label": "task-override",
    }
    proj = await _create_project(
        client,
        scaffold_cleanup,
        working_path=str(tmp_path),
        notification_targets=[proj_target],
    )
    task = await _create_task(
        client, proj["id"], notification_targets=[task_target]
    )

    # Capture which target the adapter is called with.
    captured: list[dict] = []

    async def fake_adapter(target, payload):
        captured.append(dict(target))
        return {"ok": True, "detail": "sent", "telegram_msg_id": 1}

    monkeypatch.setitem(notification_router._ADAPTERS, "telegram", fake_adapter)

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={"task_id": task["id"], "payload": {"x": "y"}, "kind": "telegram"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["ok"] is True
    # Verify the TASK target was used, not the project default.
    assert len(captured) == 1
    assert captured[0]["chat_id"] == "TASK"
    assert captured[0]["label"] == "task-override"


@pytest.mark.asyncio
async def test_deliver_falls_through_to_next_priority_on_failure(
    client, scaffold_cleanup, monkeypatch, tmp_path
) -> None:
    """First adapter ok=False → router tries the next priority target."""
    from src.services import notification_router

    targets = [
        {"kind": "telegram", "chat_id": "FIRST", "priority": 1, "label": "p1"},
        {"kind": "telegram", "chat_id": "SECOND", "priority": 2, "label": "p2"},
    ]
    proj = await _create_project(
        client,
        scaffold_cleanup,
        working_path=str(tmp_path),
        notification_targets=targets,
    )
    task = await _create_task(client, proj["id"])

    calls: list[dict] = []

    async def fake_adapter(target, payload):
        calls.append(dict(target))
        # First target fails; second succeeds.
        if target["chat_id"] == "FIRST":
            return {"ok": False, "detail": "first_failed", "telegram_msg_id": None}
        return {"ok": True, "detail": "sent", "telegram_msg_id": 7}

    monkeypatch.setitem(notification_router._ADAPTERS, "telegram", fake_adapter)

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={"task_id": task["id"], "payload": {"k": "v"}, "kind": "telegram"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Two attempts recorded; first failed, second succeeded.
    assert len(body["attempts"]) == 2
    assert body["attempts"][0]["ok"] is False
    assert body["attempts"][0]["target"]["chat_id"] == "FIRST"
    assert body["attempts"][1]["ok"] is True
    assert body["attempts"][1]["target"]["chat_id"] == "SECOND"
    # Adapter called for both (in priority order).
    assert [c["chat_id"] for c in calls] == ["FIRST", "SECOND"]


# ---------------------------------------------------------------------------
# Local-file fallback (AC4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_no_targets_writes_local_fallback(
    client, scaffold_cleanup, tmp_path
) -> None:
    """When no targets are configured at task OR project level, the router
    writes the payload to <working_path>/notifications/<task>-<ts>.txt."""
    proj = await _create_project(
        client, scaffold_cleanup, working_path=str(tmp_path)
    )
    task = await _create_task(client, proj["id"])  # no notification_targets

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={
            "task_id": task["id"],
            "payload": {"event": "halt", "reason": "test"},
            "kind": "telegram",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["attempts"]) == 1
    attempt = body["attempts"][0]
    assert attempt["target"] is None
    assert attempt["ok"] is True
    assert "wrote_local_fallback" in attempt["detail"]

    # Verify the file landed in <working_path>/notifications/
    notifications_dir = tmp_path / "notifications"
    assert notifications_dir.exists(), notifications_dir
    files = list(notifications_dir.glob(f"{task['id']}-*.txt"))
    assert len(files) == 1, files
    content = files[0].read_text(encoding="utf-8")
    assert f"task_id: {task['id']}" in content
    assert "kind: telegram" in content
    assert "fallback_reason: no_targets_configured" in content
    assert '"event": "halt"' in content


@pytest.mark.asyncio
async def test_deliver_all_adapters_failed_writes_local_fallback(
    client, scaffold_cleanup, monkeypatch, tmp_path
) -> None:
    """Every adapter returned ok=False → router writes local fallback as the
    last attempt with fallback_reason='all_adapters_failed'."""
    from src.services import notification_router

    proj = await _create_project(
        client,
        scaffold_cleanup,
        working_path=str(tmp_path),
        notification_targets=[
            {"kind": "telegram", "chat_id": "X", "priority": 1, "label": "x"},
        ],
    )
    task = await _create_task(client, proj["id"])

    async def always_fail(target, payload):
        return {"ok": False, "detail": "always_fail", "telegram_msg_id": None}

    monkeypatch.setitem(notification_router._ADAPTERS, "telegram", always_fail)

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={"task_id": task["id"], "payload": {"k": "v"}, "kind": "telegram"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Two attempts: adapter (failed) + fallback (success).
    assert len(body["attempts"]) == 2
    assert body["attempts"][0]["ok"] is False
    assert body["attempts"][1]["target"] is None
    assert body["attempts"][1]["ok"] is True
    # Fallback file written.
    files = list((tmp_path / "notifications").glob(f"{task['id']}-*.txt"))
    assert len(files) == 1
    assert "fallback_reason: all_adapters_failed" in files[0].read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# tasks_history audit row (AC7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_appends_tasks_history_notify_rows(
    client, scaffold_cleanup, db_session, monkeypatch, tmp_path
) -> None:
    """Each delivery attempt — including fallback — appends a row to
    tasks_history with operation='N' and snapshot carrying target+ok+detail+priority."""
    from src.models.task import TaskHistory
    from src.services import notification_router

    proj = await _create_project(
        client,
        scaffold_cleanup,
        working_path=str(tmp_path),
        notification_targets=[
            {"kind": "telegram", "chat_id": "OK", "priority": 1, "label": "x"},
        ],
    )
    task = await _create_task(client, proj["id"])

    async def succeed(target, payload):
        return {"ok": True, "detail": "sent", "telegram_msg_id": 42}

    monkeypatch.setitem(notification_router._ADAPTERS, "telegram", succeed)

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={"task_id": task["id"], "payload": {"event": "ping"}, "kind": "telegram"},
    )
    assert resp.status_code == 200, resp.text

    # Look up the history rows for this task with operation='N'.
    rows = (
        await db_session.execute(
            select(TaskHistory).where(
                TaskHistory.task_id == task["id"], TaskHistory.operation == "N"
            )
        )
    ).scalars().all()
    assert len(rows) == 1, [r.snapshot for r in rows]
    snap = rows[0].snapshot
    assert snap["actor"] == "notification_router"
    assert snap["ok"] is True
    assert snap["detail"] == "sent"
    assert snap["attempt_priority"] == 1
    assert snap["target"]["chat_id"] == "OK"
    assert snap["kind"] == "telegram"
    assert "attempted_at" in snap


# ---------------------------------------------------------------------------
# Endpoint negatives: 404 missing task; 400 X-Project-Id mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_404_when_task_missing(
    client, scaffold_cleanup, tmp_path
) -> None:
    proj = await _create_project(
        client, scaffold_cleanup, working_path=str(tmp_path)
    )
    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={"task_id": 999_999_999, "payload": {}, "kind": "telegram"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deliver_400_when_x_project_id_mismatch(
    client, scaffold_cleanup, tmp_path
) -> None:
    proj_a = await _create_project(
        client, scaffold_cleanup, working_path=str(tmp_path / "a")
    )
    proj_b = await _create_project(
        client, scaffold_cleanup, working_path=str(tmp_path / "b")
    )
    task_a = await _create_task(client, proj_a["id"])

    # Send X-Project-Id=B for a task in project A.
    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj_b["id"])},
        json={"task_id": task_a["id"], "payload": {}, "kind": "telegram"},
    )
    assert resp.status_code == 400
    assert "does not belong" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deliver_400_when_x_project_id_missing(
    client, scaffold_cleanup, tmp_path
) -> None:
    proj = await _create_project(
        client, scaffold_cleanup, working_path=str(tmp_path)
    )
    task = await _create_task(client, proj["id"])

    # No X-Project-Id header.
    resp = await client.post(
        "/api/notifications/deliver",
        json={"task_id": task["id"], "payload": {}, "kind": "telegram"},
    )
    assert resp.status_code == 400
    assert "X-Project-Id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Endpoint negative: 422 on malformed notification_targets JSONB body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_targets,label",
    [
        (
            [{"kind": "telegram", "chat_id": "x", "priority": "not-a-number", "label": "x"}],
            "bad_priority_type",
        ),
        (
            [{"kind": "discord", "chat_id": "x", "priority": 1, "label": "x"}],
            "unknown_kind",
        ),
    ],
)
@pytest.mark.asyncio
async def test_post_project_notification_targets_schema_rejected(
    client, scaffold_cleanup, bad_targets, label
) -> None:
    """Pydantic boundary rejects malformed notification_targets at 422."""
    name = scaffold_cleanup(_unique_name("k1224"))
    payload = _project_create_payload(name, notification_targets=bad_targets)
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, f"{label}: {resp.text}"
