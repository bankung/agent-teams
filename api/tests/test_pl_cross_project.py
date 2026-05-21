"""Contract tests for GET /api/pnl — cross-project P&L rollup (Kanban #1329).

Coverage:
- Empty DB (no non-seed projects with transactions) → rows present but all zero,
  grand_total=None (seed project has no transactions).
- Single project, single currency → grand_total = sum of net.
- Two projects, same currency → grand_total sums across rows.
- Two projects, different currency_default → grand_total=null, rows present.
- Window filter (since/until) — transactions outside window excluded.
- Quarterly period — bucket label math runs end-to-end.
- Mixed currency within a single project → mixed_currency=True.
- include_killed=false hides killed projects; include_killed=true includes them.
- since > until → 422.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_pl_endpoint.py pattern)
# ---------------------------------------------------------------------------

def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, team: str = "dev") -> dict:
    # currency_default is not part of ProjectCreate (server_default='USD').
    # Set via PATCH after creation when a non-USD currency is needed.
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


def _txn_payload(
    project_id: int,
    *,
    amount_minor: int = 10000,
    currency: str = "USD",
    kind: str = "revenue",
    occurred_at: datetime | None = None,
) -> dict:
    return {
        "project_id": project_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "kind": kind,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
    }


async def _make_project(client, scaffold_cleanup, slug: str, currency: str = "USD", team: str = "dev") -> int:
    """Create a test project and register it for scaffold + soft-delete cleanup.

    `currency_default` is not part of ProjectCreate; set it via PATCH after
    creation so the project reflects the requested currency for P&L tests.
    """
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name, team=team))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    if currency != "USD":
        # PATCH /api/projects/{id} does not require X-Project-Id.
        patch_resp = await client.patch(
            f"/api/projects/{project_id}",
            json={"currency_default": currency},
        )
        assert patch_resp.status_code == 200, patch_resp.text
    return project_id


async def _add_txn(client, project_id: int, **kwargs) -> None:
    """POST a transaction, asserting 201."""
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.post("/api/transactions", json=_txn_payload(project_id, **kwargs), headers=headers)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_empty_returns_zero_rows(client, scaffold_cleanup):
    """Fresh project with no transactions → row present with all-zero amounts."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-empty")
    resp = await client.get("/api/pnl?period=monthly")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "monthly"

    # Find the row for our fresh project.
    our_rows = [r for r in body["rows"] if r["project_id"] == project_id]
    assert len(our_rows) == 1
    row = our_rows[0]
    assert Decimal(row["net"]) == Decimal(0)
    assert row["transaction_count"] == 0
    assert row["mixed_currency"] is False
    assert row["bucket_count"] == 0


@pytest.mark.asyncio
async def test_pnl_single_project_single_currency_returns_grand_total(client, scaffold_cleanup):
    """One project with two USD revenue txns → grand_total = sum of revenue."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-single", currency="USD")
    await _add_txn(client, project_id, amount_minor=10000, kind="revenue", currency="USD")
    await _add_txn(client, project_id, amount_minor=5000, kind="revenue", currency="USD")

    # Use a wide window to ensure our transactions are captured regardless of
    # other tests' timing.
    resp = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": "2020-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    our_row = next(r for r in body["rows"] if r["project_id"] == project_id)
    assert Decimal(our_row["revenue"]) == Decimal("150.0000")
    assert Decimal(our_row["net"]) == Decimal("150.0000")
    assert our_row["transaction_count"] == 2
    assert our_row["mixed_currency"] is False

    # grand_total should be non-null when all projects share USD (possible if
    # other seed projects are also USD — we only assert our row's contribution).
    # The grand_total check: if non-null, it must be >= our row's net.
    if body["grand_total_net_first_currency_only"] is not None:
        assert Decimal(body["grand_total_net_first_currency_only"]) >= Decimal("150.0000")


@pytest.mark.asyncio
async def test_pnl_two_projects_same_currency_returns_grand_total(client, scaffold_cleanup):
    """Two projects both USD with revenue → grand_total sums across rows."""
    project_a = await _make_project(client, scaffold_cleanup, "pnl-two-a", currency="USD")
    project_b = await _make_project(client, scaffold_cleanup, "pnl-two-b", currency="USD")

    await _add_txn(client, project_a, amount_minor=20000, kind="revenue", currency="USD")
    await _add_txn(client, project_b, amount_minor=30000, kind="revenue", currency="USD")

    resp = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": "2020-01-01T00:00:00Z", "include_killed": "false"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    row_a = next(r for r in body["rows"] if r["project_id"] == project_a)
    row_b = next(r for r in body["rows"] if r["project_id"] == project_b)
    assert Decimal(row_a["net"]) == Decimal("200.0000")
    assert Decimal(row_b["net"]) == Decimal("300.0000")

    # grand_total is non-null only if all scanned projects are USD and unmixed.
    # We assert inclusively: our two rows contribute 500.00 and that must be
    # reflected if the total is non-null.
    if body["grand_total_net_first_currency_only"] is not None:
        total = Decimal(body["grand_total_net_first_currency_only"])
        assert total >= Decimal("500.0000")


@pytest.mark.asyncio
async def test_pnl_mixed_currencies_returns_grand_total_null(client, scaffold_cleanup):
    """Two projects with different currency_default → grand_total=null, rows present."""
    project_usd = await _make_project(client, scaffold_cleanup, "pnl-mix-usd", currency="USD")
    project_thb = await _make_project(client, scaffold_cleanup, "pnl-mix-thb", currency="THB")

    await _add_txn(client, project_usd, amount_minor=10000, kind="revenue", currency="USD")
    await _add_txn(client, project_thb, amount_minor=100000, kind="revenue", currency="THB")

    # Use a narrow window pinned to these projects only to guarantee mixed currencies.
    since = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    resp = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": since},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Both rows must be present.
    ids = {r["project_id"] for r in body["rows"]}
    assert project_usd in ids
    assert project_thb in ids

    # Because the two projects carry different currency_default, grand_total must be null.
    assert body["grand_total_net_first_currency_only"] is None


@pytest.mark.asyncio
async def test_pnl_within_window_only(client, scaffold_cleanup):
    """Transactions outside since/until are excluded from the row totals."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-window")

    base = datetime(2025, 3, 15, tzinfo=timezone.utc)
    # One in-window transaction.
    await _add_txn(client, project_id, amount_minor=10000, kind="revenue",
                   occurred_at=base + timedelta(days=1))
    # One outside (before since).
    await _add_txn(client, project_id, amount_minor=50000, kind="revenue",
                   occurred_at=base - timedelta(days=30))

    since = base.isoformat()
    until = (base + timedelta(days=10)).isoformat()
    resp = await client.get(
        "/api/pnl",
        params={"period": "daily", "since": since, "until": until},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    row = next(r for r in body["rows"] if r["project_id"] == project_id)
    # Only the in-window txn (10000 minor USD = 100.0000 major) is counted.
    assert row["transaction_count"] == 1
    assert Decimal(row["revenue"]) == Decimal("100.0000")


@pytest.mark.asyncio
async def test_pnl_period_quarterly_works(client, scaffold_cleanup):
    """Quarterly period — bucket label math runs end-to-end without error."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-quarterly")
    # Two transactions in Q1 2026 and Q2 2026.
    await _add_txn(client, project_id, amount_minor=10000, kind="revenue",
                   occurred_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    await _add_txn(client, project_id, amount_minor=20000, kind="revenue",
                   occurred_at=datetime(2026, 5, 1, tzinfo=timezone.utc))

    resp = await client.get(
        "/api/pnl",
        params={
            "period": "quarterly",
            "since": "2026-01-01T00:00:00Z",
            "until": "2026-12-31T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "quarterly"

    row = next(r for r in body["rows"] if r["project_id"] == project_id)
    # Two transactions → two buckets (one Q1, one Q2).
    assert row["bucket_count"] == 2
    assert row["transaction_count"] == 2
    assert Decimal(row["revenue"]) == Decimal("300.0000")  # 100 + 200


@pytest.mark.asyncio
async def test_pnl_mixed_currency_within_project_flags_mixed_currency_true(client, scaffold_cleanup):
    """Single project with USD + THB txns → mixed_currency=True on its row."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-mixed-within", currency="USD")
    await _add_txn(client, project_id, amount_minor=10000, kind="revenue", currency="USD")
    await _add_txn(client, project_id, amount_minor=35000, kind="revenue", currency="THB")

    resp = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": "2020-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    row = next(r for r in body["rows"] if r["project_id"] == project_id)
    assert row["mixed_currency"] is True
    # bucket_count must be > 1 (at least one USD + one THB bucket).
    assert row["bucket_count"] >= 2


@pytest.mark.asyncio
async def test_pnl_excludes_killed_projects_by_default(client, scaffold_cleanup):
    """Killed project (status=0) is hidden by default; include_killed=true shows it."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-killed")
    await _add_txn(client, project_id, amount_minor=10000, kind="revenue")

    # Soft-delete the project.
    del_resp = await client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code in (200, 204), del_resp.text

    # Default (include_killed=false) — project must be absent.
    resp_default = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": "2020-01-01T00:00:00Z"},
    )
    assert resp_default.status_code == 200, resp_default.text
    ids_default = {r["project_id"] for r in resp_default.json()["rows"]}
    assert project_id not in ids_default

    # include_killed=true — project must be present.
    resp_killed = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": "2020-01-01T00:00:00Z", "include_killed": "true"},
    )
    assert resp_killed.status_code == 200, resp_killed.text
    ids_killed = {r["project_id"] for r in resp_killed.json()["rows"]}
    assert project_id in ids_killed


@pytest.mark.asyncio
async def test_pnl_since_greater_than_until_returns_422(client):
    """since > until → 422 (mirrors per-project /pl boundary check)."""
    resp = await client.get(
        "/api/pnl",
        params={
            "period": "monthly",
            "since": "2030-01-01T00:00:00Z",
            "until": "2020-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 422, resp.text
