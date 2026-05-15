"""Kanban #951 — per-project budget enforcement.

Coverage matrix (AC#7 — all bullets):
  - test_null_budget_unlimited            → NULL caps → no warn / no halt regardless
  - test_soft_warn_threshold              → daily 85% → soft_warn, no halt
  - test_hard_halt_threshold              → monthly 105% → hard_halt + exceeded_cap='monthly'
  - test_manual_bypass                    → run_mode='manual' never appears in next-autorun (implicit bypass)
  - test_compute_spend_double_count_avoidance → linked session_run shadows task estimate
  - test_compute_spend_daily_window       → since=midnight excludes yesterday's spend
  - test_pct_exact_100_is_soft_warn       → boundary: exactly 100% spent = soft band, not halt
  - test_total_cap_priority_over_monthly_and_daily → all three over → exceeded_cap='total'
  - test_check_budget_unknown_project_raises → ValueError surfaced
  - test_next_autorun_hard_halt_stamps_halt_reason → integration via HTTP

Test DB is `agent_teams_test` (per conftest.py). All HTTP fixtures route through
the FastAPI ASGI app via the shared `client` fixture; direct DB writes via
`db_session` fixture for session_runs / costs (no public POST endpoint for cost
columns).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.models.project import Project
from src.models.session import Session as SessionModel
from src.models.session import SessionRun
from src.models.task import Task
from src.services.budget_enforcer import (
    BudgetVerdict,
    check_budget,
    compute_spend,
)


# ---------------------------------------------------------------------------
# Helpers — mirror test_tasks_next_autorun.py + test_task_cost_estimator.py
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"test fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str, **extras) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_project_budget(
    client,
    project_id: int,
    *,
    daily: Decimal | None | str = "_unset",
    monthly: Decimal | None | str = "_unset",
    total: Decimal | None | str = "_unset",
) -> dict:
    """PATCH cap fields. Sentinel `_unset` means "don't send"; pass None to clear."""
    body: dict = {}
    if daily != "_unset":
        body["budget_daily_usd"] = (
            None if daily is None else str(daily)
        )
    if monthly != "_unset":
        body["budget_monthly_usd"] = None if monthly is None else str(monthly)
    if total != "_unset":
        body["budget_total_usd"] = None if total is None else str(total)
    resp = await client.patch(f"/api/projects/{project_id}", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _set_task_cost(
    db_session, task_id: int, cost_usd: Decimal, completed_at: datetime | None = None
) -> None:
    """Directly write `estimated_cost_usd` (+ optional completed_at) on a task row.

    No public POST endpoint accepts these — they're server-computed on done-flip.
    The done-flip path is exercised by test_task_cost_estimator.py; here we set
    the fields directly to keep test fixtures focused on budget arithmetic.
    """
    task = await db_session.get(Task, task_id)
    assert task is not None, f"task_id={task_id} not found"
    task.estimated_cost_usd = cost_usd
    if completed_at is not None:
        task.completed_at = completed_at
    await db_session.commit()


async def _make_session_run(
    db_session,
    project_id: int,
    *,
    task_id: int | None = None,
    cost_usd: Decimal,
    created_at: datetime | None = None,
) -> int:
    """Create a session + session_run with the given cost. Returns run id."""
    # Sessions are project-scoped; one session per call is fine.
    sess = SessionModel(
        project_id=project_id,
        status="active",
        session_root_path=f"/tmp/sessions/{uuid.uuid4().hex[:8]}",
    )
    db_session.add(sess)
    await db_session.flush()  # populate sess.id

    run = SessionRun(
        session_id=sess.id,
        task_id=task_id,
        status="done",
        total_input_tokens=0,
        total_output_tokens=0,
        total_context_chars=0,
        total_cost_usd=cost_usd,
    )
    db_session.add(run)
    await db_session.flush()
    if created_at is not None:
        run.created_at = created_at
    await db_session.commit()
    return run.id


# ---------------------------------------------------------------------------
# Tests — Pydantic / schema boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_create_accepts_budget_caps_and_read_exposes(
    client, scaffold_cleanup
):
    """Round-trip: POST sets caps, GET returns them as decimal strings."""
    name = scaffold_cleanup(f"budget-create-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": "test",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    # Caps default to None on create (we didn't pass them).
    assert resp.json()["budget_daily_usd"] is None
    assert resp.json()["budget_monthly_usd"] is None
    assert resp.json()["budget_total_usd"] is None

    patched = await _patch_project_budget(
        client, pid, daily=Decimal("5.00"), monthly=Decimal("50.00"), total=Decimal("500.00")
    )
    assert patched["budget_daily_usd"] == "5.00"
    assert patched["budget_monthly_usd"] == "50.00"
    assert patched["budget_total_usd"] == "500.00"


@pytest.mark.asyncio
async def test_project_update_rejects_negative_cap_with_422(client, scaffold_cleanup):
    """ProjectUpdate ge=0 fires before reaching DB CHECK."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-neg")
    resp = await client.patch(
        f"/api/projects/{pid}",
        json={"budget_daily_usd": "-1.00"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_project_update_clears_cap_with_null(client, scaffold_cleanup):
    """Explicit null PATCH clears the cap back to unlimited."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-clear")
    await _patch_project_budget(client, pid, daily=Decimal("10.00"))
    cleared = await _patch_project_budget(client, pid, daily=None)
    assert cleared["budget_daily_usd"] is None


# ---------------------------------------------------------------------------
# Tests — check_budget / compute_spend service-level (AC #2, #3, #7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_budget_unlimited(client, scaffold_cleanup, db_session):
    """AC: NULL caps → soft_warn=False, hard_halt=False regardless of spend."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-null")
    # No PATCH — caps stay NULL.
    # Add a $999 task to confirm even a big spend doesn't fire when caps are NULL.
    task = await _make_task(client, pid, "expensive task")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("999.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    verdict = await check_budget(db_session, pid)
    assert isinstance(verdict, BudgetVerdict)
    assert verdict.soft_warn is False
    assert verdict.hard_halt is False
    assert verdict.exceeded_cap is None
    assert verdict.daily_pct == Decimal("0")
    assert verdict.monthly_pct == Decimal("0")
    assert verdict.total_pct == Decimal("0")


@pytest.mark.asyncio
async def test_soft_warn_threshold(client, scaffold_cleanup, db_session):
    """AC: 85% of daily cap → soft_warn=True, hard_halt=False."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-soft")
    await _patch_project_budget(client, pid, daily=Decimal("10.00"))
    # Burn $8.50 today → 85% of $10 daily cap.
    task = await _make_task(client, pid, "today's task")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("8.5000"),
        completed_at=datetime.now(timezone.utc),
    )
    verdict = await check_budget(db_session, pid)
    assert verdict.soft_warn is True
    assert verdict.hard_halt is False
    assert verdict.exceeded_cap is None
    # 85% within 4-place quantize.
    assert verdict.daily_pct == Decimal("85.0000")


@pytest.mark.asyncio
async def test_hard_halt_threshold(client, scaffold_cleanup, db_session):
    """AC: 105% of monthly cap → hard_halt=True, exceeded_cap='monthly'."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-hard")
    await _patch_project_budget(client, pid, monthly=Decimal("20.00"))
    # Burn $21 this month → 105% of $20 monthly cap.
    task = await _make_task(client, pid, "this month's task")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("21.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    verdict = await check_budget(db_session, pid)
    assert verdict.hard_halt is True
    assert verdict.exceeded_cap == "monthly"
    assert verdict.monthly_pct == Decimal("105.0000")


@pytest.mark.asyncio
async def test_pct_exact_100_is_soft_warn(client, scaffold_cleanup, db_session):
    """Boundary: exactly 100% spent → soft band (>80 AND <=100), NOT halt."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-100pct")
    await _patch_project_budget(client, pid, daily=Decimal("10.00"))
    task = await _make_task(client, pid, "full burn")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("10.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    verdict = await check_budget(db_session, pid)
    assert verdict.soft_warn is True
    assert verdict.hard_halt is False
    assert verdict.daily_pct == Decimal("100.0000")


@pytest.mark.asyncio
async def test_pct_exact_80_is_neither(client, scaffold_cleanup, db_session):
    """Boundary: exactly 80% → not in soft band (strict >80)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-80pct")
    await _patch_project_budget(client, pid, daily=Decimal("10.00"))
    task = await _make_task(client, pid, "80pct burn")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("8.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    verdict = await check_budget(db_session, pid)
    assert verdict.soft_warn is False
    assert verdict.hard_halt is False


@pytest.mark.asyncio
async def test_total_cap_priority_over_monthly_and_daily(
    client, scaffold_cleanup, db_session
):
    """When all 3 caps exceed 100%, exceeded_cap is 'total' (priority)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-priority")
    await _patch_project_budget(
        client,
        pid,
        daily=Decimal("1.00"),
        monthly=Decimal("1.00"),
        total=Decimal("1.00"),
    )
    task = await _make_task(client, pid, "blows all caps")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("5.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    verdict = await check_budget(db_session, pid)
    assert verdict.hard_halt is True
    assert verdict.exceeded_cap == "total"


@pytest.mark.asyncio
async def test_check_budget_unknown_project_raises(db_session):
    """Unknown project_id → ValueError (caller decides whether to 404)."""
    with pytest.raises(ValueError, match="project_id=999999 not found"):
        await check_budget(db_session, 999999)


# ---------------------------------------------------------------------------
# Tests — compute_spend window + double-count avoidance (AC #2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_spend_double_count_avoidance(
    client, scaffold_cleanup, db_session
):
    """A task with BOTH a linked session_run AND an `estimated_cost_usd`
    counts only the session_run total (real metering shadows heuristic)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-dbl")
    task = await _make_task(client, pid, "double-counted task")
    # Heuristic estimate: $3
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("3.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    # Real metering: $5 — should shadow the $3 heuristic.
    await _make_session_run(
        db_session,
        pid,
        task_id=task["id"],
        cost_usd=Decimal("5.0000"),
    )
    spend = await compute_spend(db_session, pid, since=None)
    # If we double-counted we'd see $8; correct answer is $5 (session_run only).
    assert spend == Decimal("5.0000")


@pytest.mark.asyncio
async def test_compute_spend_daily_window(client, scaffold_cleanup, db_session):
    """Two tasks: yesterday $5 + today $3 → spend(since=midnight) = $3 only."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-window")
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = midnight - timedelta(hours=2)  # well before midnight
    today_after = midnight + timedelta(hours=1)  # safely inside today's window

    t_yest = await _make_task(client, pid, "yesterday task")
    await _set_task_cost(
        db_session,
        t_yest["id"],
        Decimal("5.0000"),
        completed_at=yesterday,
    )
    t_today = await _make_task(client, pid, "today task")
    await _set_task_cost(
        db_session,
        t_today["id"],
        Decimal("3.0000"),
        completed_at=today_after,
    )
    # Since=midnight → only today's $3.
    spend_today = await compute_spend(db_session, pid, since=midnight)
    assert spend_today == Decimal("3.0000")
    # Since=None → lifetime; both count → $8.
    spend_lifetime = await compute_spend(db_session, pid, since=None)
    assert spend_lifetime == Decimal("8.0000")


@pytest.mark.asyncio
async def test_compute_spend_excludes_soft_deleted_tasks(
    client, scaffold_cleanup, db_session
):
    """Soft-deleted tasks don't count toward spend."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-soft-del")
    task = await _make_task(client, pid, "to be soft-deleted")
    await _set_task_cost(
        db_session,
        task["id"],
        Decimal("10.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    # Pre-soft-delete: spend $10
    pre = await compute_spend(db_session, pid, since=None)
    assert pre == Decimal("10.0000")
    # Soft-delete the task via API.
    headers = {"X-Project-Id": str(pid)}
    del_resp = await client.delete(f"/api/tasks/{task['id']}", headers=headers)
    assert del_resp.status_code in (200, 204), del_resp.text
    # Post-soft-delete: spend $0
    post = await compute_spend(db_session, pid, since=None)
    assert post == Decimal("0.0000")


@pytest.mark.asyncio
async def test_compute_spend_isolated_per_project(
    client, scaffold_cleanup, db_session
):
    """A task in project A doesn't contribute to project B's spend."""
    pid_a = await _make_fresh_project(client, scaffold_cleanup, "budget-iso-a")
    pid_b = await _make_fresh_project(client, scaffold_cleanup, "budget-iso-b")
    t_a = await _make_task(client, pid_a, "project A task")
    await _set_task_cost(
        db_session,
        t_a["id"],
        Decimal("7.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    assert await compute_spend(db_session, pid_a, since=None) == Decimal("7.0000")
    assert await compute_spend(db_session, pid_b, since=None) == Decimal("0.0000")


# ---------------------------------------------------------------------------
# Tests — enforcement hook on next-autorun (AC #3, #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_autorun_hard_halt_stamps_halt_reason(
    client, scaffold_cleanup, db_session
):
    """Over-cap project + runnable auto_pickup task → next_task=None +
    candidate row gets halt_reason='budget_exceeded:<cap>'."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-gate")
    await _patch_project_budget(client, pid, daily=Decimal("1.00"))
    # Burn $10 today → 1000% of $1 cap, definitely over.
    burn_task = await _make_task(client, pid, "already-burned task")
    await _set_task_cost(
        db_session,
        burn_task["id"],
        Decimal("10.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    # Now add a runnable auto_pickup task — the gate should refuse it.
    runnable = await _make_task(
        client, pid, "wants to run", run_mode="auto_pickup"
    )

    headers = {"X-Project-Id": str(pid)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["next_task"] is None

    # Re-fetch the runnable task — halt_reason should be stamped.
    detail = await client.get(f"/api/tasks/{runnable['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["halt_reason"] == "budget_exceeded:daily"


@pytest.mark.asyncio
async def test_next_autorun_soft_warn_proceeds_without_halt_reason(
    client, scaffold_cleanup, db_session
):
    """At 85% of cap, next_task IS returned and halt_reason stays NULL."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-soft-gate")
    await _patch_project_budget(client, pid, daily=Decimal("10.00"))
    burn_task = await _make_task(client, pid, "burned $8.50")
    await _set_task_cost(
        db_session,
        burn_task["id"],
        Decimal("8.5000"),
        completed_at=datetime.now(timezone.utc),
    )
    runnable = await _make_task(
        client, pid, "still allowed to run", run_mode="auto_pickup"
    )

    headers = {"X-Project-Id": str(pid)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Soft warn: still pick up the task.
    assert body["next_task"] is not None
    assert body["next_task"]["id"] == runnable["id"]

    detail = await client.get(f"/api/tasks/{runnable['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["halt_reason"] is None


@pytest.mark.asyncio
async def test_manual_bypass_via_next_autorun(client, scaffold_cleanup, db_session):
    """AC #4: a run_mode='manual' task is NEVER returned by next-autorun
    regardless of budget state — bypass is implicit at the gate layer."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-manual")
    await _patch_project_budget(client, pid, daily=Decimal("1.00"))
    burn = await _make_task(client, pid, "burned")
    await _set_task_cost(
        db_session,
        burn["id"],
        Decimal("10.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    # Create a manual task — next-autorun should ignore it entirely.
    manual = await _make_task(
        client, pid, "manual mode task", run_mode="manual"
    )

    headers = {"X-Project-Id": str(pid)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["next_task"] is None

    # Manual task's halt_reason should NOT be stamped (it never reached the gate).
    detail = await client.get(f"/api/tasks/{manual['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["halt_reason"] is None
    assert detail.json()["run_mode"] == "manual"


@pytest.mark.asyncio
async def test_next_autorun_unlimited_project_unaffected(
    client, scaffold_cleanup, db_session
):
    """NULL caps + huge spend → next-autorun returns the task unhalted."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "budget-unlim")
    burn = await _make_task(client, pid, "burned a fortune")
    await _set_task_cost(
        db_session,
        burn["id"],
        Decimal("9999.0000"),
        completed_at=datetime.now(timezone.utc),
    )
    runnable = await _make_task(
        client, pid, "still runnable", run_mode="auto_pickup"
    )

    headers = {"X-Project-Id": str(pid)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["next_task"] is not None
    assert resp.json()["next_task"]["id"] == runnable["id"]
