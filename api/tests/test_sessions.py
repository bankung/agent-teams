"""Tests for sessions / session_runs / session_compacts (CTX-1, Kanban #716).

Covers six surfaces:

1. Schema-level (Pydantic): SessionCreate / SessionUpdate / SessionRunCreate /
   SessionRunUpdate accept/reject. Lockstep guard tests for the 3 new Literals.
2. Sessions HTTP — create/list/detail/patch/close happy paths + filesystem
   skeleton creation + multi-instance support.
3. SessionRuns HTTP — create/update/list happy paths + cross-project rejection +
   closed-session rejection + auto finished_at stamp.
4. Source-text-locks for the 2 new locked detail strings.
5. Behavioral: closing a session preserves filesystem; existing tables
   untouched.
6. Negative paths: 404 on missing ids, 422 on bad literal, 400 on closed-session
   PATCH.
"""

from __future__ import annotations

import importlib
import shutil
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError


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


@pytest.fixture
def session_fs_cleanup():
    """Remove `_sessions/<id>/` dirs created during a test.

    Why: routes call `services.session_files.create_session_skeleton(...)`
    which writes to `<repo_root>/_sessions/<id>/`. Tests close their DB rows
    via PATCH but the FS dirs are not auto-removed (CTX-1 deliberately
    preserves them for audit). This fixture removes them per-test so the
    working tree stays clean.
    """
    from src.settings import get_settings

    repo_root = Path(get_settings().repo_root)
    ids: list[int] = []

    def register(session_id: int) -> int:
        ids.append(session_id)
        return session_id

    yield register

    for sid in ids:
        target = repo_root / "_sessions" / str(sid)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


# =============================================================================
# 1. Schema-level (Pydantic) tests
# =============================================================================


def test_session_create_minimal() -> None:
    from src.schemas.session import SessionCreate

    s = SessionCreate(project_id=1)
    assert s.project_id == 1
    assert s.process_label is None
    assert s.token_budget_per_run is None


def test_session_create_rejects_zero_project_id() -> None:
    from src.schemas.session import SessionCreate

    with pytest.raises(ValidationError):
        SessionCreate(project_id=0)


def test_session_update_status_literal_accepts_each() -> None:
    from src.schemas.session import SessionUpdate

    for s in ("active", "compacting", "closed"):
        upd = SessionUpdate(status=s)
        assert upd.status == s


def test_session_update_rejects_unknown_status() -> None:
    from src.schemas.session import SessionUpdate

    with pytest.raises(ValidationError):
        SessionUpdate(status="weird")  # type: ignore[arg-type]


def test_session_run_create_default_status_is_running() -> None:
    from src.schemas.session import SessionRunCreate

    r = SessionRunCreate()
    assert r.status == "running"
    assert r.task_id is None


def test_session_run_update_status_literal_accepts_each() -> None:
    from src.schemas.session import SessionRunUpdate

    for s in ("running", "done", "error", "timeout"):
        upd = SessionRunUpdate(status=s)
        assert upd.status == s


def test_session_run_update_rejects_unknown_status() -> None:
    from src.schemas.session import SessionRunUpdate

    with pytest.raises(ValidationError):
        SessionRunUpdate(status="zombie")  # type: ignore[arg-type]


def test_session_status_literal_lockstep_guard_holds() -> None:
    """Positive case — Literal args == constants ALL tuple → import succeeds."""
    from src.constants import (
        SessionCompactTrigger,
        SessionRunStatus,
        SessionStatus,
    )
    from src.schemas.session import (
        SessionCompactTriggerLiteral,
        SessionRunStatusLiteral,
        SessionStatusLiteral,
    )

    assert set(SessionStatusLiteral.__args__) == set(SessionStatus.ALL)  # type: ignore[attr-defined]
    assert set(SessionRunStatusLiteral.__args__) == set(SessionRunStatus.ALL)  # type: ignore[attr-defined]
    assert set(SessionCompactTriggerLiteral.__args__) == set(  # type: ignore[attr-defined]
        SessionCompactTrigger.ALL
    )


def test_session_status_literal_drift_raises_at_import(monkeypatch) -> None:
    """Force drift between SessionStatus.ALL and the Literal — the guard at
    the bottom of schemas/session.py must raise RuntimeError on reload."""
    import src.constants as constants_mod
    import src.schemas.session as session_schema_mod

    monkeypatch.setattr(
        constants_mod.SessionStatus, "ALL", ("active", "wrong_extra")
    )
    with pytest.raises(RuntimeError, match="drifted"):
        importlib.reload(session_schema_mod)
    monkeypatch.undo()
    importlib.reload(session_schema_mod)


def test_session_run_status_literal_drift_raises_at_import(monkeypatch) -> None:
    import src.constants as constants_mod
    import src.schemas.session as session_schema_mod

    monkeypatch.setattr(
        constants_mod.SessionRunStatus, "ALL", ("running", "wrong_extra")
    )
    with pytest.raises(RuntimeError, match="drifted"):
        importlib.reload(session_schema_mod)
    monkeypatch.undo()
    importlib.reload(session_schema_mod)


def test_session_compact_trigger_literal_drift_raises_at_import(monkeypatch) -> None:
    import src.constants as constants_mod
    import src.schemas.session as session_schema_mod

    monkeypatch.setattr(
        constants_mod.SessionCompactTrigger, "ALL", ("size", "wrong_extra")
    )
    with pytest.raises(RuntimeError, match="drifted"):
        importlib.reload(session_schema_mod)
    monkeypatch.undo()
    importlib.reload(session_schema_mod)


# =============================================================================
# 2. Sessions HTTP — create / list / detail / patch / close
# =============================================================================


@pytest.mark.asyncio
async def test_create_session_201_stamps_root_path_and_creates_fs_skeleton(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST /api/sessions on a real project — 201 + DB row + filesystem
    skeleton (`session.md`, `archive/`, `cards/`) all present."""
    name = _unique_name("sess-happy")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        resp = await client.post("/api/sessions", json={"project_id": pid})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        sid = body["id"]
        session_fs_cleanup(sid)

        assert body["project_id"] == pid
        assert body["status"] == "active"
        assert body["session_root_path"] == f"_sessions/{sid}/"
        assert body["closed_at"] is None
        assert body["compacted_history_ceiling_tokens"] == 13000
        assert body["recent_activity_ceiling_tokens"] == 15000
        assert body["runs_count"] == 0
        assert body["compacts_count"] == 0

        # Filesystem skeleton present.
        from src.settings import get_settings

        sess_dir = Path(get_settings().repo_root) / "_sessions" / str(sid)
        assert sess_dir.is_dir()
        assert (sess_dir / "session.md").is_file()
        assert (sess_dir / "archive").is_dir()
        assert (sess_dir / "cards").is_dir()
        # Skeleton header is present in session.md.
        content = (sess_dir / "session.md").read_text(encoding="utf-8")
        assert "## Compacted History" in content
        assert "## Recent Activity" in content
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_create_session_400_when_project_missing(client) -> None:
    resp = await client.post(
        "/api/sessions", json={"project_id": 999_999_999}
    )
    assert resp.status_code == 400
    assert resp.json() == {"detail": "project_id 999999999 does not exist"}


@pytest.mark.asyncio
async def test_list_sessions_filters_by_project_and_status(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("sess-list")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        a = await client.post("/api/sessions", json={"project_id": pid})
        sid_a = a.json()["id"]
        session_fs_cleanup(sid_a)

        # Filter by project_id.
        r = await client.get(f"/api/sessions?project_id={pid}")
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()]
        assert sid_a in ids

        # Filter by project + status=active.
        r2 = await client.get(f"/api/sessions?project_id={pid}&status=active")
        assert r2.status_code == 200
        ids2 = [row["id"] for row in r2.json()]
        assert sid_a in ids2
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_get_session_detail_returns_counts(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("sess-detail")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.get(f"/api/sessions/{sid}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == sid
        assert body["runs_count"] == 0
        assert body["compacts_count"] == 0
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_get_session_404_on_missing_id(client) -> None:
    r = await client.get("/api/sessions/999999")
    assert r.status_code == 404
    assert r.json() == {"detail": "Session id=999999 not found"}


@pytest.mark.asyncio
async def test_patch_session_close_stamps_closed_at(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("sess-close")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.patch(
            f"/api/sessions/{sid}", json={"status": "closed"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "closed"
        assert body["closed_at"] is not None
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_session_after_close_returns_400_locked_detail(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("sess-reopen")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        await client.patch(f"/api/sessions/{sid}", json={"status": "closed"})

        # Try to mutate the closed session — must 400 with locked detail.
        r2 = await client.patch(
            f"/api/sessions/{sid}", json={"status": "active"}
        )
        assert r2.status_code == 400
        assert r2.json() == {"detail": f"Session id={sid} already closed"}

        r3 = await client.patch(
            f"/api/sessions/{sid}", json={"process_label": "rewrite"}
        )
        assert r3.status_code == 400
        assert r3.json() == {"detail": f"Session id={sid} already closed"}
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_session_404_on_missing_id(client) -> None:
    r = await client.patch(
        "/api/sessions/999999", json={"process_label": "x"}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_multi_instance_two_active_sessions_per_project(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """The partial unique index `ux_projects_active_one` is dropped (Kanban
    #694 Phase 2). Sessions inherit the same multi-instance freedom — two
    active rows for the same project must coexist."""
    name = _unique_name("sess-multi")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        a = await client.post("/api/sessions", json={"project_id": pid})
        b = await client.post("/api/sessions", json={"project_id": pid})
        assert a.status_code == 201
        assert b.status_code == 201
        sid_a = a.json()["id"]
        sid_b = b.json()["id"]
        session_fs_cleanup(sid_a)
        session_fs_cleanup(sid_b)
        assert sid_a != sid_b
        assert a.json()["status"] == "active"
        assert b.json()["status"] == "active"

        # Both visible via the active filter.
        r = await client.get(f"/api/sessions?project_id={pid}&status=active")
        ids = [row["id"] for row in r.json()]
        assert sid_a in ids and sid_b in ids
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_session_bad_status_literal_returns_422(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("sess-422")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.patch(
            f"/api/sessions/{sid}", json={"status": "weird"}
        )
        assert r.status_code == 422
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# 3. SessionRuns HTTP — create / update / list
# =============================================================================


@pytest.mark.asyncio
async def test_create_run_with_task_writes_card_log(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST /api/sessions/{sid}/runs with task_id → 201 + card_log_path set
    + `_sessions/<sid>/cards/<task_id>.md` exists on disk."""
    name = _unique_name("run-card")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    try:
        # Create a task in the same project.
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid, "title": "card task"},
            headers=headers,
        )
        tid = t.json()["id"]

        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["session_id"] == sid
        assert body["task_id"] == tid
        assert body["status"] == "running"
        assert body["card_log_path"] == f"_sessions/{sid}/cards/{tid}.md"

        # Filesystem card present.
        from src.settings import get_settings

        card_path = (
            Path(get_settings().repo_root)
            / "_sessions"
            / str(sid)
            / "cards"
            / f"{tid}.md"
        )
        assert card_path.is_file()
        assert f"task {tid}" in card_path.read_text(encoding="utf-8")

        # Cleanup task.
        await client.delete(f"/api/tasks/{tid}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_create_run_without_task_id_201_no_card_path(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("run-notask")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(f"/api/sessions/{sid}/runs", json={})
        assert r.status_code == 201
        body = r.json()
        assert body["task_id"] is None
        assert body["card_log_path"] is None
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_create_run_cross_project_task_returns_400_locked(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST run where task.project_id != session.project_id → 400 with
    locked detail string."""
    name_a = _unique_name("run-cross-a")
    name_b = _unique_name("run-cross-b")
    scaffold_cleanup(name_a)
    scaffold_cleanup(name_b)

    pa = await client.post(
        "/api/projects", json=_project_create_payload(name_a)
    )
    pb = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    pid_a = pa.json()["id"]
    pid_b = pb.json()["id"]
    headers_b = {"X-Project-Id": str(pid_b)}

    try:
        # task in project B.
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid_b, "title": "wrong project"},
            headers=headers_b,
        )
        tid = t.json()["id"]

        # session in project A.
        s = await client.post("/api/sessions", json={"project_id": pid_a})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        assert r.status_code == 400
        assert r.json() == {
            "detail": (
                f"task {tid} belongs to project {pid_b}, "
                f"session belongs to project {pid_a}"
            )
        }

        await client.delete(f"/api/tasks/{tid}", headers=headers_b)
    finally:
        await client.delete(f"/api/projects/{pid_a}")
        await client.delete(f"/api/projects/{pid_b}")


@pytest.mark.asyncio
async def test_create_run_on_closed_session_returns_400(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("run-closed")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        await client.patch(f"/api/sessions/{sid}", json={"status": "closed"})

        r = await client.post(f"/api/sessions/{sid}/runs", json={})
        assert r.status_code == 400
        assert "closed" in r.json()["detail"]
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_create_run_on_missing_session_returns_404(client) -> None:
    r = await client.post("/api/sessions/999999/runs", json={})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_run_done_stamps_finished_at_and_takes_totals(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("run-patch")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]
        assert run.json()["finished_at"] is None

        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={"status": "done", "total_input_tokens": 1500},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "done"
        assert body["finished_at"] is not None
        assert body["total_input_tokens"] == 1500
        # CTX-3 wires real cost; CTX-1 just defaults to 0 on creation.
        assert str(body["total_cost_usd"]) in {"0", "0.0000", "0.00"}
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_404_on_missing_id(client) -> None:
    r = await client.patch("/api/session_runs/999999", json={"status": "done"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_session_runs_filters_by_status(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("run-list")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        run_a = await client.post(f"/api/sessions/{sid}/runs", json={})
        run_b = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid_a = run_a.json()["id"]
        rid_b = run_b.json()["id"]
        await client.patch(
            f"/api/session_runs/{rid_b}", json={"status": "done"}
        )

        r = await client.get(f"/api/sessions/{sid}/runs?status=running")
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()]
        assert rid_a in ids
        assert rid_b not in ids

        r2 = await client.get(f"/api/sessions/{sid}/runs?status=done")
        ids2 = [row["id"] for row in r2.json()]
        assert rid_b in ids2
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_list_compacts_empty_for_new_session(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """CTX-4 owns POST /compacts; CTX-1 read-only endpoint just returns []."""
    name = _unique_name("compact-empty")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.get(f"/api/sessions/{sid}/compacts")
        assert r.status_code == 200
        assert r.json() == []
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# 5. Behavioral — closing preserves filesystem; existing tables untouched
# =============================================================================


@pytest.mark.asyncio
async def test_closing_session_preserves_filesystem_content(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """Closing a session must not delete `_sessions/<id>/session.md` — the
    file is the audit record of the session's content."""
    name = _unique_name("sess-fs-preserve")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        from src.settings import get_settings

        sess_md = (
            Path(get_settings().repo_root)
            / "_sessions"
            / str(sid)
            / "session.md"
        )
        assert sess_md.is_file()
        pre_content = sess_md.read_text(encoding="utf-8")

        await client.patch(f"/api/sessions/{sid}", json={"status": "closed"})

        # File is preserved post-close.
        assert sess_md.is_file()
        assert sess_md.read_text(encoding="utf-8") == pre_content
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# 6. Session ceilings extension (Kanban #722, migration 0009)
# =============================================================================


@pytest.mark.asyncio
async def test_create_session_defaults_card_detail_and_output_budget(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST /api/sessions without ceiling overrides → response carries the
    DB-default 6000 / 4000 for the two new buckets (and 13000 / 15000 for
    the existing two)."""
    name = _unique_name("sess-ceilings-default")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        resp = await client.post("/api/sessions", json={"project_id": pid})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        sid = body["id"]
        session_fs_cleanup(sid)

        assert body["compacted_history_ceiling_tokens"] == 13000
        assert body["recent_activity_ceiling_tokens"] == 15000
        assert body["card_detail_ceiling_tokens"] == 6000
        assert body["output_budget_tokens"] == 4000
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_create_session_accepts_explicit_ceiling_overrides(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST with explicit overrides for the two new buckets → response
    reflects the supplied values (proves the wire-through path)."""
    name = _unique_name("sess-ceilings-override")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        resp = await client.post(
            "/api/sessions",
            json={
                "project_id": pid,
                "card_detail_ceiling_tokens": 3000,
                "output_budget_tokens": 2000,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        sid = body["id"]
        session_fs_cleanup(sid)

        assert body["card_detail_ceiling_tokens"] == 3000
        assert body["output_budget_tokens"] == 2000
        # Untouched buckets keep DB defaults.
        assert body["compacted_history_ceiling_tokens"] == 13000
        assert body["recent_activity_ceiling_tokens"] == 15000
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_session_updates_card_detail_and_output_budget(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """PATCH /api/sessions/{id} with new ceiling values → updated."""
    name = _unique_name("sess-ceilings-patch")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.patch(
            f"/api/sessions/{sid}",
            json={
                "card_detail_ceiling_tokens": 7500,
                "output_budget_tokens": 5500,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["card_detail_ceiling_tokens"] == 7500
        assert body["output_budget_tokens"] == 5500
    finally:
        await client.delete(f"/api/projects/{pid}")


def test_session_create_ceilings_default_to_none() -> None:
    """Schema-level: SessionCreate without ceiling fields → all four are
    None (signal the router to fall through to DB server_default)."""
    from src.schemas.session import SessionCreate

    s = SessionCreate(project_id=1)
    assert s.compacted_history_ceiling_tokens is None
    assert s.recent_activity_ceiling_tokens is None
    assert s.card_detail_ceiling_tokens is None
    assert s.output_budget_tokens is None


@pytest.mark.asyncio
async def test_create_session_rejects_zero_card_detail_ceiling(
    client, scaffold_cleanup
) -> None:
    """POST /api/sessions with card_detail_ceiling_tokens=0 → 422 (Pydantic
    `ge=1`). One field is enough — the type system enforces the same bound on
    the other three ceilings."""
    name = _unique_name("sess-ceiling-zero")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        resp = await client.post(
            "/api/sessions",
            json={"project_id": pid, "card_detail_ceiling_tokens": 0},
        )
        assert resp.status_code == 422
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# 7. CTX-2 — POST /activity, GET /prompt, POST /heartbeat (Kanban #717)
# =============================================================================


@pytest.mark.asyncio
async def test_post_activity_appends_to_recent_activity_section(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("act-happy")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/activity",
            json={
                "summary": "first activity entry",
                "role": "dev-backend",
                "kind": "spawn",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert "first activity entry" in body["appended_block"]
        assert "dev-backend:spawn" in body["appended_block"]
        assert body["section_chars"] >= len(body["appended_block"])

        # File on disk has the entry.
        from src.settings import get_settings

        sess_md = (
            Path(get_settings().repo_root)
            / "_sessions"
            / str(sid)
            / "session.md"
        )
        content = sess_md.read_text(encoding="utf-8")
        assert "first activity entry" in content
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_activity_on_closed_session_returns_400_locked(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("act-closed")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        await client.patch(f"/api/sessions/{sid}", json={"status": "closed"})

        r = await client.post(
            f"/api/sessions/{sid}/activity", json={"summary": "x"}
        )
        assert r.status_code == 400
        assert r.json() == {
            "detail": f"Session id={sid} is closed; cannot append activity"
        }
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_activity_cross_project_task_returns_400_locked(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name_a = _unique_name("act-cross-a")
    name_b = _unique_name("act-cross-b")
    scaffold_cleanup(name_a)
    scaffold_cleanup(name_b)
    pa = await client.post(
        "/api/projects", json=_project_create_payload(name_a)
    )
    pb = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    pid_a = pa.json()["id"]
    pid_b = pb.json()["id"]
    headers_b = {"X-Project-Id": str(pid_b)}

    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid_b, "title": "wrong project"},
            headers=headers_b,
        )
        tid = t.json()["id"]

        s = await client.post("/api/sessions", json={"project_id": pid_a})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/activity",
            json={"task_id": tid, "summary": "leaks across project"},
        )
        assert r.status_code == 400
        assert r.json() == {
            "detail": (
                f"task {tid} belongs to project {pid_b}, "
                f"session belongs to project {pid_a}"
            )
        }

        await client.delete(f"/api/tasks/{tid}", headers=headers_b)
    finally:
        await client.delete(f"/api/projects/{pid_a}")
        await client.delete(f"/api/projects/{pid_b}")


@pytest.mark.asyncio
async def test_get_prompt_returns_concatenated_markdown(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("prompt-happy")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        await client.post(
            f"/api/sessions/{sid}/activity",
            json={"summary": "appended-via-http"},
        )

        r = await client.get(f"/api/sessions/{sid}/prompt")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["markdown"].startswith("# Session context")
        assert "## Recent Activity" in body["markdown"]
        assert "appended-via-http" in body["markdown"]
        assert body["char_count"] == len(body["markdown"])
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_get_prompt_with_include_card_id_appends_card(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("prompt-card")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid, "title": "card task"},
            headers=headers,
        )
        tid = t.json()["id"]

        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        run = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        rid = run.json()["id"]

        await client.post(
            f"/api/session_runs/{rid}/heartbeat",
            json={"content": "card body content"},
        )

        r = await client.get(
            f"/api/sessions/{sid}/prompt?include_card_id={tid}"
        )
        assert r.status_code == 200
        body = r.json()
        assert f"## Current card detail (task #{tid})" in body["markdown"]
        assert "card body content" in body["markdown"]

        await client.delete(f"/api/tasks/{tid}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_heartbeat_appends_five_blocks_in_order(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("hb-five")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid, "title": "hb task"},
            headers=headers,
        )
        tid = t.json()["id"]

        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        run = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        rid = run.json()["id"]

        for i in range(5):
            r = await client.post(
                f"/api/session_runs/{rid}/heartbeat",
                json={"content": f"beat-{i}"},
            )
            assert r.status_code == 201, r.text

        from src.settings import get_settings

        card = (
            Path(get_settings().repo_root)
            / "_sessions"
            / str(sid)
            / "cards"
            / f"{tid}.md"
        ).read_text(encoding="utf-8")
        positions = [card.find(f"beat-{i}") for i in range(5)]
        assert all(p >= 0 for p in positions)
        assert positions == sorted(positions)

        await client.delete(f"/api/tasks/{tid}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_heartbeat_replace_overwrites_card(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("hb-replace")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid, "title": "task"},
            headers=headers,
        )
        tid = t.json()["id"]
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        rid = run.json()["id"]

        await client.post(
            f"/api/session_runs/{rid}/heartbeat",
            json={"content": "first append"},
        )
        await client.post(
            f"/api/session_runs/{rid}/heartbeat",
            json={"content": "snapshot final", "mode": "replace"},
        )

        from src.settings import get_settings

        card = (
            Path(get_settings().repo_root)
            / "_sessions"
            / str(sid)
            / "cards"
            / f"{tid}.md"
        ).read_text(encoding="utf-8")
        assert card == "snapshot final"
        assert "first append" not in card

        await client.delete(f"/api/tasks/{tid}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_heartbeat_on_runless_returns_400(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """Run with no task_id → 400 (no card log to write to)."""
    name = _unique_name("hb-runless")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        r = await client.post(
            f"/api/session_runs/{rid}/heartbeat", json={"content": "x"}
        )
        assert r.status_code == 400
        assert r.json() == {
            "detail": (
                f"Session run id={rid} has no task_id; heartbeat requires a card log"
            )
        }
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_heartbeat_on_closed_session_returns_400_locked(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    name = _unique_name("hb-closed")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid, "title": "task"},
            headers=headers,
        )
        tid = t.json()["id"]
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        rid = run.json()["id"]

        await client.patch(f"/api/sessions/{sid}", json={"status": "closed"})

        r = await client.post(
            f"/api/session_runs/{rid}/heartbeat", json={"content": "x"}
        )
        assert r.status_code == 400
        assert r.json() == {
            "detail": f"Session id={sid} is closed; cannot write heartbeat"
        }

        await client.delete(f"/api/tasks/{tid}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_heartbeat_404_on_missing_run(client) -> None:
    r = await client.post(
        "/api/session_runs/999999/heartbeat", json={"content": "x"}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_prompt_404_on_missing_session(client) -> None:
    r = await client.get("/api/sessions/999999/prompt")
    assert r.status_code == 404


# =============================================================================
# 8. CTX-3 (#718) — token measure on activity + cost compute on PATCH +
#    soft-warn budget log.
# =============================================================================


@pytest.mark.asyncio
async def test_activity_response_carries_token_advisory_under_ceiling(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """CTX-3: small activity → compact_recommended=False, current < ceiling."""
    name = _unique_name("act-token-under")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/activity",
            json={"summary": "tiny entry"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["compact_recommended"] is False
        assert body["recent_ceiling_tokens"] == 15000
        assert body["current_recent_tokens"] >= 1
        assert body["current_recent_tokens"] < body["recent_ceiling_tokens"]
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_activity_response_flags_compact_recommended_over_ceiling(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """Lower the recent_activity_ceiling, append a big entry → compact_recommended=True."""
    name = _unique_name("act-token-over")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        # Tight ceiling: 10 tokens. summary=4000 chars (max) → ~1000 tokens.
        s = await client.post(
            "/api/sessions",
            json={"project_id": pid, "recent_activity_ceiling_tokens": 10},
        )
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/activity",
            json={"summary": "x" * 4000},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["compact_recommended"] is True
        assert body["recent_ceiling_tokens"] == 10
        assert body["current_recent_tokens"] > 10
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_with_provider_model_computes_total_cost(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """PATCH with 1M+1M tokens + opus → server computes ~$90."""
    from decimal import Decimal

    name = _unique_name("run-cost-opus")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={
                "status": "done",
                "total_input_tokens": 1_000_000,
                "total_output_tokens": 1_000_000,
                "provider": "anthropic",
                "model": "claude-opus-4-7",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert Decimal(str(body["total_cost_usd"])) == Decimal("90.0000")
        assert body["total_input_tokens"] == 1_000_000
        assert body["total_output_tokens"] == 1_000_000
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_client_total_cost_usd_silently_ignored(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """Client-supplied total_cost_usd is dropped — server overwrites or leaves
    column at default. Sending bogus `999.99` + valid token/provider/model →
    server computes the real value, NOT the client's number."""
    from decimal import Decimal

    name = _unique_name("run-cost-override")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={
                "total_input_tokens": 1_000_000,
                "total_output_tokens": 1_000_000,
                "provider": "anthropic",
                "model": "claude-opus-4-7",
                "total_cost_usd": "999.99",  # ← attempted client override.
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert Decimal(str(body["total_cost_usd"])) == Decimal("90.0000")
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_without_provider_model_leaves_cost_at_default(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """No provider/model → no cost computation. Column stays at default (0)."""
    from decimal import Decimal

    name = _unique_name("run-cost-no-provider")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={"total_input_tokens": 500, "total_output_tokens": 500},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert Decimal(str(body["total_cost_usd"])) == Decimal("0.0000")
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_unknown_model_logs_warning_and_succeeds(
    client, scaffold_cleanup, session_fs_cleanup, caplog
) -> None:
    """Unknown (provider, model) → ValueError caught, WARNING log emitted, PATCH 200."""
    import logging
    from decimal import Decimal

    name = _unique_name("run-cost-unknown")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        with caplog.at_level(logging.WARNING, logger="src.routers.sessions"):
            r = await client.patch(
                f"/api/session_runs/{rid}",
                json={
                    "total_input_tokens": 1000,
                    "total_output_tokens": 1000,
                    "provider": "anthropic",
                    "model": "claude-mythical-99",
                },
            )
        assert r.status_code == 200
        body = r.json()
        # Cost stays at default — unknown lookup didn't stamp.
        assert Decimal(str(body["total_cost_usd"])) == Decimal("0.0000")
        assert any(
            "cost lookup failed" in rec.message and rec.levelno == logging.WARNING
            for rec in caplog.records
        )
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_over_budget_sets_warning_and_logs(
    client, scaffold_cleanup, session_fs_cleanup, caplog
) -> None:
    """Set token_budget_per_run=1000 → PATCH with input=1500 → budget_warning=true,
    WARNING log with the 4 structured fields parseable from the message."""
    import logging

    name = _unique_name("run-budget-warn")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post(
            "/api/sessions",
            json={"project_id": pid, "token_budget_per_run": 1000},
        )
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        with caplog.at_level(logging.WARNING, logger="src.routers.sessions"):
            r = await client.patch(
                f"/api/session_runs/{rid}",
                json={"total_input_tokens": 1500, "total_output_tokens": 200},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["budget_warning"] is True

        budget_logs = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "session_runs.budget_warning fired" in rec.message
        ]
        assert len(budget_logs) >= 1
        msg = budget_logs[-1].message
        # 4 structured fields parseable from the formatted message.
        assert f"session_id={sid}" in msg
        assert f"run_id={rid}" in msg
        assert "current=1500" in msg
        assert "budget=1000" in msg
        assert "over_by=500" in msg
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_under_budget_does_not_set_warning(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """input=500 vs budget=1000 → budget_warning stays False."""
    name = _unique_name("run-budget-under")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post(
            "/api/sessions",
            json={"project_id": pid, "token_budget_per_run": 1000},
        )
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={"total_input_tokens": 500},
        )
        assert r.status_code == 200, r.text
        assert r.json()["budget_warning"] is False
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_run_null_budget_never_sets_warning(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """token_budget_per_run NULL → no budget check, even on huge input."""
    name = _unique_name("run-budget-null")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)
        run = await client.post(f"/api/sessions/{sid}/runs", json={})
        rid = run.json()["id"]

        r = await client.patch(
            f"/api/session_runs/{rid}",
            json={"total_input_tokens": 999_999_999},
        )
        assert r.status_code == 200, r.text
        assert r.json()["budget_warning"] is False
    finally:
        await client.delete(f"/api/projects/{pid}")


# =============================================================================
# 9. Kanban #721 — `extra='forbid'` on Session Create-shaped schemas
#    Mirrors the `ConsentGrant` deliberate-action UX pattern (#483):
#    smuggled fields fail loud with 422 instead of silent drop.
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra_field,extra_value",
    [
        ("status", "weird"),
        ("closed_at", "2026-01-01T00:00:00Z"),
    ],
)
async def test_session_create_rejects_server_managed_field_422(
    client, extra_field, extra_value
) -> None:
    """POST /api/sessions with a smuggled server-managed field → 422.

    Locks that `status` and `closed_at` are not settable on create
    (extra=forbid schema semantics).
    """
    resp = await client.post(
        "/api/sessions", json={"project_id": 1, extra_field: extra_value}
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail[0]["loc"] == ["body", extra_field]
    assert "extra" in detail[0]["type"] or "forbid" in detail[0]["type"]


@pytest.mark.asyncio
async def test_post_activity_rejects_extra_field_422(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST /api/sessions/{sid}/activity with a smuggled field → 422."""
    name = _unique_name("act-forbid")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]

    try:
        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        r = await client.post(
            f"/api/sessions/{sid}/activity",
            json={"summary": "hi", "smuggled": "x"},
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail[0]["loc"] == ["body", "smuggled"]
        assert "extra" in detail[0]["type"] or "forbid" in detail[0]["type"]
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_post_heartbeat_rejects_extra_field_422(
    client, scaffold_cleanup, session_fs_cleanup
) -> None:
    """POST /api/session_runs/{rid}/heartbeat with a smuggled field → 422."""
    name = _unique_name("hb-forbid")
    scaffold_cleanup(name)
    p = await client.post("/api/projects", json=_project_create_payload(name))
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    try:
        t = await client.post(
            "/api/tasks",
            json={"project_id": pid, "title": "hb task"},
            headers=headers,
        )
        tid = t.json()["id"]

        s = await client.post("/api/sessions", json={"project_id": pid})
        sid = s.json()["id"]
        session_fs_cleanup(sid)

        run = await client.post(
            f"/api/sessions/{sid}/runs", json={"task_id": tid}
        )
        rid = run.json()["id"]

        r = await client.post(
            f"/api/session_runs/{rid}/heartbeat",
            json={"content": "hi", "smuggled": "x"},
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail[0]["loc"] == ["body", "smuggled"]
        assert "extra" in detail[0]["type"] or "forbid" in detail[0]["type"]

        await client.delete(f"/api/tasks/{tid}", headers=headers)
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_existing_seeded_tasks_untouched(client) -> None:
    """CTX-1 must not perturb the seeded `agent-teams` project's tasks. Spot-
    check: the seeded `agent-teams` project still resolves by-name and has
    tasks listable via the existing endpoint."""
    p = await client.get("/api/projects/by-name/agent-teams")
    assert p.status_code == 200
    pid = p.json()["id"]
    headers = {"X-Project-Id": str(pid)}

    r = await client.get("/api/tasks?limit=5", headers=headers)
    assert r.status_code == 200
    # We don't assert a specific count (other tests may transiently soft-delete
    # rows); we just confirm the contract still works after CTX-1 ships.
    assert isinstance(r.json(), list)
