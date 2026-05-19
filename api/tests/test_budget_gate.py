"""Kanban #1194 AC4-7 — spawn-time hard cap gate + threshold alerts.

Coverage matrix (AC7):
  1. null cap → allowed, reason="no_cap_configured"
  2. cap=$10, no spend → allowed, used=$0
  3. cap=$0.01, estimate=$0.50 would exceed → NOT allowed
  4. cap=$10, prior $8.50 today → allowed, pct_used=85
  5. cap=$10, prior $11 → projected $11 → NOT allowed
  6. POST /api/tasks task_kind='ai' with project cap=$0.01 → 429
  7. POST /api/tasks with override pair → 201 (allowed + logged)
  8. POST /api/tasks task_kind='human' → always allowed
  9. Threshold alert fires on >=80% crossing
  10. De-dupe: 80% alert fires only once per project per day

Test DB is `agent_teams_test` (per conftest.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.models.task import Task
from src.services import budget_gate
from src.services.budget_gate import (
    BudgetCheckResult,
    check_budget,
    reconcile_budget,
)


# ---------------------------------------------------------------------------
# Helpers — mirror test_budget_enforcer.py shapes
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


async def _set_daily_cap(client, project_id: int, cap: Decimal | None) -> None:
    body = {"budget_daily_usd": None if cap is None else str(cap)}
    resp = await client.patch(f"/api/projects/{project_id}", json=body)
    assert resp.status_code == 200, resp.text


async def _make_task(client, project_id: int, **extras) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": "k1194 fixture", **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    return resp


async def _seed_today_spend(db_session, project_id: int, cost: Decimal) -> int:
    """Insert a completed-today task with the given cost. Returns task id."""
    task = Task(
        project_id=project_id,
        title=f"seed-spend-{uuid.uuid4().hex[:6]}",
        estimated_cost_usd=cost,
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    return task.id


@pytest.fixture(autouse=True)
def _clear_alert_cache():
    """Reset module-level alert de-dupe cache between tests."""
    budget_gate._reset_alert_cache_for_tests()
    yield
    budget_gate._reset_alert_cache_for_tests()


# ---------------------------------------------------------------------------
# 1. Service-level — check_budget logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_cap_returns_no_cap_configured(client, scaffold_cleanup, db_session):
    """AC: budget_daily_usd=NULL → allowed=True, reason='no_cap_configured'."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-null")
    # No cap set — column stays NULL.
    bc = await check_budget(db_session, pid, Decimal("100.00"))
    assert isinstance(bc, BudgetCheckResult)
    assert bc.allowed is True
    assert bc.reason == "no_cap_configured"
    assert bc.cap_daily_usd is None
    assert bc.pct_used is None
    assert bc.used_today_usd == Decimal("0.0000")


@pytest.mark.asyncio
async def test_cap_no_spend_allowed(client, scaffold_cleanup, db_session):
    """AC: cap=$10, no prior spend → allowed, used=$0."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-empty")
    await _set_daily_cap(client, pid, Decimal("10.00"))
    bc = await check_budget(db_session, pid, None)
    assert bc.allowed is True
    assert bc.reason == "ok"
    assert bc.used_today_usd == Decimal("0.0000")
    assert bc.projected_usd == Decimal("0.0000")
    assert bc.pct_used == Decimal("0.0000")


@pytest.mark.asyncio
async def test_estimate_alone_exceeds_tiny_cap(client, scaffold_cleanup, db_session):
    """AC: cap=$0.01, estimate=$0.50 → projected $0.50 > cap → NOT allowed."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-tiny")
    await _set_daily_cap(client, pid, Decimal("0.01"))
    bc = await check_budget(db_session, pid, Decimal("0.50"))
    assert bc.allowed is False
    assert bc.reason == "would_exceed_daily_cap"
    assert bc.projected_usd == Decimal("0.5000")


@pytest.mark.asyncio
async def test_cap_with_85pct_prior_spend(client, scaffold_cleanup, db_session):
    """AC: cap=$10, prior $8.50 today → allowed, pct_used~85."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-85")
    await _set_daily_cap(client, pid, Decimal("10.00"))
    await _seed_today_spend(db_session, pid, Decimal("8.5000"))
    bc = await check_budget(db_session, pid, None)
    assert bc.allowed is True
    assert bc.used_today_usd == Decimal("8.5000")
    assert bc.pct_used == Decimal("85.0000")


@pytest.mark.asyncio
async def test_cap_with_110pct_prior_spend_blocks(client, scaffold_cleanup, db_session):
    """AC: cap=$10, prior $11 → projected $11 > $10 → NOT allowed."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-110")
    await _set_daily_cap(client, pid, Decimal("10.00"))
    await _seed_today_spend(db_session, pid, Decimal("11.0000"))
    bc = await check_budget(db_session, pid, None)
    assert bc.allowed is False
    assert bc.reason == "would_exceed_daily_cap"
    assert bc.pct_used == Decimal("110.0000")


@pytest.mark.asyncio
async def test_unknown_project_raises(db_session):
    with pytest.raises(ValueError, match="project_id=999999 not found"):
        await check_budget(db_session, 999999, Decimal("1.00"))


# ---------------------------------------------------------------------------
# 2. POST /api/tasks integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_ai_task_blocked_by_tiny_cap_429(
    client, scaffold_cleanup, db_session
):
    """AC: POST /api/tasks task_kind='ai' against project with cap=$0.01 + an
    estimate over the cap returns 429 with the documented detail shape."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-post-429")
    await _set_daily_cap(client, pid, Decimal("0.01"))
    resp = await _make_task(
        client,
        pid,
        task_kind="ai",
        estimated_cost_usd="5.00",
    )
    assert resp.status_code == 429, resp.text
    detail = resp.json()["detail"]
    assert detail["reason"] == "would_exceed_daily_cap"
    assert detail["used_today_usd"] == "0.0000"
    assert "override_hint" in detail


@pytest.mark.asyncio
async def test_post_ai_task_with_override_allowed_201(
    client, scaffold_cleanup, db_session
):
    """AC: override pair lets the spawn through."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-post-override")
    await _set_daily_cap(client, pid, Decimal("0.01"))
    resp = await _make_task(
        client,
        pid,
        task_kind="ai",
        estimated_cost_usd="5.00",
        budget_override_authorized_by="operator",
        budget_override_reason="urgent prod hotfix, accept the daily overrun",
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_post_override_pair_required_together_422(
    client, scaffold_cleanup
):
    """Asymmetric override pair → 422 from Pydantic, before reaching the gate."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-post-asym")
    resp = await _make_task(
        client,
        pid,
        budget_override_authorized_by="operator",
        # missing budget_override_reason
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_human_task_bypasses_gate(client, scaffold_cleanup):
    """AC: task_kind='human' (or interaction_kind='question' → coerced to human)
    never hits the gate even with a punishing cap."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-post-human")
    await _set_daily_cap(client, pid, Decimal("0.01"))
    resp = await _make_task(
        client,
        pid,
        task_kind="human",
        run_mode="manual",
        estimated_cost_usd="100.00",
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 3. AC5 — threshold alerts (mocked notification_router.deliver)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_threshold_alert_fires_at_80pct(
    client, scaffold_cleanup, db_session, monkeypatch
):
    """When projected pct crosses 80, deliver() is invoked once."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-alert-80")
    await _set_daily_cap(client, pid, Decimal("10.00"))
    # Attach a telegram target so the alert path actually fires
    # (no targets → suppressed per design).
    await client.patch(
        f"/api/projects/{pid}",
        json={
            "notification_targets": [
                {
                    "kind": "telegram",
                    "chat_id": "test-chat",
                    "priority": 1,
                    "label": "test",
                }
            ]
        },
    )
    # Prior $8 spend, no new estimate → projected $8 / $10 = 80% → alert.
    await _seed_today_spend(db_session, pid, Decimal("8.0000"))

    calls: list[dict] = []

    async def _fake_deliver(*, task_id, payload, kind, session):
        calls.append({"task_id": task_id, "payload": payload, "kind": kind})
        return {"task_id": task_id, "attempts": []}

    monkeypatch.setattr(
        "src.services.notification_router.deliver", _fake_deliver
    )

    bc = await check_budget(db_session, pid, None)
    assert bc.pct_used == Decimal("80.0000")
    assert len(calls) == 1, f"expected 1 notification, got {len(calls)}"
    assert calls[0]["payload"]["event"] == "budget_threshold_80"
    assert calls[0]["payload"]["project_id"] == pid


@pytest.mark.asyncio
async def test_threshold_alert_dedupes_per_project_per_day(
    client, scaffold_cleanup, db_session, monkeypatch
):
    """Two checks in the same day → notification fires once."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-alert-dedupe")
    await _set_daily_cap(client, pid, Decimal("10.00"))
    await client.patch(
        f"/api/projects/{pid}",
        json={
            "notification_targets": [
                {
                    "kind": "telegram",
                    "chat_id": "test-chat",
                    "priority": 1,
                    "label": "test",
                }
            ]
        },
    )
    await _seed_today_spend(db_session, pid, Decimal("8.5000"))

    calls: list[dict] = []

    async def _fake_deliver(*, task_id, payload, kind, session):
        calls.append(payload["event"])
        return {"task_id": task_id, "attempts": []}

    monkeypatch.setattr(
        "src.services.notification_router.deliver", _fake_deliver
    )

    await check_budget(db_session, pid, None)
    await check_budget(db_session, pid, None)
    # Both checks cross 80%, but de-dupe should suppress the second.
    assert calls == ["budget_threshold_80"], f"expected single alert, got {calls}"


# ---------------------------------------------------------------------------
# 4. AC6 — on-demand reconciliation endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_endpoint_returns_current_numbers(
    client, scaffold_cleanup, db_session
):
    pid = await _make_fresh_project(client, scaffold_cleanup, "gate-reconcile")
    await _set_daily_cap(client, pid, Decimal("10.00"))
    await _seed_today_spend(db_session, pid, Decimal("2.5000"))
    resp = await client.post(f"/api/projects/{pid}/reconcile-budget")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_today_usd"] == "2.5000"
    assert body["cap_daily_usd"] == "10.00"
    assert body["pct_used"] == "25.0000"


@pytest.mark.asyncio
async def test_reconcile_endpoint_unknown_project_404(client):
    resp = await client.post("/api/projects/999999/reconcile-budget")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_reconcile_service_unknown_raises(db_session):
    with pytest.raises(ValueError, match="project_id=999999 not found"):
        await reconcile_budget(db_session, 999999)
