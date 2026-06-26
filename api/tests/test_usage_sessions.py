"""Contract-smoke tests for GET /api/usage/sessions (Kanban #2728).

First-pass coverage of the per-session cost aggregate over the append-only
``usage_events`` ledger. The endpoint groups events by ``session_ext_id`` (the
Claude Code session uuid), with a per-(agent, model) breakdown — ``agent_name
IS NULL`` is the Lead/main turn, a non-null name is a subagent.

Seed mechanism MIRRORS ``test_usage_monthly.py``: each usage_events row is
created via ``POST /api/usage/events`` with the ``X-Project-Id`` header; the
server computes ``cost_usd`` from the token vector. There is NO delete API for
``usage_events`` — teardown is the ``scaffold_cleanup`` fixture, which
soft-deletes the project (CASCADE removes its events) and rmtrees the scaffolded
folder. occurred_at is bounded to ``[now-30d, now+5min]`` by the POST validator,
so all seed timestamps use recent offsets from ``now``.

Coverage:
1. Empty → ``sessions == []``, ``total_cost_usd == "0.0000"``.
2. One session: Lead row (agent_name=None) + subagent row (different model) →
   both agents present, NULL-agent isolatable, session totals = Lead+subagent,
   event_count correct, cache_hit_ratio matches the formula on a known vector.
3. project_id filter scopes to one project (two projects, isolation asserted).
4. Reconciliation-shape: a known opus Lead vector → exact cost + cache_hit_ratio.
5. Pagination: 3 sessions, limit=2 → 2 most-recent; offset=2 → the 3rd.

The rigorous suite (FK CASCADE on project delete, SET NULL on task delete,
unindexed-scan perf, malformed-session edge cases) is dev-tester's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"usage sessions fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, prefix: str) -> int:
    name = scaffold_cleanup(_unique_name(prefix))
    resp = await client.post("/api/projects", json=_project_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_event(
    client,
    project_id: int,
    *,
    session_ext_id: str,
    occurred_at: datetime,
    agent_name: str | None = None,
    model: str = "claude-opus-4-8",
    provider: str = "anthropic",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> dict:
    """POST one usage_events row at an explicit (session, agent, occurred_at).

    Returns the stored row JSON (incl. the server-computed ``cost_usd``).
    """
    payload: dict = {
        "model": model,
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "occurred_at": occurred_at.isoformat(),
        "session_ext_id": session_ext_id,
        "dedup_key": f"us-{uuid.uuid4().hex}",
    }
    if agent_name is not None:
        payload["agent_name"] = agent_name
    resp = await client.post(
        "/api/usage/events",
        json=payload,
        headers={"X-Project-Id": str(project_id)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# =============================================================================
# 1. Empty window
# =============================================================================


@pytest.mark.asyncio
async def test_empty_returns_no_sessions(client, scaffold_cleanup) -> None:
    """A project with no usage_events → empty sessions, total "0.0000"."""
    project_id = await _make_project(client, scaffold_cleanup, "us-empty")

    resp = await client.get(f"/api/usage/sessions?project_id={project_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["sessions"] == []
    assert body["total_cost_usd"] == "0.0000"
    assert body["returned"] == 0
    assert body["limit"] == 50
    assert body["offset"] == 0


# =============================================================================
# 2. One session: Lead + subagent breakdown, totals, cache_hit_ratio
# =============================================================================


@pytest.mark.asyncio
async def test_session_lead_plus_subagent_breakdown(client, scaffold_cleanup) -> None:
    """One session with a Lead row (agent_name=None) + a subagent row.

    POSITIVE: both agent rows present; the NULL-agent (Lead) entry isolatable;
    session totals = Lead + subagent across every token column + event_count;
    cache_hit_ratio matches the formula on the seeded token vector.
    NEGATIVE: the session is not just one of the two rows (total strictly > each).
    """
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    project_id = await _make_project(client, scaffold_cleanup, "us-mix")
    sid = f"sess-{uuid.uuid4().hex}"

    # Lead row (agent_name=None) — opus, with cache tokens.
    lead = await _seed_event(
        client,
        project_id,
        session_ext_id=sid,
        occurred_at=now - timedelta(minutes=10),
        agent_name=None,
        model="claude-opus-4-8",
        input_tokens=2000,
        output_tokens=800,
        cache_read_input_tokens=10_000,
        cache_creation_input_tokens=1_000,
    )
    # Subagent row — DIFFERENT model so it groups as a distinct agent row.
    sub = await _seed_event(
        client,
        project_id,
        session_ext_id=sid,
        occurred_at=now - timedelta(minutes=5),
        agent_name="dev-x",
        model="claude-sonnet-4-6",
        input_tokens=500,
        output_tokens=300,
        cache_read_input_tokens=4_000,
        cache_creation_input_tokens=0,
    )

    resp = await client.get(f"/api/usage/sessions?project_id={project_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["returned"] == 1
    session = body["sessions"][0]
    assert session["session_ext_id"] == sid

    # Two agent rows: the Lead (None) and the subagent.
    agents = session["agents"]
    assert len(agents) == 2
    by_agent = {a["agent_name"]: a for a in agents}
    # NULL-agent (Lead) is isolatable.
    assert None in by_agent, f"expected a NULL (Lead) agent row: {agents}"
    assert "dev-x" in by_agent
    lead_row = by_agent[None]
    sub_row = by_agent["dev-x"]
    assert lead_row["model"] == "claude-opus-4-8"
    assert sub_row["model"] == "claude-sonnet-4-6"

    # Per-agent costs equal the server-computed POST costs.
    assert Decimal(lead_row["cost_usd"]) == Decimal(str(lead["cost_usd"]))
    assert Decimal(sub_row["cost_usd"]) == Decimal(str(sub["cost_usd"]))
    # Lead sorts first (None before the subagent).
    assert agents[0]["agent_name"] is None

    # Session totals = Lead + subagent across every column.
    assert session["input_tokens"] == 2000 + 500
    assert session["output_tokens"] == 800 + 300
    assert session["cache_read_input_tokens"] == 10_000 + 4_000
    assert session["cache_creation_input_tokens"] == 1_000 + 0
    assert session["event_count"] == 2
    expected_total = Decimal(str(lead["cost_usd"])) + Decimal(str(sub["cost_usd"]))
    assert Decimal(session["total_cost_usd"]) == expected_total
    # NEGATIVE: total is the SUM, not just one row (each row is strictly smaller).
    assert Decimal(session["total_cost_usd"]) > Decimal(str(lead["cost_usd"]))
    assert Decimal(session["total_cost_usd"]) > Decimal(str(sub["cost_usd"]))

    # cache_hit_ratio = cache_read / (input + cache_creation + cache_read).
    s_in = 2000 + 500
    s_cread = 10_000 + 4_000
    s_ccreate = 1_000
    expected_ratio = round(s_cread / (s_in + s_ccreate + s_cread), 4)
    assert session["cache_hit_ratio"] == expected_ratio


# =============================================================================
# 3. project_id filter isolation
# =============================================================================


@pytest.mark.asyncio
async def test_project_filter_isolates_sessions(client, scaffold_cleanup) -> None:
    """A sessions query for project A must not surface project B's sessions."""
    now = datetime.now(timezone.utc)
    project_a = await _make_project(client, scaffold_cleanup, "us-pf-a")
    project_b = await _make_project(client, scaffold_cleanup, "us-pf-b")

    sid_a = f"sess-a-{uuid.uuid4().hex}"
    sid_b = f"sess-b-{uuid.uuid4().hex}"
    await _seed_event(
        client, project_a, session_ext_id=sid_a, occurred_at=now - timedelta(minutes=3)
    )
    await _seed_event(
        client, project_b, session_ext_id=sid_b, occurred_at=now - timedelta(minutes=3)
    )

    # Query A → only A's session, B excluded.
    resp_a = await client.get(f"/api/usage/sessions?project_id={project_a}")
    assert resp_a.status_code == 200, resp_a.text
    ids_a = {s["session_ext_id"] for s in resp_a.json()["sessions"]}
    assert sid_a in ids_a
    # NEGATIVE: B's session did not leak into A.
    assert sid_b not in ids_a

    # POSITIVE control: querying B surfaces B's session.
    resp_b = await client.get(f"/api/usage/sessions?project_id={project_b}")
    assert resp_b.status_code == 200, resp_b.text
    ids_b = {s["session_ext_id"] for s in resp_b.json()["sessions"]}
    assert sid_b in ids_b
    assert sid_a not in ids_b


# =============================================================================
# 4. Reconciliation-shape: known opus Lead vector → exact cost + ratio
# =============================================================================


@pytest.mark.asyncio
async def test_reconciliation_opus_lead_vector(client, scaffold_cleanup) -> None:
    """A known opus Lead token vector reconciles to an exact cost + cache_hit_ratio.

    opus-4-8 vector: input=19869, output=94513, cache_read=12551957,
    cache_creation=547871. Server-computed cost = $12.1623 (input 0.099345 +
    output 2.362825 + cache_write 3.42419375 + cache_read 6.2759785, 4dp).
    """
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    project_id = await _make_project(client, scaffold_cleanup, "us-recon")
    sid = f"sess-recon-{uuid.uuid4().hex}"

    input_tokens = 19869
    cache_creation = 547871
    cache_read = 12551957

    stored = await _seed_event(
        client,
        project_id,
        session_ext_id=sid,
        occurred_at=now - timedelta(minutes=2),
        agent_name=None,
        model="claude-opus-4-8",
        provider="anthropic",
        input_tokens=input_tokens,
        output_tokens=94513,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )
    # Server computed the cost from the vector — pin the expected figure.
    assert Decimal(str(stored["cost_usd"])) == Decimal("12.1623")

    resp = await client.get(f"/api/usage/sessions?project_id={project_id}")
    assert resp.status_code == 200, resp.text
    session = resp.json()["sessions"][0]

    lead_row = next(a for a in session["agents"] if a["agent_name"] is None)
    # Lead agent row reconciles to the exact server-computed cost.
    assert Decimal(lead_row["cost_usd"]) == Decimal("12.1623")
    assert lead_row["cache_read_input_tokens"] == cache_read

    # Session cache_hit_ratio on the exact denominator.
    expected_ratio = round(
        cache_read / (input_tokens + cache_creation + cache_read), 4
    )
    assert session["cache_hit_ratio"] == expected_ratio
    # Session total equals the single Lead row's cost (one event).
    assert Decimal(session["total_cost_usd"]) == Decimal("12.1623")


# =============================================================================
# 5. Pagination: limit + offset, most-recent first by last occurred_at
# =============================================================================


@pytest.mark.asyncio
async def test_pagination_limit_offset(client, scaffold_cleanup) -> None:
    """3 sessions, limit=2 → 2 most-recent; offset=2 → the 3rd (oldest).

    Sessions are ordered by max(occurred_at) desc. Seeded with strictly
    increasing recency: s1 oldest, s3 newest.
    """
    now = datetime.now(timezone.utc)
    project_id = await _make_project(client, scaffold_cleanup, "us-page")

    sid1 = f"sess-1-{uuid.uuid4().hex}"  # oldest
    sid2 = f"sess-2-{uuid.uuid4().hex}"
    sid3 = f"sess-3-{uuid.uuid4().hex}"  # newest
    await _seed_event(
        client, project_id, session_ext_id=sid1, occurred_at=now - timedelta(minutes=30)
    )
    await _seed_event(
        client, project_id, session_ext_id=sid2, occurred_at=now - timedelta(minutes=20)
    )
    await _seed_event(
        client, project_id, session_ext_id=sid3, occurred_at=now - timedelta(minutes=10)
    )

    # Page 1: limit=2 → the two most-recent (s3, s2) in that order.
    resp1 = await client.get(
        f"/api/usage/sessions?project_id={project_id}&limit=2&offset=0"
    )
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()
    ids1 = [s["session_ext_id"] for s in body1["sessions"]]
    assert ids1 == [sid3, sid2], ids1
    assert body1["returned"] == 2
    assert body1["limit"] == 2
    # NEGATIVE: the oldest session is NOT on page 1.
    assert sid1 not in ids1

    # Page 2: offset=2 → the 3rd (oldest) session only.
    resp2 = await client.get(
        f"/api/usage/sessions?project_id={project_id}&limit=2&offset=2"
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    ids2 = [s["session_ext_id"] for s in body2["sessions"]]
    assert ids2 == [sid1], ids2
    assert body2["returned"] == 1
    assert body2["offset"] == 2
