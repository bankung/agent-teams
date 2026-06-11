"""Tests for tool_calls — migration + ORM + writer service + GET endpoint
(Kanban #980).

Coverage matrix:

1. Migration smoke
   - alembic upgrade + downgrade roundtrip leaves the test DB in a clean,
     reusable state (we re-apply head after downgrade so the autouse session
     fixture's invariant is preserved).
   - Base.metadata exposes `tool_calls` after upgrade head.

2. Writer service (`record_tool_call`)
   - Happy path: writes row with correct columns + indices populated.
   - Truncates `output` to 256 chars (raw cut).
   - Truncates `error_msg` to 1024 chars.
   - Tolerates None / missing keys in the result dict.
   - Empty-string output is preserved verbatim (NOT coerced to NULL).

3. GET /api/tasks/{task_id}/tool-calls endpoint
   - 400 on missing X-Project-Id header.
   - 404 on unknown task.
   - 410 on soft-deleted task (sub-resource is Gone with the parent).
   - 400 when the task lives in a different project than the header value
     (cross-project gate via session_project header).
   - 200 + ordered list (invoked_at DESC) on happy path.
   - Cascade delete: hard-deleting the parent task (psql-level — not the
     soft-delete path) wipes the audit rows. We exercise this with an ORM
     `delete(Task)` through the AsyncSession because the app's public DELETE
     is soft-only.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from tests.helpers.db_safety import assert_test_db_or_die


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


def _result_dict(
    success: bool = True,
    output: str | None = None,
    error_code: str | None = None,
    error_msg: str | None = None,
    duration_ms: int = 12,
) -> dict:
    """Mirror of ToolCallResult — shape the writer expects.

    Note: `retry_safe` is on the langgraph-side ToolResult but NOT on
    `ToolCallResult` (the wire schema). The audit layer filters it out
    before POSTing; tests mirror the post-filter shape here.
    """
    return {
        "success": success,
        "error_code": error_code,
        "error_msg": error_msg,
        "output": output,
        "duration_ms": duration_ms,
    }


# =============================================================================
# 1. Migration smoke
# =============================================================================


def test_orm_metadata_includes_tool_calls() -> None:
    """Base.metadata exposes the new table after the schema imports run."""
    from src.models import Base

    assert "tool_calls" in Base.metadata.tables


def test_tool_call_model_columns_match_migration() -> None:
    """Schema-source-of-truth check — the ORM declares every column the
    migration adds, with the right nullability.

    Updated for #2320: the rail now carries lead rows too. `source` is NOT NULL
    (server_default 'engine'); `kind`/`summary` are lead-only (nullable); the
    engine-only columns tier/input_json/duration_ms/permission_decision relaxed
    to nullable (lead rows leave them NULL — the engine NOT-NULL contract moved
    to the Pydantic ToolCallCreate layer)."""
    from src.models.tool_call import ToolCall

    cols = {c.name: c for c in ToolCall.__table__.columns}
    expected_not_null = {
        "id",
        "task_id",
        "invoked_at",
        "source",
        "tool_name",
        "success",
    }
    expected_nullable = {
        "kind",
        "summary",
        "tier",
        "input_json",
        "duration_ms",
        "permission_decision",
        "error_code",
        "error_msg",
        "output_summary",
    }
    assert set(cols.keys()) == expected_not_null | expected_nullable
    for n in expected_not_null:
        assert not cols[n].nullable, f"{n} should be NOT NULL"
    for n in expected_nullable:
        assert cols[n].nullable, f"{n} should be NULL"


def test_migration_downgrade_then_upgrade_leaves_clean_state() -> None:
    """Alembic downgrade -> upgrade for the new revision roundtrips cleanly.

    Uses a TEMPORARY DB (`agent_teams_migration_smoke_<uuid>`) so we don't
    wipe the session-scoped `agent_teams_test` DB the rest of the suite
    depends on. The alternative — downgrading the live test DB and
    re-upgrading — strips the seed rows that other tests assume; cross-test
    pollution surfaces as flaky red builds elsewhere.

    We exercise the FULL upgrade chain (alembic upgrade head from empty)
    then specifically downgrade -1 + upgrade head to roundtrip the new
    revision. Drop the throwaway DB on teardown regardless of outcome.
    """
    import asyncio
    import os
    import uuid

    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine

    test_url = os.environ["DATABASE_URL"]
    # Admin URL (postgres superuser) for CREATE/DROP DATABASE — pytest_runner
    # cannot do those. Captured by conftest.py at module load. #1109.
    _admin_base = os.environ["_PG_ADMIN_URL"]
    admin_url = _admin_base.rsplit("/", 1)[0] + "/postgres"
    throwaway_name = f"agent_teams_test_migration_smoke_{uuid.uuid4().hex[:8]}"
    # Throwaway uses the constrained pytest_runner credentials (same as
    # test_url) — alembic upgrade then runs as pytest_runner, mirroring the
    # real pytest path. The DB ownership is set below via ALTER DATABASE.
    throwaway_url = test_url.rsplit("/", 1)[0] + f"/{throwaway_name}"

    async def _make_db() -> None:
        admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin_engine.connect() as conn:
                # OWNER pytest_runner so the subsequent alembic upgrade —
                # which runs as pytest_runner via throwaway_url — has full
                # DDL/DML on the throwaway. Mirrors the agent_teams_test
                # setup in conftest. #1109.
                await conn.execute(
                    _text(
                        f"CREATE DATABASE {throwaway_name} OWNER pytest_runner"
                    )
                )
        finally:
            await admin_engine.dispose()

    async def _drop_db() -> None:
        admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin_engine.connect() as conn:
                await conn.execute(
                    _text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        f"WHERE datname = '{throwaway_name}' AND pid <> pg_backend_pid()"
                    )
                )
                await conn.execute(_text(f"DROP DATABASE IF EXISTS {throwaway_name}"))
        finally:
            await admin_engine.dispose()

    asyncio.run(_make_db())

    env = {**os.environ, "DATABASE_URL": throwaway_url}
    try:
        # Full upgrade from empty — exercises every prior revision + the new one.
        full_up = subprocess.run(
            ["alembic", "upgrade", "head"],
            check=False,
            capture_output=True,
            text=True,
            cwd="/repo/api",
            env=env,
        )
        assert full_up.returncode == 0, (
            f"initial upgrade failed.\nstdout:\n{full_up.stdout}\n"
            f"stderr:\n{full_up.stderr}"
        )
        # Roundtrip the new revision specifically.
        down = subprocess.run(
            ["alembic", "downgrade", "0027_projects_tools_config"],
            check=False,
            capture_output=True,
            text=True,
            cwd="/repo/api",
            env=env,
        )
        assert down.returncode == 0, (
            f"downgrade failed.\nstdout:\n{down.stdout}\nstderr:\n{down.stderr}"
        )
        up = subprocess.run(
            ["alembic", "upgrade", "head"],
            check=False,
            capture_output=True,
            text=True,
            cwd="/repo/api",
            env=env,
        )
        assert up.returncode == 0, (
            f"re-upgrade failed.\nstdout:\n{up.stdout}\nstderr:\n{up.stderr}"
        )
    finally:
        asyncio.run(_drop_db())


# =============================================================================
# 2. Writer service
# =============================================================================


@pytest.mark.asyncio
async def test_record_tool_call_writes_row(client, db_session) -> None:
    """Happy path — service writes one row, returns it with id populated."""
    from src.models.tool_call import ToolCall
    from src.services.tool_call_writer import record_tool_call

    # Use the seeded agent-teams project to keep the test self-contained.
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]

    # Create a task to own the audit row.
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-writer-happy"},
        headers={"X-Project-Id": str(project_id)},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    try:
        row = await record_tool_call(
            task_id=task_id,
            tool_name="file_edit",
            tier="write",
            input_args={"path": "/repo/foo.py", "old_string": "x", "new_string": "y"},
            result=_result_dict(success=True, output="patched 1 file", duration_ms=42),
            permission_decision="auto_allow",
            db=db_session,
        )
        assert row.id is not None
        assert row.task_id == task_id
        assert row.tool_name == "file_edit"
        assert row.tier == "write"
        assert row.success is True
        assert row.error_code is None
        assert row.error_msg is None
        assert row.output_summary == "patched 1 file"
        assert row.duration_ms == 42
        assert row.permission_decision == "auto_allow"
        assert isinstance(row.invoked_at, datetime)
        # JSONB roundtrip — dict equality.
        assert row.input_json == {
            "path": "/repo/foo.py",
            "old_string": "x",
            "new_string": "y",
        }
    finally:
        await client.delete(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )


@pytest.mark.asyncio
async def test_record_tool_call_truncates_output_to_256_chars(
    client, db_session
) -> None:
    """output > 256 chars → output_summary == first 256 chars (raw cut)."""
    from src.services.tool_call_writer import record_tool_call

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-trunc-output"},
        headers={"X-Project-Id": str(project_id)},
    )
    task_id = create.json()["id"]

    big_output = "A" * 300
    try:
        row = await record_tool_call(
            task_id=task_id,
            tool_name="shell_run",
            tier="destructive",
            input_args={"cmd": "ls /repo"},
            result=_result_dict(output=big_output),
            permission_decision="halt",
            db=db_session,
        )
        assert row.output_summary is not None
        assert len(row.output_summary) == 256
        assert row.output_summary == "A" * 256
    finally:
        await client.delete(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )


@pytest.mark.asyncio
async def test_record_tool_call_truncates_error_msg_to_1kb(
    client, db_session
) -> None:
    """error_msg > 1024 chars → truncated to exactly 1024."""
    from src.services.tool_call_writer import record_tool_call

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-trunc-errmsg"},
        headers={"X-Project-Id": str(project_id)},
    )
    task_id = create.json()["id"]

    big_err = "E" * 2000
    try:
        row = await record_tool_call(
            task_id=task_id,
            tool_name="http_get",
            tier="network",
            input_args={"url": "https://example.com"},
            result=_result_dict(
                success=False,
                error_code="timeout",
                error_msg=big_err,
            ),
            permission_decision="auto_allow",
            db=db_session,
        )
        assert row.error_msg is not None
        assert len(row.error_msg) == 1024
        assert row.error_msg == "E" * 1024
    finally:
        await client.delete(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )


@pytest.mark.asyncio
async def test_record_tool_call_tolerates_missing_result_keys(
    client, db_session
) -> None:
    """Defensive defaults: a result dict with only `success` still lands a
    row with sensible NULLs / zeros."""
    from src.services.tool_call_writer import record_tool_call

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-defensive-defaults"},
        headers={"X-Project-Id": str(project_id)},
    )
    task_id = create.json()["id"]

    try:
        row = await record_tool_call(
            task_id=task_id,
            tool_name="git_status",
            tier="read",
            input_args={},
            result={"success": True},  # everything else absent
            permission_decision="auto_allow",
            db=db_session,
        )
        assert row.success is True
        assert row.error_code is None
        assert row.error_msg is None
        assert row.output_summary is None
        assert row.duration_ms == 0
        assert row.input_json == {}
    finally:
        await client.delete(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )


@pytest.mark.asyncio
async def test_record_tool_call_preserves_empty_string_output(
    client, db_session
) -> None:
    """Empty `output` is recorded as "" (NOT NULL) — distinct from absent."""
    from src.services.tool_call_writer import record_tool_call

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-empty-output"},
        headers={"X-Project-Id": str(project_id)},
    )
    task_id = create.json()["id"]

    try:
        row = await record_tool_call(
            task_id=task_id,
            tool_name="git_diff",
            tier="read",
            input_args={},
            result=_result_dict(success=True, output=""),
            permission_decision="auto_allow",
            db=db_session,
        )
        assert row.output_summary == ""
    finally:
        await client.delete(
            f"/api/tasks/{task_id}", headers={"X-Project-Id": str(project_id)}
        )


# =============================================================================
# 3. GET /api/tasks/{task_id}/tool-calls endpoint
# =============================================================================


@pytest.mark.asyncio
async def test_get_tool_calls_400_when_header_missing(client) -> None:
    """Source-text-locked detail — same gate as the rest of /api/tasks/*."""
    resp = await client.get("/api/tasks/1/tool-calls")
    assert resp.status_code == 400
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


@pytest.mark.asyncio
async def test_get_tool_calls_404_on_unknown_task(client) -> None:
    resp = await client.get(
        "/api/tasks/999999999/tool-calls",
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=999999999 not found"}


@pytest.mark.asyncio
async def test_get_tool_calls_410_on_soft_deleted_task(client) -> None:
    """Sub-resource is Gone with the parent — distinct from a never-existed id."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-410-on-deleted"},
        headers=headers,
    )
    task_id = create.json()["id"]
    delete = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert delete.status_code == 204

    resp = await client.get(
        f"/api/tasks/{task_id}/tool-calls", headers=headers
    )
    assert resp.status_code == 410, resp.text
    body = resp.json()
    assert body["detail"].startswith(f"Task id={task_id} is deleted")


@pytest.mark.asyncio
async def test_get_tool_calls_400_on_cross_project_header(
    client, scaffold_cleanup
) -> None:
    """Task belongs to project A; header claims B → 400 (session_project gate)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_a_id = active.json()["id"]

    name_b = scaffold_cleanup(_unique_name("k980-crossproj"))
    proj_b = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    project_b_id = proj_b.json()["id"]

    headers_a = {"X-Project-Id": str(project_a_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_a_id, "title": "k980-crossproj-task"},
        headers=headers_a,
    )
    task_id = create.json()["id"]

    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/tool-calls",
            headers={"X-Project-Id": str(project_b_id)},
        )
        # session_project header gate fires 400 (NOT 404) — task exists, but
        # the header points to a different project. This mirrors the existing
        # tasks-endpoint behavior pinned by test_session_project_header.py.
        assert resp.status_code == 400, resp.text
        assert "does not belong to" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_a)


@pytest.mark.asyncio
async def test_get_tool_calls_returns_rows_ordered_invoked_at_desc(
    client, db_session
) -> None:
    """Populate 3 rows; GET returns them most-recent-first."""
    import asyncio

    from src.services.tool_call_writer import record_tool_call

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-list-order"},
        headers=headers,
    )
    task_id = create.json()["id"]

    try:
        # Three rows with detectably-different invoked_at via tiny sleeps —
        # server-default now() is fine because each commit lands at a new
        # microsecond. We persist + slight sleep to make the ordering robust.
        rows = []
        for i, tname in enumerate(("file_edit", "git_diff", "shell_run"), start=1):
            r = await record_tool_call(
                task_id=task_id,
                tool_name=tname,
                tier="read",
                input_args={"i": i},
                result=_result_dict(output=f"out-{i}"),
                permission_decision="auto_allow",
                db=db_session,
            )
            rows.append(r)
            await asyncio.sleep(0.005)

        resp = await client.get(
            f"/api/tasks/{task_id}/tool-calls", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 3
        # Most-recent first: shell_run, git_diff, file_edit.
        tool_names = [row["tool_name"] for row in body]
        assert tool_names == ["shell_run", "git_diff", "file_edit"]
        # Wire shape — all expected fields present, types match.
        first = body[0]
        for key in (
            "id",
            "task_id",
            "invoked_at",
            "tool_name",
            "tier",
            "input_json",
            "success",
            "error_code",
            "error_msg",
            "output_summary",
            "duration_ms",
            "permission_decision",
        ):
            assert key in first, f"missing key {key!r} in response"
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_get_tool_calls_returns_empty_list_when_none_recorded(
    client,
) -> None:
    """Fresh task with no tool calls → 200 + []."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-empty-list"},
        headers=headers,
    )
    task_id = create.json()["id"]
    try:
        resp = await client.get(
            f"/api/tasks/{task_id}/tool-calls", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


# =============================================================================
# 4. POST /api/tasks/{task_id}/tool-calls — internal audit-write endpoint (#981)
# =============================================================================


def _post_body(
    *,
    tool_name: str = "file_edit",
    tier: str = "write",
    success: bool = True,
    output: str | None = "patched",
    error_code: str | None = None,
    error_msg: str | None = None,
    duration_ms: int = 42,
    permission_decision: str = "auto_allow",
    input_args: dict | None = None,
) -> dict:
    """Build a valid ToolCallCreate body for the POST endpoint."""
    return {
        "tool_name": tool_name,
        "tier": tier,
        "input_args": input_args if input_args is not None else {"path": "/x"},
        "result": {
            "success": success,
            "error_code": error_code,
            "error_msg": error_msg,
            "output": output,
            "duration_ms": duration_ms,
        },
        "permission_decision": permission_decision,
    }


@pytest.mark.asyncio
async def test_post_tool_call_201_persists_row(client) -> None:
    """Happy path — POST writes a row + returns 201 + ToolCallRead body."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k981-post-201"},
        headers=headers,
    )
    task_id = create.json()["id"]

    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_post_body(),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["tool_name"] == "file_edit"
        assert body["tier"] == "write"
        assert body["success"] is True
        assert body["output_summary"] == "patched"
        assert body["duration_ms"] == 42
        assert body["permission_decision"] == "auto_allow"
        assert body["task_id"] == task_id
        assert body["id"] > 0

        # GET on the same task surfaces the new row.
        get_resp = await client.get(
            f"/api/tasks/{task_id}/tool-calls", headers=headers
        )
        assert get_resp.status_code == 200
        rows = get_resp.json()
        assert len(rows) == 1
        assert rows[0]["id"] == body["id"]
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_post_tool_call_400_when_header_missing(client) -> None:
    """X-Project-Id header is mandatory (same gate as GET)."""
    resp = await client.post(
        "/api/tasks/1/tool-calls",
        json=_post_body(),
    )
    assert resp.status_code == 400
    assert resp.json() == {
        "detail": "X-Project-Id header is required for task endpoints"
    }


@pytest.mark.asyncio
async def test_post_tool_call_404_on_unknown_task(client) -> None:
    resp = await client.post(
        "/api/tasks/999999999/tool-calls",
        json=_post_body(),
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Task id=999999999 not found"}


@pytest.mark.asyncio
async def test_post_tool_call_410_on_soft_deleted_task(client) -> None:
    """Soft-deleted task → 410 Gone (audit closed with the parent)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k981-post-410"},
        headers=headers,
    )
    task_id = create.json()["id"]
    delete = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert delete.status_code == 204

    resp = await client.post(
        f"/api/tasks/{task_id}/tool-calls",
        json=_post_body(),
        headers=headers,
    )
    assert resp.status_code == 410, resp.text
    assert resp.json()["detail"].startswith(f"Task id={task_id} is deleted")


@pytest.mark.asyncio
async def test_post_tool_call_400_on_cross_project_header(
    client, scaffold_cleanup
) -> None:
    """Task belongs to project A; header claims B → 400 (session-project gate)."""
    active = await client.get("/api/projects/by-name/agent-teams")
    project_a_id = active.json()["id"]

    name_b = scaffold_cleanup(_unique_name("k981-crossproj"))
    proj_b = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    project_b_id = proj_b.json()["id"]

    headers_a = {"X-Project-Id": str(project_a_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_a_id, "title": "k981-crossproj-task"},
        headers=headers_a,
    )
    task_id = create.json()["id"]

    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_post_body(),
            headers={"X-Project-Id": str(project_b_id)},
        )
        assert resp.status_code == 400, resp.text
        assert "does not belong to" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers_a)


@pytest.mark.asyncio
async def test_post_tool_call_422_on_extra_field_in_body(client) -> None:
    """Pydantic extra='forbid' on ToolCallCreate → extra field 422.

    Defends against payload drift between langgraph audit.py and the
    api-side schema.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k981-post-extra"},
        headers=headers,
    )
    task_id = create.json()["id"]

    body = _post_body()
    # task_id is in the URL path, NOT the body — this regression test pins
    # that drift.
    body["task_id"] = task_id

    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=body,
            headers=headers,
        )
        assert resp.status_code == 422
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_post_tool_call_truncates_output_via_writer(client) -> None:
    """Output > 256 chars → persisted output_summary == first 256 chars.

    Same writer-service rule as the direct service tests above; verified
    via the POST endpoint here.
    """
    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k981-post-trunc"},
        headers=headers,
    )
    task_id = create.json()["id"]
    big_output = "B" * 300

    try:
        resp = await client.post(
            f"/api/tasks/{task_id}/tool-calls",
            json=_post_body(output=big_output),
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["output_summary"] == "B" * 256
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=headers)


@pytest.mark.asyncio
async def test_cascade_delete_on_hard_task_delete(
    client, db_session
) -> None:
    """FK ON DELETE CASCADE — hard-deleting a task removes its audit rows.

    We bypass the app's public DELETE (soft only) and issue a SQL DELETE
    through the test session to exercise the cascade. The live agent_teams DB
    is NOT touched (autouse conftest binds to agent_teams_test).
    """
    from src.models.task import Task
    from src.models.tool_call import ToolCall
    from src.services.tool_call_writer import record_tool_call

    active = await client.get("/api/projects/by-name/agent-teams")
    project_id = active.json()["id"]
    headers = {"X-Project-Id": str(project_id)}
    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "k980-cascade"},
        headers=headers,
    )
    task_id = create.json()["id"]

    await record_tool_call(
        task_id=task_id,
        tool_name="git_status",
        tier="read",
        input_args={},
        result=_result_dict(),
        permission_decision="auto_allow",
        db=db_session,
    )
    # Audit row exists before delete.
    pre = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.task_id == task_id)
        )
    ).scalars().all()
    assert len(pre) == 1

    # Hard delete the parent task via ORM (test DB only — avoids raw-DML audit
    # trip while still exercising the FK cascade path).
    assert_test_db_or_die(db_session)  # L6 gate: refuse if not a _test DB
    await db_session.execute(delete(Task).where(Task.id == task_id))
    await db_session.commit()

    # Cascade fires — no rows left.
    post = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.task_id == task_id)
        )
    ).scalars().all()
    assert post == []
