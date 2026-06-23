"""Kanban #1304 AC5 — `projects.cost_forecast_threshold_usd` default + null wire-up.

The column is nullable NUMERIC(10,2). `ProjectCreate` defaults the field to
Decimal("1.00"); the router persists it via plain passthrough so the $1 default
lands in the DB for every new project that omits the field.

This file pins the three AC5 contract-smoke cases:

1. POST with NO `cost_forecast_threshold_usd` in the body
   → response (and GET) returns "1.00"  (the ProjectCreate default is persisted)

2. POST with explicit `null`
   → response (and GET) returns null  (opt-out of the gate)

3. POST with an explicit value (2.50)
   → response (and GET) returns "2.50"  (arbitrary positive decimal round-trips)

Note: the Decimal column serializes as a JSON string over HTTP (Pydantic v2
default for Decimal). Assertions use the string form ("1.00", "2.50") because
that is the wire representation seen by callers (including the FE gate logic).

Cleanup uses `scaffold_cleanup` + DELETE /api/projects/{id} so the live-DB
row-count invariant in conftest stays happy.

pytest is NOT run by this agent — gated by block-pytest-on-live-db hook;
Lead + dev-tester execute the suite with the live DB after migration is applied.
"""

from __future__ import annotations

import uuid

import pytest


# ---- helpers ----------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _base_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k1304 cost_forecast_threshold fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


# ---- 1. POST without cost_forecast_threshold_usd → defaults to "1.00" -------


@pytest.mark.asyncio
async def test_create_project_cost_forecast_threshold_defaults_to_one_dollar(
    client, scaffold_cleanup
) -> None:
    """POST with NO cost_forecast_threshold_usd → column lands "1.00".

    AC5 first half: new projects OPT INTO the gate at $1 by default.
    POSITIVE assertion: the value is "1.00", not null and not absent.
    """
    name = scaffold_cleanup(_unique_name("k1304-default"))
    resp = await client.post("/api/projects", json=_base_payload(name))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        body = resp.json()
        # Decimal serializes as a JSON string over the wire.
        assert body["cost_forecast_threshold_usd"] == "1.00", (
            f"expected '1.00' (default gate), got {body.get('cost_forecast_threshold_usd')!r}"
        )
        assert body["cost_forecast_threshold_usd"] is not None, (
            "default must not be null — that would silently disable the gate"
        )

        # Confirm GET echoes the same value.
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["cost_forecast_threshold_usd"] == "1.00", get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. POST with explicit null → column NULL (no gate) ---------------------


@pytest.mark.asyncio
async def test_create_project_cost_forecast_threshold_explicit_null_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST with explicit `cost_forecast_threshold_usd: null` → column NULL.

    AC5 second half: an explicit null opts the project OUT of the gate.
    NEGATIVE/lock assertion: the value is null, NOT "1.00" and NOT any other value.
    """
    name = scaffold_cleanup(_unique_name("k1304-null"))
    payload = _base_payload(name)
    payload["cost_forecast_threshold_usd"] = None

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        body = resp.json()
        assert body["cost_forecast_threshold_usd"] is None, (
            f"explicit null must persist as null (no gate), got {body.get('cost_forecast_threshold_usd')!r}"
        )
        # Lock: must NOT fall back to the $1 default when client sends null explicitly.
        assert body["cost_forecast_threshold_usd"] != "1.00", (
            "explicit null must not be overridden by the schema default"
        )

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["cost_forecast_threshold_usd"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 3. POST with explicit value (2.50) → round-trips verbatim --------------


@pytest.mark.asyncio
async def test_create_project_cost_forecast_threshold_explicit_value_round_trips(
    client, scaffold_cleanup
) -> None:
    """POST with explicit cost_forecast_threshold_usd=2.50 → persists and echoes "2.50"."""
    name = scaffold_cleanup(_unique_name("k1304-explicit"))
    payload = _base_payload(name)
    payload["cost_forecast_threshold_usd"] = 2.50

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        body = resp.json()
        assert body["cost_forecast_threshold_usd"] == "2.50", (
            f"expected '2.50', got {body.get('cost_forecast_threshold_usd')!r}"
        )

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["cost_forecast_threshold_usd"] == "2.50", get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")
