"""Tests for tasks.task_kind + recurrence template fields — Kanban #706.

V3+ scope-lock T1 (2026-05-10). Foundation slice for the 4-feature scope-lock.
Covers six surfaces (mirrors test_run_mode_consent.py structure):

1. Schema-level (Pydantic): TaskCreate / TaskUpdate field acceptance, default
   values, cron / TZ validators, template-completeness model_validator,
   spawned_from_task_id rejection on PATCH, lockstep guard for TaskKindLiteral.
2. POST /api/tasks happy-path round-trips for the new fields.
3. POST /api/tasks task_kind ↔ run_mode cross-table validator.
4. PATCH /api/tasks/{id} resolved-final cross-table validator + spawned_from
   rejection.
5. Behavioral backfill — seeded rows have task_kind='human' / is_template=false
   / recurrence_timezone='UTC' / NULLs on the rest.
6. Source-text-locks for the new 400 detail strings + the lockstep RuntimeError
   guard.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.constants import TaskKind, TaskRunMode


# -----------------------------------------------------------------------------
# Helpers (mirror of test_run_mode_consent.py — kept local to avoid import churn)
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


def _future_iso() -> str:
    """An ISO-8601 datetime ~1 hour in the future, with TZ — fits next_fire_at."""
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


# =============================================================================
# 1. Schema-level (Pydantic) tests
# =============================================================================


def test_task_create_task_kind_default_is_human() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(project_id=1, title="x")
    assert task.task_kind == TaskKind.HUMAN


def test_task_create_task_kind_accepts_each_valid_value() -> None:
    from src.schemas.task import TaskCreate

    for kind in TaskKind.ALL:
        # ai must pair with non-default run_mode; for human, default manual
        # is fine. Use auto_pickup for ai to keep this purely a schema test
        # (no router cross-validator).
        run_mode = TaskRunMode.AUTO_PICKUP if kind == TaskKind.AI else TaskRunMode.MANUAL
        task = TaskCreate(project_id=1, title="x", task_kind=kind, run_mode=run_mode)
        assert task.task_kind == kind


def test_task_create_task_kind_rejects_unknown() -> None:
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", task_kind="robot")
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "ai" in msg and "human" in msg


def test_task_create_recurrence_defaults() -> None:
    """Defaults: is_template=false, recurrence_rule=None, timezone='UTC',
    next_fire_at=None, spawned_from_task_id=None."""
    from src.schemas.task import TaskCreate

    task = TaskCreate(project_id=1, title="x")
    assert task.is_template is False
    assert task.recurrence_rule is None
    assert task.recurrence_timezone == "UTC"
    assert task.next_fire_at is None
    assert task.spawned_from_task_id is None


def test_task_create_invalid_cron_rejected_at_422() -> None:
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(
            project_id=1,
            title="x",
            recurrence_rule="not a cron",
        )
    err = ei.value.errors()
    assert any("recurrence_rule" in e["loc"] for e in err)


def test_task_create_valid_cron_accepted() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(
        project_id=1,
        title="x",
        recurrence_rule="0 9 * * MON",
    )
    assert task.recurrence_rule == "0 9 * * MON"


def test_task_create_invalid_timezone_rejected_at_422() -> None:
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(
            project_id=1,
            title="x",
            recurrence_timezone="Mars/Olympus",
        )
    err = ei.value.errors()
    assert any("recurrence_timezone" in e["loc"] for e in err)


def test_task_create_valid_iana_timezone_accepted() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(
        project_id=1,
        title="x",
        recurrence_timezone="Asia/Bangkok",
    )
    assert task.recurrence_timezone == "Asia/Bangkok"


def test_task_create_template_without_rule_or_fire_at_rejected() -> None:
    """is_template=true requires both recurrence_rule + next_fire_at."""
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(project_id=1, title="x", is_template=True)
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "recurrence_rule" in msg and "next_fire_at" in msg


def test_task_create_template_with_rule_only_rejected() -> None:
    """is_template=true + recurrence_rule but no next_fire_at → 422."""
    from src.schemas.task import TaskCreate

    with pytest.raises(ValidationError) as ei:
        TaskCreate(
            project_id=1,
            title="x",
            is_template=True,
            recurrence_rule="0 9 * * MON",
        )
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "next_fire_at" in msg


def test_task_create_template_complete_accepted() -> None:
    from src.schemas.task import TaskCreate

    task = TaskCreate(
        project_id=1,
        title="weekly review",
        is_template=True,
        recurrence_rule="0 9 * * MON",
        next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert task.is_template is True


def test_task_update_rejects_spawned_from_task_id_present() -> None:
    """V1 forbids re-parenting lineage — explicit value or null both rejected."""
    from src.schemas.task import TaskUpdate

    with pytest.raises(ValidationError) as ei:
        TaskUpdate(spawned_from_task_id=42)
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "spawned_from_task_id" in msg


def test_task_update_rejects_spawned_from_task_id_explicit_null() -> None:
    """Explicit null is treated identically to a non-null value (mirror of
    parent_task_id rejection)."""
    from src.schemas.task import TaskUpdate

    with pytest.raises(ValidationError) as ei:
        TaskUpdate(spawned_from_task_id=None)
    msg = " | ".join(e["msg"] for e in ei.value.errors())
    assert "spawned_from_task_id" in msg


def test_task_update_omitting_spawned_from_task_id_works() -> None:
    """Just confirming PATCH semantics — omitted key is fine."""
    from src.schemas.task import TaskUpdate

    upd = TaskUpdate(title="renamed")
    assert "spawned_from_task_id" not in upd.model_fields_set


def test_task_kind_literal_matches_constants_all() -> None:
    """Lockstep guard's positive case — sets equal at import time."""
    from src.schemas.task import TaskKindLiteral

    assert set(TaskKindLiteral.__args__) == set(TaskKind.ALL)  # type: ignore[attr-defined]


def test_task_kind_literal_drift_raises_at_import(monkeypatch) -> None:
    """Force drift between TaskKind.ALL and the Literal — guard at the bottom
    of schemas/task.py must raise RuntimeError. Mirrors the TaskRunMode drift
    test."""
    import src.constants as constants_mod
    import src.schemas.task as task_schema_mod

    monkeypatch.setattr(constants_mod.TaskKind, "ALL", ("human", "wrong_extra"))
    with pytest.raises(RuntimeError, match="drifted"):
        importlib.reload(task_schema_mod)
    # Restore module state so other tests aren't poisoned.
    monkeypatch.undo()
    importlib.reload(task_schema_mod)


# =============================================================================
# 2. POST /api/tasks happy-path round-trips
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_ai_auto_pickup_201(client, scaffold_cleanup) -> None:
    """task_kind='ai' + run_mode='auto_pickup' (no consent needed) → 201."""
    name = _unique_name("ai-pickup")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "ai pickup task",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_kind"] == "ai"
        assert body["run_mode"] == "auto_pickup"

        # Round-trip via GET
        got = await client.get(f"/api/tasks/{body['id']}", headers=headers)
        assert got.json()["task_kind"] == "ai"

        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_ai_auto_headless_after_consent_201(
    client, scaffold_cleanup
) -> None:
    """task_kind='ai' + run_mode='auto_headless' on consented project → 201."""
    name = _unique_name("ai-headless")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "ai headless task",
                "task_kind": "ai",
                "run_mode": "auto_headless",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_kind"] == "ai"
        assert body["run_mode"] == "auto_headless"
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_human_manual_default_201(client, scaffold_cleanup) -> None:
    """Default body (no task_kind / run_mode) → human + manual → 201."""
    name = _unique_name("human-default")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={"project_id": project_id, "title": "default human task"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["task_kind"] == "human"
        assert body["run_mode"] == "manual"
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_template_round_trips(client, scaffold_cleanup) -> None:
    """is_template=true + cron + IANA TZ + next_fire_at → 201; full round-trip."""
    name = _unique_name("template-rt")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "weekly review template",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "recurrence_timezone": "Asia/Bangkok",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["is_template"] is True
        assert body["recurrence_rule"] == "0 9 * * MON"
        assert body["recurrence_timezone"] == "Asia/Bangkok"
        assert body["next_fire_at"] is not None
        assert body["spawned_from_task_id"] is None
        await client.delete(f"/api/tasks/{body['id']}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_with_spawned_from_task_id_201(
    client, scaffold_cleanup
) -> None:
    """T2 scheduler spawns a child via the public POST endpoint passing
    spawned_from_task_id → 201; field round-trips."""
    name = _unique_name("spawn-child")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # First create a template (parent of the spawned row).
        template = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "template parent",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "is_template": True,
                "recurrence_rule": "0 9 * * MON",
                "next_fire_at": _future_iso(),
            },
            headers=headers,
        )
        template_id = template.json()["id"]

        # Then a "spawned child" pointing at it.
        child = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "spawned child",
                "task_kind": "ai",
                "run_mode": "auto_pickup",
                "spawned_from_task_id": template_id,
            },
            headers=headers,
        )
        assert child.status_code == 201, child.text
        assert child.json()["spawned_from_task_id"] == template_id

        await client.delete(f"/api/tasks/{child.json()['id']}", headers=headers)
        await client.delete(f"/api/tasks/{template_id}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 3. POST /api/tasks task_kind ↔ run_mode cross-table validator
# =============================================================================


@pytest.mark.asyncio
async def test_post_task_human_auto_pickup_400(client, scaffold_cleanup) -> None:
    """task_kind='human' + run_mode='auto_pickup' → 400 with locked detail."""
    name = _unique_name("human-pickup-bad")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "human pickup invalid",
                "task_kind": "human",
                "run_mode": "auto_pickup",
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "detail": "task_kind 'human' is incompatible with run_mode 'auto_pickup'"
        }
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_human_auto_headless_400(client, scaffold_cleanup) -> None:
    """task_kind='human' + run_mode='auto_headless' → 400. Note: this fires
    BEFORE the consent gate (cheaper pure-function check ordering)."""
    name = _unique_name("human-headless-bad")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        # Even with consent, the kind/run_mode mismatch fires first.
        await client.post(
            f"/api/projects/{project_id}/grant-consent",
            json={"confirm_name": name},
        )
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "human headless invalid",
                "task_kind": "human",
                "run_mode": "auto_headless",
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "detail": "task_kind 'human' is incompatible with run_mode 'auto_headless'"
        }
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_invalid_cron_returns_422(client, scaffold_cleanup) -> None:
    """Pydantic field validator catches invalid cron at 422 before any DB hit."""
    name = _unique_name("bad-cron")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "bad cron",
                "recurrence_rule": "this is not a cron",
            },
            headers=headers,
        )
        assert resp.status_code == 422
        body = resp.json()
        # FastAPI default error envelope is {"detail":[{...},...]} for 422.
        assert "recurrence_rule" in str(body)
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_invalid_timezone_returns_422(
    client, scaffold_cleanup
) -> None:
    name = _unique_name("bad-tz")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "bad tz",
                "recurrence_timezone": "Mars/Olympus",
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert "recurrence_timezone" in str(resp.json())
    finally:
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_post_task_template_incomplete_returns_422(
    client, scaffold_cleanup
) -> None:
    """is_template=true without recurrence_rule → 422 (Pydantic catches first)."""
    name = _unique_name("template-bad")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    try:
        resp = await client.post(
            "/api/tasks",
            json={
                "project_id": project_id,
                "title": "incomplete template",
                "is_template": True,
            },
            headers=headers,
        )
        assert resp.status_code == 422
        # Pydantic catches at the model_validator before DB IntegrityError 400.
        assert "recurrence_rule" in str(resp.json()) or "next_fire_at" in str(
            resp.json()
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 4. PATCH /api/tasks/{id} resolved-final cross-table validator + spawn rejection
# =============================================================================


@pytest.mark.asyncio
async def test_patch_task_spawned_from_task_id_rejected_422(
    client, scaffold_cleanup
) -> None:
    """PATCH body containing spawned_from_task_id → 422 with field name in error."""
    name = _unique_name("patch-spawn")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "x"},
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"spawned_from_task_id": 1},
            headers=headers,
        )
        assert resp.status_code == 422
        assert "spawned_from_task_id" in str(resp.json())
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_kind_to_human_only_resolved_400(
    client, scaffold_cleanup
) -> None:
    """Existing ai+auto_pickup; PATCH task_kind='human' alone → 400 (resolved
    state = human + auto_pickup, fails)."""
    name = _unique_name("patch-resolved-fail")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "ai pickup",
            "task_kind": "ai",
            "run_mode": "auto_pickup",
        },
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"task_kind": "human"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "detail": "task_kind 'human' is incompatible with run_mode 'auto_pickup'"
        }
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


@pytest.mark.asyncio
async def test_patch_task_kind_and_run_mode_together_200(
    client, scaffold_cleanup
) -> None:
    """Same task downgrading both task_kind='human' AND run_mode='manual' in
    one body → 200 success (resolved = human + manual, valid)."""
    name = _unique_name("patch-both")
    scaffold_cleanup(name)
    create = await client.post("/api/projects", json=_project_create_payload(name))
    project_id = create.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    task = await client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "ai pickup",
            "task_kind": "ai",
            "run_mode": "auto_pickup",
        },
        headers=headers,
    )
    task_id = task.json()["id"]

    try:
        resp = await client.patch(
            f"/api/tasks/{task_id}",
            json={"task_kind": "human", "run_mode": "manual"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_kind"] == "human"
        assert body["run_mode"] == "manual"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)
        await client.delete(f"/api/projects/{project_id}")


# =============================================================================
# 5. Behavioral backfill — seeded rows and the migration's server_defaults
# =============================================================================


@pytest.mark.asyncio
async def test_seeded_tasks_have_correct_backfill_defaults(client) -> None:
    """All existing tasks (seeded + any user-created via T1 baseline) carry the
    migration 0007 server_defaults: task_kind='human', is_template=false,
    recurrence_timezone='UTC', NULL on the rest. Defends server_default
    correctness on ADD COLUMN."""
    headers = {"X-Project-Id": "1"}
    resp = await client.get("/api/tasks?limit=500", headers=headers)
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 1, "expected at least the seeded tasks"

    for t in tasks:
        assert t["task_kind"] == "human", (
            f"task {t['id']} backfilled to {t['task_kind']!r} not 'human'"
        )
        assert t["is_template"] is False, (
            f"task {t['id']} backfilled to is_template={t['is_template']!r} not false"
        )
        assert t["recurrence_rule"] is None, (
            f"task {t['id']} backfilled with recurrence_rule={t['recurrence_rule']!r}"
        )
        assert t["recurrence_timezone"] == "UTC", (
            f"task {t['id']} backfilled with timezone={t['recurrence_timezone']!r}"
        )
        assert t["next_fire_at"] is None, (
            f"task {t['id']} backfilled with next_fire_at={t['next_fire_at']!r}"
        )
        assert t["spawned_from_task_id"] is None, (
            f"task {t['id']} backfilled with spawned_from_task_id="
            f"{t['spawned_from_task_id']!r}"
        )


# =============================================================================
# 6. Source-text-locks for new wire-contract strings
# =============================================================================


def test_task_kind_run_mode_detail_string_pinned_in_service_source() -> None:
    """Source-text-lock per Kanban #122 pattern: the 400 detail template for
    task_kind ↔ run_mode mismatch is wire contract — drift breaks any FE that
    string-matches it."""
    from src.services import task_kind as task_kind_service

    source = Path(task_kind_service.__file__).read_text(encoding="utf-8")
    # f-string template form: f"task_kind 'human' is incompatible with run_mode '{run_mode}'"
    pinned = "\"task_kind 'human' is incompatible with run_mode '{run_mode}'\""
    # Strip f-string prefix so the assertion matches the literal-template form.
    normalized = source.replace("f\"", "\"")
    assert pinned in normalized, (
        f"task_kind/run_mode detail string drifted in services/task_kind.py — "
        f"expected {pinned!r}"
    )


def test_post_task_kind_check_detail_strings_pinned_in_router_source() -> None:
    """Router IntegrityError fallback strings for the two new CHECKs are
    defense-in-depth wire contract (only reachable via raw-SQL bypass / future
    schema drift)."""
    from src.routers import tasks as tasks_router

    source = Path(tasks_router.__file__).read_text(encoding="utf-8")
    pinned_pairs = [
        ('"ck_tasks_task_kind_valid"', '"task_kind violates ck_tasks_task_kind_valid"'),
        (
            '"ck_tasks_template_recurrence_complete"',
            '"template fields incomplete violates "',
        ),
    ]
    for constraint_pin, detail_pin in pinned_pairs:
        assert constraint_pin in source, (
            f"constraint name {constraint_pin} dropped from routers/tasks.py"
        )
        assert detail_pin in source, (
            f"detail string {detail_pin} drifted in routers/tasks.py"
        )
