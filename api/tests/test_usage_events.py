"""Contract-smoke tests for POST /api/usage/events (Kanban #2354, P1).

First-pass smoke coverage of the Mode-A usage-event ingest endpoint:
  - server-side cost computation (opus, with cache multipliers) — exact Decimal;
  - idempotency on dedup_key (one row, same id on repeat);
  - unknown model → row stored, cost 0, tokens preserved, NOT 422;
  - occurred_at default ≈ now when omitted, honored when supplied;
  - missing model → 422.

Review-fix tests (2026-06-13):
  - same dedup_key in two different projects → both insert (Fix 1 — per-project
    composite unique, eliminates cross-project 500/oracle);
  - same dedup_key twice in same project → still idempotent (Fix 1 regression);
  - task_id from a different project → 400 (Fix 2 — task_id project guard);
  - over-max_length field → 422 (Fix 3 — Pydantic max_length).

The rigorous suite (race, FK SET NULL on task delete, CASCADE on project delete,
negative tokens 422, etc.) is dev-tester's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select


# ---------------------------------------------------------------------------
# Helpers for cross-project tests
# ---------------------------------------------------------------------------


def _unique_name(prefix: str = "ue") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "usage_events cross-project fixture",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _expected_opus_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_creation: int,
) -> Decimal:
    """Hand-calc the opus cost from cost_tracker's PRICING + cache multipliers.

    Computed from the price card (not hardcoded) so a price change makes the
    test fail loudly rather than silently diverge from the implementation.
    """
    from decimal import ROUND_HALF_UP

    from src.services import cost_tracker as ct

    rates = ct.PRICING[("anthropic", "claude-opus-4-8")]
    base_in = Decimal(str(rates["input"]))
    base_out = Decimal(str(rates["output"]))
    per_m = Decimal("1000000")
    total = (
        (base_in * Decimal(input_tokens)) / per_m
        + (base_out * Decimal(output_tokens)) / per_m
        + (base_in * ct._CACHE_WRITE_MULTIPLIER * Decimal(cache_creation)) / per_m
        + (base_in * ct._CACHE_READ_MULTIPLIER * Decimal(cache_read)) / per_m
    )
    return total.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


async def _project_id(client) -> int:
    resp = await client.get("/api/projects/by-name/agent-teams")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_opus_event_cost_computed_server_side(client) -> None:
    """An opus event with cache tokens → cost_usd == the hand-calc from the
    price card + cache multipliers."""
    project_id = await _project_id(client)
    payload = {
        "model": "claude-opus-4-8",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 2000,
        "cache_creation_input_tokens": 100,
    }
    resp = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    expected = _expected_opus_cost(1000, 500, 2000, 100)
    # Sanity: the hand-calc is the known fixed value (0.0191) — guards against a
    # vacuous self-referential equality if PRICING ever went malformed.
    assert expected == Decimal("0.0191")
    assert Decimal(str(body["cost_usd"])) == expected

    # Tokens + scoping round-trip; client cannot assert cost (server owns it).
    assert body["project_id"] == project_id
    assert body["model"] == "claude-opus-4-8"
    assert body["input_tokens"] == 1000
    assert body["output_tokens"] == 500
    assert body["cache_read_input_tokens"] == 2000
    assert body["cache_creation_input_tokens"] == 100
    assert body["is_estimate"] is True
    assert body["source"] == "mode_a"
    assert body["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_dedup_key_idempotent_single_row(client, db_session) -> None:
    """POST the same dedup_key twice → exactly ONE row; 2nd response = same id
    with 200 (idempotent hit), not a 2nd insert."""
    from src.models.usage_event import UsageEvent

    project_id = await _project_id(client)
    dedup = f"mode-a-{uuid.uuid4().hex}"
    payload = {
        "model": "claude-opus-4-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "dedup_key": dedup,
    }

    first = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_id)},
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    second = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_id)},
    )
    # POSITIVE: idempotent hit returns the SAME row with 200.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first_id

    # NEGATIVE lock: exactly one row carries this dedup_key (no 2nd insert).
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.dedup_key == dedup)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_unknown_model_stores_row_cost_zero_not_422(client) -> None:
    """An unknown model is NOT a 422 — the row is stored with cost_usd=0 and the
    tokens preserved (partial signal beats no signal)."""
    project_id = await _project_id(client)
    resp = await client.post(
        "/api/usage/events",
        json={
            "model": "made-up-model",
            "input_tokens": 1234,
            "output_tokens": 567,
        },
        headers={"X-Project-Id": str(project_id)},
    )
    # POSITIVE: stored (201), not rejected.
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # NEGATIVE lock on the cost: exactly zero, NOT a vacuous "any value".
    assert Decimal(str(body["cost_usd"])) == Decimal("0")
    # Tokens preserved.
    assert body["input_tokens"] == 1234
    assert body["output_tokens"] == 567
    assert body["model"] == "made-up-model"


@pytest.mark.asyncio
async def test_occurred_at_defaults_now_when_omitted(client) -> None:
    """Omitted occurred_at ≈ now (server default); supplied occurred_at honored."""
    project_id = await _project_id(client)

    before = datetime.now(timezone.utc)
    omitted = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8"},
        headers={"X-Project-Id": str(project_id)},
    )
    after = datetime.now(timezone.utc)
    assert omitted.status_code == 201, omitted.text
    occurred = datetime.fromisoformat(omitted.json()["occurred_at"])
    # Default lands within the request window (small slack for clock skew).
    assert before.timestamp() - 5 <= occurred.timestamp() <= after.timestamp() + 5

    # Supplied value is honored (a clearly-not-now timestamp).
    supplied = "2025-01-15T08:30:00+00:00"
    honored = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8", "occurred_at": supplied},
        headers={"X-Project-Id": str(project_id)},
    )
    assert honored.status_code == 201, honored.text
    got = datetime.fromisoformat(honored.json()["occurred_at"])
    assert got == datetime.fromisoformat(supplied)
    # NEGATIVE lock: the honored value is NOT the default-now value.
    assert got.year == 2025


@pytest.mark.asyncio
async def test_missing_model_is_422(client) -> None:
    """Missing `model` → 422 (Pydantic), not a stored row."""
    project_id = await _project_id(client)
    resp = await client.post(
        "/api/usage/events",
        json={"input_tokens": 100},
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_missing_header_is_400(client) -> None:
    """X-Project-Id header is mandatory (same gate as the tasks endpoints)."""
    resp = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == (
        "X-Project-Id header is required for task endpoints"
    )


# ---------------------------------------------------------------------------
# Review-fix tests (2026-06-13): Fix 1 (dedup per-project), Fix 2 (task_id
# project validation), Fix 3 (max_length 422).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_key_cross_project_both_insert(
    client, db_session, scaffold_cleanup
) -> None:
    """Same dedup_key in TWO different projects → BOTH rows insert (no 500).

    Proves the cross-project 500 (reviewer M1) and enumeration oracle (W1) are
    gone: the UNIQUE constraint is now composite (project_id, dedup_key), so the
    same key string in a different project is a distinct pair and inserts cleanly.
    """
    from src.models.usage_event import UsageEvent

    project_a = await _project_id(client)

    # Create a second project for the cross-project half of the test.
    name_b = scaffold_cleanup(_unique_name("ue-xp"))
    resp_create = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    assert resp_create.status_code == 201, resp_create.text
    project_b = resp_create.json()["id"]

    dedup = f"cross-project-{uuid.uuid4().hex}"
    payload = {"model": "claude-opus-4-8", "input_tokens": 10, "dedup_key": dedup}

    resp_a = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_a)},
    )
    # POSITIVE: project A inserts cleanly.
    assert resp_a.status_code == 201, resp_a.text
    id_a = resp_a.json()["id"]

    resp_b = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_b)},
    )
    # POSITIVE: project B also inserts cleanly (no 500, no idempotent-hit 200).
    assert resp_b.status_code == 201, resp_b.text
    id_b = resp_b.json()["id"]

    # NEGATIVE: they are two distinct rows with distinct ids.
    assert id_a != id_b

    # DB confirms exactly 2 rows with this dedup_key (one per project).
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.dedup_key == dedup)
        )
    ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_dedup_key_same_project_still_idempotent(client, db_session) -> None:
    """Same dedup_key twice in the SAME project → 1 row, 2nd call = 200.

    Confirms the per-project idempotency contract was not broken by the composite-
    key change: within one project the behaviour is identical to before.
    """
    from src.models.usage_event import UsageEvent

    project_id = await _project_id(client)
    dedup = f"same-project-{uuid.uuid4().hex}"
    payload = {"model": "claude-opus-4-8", "input_tokens": 5, "dedup_key": dedup}

    first = await client.post(
        "/api/usage/events", json=payload, headers={"X-Project-Id": str(project_id)}
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    second = await client.post(
        "/api/usage/events", json=payload, headers={"X-Project-Id": str(project_id)}
    )
    # POSITIVE: idempotent hit returns the same row with 200.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first_id

    # NEGATIVE: exactly one row with this dedup_key in this project.
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.dedup_key == dedup, UsageEvent.project_id == project_id)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_task_id_from_different_project_is_400(
    client, scaffold_cleanup
) -> None:
    """task_id belonging to a different project → 400 (review fix m1).

    Protects per-task cost attribution: cross-project task_id would silently
    charge tokens to the wrong task row.
    """
    project_a = await _project_id(client)

    # Create project B and a task inside it.
    name_b = scaffold_cleanup(_unique_name("ue-tid"))
    resp_create = await client.post(
        "/api/projects", json=_project_create_payload(name_b)
    )
    assert resp_create.status_code == 201, resp_create.text
    project_b = resp_create.json()["id"]

    task_resp = await client.post(
        "/api/tasks",
        json={
            "title": "cross-project task for usage_events test",
            "project_id": project_b,
            "process_status": 1,
            "priority": 3,
        },
        headers={"X-Project-Id": str(project_b)},
    )
    assert task_resp.status_code == 201, task_resp.text
    foreign_task_id = task_resp.json()["id"]

    # POST to project_a with a task_id that belongs to project_b → 400.
    resp = await client.post(
        "/api/usage/events",
        json={
            "model": "claude-opus-4-8",
            "input_tokens": 1,
            "task_id": foreign_task_id,
        },
        headers={"X-Project-Id": str(project_a)},
    )
    # NEGATIVE: not 201 or 200 — rejected as a cross-project task reference.
    assert resp.status_code == 400, resp.text
    assert "task_id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_overlength_field_is_422(client) -> None:
    """A field exceeding max_length → 422 (Pydantic validation, review fix N1).

    Tests the source field (max 32 chars). A 33-char value must be rejected at
    the Pydantic boundary, not reach the DB.
    """
    project_id = await _project_id(client)

    # source max_length=32; send 33 chars.
    resp = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8", "source": "x" * 33},
        headers={"X-Project-Id": str(project_id)},
    )
    # NEGATIVE: rejected with 422, not stored.
    assert resp.status_code == 422, resp.text

    # Also test dedup_key max_length=256; send 257 chars.
    resp2 = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8", "dedup_key": "d" * 257},
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp2.status_code == 422, resp2.text


# ---------------------------------------------------------------------------
# Token upper-bound tests (2026-06-13, Fix 1 — le=1_000_000_000 guard).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_above_1b_is_422(client) -> None:
    """cache_read_input_tokens=2_000_000_000 (2B) exceeds le=1_000_000_000 → 422.

    Guards the Numeric(10,4) cost-overflow path: a 2B-token event at any
    non-zero price would exceed the $999,999.9999 column ceiling.
    """
    project_id = await _project_id(client)
    resp = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8", "cache_read_input_tokens": 2_000_000_000},
        headers={"X-Project-Id": str(project_id)},
    )
    # NEGATIVE: above the 1B ceiling → rejected, not stored.
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_token_60m_is_accepted(client) -> None:
    """cache_read_input_tokens=60_000_000 (60M, matching observed live row id=9)
    is ACCEPTED — guards against a too-tight bound that would reject real data.
    """
    project_id = await _project_id(client)
    resp = await client.post(
        "/api/usage/events",
        json={"model": "claude-opus-4-8", "cache_read_input_tokens": 60_000_000},
        headers={"X-Project-Id": str(project_id)},
    )
    # POSITIVE: 60M is well within the 1B ceiling.
    assert resp.status_code == 201, resp.text
    assert resp.json()["cache_read_input_tokens"] == 60_000_000
