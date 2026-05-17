"""Tests for the per-template auto-headless confirmation (Kanban #1122 — L15 prevention).

Covers acceptance criteria 1-6 from the spec:

1. Migration adds tasks.template_auto_run_confirmed_at column (smoke check
   that the ORM column maps + accepts NULL backfill).
2. POST /api/tasks with is_template=true + run_mode=auto_headless requires
   template_auto_run_confirmed_at non-null → 422 without, 201 with.
3. New endpoint POST /api/tasks/{id}/confirm-template-auto-run stamps the
   timestamp idempotently.
4. recurrence.fire_template refuses to spawn when run_mode=auto_headless +
   no confirm (returns None without advancing next_fire_at).
5. PATCH that lands at (is_template=true AND run_mode=auto_headless AND
   confirm IS NULL) → 422 — covers cross-state PATCH (existing row has the
   other half).
6. tick_once spawn count is 0 (not 1) on an un-confirmed auto_headless
   template tick — also asserts the warning log fires.

Sibling: test_recurrence_max_children.py (L21 cap gate). Same patterns:
unique project per test, scaffold_cleanup, _make_template helper, direct
fire_template invocation for the scheduler tests.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest


# -----------------------------------------------------------------------------
# Helpers (mirrored from test_recurrence_max_children.py)
# -----------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(
    name: str, *, team: str = "dev", is_active: bool = False
) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": is_active,
        "team": team,
    }


def _future_iso(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _create_project_with_consent(client, name: str) -> int:
    """POST a fresh project AND grant Mode-B consent (so auto_headless on a
    task in that project passes the project-level gate)."""
    create = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    consent = await client.post(
        f"/api/projects/{project_id}/grant-consent",
        json={"confirm_name": name},
    )
    assert consent.status_code == 200, consent.text
    return project_id


# =============================================================================
# AC #1: Pydantic schema accepts the field (smoke for migration / ORM column)
# =============================================================================


def test_task_create_accepts_template_auto_run_confirmed_at_none() -> None:
    """Non-template / non-auto_headless POST: field is allowed to be None
    (and is None by default). Smoke that the field exists on TaskCreate."""
    from src.schemas.task import TaskCreate

    body = TaskCreate(project_id=1, title="t")
    assert body.template_auto_run_confirmed_at is None


def test_task_create_accepts_template_auto_run_confirmed_at_datetime() -> None:
    """Round-trips a datetime through the schema field."""
    from src.schemas.task import TaskCreate

    confirmed_at = datetime.now(timezone.utc)
    body = TaskCreate(
        project_id=1,
        title="t",
        is_template=True,
        run_mode="auto_pickup",
        recurrence_rule="* * * * *",
        next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
        template_auto_run_confirmed_at=confirmed_at,
    )
    assert body.template_auto_run_confirmed_at == confirmed_at


# =============================================================================
# AC #2: POST template + auto_headless without confirm → 422
# =============================================================================


def test_task_create_rejects_auto_headless_template_without_confirm() -> None:
    """Pydantic 422 fires WITHOUT touching the DB — pure schema-level check."""
    from pydantic import ValidationError
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as exc_info:
        TaskCreate(
            project_id=1,
            title="t",
            is_template=True,
            run_mode="auto_headless",
            recurrence_rule="* * * * *",
            next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
            # template_auto_run_confirmed_at omitted → defaults to None → 422
        )
    msg = str(exc_info.value)
    assert "auto_headless" in msg
    assert "template_auto_run_confirmed_at" in msg
    assert "confirm-template-auto-run" in msg


def test_task_create_accepts_auto_headless_template_with_confirm() -> None:
    """Same payload + confirmed_at set → constructs cleanly."""
    from src.schemas.task import TaskCreate

    body = TaskCreate(
        project_id=1,
        title="t",
        is_template=True,
        run_mode="auto_headless",
        recurrence_rule="* * * * *",
        next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
        template_auto_run_confirmed_at=datetime.now(timezone.utc),
    )
    assert body.run_mode == "auto_headless"
    assert body.template_auto_run_confirmed_at is not None


def test_task_create_allows_auto_pickup_template_without_confirm() -> None:
    """The cross-column rule is run_mode-specific — auto_pickup templates do
    NOT need the L15 confirm. Mirrors the L15 spec: scope is auto_headless only.
    """
    from src.schemas.task import TaskCreate

    body = TaskCreate(
        project_id=1,
        title="t",
        is_template=True,
        run_mode="auto_pickup",
        recurrence_rule="* * * * *",
        next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert body.template_auto_run_confirmed_at is None


# =============================================================================
# AC #2 (end-to-end): POST 422 / 201 through HTTP
# =============================================================================


@pytest.mark.asyncio
async def test_post_auto_headless_template_no_confirm_422(
    client, scaffold_cleanup
) -> None:
    """End-to-end POST: no confirm → 422 (Pydantic validation fires)."""
    name = _unique_name("l15-post-422")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "auto-headless tpl",
                "is_template": True,
                "run_mode": "auto_headless",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers={"X-Project-Id": str(project_id)},
        )
        assert resp.status_code == 422, resp.text
        body = resp.text
        assert "template_auto_run_confirmed_at" in body
        assert "auto_headless" in body
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_auto_headless_template_with_confirm_201(
    client, scaffold_cleanup
) -> None:
    """End-to-end POST: explicit confirm timestamp → 201."""
    name = _unique_name("l15-post-201")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    try:
        confirmed_at = datetime.now(timezone.utc).isoformat()
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "auto-headless tpl",
                "is_template": True,
                "run_mode": "auto_headless",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
                "template_auto_run_confirmed_at": confirmed_at,
            },
            headers={"X-Project-Id": str(project_id)},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["run_mode"] == "auto_headless"
        assert body["template_auto_run_confirmed_at"] is not None
        # Cleanup the row.
        await client.delete(
            f"/api/tasks/{body['id']}", headers={"X-Project-Id": str(project_id)}
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# AC #3: confirm-template-auto-run endpoint
# =============================================================================


@pytest.mark.asyncio
async def test_confirm_endpoint_stamps_timestamp(
    client, scaffold_cleanup
) -> None:
    """POST the endpoint on a non-headless template → stamps confirmed_at."""
    name = _unique_name("l15-confirm-ep")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        # Create a fresh template with auto_pickup (confirm not strictly
        # required for auto_pickup, but the endpoint accepts pre-confirmation).
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "pre-confirm tpl",
                "is_template": True,
                "run_mode": "auto_pickup",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        tpl_id = resp.json()["id"]
        assert resp.json()["template_auto_run_confirmed_at"] is None

        confirm = await client.post(
            f"/api/tasks/{tpl_id}/confirm-template-auto-run", headers=headers
        )
        assert confirm.status_code == 200, confirm.text
        body = confirm.json()
        assert body["task_id"] == tpl_id
        assert body["confirmed_at"] is not None

        # Verify it landed in the row.
        get_resp = await client.get(f"/api/tasks/{tpl_id}", headers=headers)
        assert get_resp.json()["template_auto_run_confirmed_at"] is not None

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_confirm_endpoint_idempotent(client, scaffold_cleanup) -> None:
    """Re-POSTing on a confirmed template returns 200 with a new timestamp
    (re-confirm = operator re-reviewed; intentional overwrite)."""
    import asyncio

    name = _unique_name("l15-confirm-idem")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "idem tpl",
                "is_template": True,
                "run_mode": "auto_pickup",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = resp.json()["id"]

        first = await client.post(
            f"/api/tasks/{tpl_id}/confirm-template-auto-run", headers=headers
        )
        first_ts = first.json()["confirmed_at"]

        # Ensure the second call lands at a strictly later timestamp.
        await asyncio.sleep(0.01)

        second = await client.post(
            f"/api/tasks/{tpl_id}/confirm-template-auto-run", headers=headers
        )
        assert second.status_code == 200
        second_ts = second.json()["confirmed_at"]
        # Both calls succeeded; second stamped a fresh "now()" — never less
        # than the first.
        assert second_ts >= first_ts

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_confirm_endpoint_rejects_non_template_422(
    client, scaffold_cleanup
) -> None:
    """A regular (non-template) task → 422 with the meaningful-for-templates message."""
    name = _unique_name("l15-confirm-nontpl")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        # POST a regular task.
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "regular task",
            },
            headers=headers,
        )
        task_id = resp.json()["id"]

        confirm = await client.post(
            f"/api/tasks/{task_id}/confirm-template-auto-run", headers=headers
        )
        assert confirm.status_code == 422, confirm.text
        assert "only meaningful for templates" in confirm.text

        await client.delete(f"/api/tasks/{task_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_confirm_endpoint_404_on_unknown(
    client, scaffold_cleanup
) -> None:
    """A task id that doesn't exist → 404."""
    name = _unique_name("l15-confirm-404")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = await client.post(
            "/api/tasks/99999999/confirm-template-auto-run", headers=headers
        )
        # 400 (cross-project) is acceptable since the row doesn't exist in any
        # project — get_or_404 is the gate. Accept either 404 or 400; the
        # contract is "fail loudly, don't stamp".
        assert resp.status_code in (400, 404), resp.text
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# AC #4: recurrence.fire_template refuses to spawn without confirm
# =============================================================================


@pytest.mark.asyncio
async def test_fire_template_refuses_unconfirmed_auto_headless(
    client, scaffold_cleanup
) -> None:
    """Direct fire_template invocation on an un-confirmed auto_headless
    template returns None without advancing next_fire_at or spawning a child."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("l15-fire-refuse")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        # POST as auto_pickup so the Pydantic gate doesn't fire; then flip the
        # run_mode column directly via the ORM session (legitimate test path —
        # we want to construct the "shouldn't be possible via API but might
        # arise from migration / raw-SQL drift" state for defense-in-depth).
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "refuse tpl",
                "is_template": True,
                "run_mode": "auto_pickup",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = resp.json()["id"]

        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            tpl.run_mode = "auto_headless"
            # template_auto_run_confirmed_at left NULL on purpose.
            await db.commit()
            await db.refresh(tpl)
            original_next_fire = tpl.next_fire_at

        # fire_template should refuse and NOT advance next_fire_at.
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            result = await fire_template(db, tpl)
            assert result is None, "must return None on un-confirmed auto_headless"

        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            assert tpl.next_fire_at == original_next_fire, (
                "next_fire_at must NOT advance on a refused fire — operator "
                "needs to confirm before any progression"
            )
            # Template state unchanged — no halt (L15 is a state-preserving refuse).
            assert tpl.process_status == 1  # TODO
            assert tpl.halt_reason is None

        # Verify no child was spawned.
        children = await client.get("/api/tasks?limit=500", headers=headers)
        spawned = [
            t for t in children.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(spawned) == 0, f"expected no children, got {spawned}"

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_fire_template_succeeds_after_confirm(
    client, scaffold_cleanup
) -> None:
    """After POSTing the confirm endpoint, fire_template spawns normally."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import fire_template

    name = _unique_name("l15-fire-ok")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "ok-after-confirm tpl",
                "is_template": True,
                "run_mode": "auto_pickup",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = resp.json()["id"]

        # Flip run_mode to auto_headless via PATCH AFTER confirming.
        confirm = await client.post(
            f"/api/tasks/{tpl_id}/confirm-template-auto-run", headers=headers
        )
        assert confirm.status_code == 200
        patch = await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"run_mode": "auto_headless"},
            headers=headers,
        )
        assert patch.status_code == 200, patch.text

        # fire_template should spawn (gate passes).
        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            child = await fire_template(db, tpl)
            assert child is not None, "confirmed auto_headless must spawn"
            child_id = child.id

        await client.delete(f"/api/tasks/{child_id}", headers=headers)
        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# AC #5: PATCH cross-state 422 (resolved-final check)
# =============================================================================


@pytest.mark.asyncio
async def test_patch_to_auto_headless_without_confirm_422(
    client, scaffold_cleanup
) -> None:
    """Existing auto_pickup template + PATCH run_mode='auto_headless' WITHOUT
    confirming first → 422 (resolved-final gate in the router)."""
    name = _unique_name("l15-patch-422")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "patch tpl",
                "is_template": True,
                "run_mode": "auto_pickup",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = resp.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{tpl_id}",
            json={"run_mode": "auto_headless"},
            headers=headers,
        )
        assert patch.status_code == 422, patch.text
        assert "template_auto_run_confirmed_at" in patch.text
        assert "confirm-template-auto-run" in patch.text

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# AC #6: scheduler tick on un-confirmed auto_headless → no spawn + warning
# =============================================================================


@pytest.mark.asyncio
async def test_tick_once_no_spawn_for_unconfirmed_auto_headless(
    client, scaffold_cleanup, caplog
) -> None:
    """tick_once on an auto_headless template without confirm: spawned=0 and
    a warning log line containing the L15 keyword fires."""
    from src.db import SessionLocal
    from src.models.task import Task
    from src.services.recurrence import tick_once

    name = _unique_name("l15-tick")
    scaffold_cleanup(name)
    project_id = await _create_project_with_consent(client, name)
    headers = {"X-Project-Id": str(project_id)}
    try:
        # Create as auto_pickup, then bypass the API and flip to auto_headless
        # via ORM (simulating a drift / migration state). Push next_fire_at
        # into the past so tick_once picks it up.
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "tick tpl",
                "is_template": True,
                "run_mode": "auto_pickup",
                "recurrence_rule": "* * * * *",
                "recurrence_timezone": "UTC",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        tpl_id = resp.json()["id"]

        async with SessionLocal() as db:
            tpl = await db.get(Task, tpl_id)
            tpl.run_mode = "auto_headless"
            tpl.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            await db.commit()

        # Capture WARN logs from recurrence.
        caplog.set_level(logging.WARNING, logger="src.services.recurrence")
        result = await tick_once(SessionLocal)
        assert result["spawned"] == 0, (
            f"un-confirmed auto_headless template must NOT spawn, got {result}"
        )

        # Warning log fired with the L15 marker.
        warning_messages = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("L15" in m for m in warning_messages), (
            f"expected an L15 warning, got: {warning_messages}"
        )

        # No child landed.
        listing = await client.get("/api/tasks?limit=500", headers=headers)
        spawned = [
            t for t in listing.json() if t.get("spawned_from_task_id") == tpl_id
        ]
        assert len(spawned) == 0

        await client.delete(f"/api/tasks/{tpl_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")
