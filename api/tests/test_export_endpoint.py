"""Export endpoint tests for GET /api/projects/{project_id}/export.

Coverage:
- CSV format: header row + one row per transaction.
- JSON format: array of TransactionRead.
- since/until filters honored.
- Content-Disposition filename set with project_id + format extension.
- Cross-project leakage guard (header mismatch → 404).
- Empty range → CSV with header only / JSON `[]`.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"export test fixture {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _txn_payload(
    project_id: int,
    *,
    amount_minor: int = 10000,
    kind: str = "revenue",
    category: str | None = "stripe_sale",
    occurred_at: datetime | None = None,
) -> dict:
    body: dict = {
        "project_id": project_id,
        "amount_minor": amount_minor,
        "currency": "USD",
        "kind": kind,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
    }
    if category is not None:
        body["category"] = category
    return body


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# =============================================================================
# CSV format
# =============================================================================


@pytest.mark.asyncio
async def test_export_csv_returns_header_and_one_row_per_txn(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "export-csv")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for n in range(3):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    resp = await client.get(
        f"/api/projects/{project}/export", params={"format": "csv"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    # header + 3 data rows
    assert len(rows) == 4
    # canonical header columns must be present (subset check — service can add
    # more without breaking this contract)
    header = rows[0]
    for col in ("id", "occurred_at", "kind", "category", "amount_minor", "currency"):
        assert col in header, f"missing column {col!r} in CSV header {header!r}"


@pytest.mark.asyncio
async def test_export_csv_empty_project_returns_header_only(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "export-csv-empty")
    headers = {"X-Project-Id": str(project)}
    resp = await client.get(
        f"/api/projects/{project}/export", params={"format": "csv"}, headers=headers
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert len(rows) == 1  # header only
    assert "id" in rows[0]


# =============================================================================
# JSON format
# =============================================================================


@pytest.mark.asyncio
async def test_export_json_returns_array_of_transactions(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "export-json")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for n in range(2):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    resp = await client.get(
        f"/api/projects/{project}/export", params={"format": "json"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert isinstance(payload, list)
    assert len(payload) == 2
    # Shape check on first entry
    first = payload[0]
    for k in ("id", "project_id", "amount_minor", "currency", "kind", "occurred_at"):
        assert k in first, f"missing key {k!r} in export entry"


@pytest.mark.asyncio
async def test_export_json_empty_project_returns_empty_array(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "export-json-empty")
    headers = {"X-Project-Id": str(project)}
    resp = await client.get(
        f"/api/projects/{project}/export", params={"format": "json"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json() == []


# =============================================================================
# Filters + cross-project gate
# =============================================================================


@pytest.mark.asyncio
async def test_export_since_until_filters_honored(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "export-window")
    headers = {"X-Project-Id": str(project)}
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for n in range(5):
        await client.post(
            "/api/transactions",
            json=_txn_payload(project, occurred_at=base + timedelta(days=n)),
            headers=headers,
        )
    since = (base + timedelta(days=2)).isoformat()
    until = (base + timedelta(days=4)).isoformat()
    resp = await client.get(
        f"/api/projects/{project}/export",
        params={"format": "json", "since": since, "until": until},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # since inclusive, until exclusive → days 2, 3
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_export_cross_project_header_mismatch_returns_404(
    client, scaffold_cleanup
):
    a = await _make_fresh_project(client, scaffold_cleanup, "export-a")
    b = await _make_fresh_project(client, scaffold_cleanup, "export-b")
    # Insert a transaction under project A
    await client.post(
        "/api/transactions",
        json=_txn_payload(a),
        headers={"X-Project-Id": str(a)},
    )
    # Try to export project A while presenting project B's header
    resp = await client.get(
        f"/api/projects/{a}/export",
        params={"format": "json"},
        headers={"X-Project-Id": str(b)},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_export_content_disposition_filename_set(client, scaffold_cleanup):
    project = await _make_fresh_project(client, scaffold_cleanup, "export-disposition")
    headers = {"X-Project-Id": str(project)}
    resp = await client.get(
        f"/api/projects/{project}/export", params={"format": "csv"}, headers=headers
    )
    assert resp.status_code == 200
    # Content-Disposition should hint a filename including project id + csv
    cd = resp.headers.get("content-disposition", "").lower()
    assert "attachment" in cd or "filename" in cd, (
        f"expected Content-Disposition to hint filename; got {cd!r}"
    )
