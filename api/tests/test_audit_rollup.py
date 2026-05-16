"""Kanban #1082 — GET /api/audit/daily-rollup cross-project auditor rollup.

Aggregates `tasks.audit_report` (migration 0030) into per-(project, day)
buckets. Verdict → bucket mapping pinned in `src.schemas.audit` module
docstring; tests below lock the behavior so any drift fires a fast
regression.

Test data is built ORM-side via the `db_session` fixture (no API surface
writes `audit_report` directly — it's set by `langgraph/nodes.py::auditor_node`
on the worker path). ORM `INSERT`s via SQLAlchemy session are explicitly the
supported channel (the platform's no-raw-SQL-DML rule applies to `psql -c`
and ad-hoc Python scripts, not pytest fixtures).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.constants import RecordStatus, TaskStatus
from src.db import SessionLocal
from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionCompact, SessionRun
from src.models.task import Task, TaskHistory


# ---- fixtures: scoped DB purge so each test starts from a known baseline ---


@pytest_asyncio.fixture
async def empty_db():
    """Purge every row before the test; restore canonical seed after.

    Audit rollup tests assert exact bucket counts — a seeded `agent-teams`
    project with zero audit_report rows wouldn't break the assertions
    today, but a future seed addition might. Re-using the empty-DB pattern
    from `test_empty_db_smoke.py` keeps every test isolated AND the seed
    canonical for sibling modules.

    Child-first FK sweep: session_compacts → session_runs → sessions →
    tasks → tasks_history (audit-trigger emits rows during tasks delete,
    so sweep after) → projects.
    """
    async with SessionLocal() as session:
        await session.execute(delete(SessionCompact))
        await session.execute(delete(SessionRun))
        await session.execute(delete(SessionModel))
        await session.execute(delete(Task))
        await session.execute(delete(TaskHistory))
        await session.execute(delete(Project))
        await session.commit()

    yield

    # Teardown — re-seed for sibling test modules.
    from src.db import engine as _engine
    from scripts.seed import _seed

    await _engine.dispose()
    await _seed()
    await _engine.dispose()


def _utc(year: int, month: int, day_n: int, hour: int = 12) -> datetime:
    """Helper: build a UTC datetime at hour 12 (avoids midnight TZ edge cases)."""
    return datetime(year, month, day_n, hour, 0, 0, tzinfo=timezone.utc)


def _audit_report(
    verdict: str,
    *,
    severity: str = "info",
    action: str | None = None,
) -> dict:
    """Build a minimal but realistic audit_report JSONB blob.

    Mirrors the shape `langgraph/nodes.py::_build_pass_report` and
    `_normalise_llm_verdict` emit. Tests don't depend on the full shape —
    only `verdict` is read by the rollup endpoint — but matching the
    contract keeps the fixtures forward-compatible if the SQL gains more
    extracted fields.
    """
    return {
        "verdict": verdict,
        "severity": severity,
        "evidence": [],
        "action_taken": action or f"auto_{verdict}",
        "escalation_payload": None,
        "llm_skipped": False,
        "audited_at": "2026-05-17T00:00:00Z",
        "retry_count_at_audit": 0,
    }


def _project_name(slug: str) -> str:
    """Generate a unique-per-test project name."""
    return f"k1082-{slug}-{uuid.uuid4().hex[:6]}"


async def _make_project(name: str | None = None, *, team: str = "dev") -> Project:
    """Insert a project via ORM. Returns the persisted row with id populated."""
    async with SessionLocal() as session:
        project = Project(
            name=name or _project_name("p"),
            description="audit rollup test fixture",
            paths_web="/tmp/x/web",
            paths_api="/tmp/x/api",
            paths_db="/tmp/x/db",
            stack_web="nextjs",
            stack_api="fastapi",
            stack_db="postgres",
            config={},
            is_active=False,
            team=team,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def _make_task(
    project_id: int,
    *,
    title: str = "audit fixture task",
    process_status: int = TaskStatus.TODO,
    audit_report: dict | None = None,
    halt_reason: str | None = None,
    updated_at: datetime | None = None,
    status: int = RecordStatus.ACTIVE,
) -> Task:
    """Insert a task with audit-relevant fields populated.

    `updated_at` is overridden post-insert (the server_default fires on
    INSERT — we explicitly UPDATE it to land the row in a specific window
    bucket for the rollup query).
    """
    async with SessionLocal() as session:
        task = Task(
            project_id=project_id,
            title=title,
            process_status=process_status,
            audit_report=audit_report,
            halt_reason=halt_reason,
            status=status,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        if updated_at is not None:
            # Pin the row to a specific calendar day — the rollup query
            # groups by `date_trunc('day', updated_at)`.
            task.updated_at = updated_at
            await session.commit()
            await session.refresh(task)

        return task


# ---- 1. empty DB returns empty list -----------------------------------------


@pytest.mark.asyncio
async def test_empty_db_returns_empty_list(client, empty_db) -> None:
    """No projects, no tasks — endpoint returns `[]` with status 200 (NOT
    204). Mirrors the fresh-DB invariant from `test_empty_db_smoke.py`.
    """
    resp = await client.get("/api/audit/daily-rollup")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list), body
    assert body == [], body


# ---- 2. mixed verdicts aggregate correctly ----------------------------------


@pytest.mark.asyncio
async def test_mixed_verdicts_aggregate_correctly(client, empty_db) -> None:
    """5-10 tasks across 2 projects + 3 days; verify per-day counts match.

    Layout:
      Project A, day D0:  2 pass, 1 auto_resolve     → pass=2, auto_resolved=1
      Project A, day D1:  1 escalate+DONE            → escalated=1
      Project B, day D1:  1 escalate+TODO            → pending_escalation=1
      Project B, day D2:  1 giveup, 1 pass           → failed_giveup=1, pass=1

    Total: 8 tasks across 4 distinct (project, day) buckets.
    """
    today = date.today()
    d0 = today - timedelta(days=2)
    d1 = today - timedelta(days=1)
    d2 = today

    proj_a = await _make_project(_project_name("mix-a"))
    proj_b = await _make_project(_project_name("mix-b"))

    # Project A day D0 — 2 pass, 1 auto_resolve
    await _make_task(
        proj_a.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(d0.year, d0.month, d0.day),
    )
    await _make_task(
        proj_a.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(d0.year, d0.month, d0.day),
    )
    await _make_task(
        proj_a.id,
        audit_report=_audit_report("auto_resolve"),
        updated_at=_utc(d0.year, d0.month, d0.day),
    )

    # Project A day D1 — 1 escalate that the operator resolved (DONE)
    await _make_task(
        proj_a.id,
        process_status=TaskStatus.DONE,
        audit_report=_audit_report("escalate"),
        updated_at=_utc(d1.year, d1.month, d1.day),
    )

    # Project B day D1 — 1 escalate still pending (TODO)
    await _make_task(
        proj_b.id,
        process_status=TaskStatus.TODO,
        audit_report=_audit_report("escalate"),
        updated_at=_utc(d1.year, d1.month, d1.day),
    )

    # Project B day D2 — 1 giveup, 1 pass
    await _make_task(
        proj_b.id,
        process_status=TaskStatus.BLOCKED,
        audit_report=_audit_report("auto_resolve"),
        halt_reason="auditor_giveup",
        updated_at=_utc(d2.year, d2.month, d2.day),
    )
    await _make_task(
        proj_b.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(d2.year, d2.month, d2.day),
    )

    resp = await client.get("/api/audit/daily-rollup")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Index by (project_id, day) for easier assertions.
    by_key = {(e["project_id"], e["day"]): e for e in body}
    assert len(body) == 4, body

    # Project A, D0
    a_d0 = by_key[(proj_a.id, d0.isoformat())]
    assert a_d0["project_name"] == proj_a.name
    assert a_d0["counts"] == {
        "pass": 2,
        "auto_resolved": 1,
        "escalated": 0,
        "failed_giveup": 0,
        "pending_escalation": 0,
    }, a_d0

    # Project A, D1
    a_d1 = by_key[(proj_a.id, d1.isoformat())]
    assert a_d1["counts"] == {
        "pass": 0,
        "auto_resolved": 0,
        "escalated": 1,
        "failed_giveup": 0,
        "pending_escalation": 0,
    }, a_d1

    # Project B, D1
    b_d1 = by_key[(proj_b.id, d1.isoformat())]
    assert b_d1["counts"] == {
        "pass": 0,
        "auto_resolved": 0,
        "escalated": 0,
        "failed_giveup": 0,
        "pending_escalation": 1,
    }, b_d1

    # Project B, D2
    b_d2 = by_key[(proj_b.id, d2.isoformat())]
    assert b_d2["counts"] == {
        "pass": 1,
        "auto_resolved": 0,
        "escalated": 0,
        "failed_giveup": 1,
        "pending_escalation": 0,
    }, b_d2


# ---- 3. out-of-range excluded -----------------------------------------------


@pytest.mark.asyncio
async def test_out_of_range_excluded(client, empty_db) -> None:
    """A task whose `updated_at` is outside `[from, to]` must NOT appear,
    even when its audit_report is non-null and verdict is recognized.

    Build two tasks:
      - one INSIDE the window (day D0 = from+1)
      - one OUTSIDE the window (day from-3)
    Query with explicit `from`/`to` and verify only the inside task surfaces.
    """
    today = date.today()
    from_date = today - timedelta(days=2)
    to_date = today
    inside_day = today - timedelta(days=1)
    outside_day = today - timedelta(days=5)

    proj = await _make_project(_project_name("range"))

    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(inside_day.year, inside_day.month, inside_day.day),
    )
    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(outside_day.year, outside_day.month, outside_day.day),
    )

    resp = await client.get(
        f"/api/audit/daily-rollup?from={from_date.isoformat()}"
        f"&to={to_date.isoformat()}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Exactly one (project, day) entry — for the inside day.
    assert len(body) == 1, body
    entry = body[0]
    assert entry["day"] == inside_day.isoformat()
    assert entry["counts"]["pass"] == 1
    # Defensive — other buckets are zero.
    assert entry["counts"]["auto_resolved"] == 0
    assert entry["counts"]["escalated"] == 0
    assert entry["counts"]["failed_giveup"] == 0
    assert entry["counts"]["pending_escalation"] == 0


# ---- 4. default window is 7 days --------------------------------------------


@pytest.mark.asyncio
async def test_default_window_is_7_days(client, empty_db) -> None:
    """No query params → window defaults to `today - 7 days ... today` (UTC).

    Build three tasks:
      - day = today                (inside)
      - day = today - 7 days       (inside, lower boundary)
      - day = today - 8 days       (outside)
    Call WITHOUT `from`/`to` and verify only the two inside tasks surface.
    """
    today = date.today()
    lower_bound = today - timedelta(days=7)
    outside_day = today - timedelta(days=8)

    proj = await _make_project(_project_name("default"))

    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(today.year, today.month, today.day, hour=10),
    )
    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(lower_bound.year, lower_bound.month, lower_bound.day),
    )
    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(outside_day.year, outside_day.month, outside_day.day),
    )

    resp = await client.get("/api/audit/daily-rollup")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Exactly 2 (project, day) entries — today and lower_bound, NOT outside_day.
    days = sorted(e["day"] for e in body)
    assert days == sorted([today.isoformat(), lower_bound.isoformat()]), body
    # Every entry pass=1 — sanity on the bucket mapping.
    for entry in body:
        assert entry["counts"]["pass"] == 1, entry


# ---- 5. from > to returns 422 ------------------------------------------------


@pytest.mark.asyncio
async def test_from_after_to_returns_422(client, empty_db) -> None:
    """Inverted window must reject with 422 + stable detail.

    Uses an explicit `from` later than `to`. The stable detail string is
    locked here so future Pydantic upgrades don't silently re-word it.
    """
    today = date.today()
    resp = await client.get(
        f"/api/audit/daily-rollup?from={today.isoformat()}"
        f"&to={(today - timedelta(days=1)).isoformat()}"
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"] == "from must be <= to", body


# ---- 6. pending_escalation distinct from escalated --------------------------


@pytest.mark.asyncio
async def test_pending_escalation_distinct_from_escalated(client, empty_db) -> None:
    """`verdict='escalate'` splits into two buckets based on process_status:
      - process_status=DONE (5) → `escalated` (operator resolved)
      - process_status in TODO/IN_PROGRESS/REVIEW/BLOCKED → `pending_escalation`
        (operator hasn't decided yet)

    Locks the dominant-state semantics: a `pending_escalation` row that later
    flips to DONE would land in `escalated` on the next rollup — that's the
    intended pipeline. This test exercises both states ON THE SAME day in
    the same project so the row gets a single bucket entry split between
    `escalated` and `pending_escalation`.
    """
    today = date.today()
    proj = await _make_project(_project_name("pend"))

    # 1 escalate row that's been resolved (DONE).
    await _make_task(
        proj.id,
        process_status=TaskStatus.DONE,
        audit_report=_audit_report("escalate"),
        updated_at=_utc(today.year, today.month, today.day),
    )
    # 1 escalate row still in TODO.
    await _make_task(
        proj.id,
        process_status=TaskStatus.TODO,
        audit_report=_audit_report("escalate"),
        updated_at=_utc(today.year, today.month, today.day),
    )
    # 1 escalate row still BLOCKED.
    await _make_task(
        proj.id,
        process_status=TaskStatus.BLOCKED,
        audit_report=_audit_report("escalate"),
        updated_at=_utc(today.year, today.month, today.day),
    )

    resp = await client.get("/api/audit/daily-rollup")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1, body
    entry = body[0]
    assert entry["counts"]["escalated"] == 1, entry  # the DONE one
    assert entry["counts"]["pending_escalation"] == 2, entry  # the TODO + BLOCKED ones
    # Sanity: other buckets zero.
    assert entry["counts"]["pass"] == 0
    assert entry["counts"]["auto_resolved"] == 0
    assert entry["counts"]["failed_giveup"] == 0


# ---- 7. soft-deleted tasks excluded -----------------------------------------


@pytest.mark.asyncio
async def test_soft_deleted_tasks_excluded(client, empty_db) -> None:
    """Tasks with `status=0` (soft-deleted) MUST NOT appear in the rollup,
    even when audit_report is non-null. Mirrors the soft-delete exclusion
    pattern in `/api/projects/stats`.
    """
    today = date.today()
    proj = await _make_project(_project_name("softdel"))

    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(today.year, today.month, today.day),
        status=RecordStatus.DELETED,
    )

    resp = await client.get("/api/audit/daily-rollup")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [], body


# ---- 8. audit_report NULL excluded ------------------------------------------


@pytest.mark.asyncio
async def test_audit_report_null_excluded(client, empty_db) -> None:
    """Tasks without an audit_report row (no auditor pass yet) MUST NOT
    appear. This is the dominant case for tasks pre-dating the auditor
    rollout (migration 0030, Kanban #952) — every row in the DB at that
    moment has `audit_report IS NULL`.
    """
    today = date.today()
    proj = await _make_project(_project_name("nullrep"))

    # No audit_report.
    await _make_task(
        proj.id,
        audit_report=None,
        updated_at=_utc(today.year, today.month, today.day),
    )
    # Sanity: one row WITH audit_report so the assertion isn't vacuous.
    await _make_task(
        proj.id,
        audit_report=_audit_report("pass"),
        updated_at=_utc(today.year, today.month, today.day),
    )

    resp = await client.get("/api/audit/daily-rollup")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1, body
    # Only the audit_report=non-null row counts.
    assert body[0]["counts"]["pass"] == 1, body
