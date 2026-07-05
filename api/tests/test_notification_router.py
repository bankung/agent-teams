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


# ---------------------------------------------------------------------------
# Kanban #1285 — fallback path anchored at repo_root (CWD-relative bug fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_path_anchored_at_repo_root_when_working_path_null(
    client, scaffold_cleanup
) -> None:
    """AC2 (Kanban #1285): when project.working_path is null, the fallback file
    MUST land under settings.repo_root/context/projects/<name>/notifications/,
    NOT under /repo/api/context/... (the CWD-relative bug).

    Uses a real Project row with working_path=None (no tmp_path patching) so
    the actual _write_local_fallback code path is exercised end-to-end.

    The test verifies:
    1. The returned path starts with the absolute repo_root (not CWD-relative).
    2. The notification file physically exists at the absolute path.
    3. No 'api/context' segment appears in the path (the old bug surface).
    """
    from src.settings import get_settings
    from pathlib import Path as _Path

    # Create a project with no working_path (the null case from AC2).
    proj = await _create_project(client, scaffold_cleanup)  # working_path omitted → null
    task = await _create_task(client, proj["id"])

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={
            "task_id": task["id"],
            "payload": {"event": "ac2_test"},
            "kind": "telegram",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["attempts"]) == 1
    attempt = body["attempts"][0]
    assert attempt["target"] is None, "should have hit local fallback (no targets)"
    assert attempt["ok"] is True

    written_path = attempt["path"]
    assert written_path is not None

    # AC2 core assertion: path is absolute and anchored at repo_root.
    repo_root = str(get_settings().repo_root)
    assert written_path.startswith(repo_root), (
        f"Fallback wrote to {written_path!r} which does NOT start with "
        f"repo_root={repo_root!r}. CWD-relative bug is still present."
    )

    # The old bug produced paths containing '/api/context/' — ensure it's gone.
    assert "/api/context/" not in written_path, (
        f"Path {written_path!r} contains '/api/context/' — the CWD-relative "
        "bug from Kanban #1285 is still present."
    )

    # Verify the file physically exists (not just a string claim).
    assert _Path(written_path).exists(), (
        f"Fallback file {written_path!r} does not exist on disk."
    )


@pytest.mark.asyncio
async def test_fallback_path_uses_repo_root_for_windows_working_path(
    client, scaffold_cleanup
) -> None:
    """Bonus AC (Kanban #1285): when project.working_path is a Windows-absolute
    path (e.g. C:\\Users\\...)), the Linux container must NOT create a nested
    directory tree by resolving it as a relative path.

    The router must detect the non-absolute-on-Linux path and fall back to
    repo_root/context/projects/<name>/notifications/ with a WARNING log.

    Verifies:
    1. ok=True (delivery still completes via fallback).
    2. Returned path is anchored at repo_root, not at some CWD-relative location.
    3. No directory starting with 'C:' or similar Windows drive prefix is created
       relative to CWD.
    """
    import logging
    from pathlib import Path as _Path
    from src.settings import get_settings

    # Use a real Windows-style absolute path as working_path.
    windows_path = r"C:\Users\banku\Documents\Personal\Projects\WebApp\newsanalyzer"
    proj = await _create_project(client, scaffold_cleanup, working_path=windows_path)
    task = await _create_task(client, proj["id"])

    with pytest.raises(Exception) if False else __import__("contextlib").nullcontext():
        resp = await client.post(
            "/api/notifications/deliver",
            headers={"X-Project-Id": str(proj["id"])},
            json={
                "task_id": task["id"],
                "payload": {"event": "windows_path_test"},
                "kind": "telegram",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    attempt = body["attempts"][0]
    assert attempt["ok"] is True, f"Fallback failed: {attempt['detail']}"

    written_path = attempt["path"]
    assert written_path is not None

    repo_root = str(get_settings().repo_root)

    # NEGATIVE assertion: the path must NOT be a relative Windows-style path
    # resolved under CWD (/repo/api). This is the bug surface.
    assert not written_path.startswith("/repo/api/C"), (
        f"CWD-relative Windows path bug still present: {written_path!r}"
    )
    assert not written_path.startswith("C:"), (
        f"Windows path written verbatim on Linux: {written_path!r}"
    )

    # POSITIVE assertion: must be anchored at repo_root.
    assert written_path.startswith(repo_root), (
        f"Fallback wrote to {written_path!r}, not under repo_root={repo_root!r}"
    )

    # Physical existence check.
    assert _Path(written_path).exists(), (
        f"Fallback file {written_path!r} does not exist on disk."
    )


# ---------------------------------------------------------------------------
# Kanban #1937 — event_kind forwarding through POST /api/notifications/deliver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_event_kind_forwarded_to_push_subscription_resolver(
    client, scaffold_cleanup, monkeypatch, tmp_path
) -> None:
    """AC4 (Kanban #1937): POST /api/notifications/deliver accepts event_kind and
    forwards it to notification_router.deliver() so the push_subscription branch
    fires.

    Verifies (POSITIVE path):
    - When kind='web_push' and event_kind='session_waiting', the endpoint returns
      200 and the push-subscription resolver is invoked (not skipped).
    - The deliver() call receives the event_kind from the request body.

    Uses monkeypatch on _resolve_push_subscription_targets to capture invocations
    without needing a live push_subscription row. Any call with a matching
    event_kind constitutes a pass.
    """
    from src.services import notification_router

    proj = await _create_project(client, scaffold_cleanup, working_path=str(tmp_path))
    task = await _create_task(client, proj["id"])

    resolver_calls: list[str] = []

    async def fake_push_resolver(session, project_id, event_kind):
        resolver_calls.append(event_kind)
        # Return an empty list so the fallback path fires (keeps the test
        # self-contained — no real web_push adapter needed).
        return []

    monkeypatch.setattr(
        notification_router,
        "_resolve_push_subscription_targets",
        fake_push_resolver,
    )

    resp = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={
            "task_id": task["id"],
            "payload": {"message": "Lead is waiting for your input"},
            "kind": "web_push",
            "event_kind": "session_waiting",
        },
    )
    assert resp.status_code == 200, resp.text

    # POSITIVE: resolver was called with the forwarded event_kind.
    assert resolver_calls == ["session_waiting"], (
        f"Expected _resolve_push_subscription_targets called with 'session_waiting', "
        f"but got: {resolver_calls}"
    )

    # NEGATIVE: without event_kind, the resolver is NOT called (backwards-compat).
    resolver_calls.clear()
    resp2 = await client.post(
        "/api/notifications/deliver",
        headers={"X-Project-Id": str(proj["id"])},
        json={
            "task_id": task["id"],
            "payload": {"message": "no event_kind"},
            "kind": "web_push",
        },
    )
    assert resp2.status_code == 200, resp2.text
    assert resolver_calls == [], (
        "Resolver must NOT be called when event_kind is omitted (backwards-compat)."
    )


# ---------------------------------------------------------------------------
# Kanban #2657 — fallback_on_empty=False suppresses the empty-target fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_fallback_on_empty_false_suppresses_file_and_history_row(
    client, scaffold_cleanup, db_session, tmp_path
) -> None:
    """fallback_on_empty=False + no telegram target → NO TaskHistory NOTIFY row
    and NO fallback .txt file written (Kanban #2657, AC-a).

    Calls deliver() directly (not via the HTTP endpoint) so the flag can be
    forwarded without needing an API body field.
    """
    from sqlalchemy import select as sa_select
    from src.models.task import TaskHistory
    from src.services.notification_router import deliver, NOTIFY_OP_CODE

    # Project + task with no notification_targets anywhere.
    proj = await _create_project(client, scaffold_cleanup, working_path=str(tmp_path))
    task = await _create_task(client, proj["id"])

    # Snapshot history row count before the call.
    before_rows = (
        await db_session.execute(
            sa_select(TaskHistory).where(
                TaskHistory.task_id == task["id"],
                TaskHistory.operation == NOTIFY_OP_CODE,
            )
        )
    ).scalars().all()
    before_count = len(before_rows)

    await deliver(
        task_id=task["id"],
        payload={"event": "test_2657"},
        kind="telegram",
        session=db_session,
        fallback_on_empty=False,
    )

    # NEGATIVE: no new NOTIFY history row written.
    after_rows = (
        await db_session.execute(
            sa_select(TaskHistory).where(
                TaskHistory.task_id == task["id"],
                TaskHistory.operation == NOTIFY_OP_CODE,
            )
        )
    ).scalars().all()
    assert len(after_rows) == before_count, (
        f"Expected no new NOTIFY rows, got {len(after_rows) - before_count} new row(s): "
        f"{[r.snapshot for r in after_rows[before_count:]]}"
    )

    # NEGATIVE: no fallback file written.
    notifications_dir = tmp_path / "notifications"
    files = list(notifications_dir.glob(f"{task['id']}-*.txt")) if notifications_dir.exists() else []
    assert files == [], f"Expected no fallback file, found: {files}"


@pytest.mark.asyncio
async def test_deliver_default_true_still_writes_fallback_when_no_targets(
    client, scaffold_cleanup, db_session, tmp_path
) -> None:
    """Regression: default deliver() (fallback_on_empty omitted/True) with no
    resolvable target STILL writes the fallback file AND a NOTIFY history row
    (Kanban #2657, AC-b — default-True path byte-unchanged).
    """
    from sqlalchemy import select as sa_select
    from src.models.task import TaskHistory
    from src.services.notification_router import deliver, NOTIFY_OP_CODE

    proj = await _create_project(client, scaffold_cleanup, working_path=str(tmp_path))
    task = await _create_task(client, proj["id"])

    result = await deliver(
        task_id=task["id"],
        payload={"event": "regression_2657"},
        kind="telegram",
        session=db_session,
        # fallback_on_empty intentionally omitted — tests the default=True path.
    )

    # POSITIVE: attempt list has the fallback entry.
    attempts = result["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["target"] is None
    assert attempts[0]["ok"] is True
    assert "wrote_local_fallback" in attempts[0]["detail"]

    # POSITIVE: NOTIFY history row was written.
    rows = (
        await db_session.execute(
            sa_select(TaskHistory).where(
                TaskHistory.task_id == task["id"],
                TaskHistory.operation == NOTIFY_OP_CODE,
            )
        )
    ).scalars().all()
    assert len(rows) == 1, f"Expected 1 NOTIFY row, got {len(rows)}"
    assert rows[0].snapshot["fallback_reason"] == "no_targets_configured"

    # POSITIVE: fallback file exists on disk.
    files = list((tmp_path / "notifications").glob(f"{task['id']}-*.txt"))
    assert len(files) == 1, f"Expected 1 fallback file, found: {files}"
