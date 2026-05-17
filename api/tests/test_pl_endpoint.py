"""HTTP-level contract tests for GET /api/projects/{id}/pl (Kanban #953).

Coverage:
- Empty project → zero summary with correct shape.
- Single revenue txn → reflected in summary.
- ?period= honored (monthly default).
- ?since / ?until honored.
- since > until → 422.
- X-Project-Id mismatch → 404.
- Multi-currency → per-currency buckets.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _txn_payload(
    project_id: int, *, amount_minor=10000, currency="USD", kind="revenue",
    occurred_at: datetime | None = None,
) -> dict:
    return {
        "project_id": project_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "kind": kind,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
    }


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# =============================================================================
# Happy paths
# =============================================================================


@pytest.mark.asyncio
async def test_pl_endpoint_empty_project_returns_zero_summary(
    client, scaffold_cleanup
):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-empty")
    resp = await client.get(
        f"/api/projects/{project}/pl?period=monthly",
        headers={"X-Project-Id": str(project)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "monthly"
    assert body["currency"] == "USD"  # fresh project default
    assert Decimal(body["revenue"]) == Decimal(0)
    assert Decimal(body["net"]) == Decimal(0)
    assert body["transaction_count"] == 0
    assert body["buckets"] == []


@pytest.mark.asyncio
async def test_pl_endpoint_single_revenue_reflects_in_summary(
    client, scaffold_cleanup
):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-rev")
    headers = {"X-Project-Id": str(project)}
    await client.post(
        "/api/transactions",
        json=_txn_payload(project, amount_minor=10000, kind="revenue"),
        headers=headers,
    )
    resp = await client.get(
        f"/api/projects/{project}/pl?period=monthly", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["revenue"]) == Decimal("100.0000")
    assert Decimal(body["net"]) == Decimal("100.0000")
    assert body["transaction_count"] == 1
    assert len(body["buckets"]) == 1


@pytest.mark.asyncio
async def test_pl_endpoint_default_period_is_monthly(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-default")
    resp = await client.get(
        f"/api/projects/{project}/pl", headers={"X-Project-Id": str(project)}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["period"] == "monthly"


@pytest.mark.asyncio
async def test_pl_endpoint_period_daily_groups_by_day(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-daily")
    headers = {"X-Project-Id": str(project)}
    base = datetime.now(timezone.utc) - timedelta(days=2)
    for n in range(3):
        await client.post(
            "/api/transactions",
            json=_txn_payload(
                project,
                amount_minor=1000,
                kind="revenue",
                occurred_at=base + timedelta(days=n),
            ),
            headers=headers,
        )
    resp = await client.get(
        f"/api/projects/{project}/pl?period=daily", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["buckets"]) == 3


@pytest.mark.asyncio
async def test_pl_endpoint_since_until_filter_honored(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-window")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for n in range(5):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    since = (base + timedelta(days=1)).isoformat()
    until = (base + timedelta(days=4)).isoformat()
    # Use params= dict so httpx URL-encodes `+00:00` correctly; literal `+`
    # in an f-string query gets decoded as space by the server (HTTP form rule).
    resp = await client.get(
        f"/api/projects/{project}/pl",
        params={"period": "daily", "since": since, "until": until},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # since inclusive, until exclusive → days 1, 2, 3.
    assert resp.json()["transaction_count"] == 3


@pytest.mark.asyncio
async def test_pl_endpoint_since_after_until_returns_422(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-bad-window")
    headers = {"X-Project-Id": str(project)}
    since = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    until = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    resp = await client.get(
        f"/api/projects/{project}/pl?since={since}&until={until}", headers=headers
    )
    assert resp.status_code == 422, resp.text


# =============================================================================
# Cross-project + 404
# =============================================================================


@pytest.mark.asyncio
async def test_pl_endpoint_cross_project_returns_404(client, scaffold_cleanup):
    project_b = await _make_fresh_project(client, scaffold_cleanup, "pl-cross-b")
    # Header bound to project=1 but path is project_b → 404 (B invisible).
    resp = await client.get(
        f"/api/projects/{project_b}/pl",
        headers={"X-Project-Id": "1"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == f"Project id={project_b} not found"


@pytest.mark.asyncio
async def test_pl_endpoint_unknown_project_id_returns_404(client):
    resp = await client.get(
        "/api/projects/9999999/pl", headers={"X-Project-Id": "9999999"}
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_pl_endpoint_missing_header_returns_400(client):
    resp = await client.get("/api/projects/1/pl")
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_pl_endpoint_multi_currency_yields_separate_buckets(
    client, scaffold_cleanup
):
    project = await _make_fresh_project(client, scaffold_cleanup, "pl-multi-ccy")
    headers = {"X-Project-Id": str(project)}
    await client.post(
        "/api/transactions",
        json=_txn_payload(project, currency="USD", amount_minor=10000),
        headers=headers,
    )
    await client.post(
        "/api/transactions",
        json=_txn_payload(project, currency="THB", amount_minor=35000),
        headers=headers,
    )
    resp = await client.get(
        f"/api/projects/{project}/pl?period=monthly", headers=headers
    )
    assert resp.status_code == 200, resp.text
    buckets = resp.json()["buckets"]
    currencies = {b["currency"] for b in buckets}
    assert currencies == {"USD", "THB"}
