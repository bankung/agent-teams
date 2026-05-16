"""Empty-DB smoke gate — Kanban #994.

Fresh agent-teams instance with an EMPTY DB (no seeded `agent-teams` project,
zero tasks, zero sessions, zero session_runs) MUST NOT return HTTP 500 on the
endpoints the Web shell hits during first page-load. Each list endpoint
returns `[]`. Each aggregate endpoint returns zero-filled values. The single
detail endpoint `GET /api/projects/by-name/{name}` returns 404 with the
documented stable detail string (the FE catches that 404 → Next.js notFound).

The conftest's session-scoped `_setup_test_database` fixture builds a fresh
`agent_teams_test` DB and runs the seed once. This module's autouse fixture
purges every row via SQLAlchemy ORM `delete()` BEFORE each test and re-seeds
the canonical `agent-teams` project at teardown so sibling test modules in
the same pytest invocation continue to see their expected baseline.

ORM-only purge — no raw SQL DML. Hard constraint from the spawn brief
matches the platform-wide rule: subagents never issue raw INSERT/UPDATE/DELETE
through `psql -c` or ad-hoc Python. SQLAlchemy ORM via pytest fixtures is
explicitly the supported channel.

Coverage:
- `GET /api/projects?status=1`        → `[]`
- `GET /api/projects/stats`           → `[]`
- `GET /api/projects/by-name/<missing>` → 404 with stable detail
- `GET /api/projects/{nonexistent_id}`  → 404
- `GET /api/tasks` (X-Project-Id=999)   → `[]` (project doesn't exist; empty filter result)
- `GET /api/tasks/next-autorun` (X-Project-Id=999) → 200 with all-nullable shape
- `GET /health`                         → 200 (sanity)

The "fresh-DB invariant" rule (documented in `api/tests/README.md`):
every list / stats endpoint MUST handle an empty result set gracefully —
empty array / zero aggregates / 404 — never 500.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from src.db import SessionLocal
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionCompact, SessionRun
from src.models.task import Task, TaskHistory


@pytest_asyncio.fixture(autouse=True)
async def _purge_db_for_empty_smoke():
    """Purge every row via ORM before each test; re-seed `agent-teams` on teardown.

    Order matters — FK dependencies dictate child-first:
      session_compacts -> session_runs -> sessions -> tasks_history -> tasks -> projects

    `session_runs.task_id` is ON DELETE SET NULL so we can drop tasks ahead of
    session_runs in theory, but the conservative child-first sweep keeps the
    fixture robust to future FK additions.

    `tasks_history` is populated by the audit trigger on the `tasks` table.
    A DELETE on `tasks` itself fires the trigger, which would APPEND new
    history rows mid-purge — so we delete `tasks_history` AFTER `tasks` to
    sweep both the pre-existing rows and any rows the trigger emitted during
    the `tasks` delete pass.

    Re-seed at teardown uses the canonical `scripts.seed._seed` coroutine so
    the seed pattern stays single-source-of-truth.
    """
    async with SessionLocal() as session:
        await session.execute(delete(SessionCompact))
        await session.execute(delete(SessionRun))
        await session.execute(delete(SessionModel))
        await session.execute(delete(Task))
        # Trigger appends rows during the `tasks` delete — sweep after.
        await session.execute(delete(TaskHistory))
        await session.execute(delete(Project))
        # Reset SERIAL sequences for every purged table — Kanban #1085.
        # Without this, the teardown re-seed re-inserts `agent-teams` with
        # `projects.id >= 2` (the sequence already advanced past 1 during the
        # live-DB session before this test module ran). Sibling test modules
        # then fail when they hardcode `id=1` for the seeded `agent-teams`
        # project (5 failures: test_routes_smoke, test_run_mode_consent,
        # test_task_kind_recurrence, test_task_type, test_tasks_scheduled_at).
        # `tool_calls_id_seq` is included even though `tool_calls` rows aren't
        # directly purged here — its rows FK-cascade-delete with `tasks`, so
        # they're gone too. ALTER SEQUENCE RESTART is DDL (a sequence reset,
        # not a row mutation) so it doesn't trigger the no-raw-DML constraint.
        for seq in (
            "projects_id_seq",
            "tasks_id_seq",
            "tasks_history_id_seq",
            "sessions_id_seq",
            "session_runs_id_seq",
            "session_compacts_id_seq",
            "tool_calls_id_seq",
        ):
            await session.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH 1"))
        await session.commit()

    yield

    # Teardown — re-seed so sibling test modules see the canonical baseline.
    # The conftest's session-scoped setup runs seed ONCE per pytest invocation;
    # we restore manually after each test in this module.
    from src.db import engine as _engine
    from scripts.seed import _seed

    await _engine.dispose()
    await _seed()
    # Dispose again so the test-level engine-pool reset fixture in conftest
    # starts each subsequent test on a fresh pool. Mirrors the conftest pattern.
    await _engine.dispose()


# ---- Sanity gate: confirm purge fixture actually empties the DB ----


@pytest.mark.asyncio
async def test_purge_fixture_left_empty_db(client) -> None:
    """The purge fixture above MUST land the DB at zero rows in `projects`,
    `tasks`, `sessions`, `session_runs`. Direct count check via raw SELECT
    (read-only — not DML — allowed under the platform rule).
    """
    async with SessionLocal() as session:
        proj_count = (
            await session.execute(text("SELECT count(*) FROM projects"))
        ).scalar_one()
        task_count = (
            await session.execute(text("SELECT count(*) FROM tasks"))
        ).scalar_one()
        sess_count = (
            await session.execute(text("SELECT count(*) FROM sessions"))
        ).scalar_one()
        run_count = (
            await session.execute(text("SELECT count(*) FROM session_runs"))
        ).scalar_one()

    assert proj_count == 0, f"projects not purged: {proj_count} rows remain"
    assert task_count == 0, f"tasks not purged: {task_count} rows remain"
    assert sess_count == 0, f"sessions not purged: {sess_count} rows remain"
    assert run_count == 0, f"session_runs not purged: {run_count} rows remain"


# ---- Surface 1: GET /api/projects?status=1 (dashboard) ----


@pytest.mark.asyncio
async def test_list_projects_status_active_returns_empty_array(client) -> None:
    """Dashboard hits this on every render — empty DB must return `[]`, never 500."""
    resp = await client.get("/api/projects?status=1")
    assert resp.status_code == 200, (
        f"empty-DB GET /api/projects?status=1 returned "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert isinstance(body, list), f"expected list, got {type(body)}: {body!r}"
    assert body == [], f"expected empty list, got {body!r}"


# ---- Surface 1b: GET /api/projects (no filter) ----


@pytest.mark.asyncio
async def test_list_projects_unfiltered_returns_empty_array(client) -> None:
    """Sibling of the status=1 case — bare list endpoint must also degrade
    cleanly when the DB is empty."""
    resp = await client.get("/api/projects")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ---- Surface 2: GET /api/projects/stats (dashboard) ----


@pytest.mark.asyncio
async def test_list_projects_stats_returns_empty_array(client) -> None:
    """`GET /api/projects/stats` powers the dashboard aggregate row. Empty
    DB must return `[]` — NOT 500 from a divide-by-zero or a dereference of
    a `None` aggregate.

    Spawn-brief hypothesis: handlers assume seeded baseline. This test pins
    that the stats endpoint's three-query stitch survives the zero-row case.
    """
    resp = await client.get("/api/projects/stats")
    assert resp.status_code == 200, (
        f"empty-DB GET /api/projects/stats returned "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert isinstance(body, list), f"expected list, got {type(body)}: {body!r}"
    assert body == [], f"expected empty list, got {body!r}"


# ---- Surface 3: GET /api/projects/by-name/{name} (per-project board) ----


@pytest.mark.asyncio
async def test_get_project_by_name_missing_returns_404(client) -> None:
    """The Web `/p/[name]` server component catches 404 → Next.js notFound.
    A 500 here would render the global error boundary instead. Verify the
    documented stable detail string ("Project 'agent-teams' not found").
    """
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 404, (
        f"empty-DB GET /api/projects/by-name/agent-teams returned "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["detail"] == "Project 'agent-teams' not found", body


# ---- Surface 3b: GET /api/projects/{id} (id-based detail) ----


@pytest.mark.asyncio
async def test_get_project_by_id_missing_returns_404(client) -> None:
    """Parity with the by-name case — id-based lookup must 404, never 500."""
    resp = await client.get("/api/projects/1")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"] == "Project id=1 not found", body


# ---- Surface 4: GET /api/tasks (per-project board, X-Project-Id header) ----


@pytest.mark.asyncio
async def test_list_tasks_returns_empty_array_for_unknown_project(client) -> None:
    """`GET /api/tasks` with an X-Project-Id pointing at a non-existent project.

    Empty DB → no row matches Task.project_id == 999 → `[]`. Locks the rule
    that this endpoint doesn't try to validate the project exists (Lead
    bootstrap may legitimately hit /api/tasks for a project that's just
    been soft-deleted; returning `[]` is correct).
    """
    resp = await client.get(
        "/api/tasks?limit=500", headers={"X-Project-Id": "999"}
    )
    assert resp.status_code == 200, (
        f"empty-DB GET /api/tasks returned "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert isinstance(body, list), body
    assert body == []


# (surface 5 absorbed into surface 4 — both cover GET /api/tasks behaviour)
# ---- Surface 6: GET /api/tasks/next-autorun (X-Project-Id header) ----


@pytest.mark.asyncio
async def test_next_autorun_returns_safe_shape_for_unknown_project(client) -> None:
    """`GET /api/tasks/next-autorun` is hit by the headless auto-run loop.

    Empty DB → no candidate, no resume, no questions, zero blocked count.
    The response shape MUST be the full NextAutorunResponse with `null` for
    `next_task` (single optional) and `[]` for the two list fields.
    `blocked_count` MUST be 0 (the scalar_one() on an empty count() returns
    0, not None — this test pins that invariant).
    """
    resp = await client.get(
        "/api/tasks/next-autorun", headers={"X-Project-Id": "999"}
    )
    assert resp.status_code == 200, (
        f"empty-DB GET /api/tasks/next-autorun returned "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["next_task"] is None, body
    assert body["resume_tasks"] == [], body
    assert body["pending_questions"] == [], body
    assert body["blocked_count"] == 0, body


# ---- Surface 7 (sanity): GET /health ----


@pytest.mark.asyncio
async def test_health_endpoint_empty_db(client) -> None:
    """Liveness probe — must not touch the DB, must return 200 with the
    expected envelope even when the DB is empty (or in this case, when the
    purge fixture has just emptied it)."""
    resp = await client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert "env" in body


# ---- Surface: GET /api/sessions (sessions list, no filter) ----


@pytest.mark.asyncio
async def test_list_sessions_returns_empty_array(client) -> None:
    """Sessions endpoint — not on the Web first-page-load surface today, but
    the headless auto-run / session bootstrap path hits it. Empty DB → `[]`.
    Pinned to prevent regression alongside the rest of the empty-DB suite.
    """
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
