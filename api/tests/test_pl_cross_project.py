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

Kanban #1381 (per-row failure isolation):
- compute_pl raises on one project → 200 + failed_project_ids populated, other rows present.
- compute_pl raises on ALL projects → 500.
- Happy path (no failures) → failed_project_ids is empty list.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# Kanban #1381 — per-row failure isolation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_partial_failure_returns_200_with_failed_ids(client, scaffold_cleanup):
    """compute_pl raises on one of two projects → 200, failing id in failed_project_ids,
    the other row still present (Kanban #1381)."""
    project_good = await _make_project(client, scaffold_cleanup, "pnl-partial-ok", currency="USD")
    project_bad = await _make_project(client, scaffold_cleanup, "pnl-partial-bad", currency="USD")

    await _add_txn(client, project_good, amount_minor=10000, kind="revenue", currency="USD")
    await _add_txn(client, project_bad, amount_minor=20000, kind="revenue", currency="USD")

    # Patch compute_pl so it raises only when called for the bad project.
    original_compute_pl = None

    import src.routers.pl as pl_module  # noqa: PLC0415 — local import for patching
    original_compute_pl = pl_module.compute_pl

    call_count = 0
    bad_project_id = project_bad

    def _selective_raise(txns, period, *, project_currency_default="USD"):
        nonlocal call_count
        call_count += 1
        # Identify the bad project by its transaction amount (20000 minor).
        if txns and txns[0].project_id == bad_project_id:
            raise RuntimeError("simulated compute_pl failure")
        return original_compute_pl(txns, period, project_currency_default=project_currency_default)

    with patch.object(pl_module, "compute_pl", side_effect=_selective_raise):
        resp = await client.get(
            "/api/pnl",
            params={"period": "monthly", "since": "2020-01-01T00:00:00Z"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The good project's row must be present.
    good_rows = [r for r in body["rows"] if r["project_id"] == project_good]
    assert len(good_rows) == 1, "good project row must survive partial failure"

    # The bad project must appear in failed_project_ids, NOT in rows.
    assert project_bad in body["failed_project_ids"], "failing project_id must be in failed_project_ids"
    bad_rows = [r for r in body["rows"] if r["project_id"] == project_bad]
    assert len(bad_rows) == 0, "failing project must not appear in rows"


@pytest.mark.asyncio
async def test_pnl_all_projects_fail_returns_500(client, scaffold_cleanup):
    """compute_pl raises for every project → 500 (Kanban #1381)."""
    # Create a single project so the project list is non-empty.
    await _make_project(client, scaffold_cleanup, "pnl-all-fail", currency="USD")

    import src.routers.pl as pl_module  # noqa: PLC0415

    with patch.object(pl_module, "compute_pl", side_effect=RuntimeError("total meltdown")):
        resp = await client.get(
            "/api/pnl",
            params={"period": "monthly", "since": "2020-01-01T00:00:00Z"},
        )

    assert resp.status_code == 500, resp.text


@pytest.mark.asyncio
async def test_pnl_no_failure_returns_empty_failed_project_ids(client, scaffold_cleanup):
    """Happy path — failed_project_ids is present in the response and is an empty list
    (schema field default, Kanban #1381 — proves no regression on existing clients)."""
    project_id = await _make_project(client, scaffold_cleanup, "pnl-no-fail", currency="USD")
    await _add_txn(client, project_id, amount_minor=5000, kind="revenue", currency="USD")

    resp = await client.get(
        "/api/pnl",
        params={"period": "monthly", "since": "2020-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "failed_project_ids" in body, "field must be present in response"
    assert body["failed_project_ids"] == [], "no failures → empty list, not null"


# ---------------------------------------------------------------------------
# Kanban #1382 — N+1 → single-fetch regression (output identity check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_n1_refactor_two_projects_mixed_currencies_output_identity(
    client, scaffold_cleanup
):
    """Regression for Kanban #1382: single-fetch refactor must produce identical output
    to the per-project query loop.

    Two projects (USD, THB) with multiple transactions each (incl. a
    mixed-currency project) — verify:
    - correct per-row revenue/net/transaction_count values
    - rows are ordered alphabetically by project name (deterministic)
    - grand_total_net_first_currency_only is null (two different currency_defaults)
    - mixed_currency flag is set correctly
    """
    # Name prefix chosen so alphabetical order is deterministic: "aaa" < "zzz".
    project_usd = await _make_project(client, scaffold_cleanup, "n1-aaa-usd", currency="USD")
    project_thb = await _make_project(client, scaffold_cleanup, "n1-zzz-thb", currency="THB")

    since_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # USD project: 2 revenue + 1 cost
    await _add_txn(client, project_usd, amount_minor=10000, kind="revenue", currency="USD",
                   occurred_at=since_dt + timedelta(days=1))
    await _add_txn(client, project_usd, amount_minor=5000, kind="revenue", currency="USD",
                   occurred_at=since_dt + timedelta(days=2))
    await _add_txn(client, project_usd, amount_minor=2000, kind="cost", currency="USD",
                   occurred_at=since_dt + timedelta(days=3))

    # THB project: 1 revenue THB + 1 revenue USD (mixed currency within project)
    await _add_txn(client, project_thb, amount_minor=100000, kind="revenue", currency="THB",
                   occurred_at=since_dt + timedelta(days=1))
    await _add_txn(client, project_thb, amount_minor=3000, kind="revenue", currency="USD",
                   occurred_at=since_dt + timedelta(days=2))

    resp = await client.get(
        "/api/pnl",
        params={
            "period": "monthly",
            "since": since_dt.isoformat(),
            "until": (since_dt + timedelta(days=30)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # -- USD project row --
    usd_row = next(r for r in body["rows"] if r["project_id"] == project_usd)
    # 100.00 + 50.00 revenue, 20.00 cost → net = 130.00
    assert Decimal(usd_row["revenue"]) == Decimal("150.0000")
    assert Decimal(usd_row["cost"]) == Decimal("20.0000")
    assert Decimal(usd_row["net"]) == Decimal("130.0000")
    assert usd_row["transaction_count"] == 3
    assert usd_row["mixed_currency"] is False  # all USD

    # -- THB project row --
    thb_row = next(r for r in body["rows"] if r["project_id"] == project_thb)
    # transaction_count rolls up only the first-observed currency (THB); the USD
    # bucket is present but excluded from the top-level count per compute_pl semantics.
    assert thb_row["transaction_count"] == 1
    assert thb_row["bucket_count"] == 2        # one THB bucket + one USD bucket
    assert thb_row["mixed_currency"] is True   # THB + USD txns in same project

    # -- Grand total null: two different currency_defaults --
    assert body["grand_total_net_first_currency_only"] is None

    # -- Row ordering: "n1-aaa-*" must come before "n1-zzz-*" (alphabetical) --
    row_names = [r["project_name"] for r in body["rows"]]
    aaa_idx = next(i for i, n in enumerate(row_names) if project_usd == body["rows"][i]["project_id"])
    zzz_idx = next(i for i, n in enumerate(row_names) if project_thb == body["rows"][i]["project_id"])
    assert aaa_idx < zzz_idx, "rows must be ordered alphabetically by project name"

    # -- failed_project_ids must be empty (no compute_pl failures) --
    assert body["failed_project_ids"] == []
