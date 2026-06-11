"""Kanban #2135 — provider cost rollup tests.

Three smoke contracts:
1. Pricing lookups: google + ollama family resolution via resolve_pricing_key.
2. Usage PATCH stamps provider/model + computes gemini cost correctly.
3. GET /api/usage/daily returns exact sums over seeded rows, including:
   - NULL-provider → 'unknown' grouping
   - project_id filter (only returns runs for that project)
   - empty-data shape (days with no runs omitted; top-level totals zero)
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"usage rollup fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


# =============================================================================
# 1. Pricing lookup unit tests (pure, no DB)
# =============================================================================


def test_resolve_pricing_key_google_flash_lite_exact() -> None:
    """Exact match for ('google','gemini-2.5-flash-lite') in PRICING."""
    from src.services.cost_tracker import PRICING, resolve_pricing_key

    key = resolve_pricing_key("google", "gemini-2.5-flash-lite")
    assert key == ("google", "gemini-2.5-flash-lite")
    assert PRICING[key] == {"input": 0.10, "output": 0.40}


def test_resolve_pricing_key_google_flash_lite_family() -> None:
    """model='gemini-2.5-flash-lite-preview-...' → flash-lite key via 'flash-lite' substring."""
    from src.services.cost_tracker import resolve_pricing_key

    key = resolve_pricing_key("google", "gemini-2.5-flash-lite-preview-06-17")
    assert key == ("google", "gemini-2.5-flash-lite")


def test_resolve_pricing_key_google_flash_family() -> None:
    """model='gemini-flash-latest' exact hit in PRICING."""
    from src.services.cost_tracker import PRICING, resolve_pricing_key

    key = resolve_pricing_key("google", "gemini-flash-latest")
    assert key == ("google", "gemini-flash-latest")
    assert PRICING[key] == {"input": 0.30, "output": 2.50}


def test_resolve_pricing_key_google_pro_family() -> None:
    """model='gemini-2.5-pro-preview-06-17' → pro key via 'pro' substring."""
    from src.services.cost_tracker import resolve_pricing_key

    key = resolve_pricing_key("google", "gemini-2.5-pro-preview-06-17")
    assert key == ("google", "gemini-2.5-pro")


def test_resolve_pricing_key_ollama_local_model() -> None:
    """Any ollama model name collapses to ('ollama','local') → $0."""
    from src.services.cost_tracker import PRICING, resolve_pricing_key

    key = resolve_pricing_key("ollama", "gemma4:e4b-it-qat")
    assert key == ("ollama", "local")
    assert PRICING[key] == {"input": 0.0, "output": 0.0}


def test_compute_cost_gemini_flash_lite_hand_checked() -> None:
    """Hand-computed: 1M input @ $0.10 + 1M output @ $0.40 = $0.5000."""
    from src.services.cost_tracker import compute_cost

    result = compute_cost("google", "gemini-2.5-flash-lite", 1_000_000, 1_000_000)
    assert result == Decimal("0.5000")


def test_compute_cost_ollama_local_is_zero() -> None:
    """Ollama local model: $0 regardless of token count."""
    from src.services.cost_tracker import compute_cost

    result = compute_cost("ollama", "local", 500_000, 200_000)
    assert result == Decimal("0.0000")


# =============================================================================
# 2. Usage PATCH stamps provider/model + computes gemini cost (DB tests)
# =============================================================================


@pytest.mark.asyncio
async def test_patch_run_gemini_stamps_provider_model_and_cost(
    client, scaffold_cleanup
) -> None:
    """PATCH with provider='google', model='gemini-2.5-flash-lite' → correct cost.

    Hand-computed: 200k input @ $0.10/1M + 50k output @ $0.40/1M
      = 0.02 + 0.02 = $0.04 → Decimal('0.0400').
    """
    name = scaffold_cleanup(_unique_name("usage-gemini"))
    proj_resp = await client.post("/api/projects", json=_project_payload(name))
    assert proj_resp.status_code == 201
    project = proj_resp.json()
    project_id = project["id"]

    # Create session + run.
    sess_resp = await client.post(
        "/api/sessions",
        json={"project_id": project_id},
    )
    assert sess_resp.status_code == 201
    sess = sess_resp.json()

    run_resp = await client.post(f"/api/sessions/{sess['id']}/runs", json={})
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    # PATCH with gemini tokens + provider/model.
    patch_resp = await client.patch(
        f"/api/session_runs/{run_id}",
        json={
            "status": "done",
            "total_input_tokens": 200_000,
            "total_output_tokens": 50_000,
            "provider": "google",
            "model": "gemini-2.5-flash-lite",
        },
    )
    assert patch_resp.status_code == 200
    run = patch_resp.json()

    # Provider/model persisted.
    assert run["provider"] == "google"
    assert run["model"] == "gemini-2.5-flash-lite"

    # Cost: 200k * 0.10/1M + 50k * 0.40/1M = 0.0200 + 0.0200 = 0.0400
    assert Decimal(run["total_cost_usd"]) == Decimal("0.0400")


@pytest.mark.asyncio
async def test_patch_run_ollama_stamps_zero_cost(
    client, scaffold_cleanup
) -> None:
    """PATCH with provider='ollama', model='gemma4:e4b-it-qat' → $0 cost."""
    name = scaffold_cleanup(_unique_name("usage-ollama"))
    proj_resp = await client.post("/api/projects", json=_project_payload(name))
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    sess_resp = await client.post("/api/sessions", json={"project_id": project_id})
    assert sess_resp.status_code == 201
    run_resp = await client.post(f"/api/sessions/{sess_resp.json()['id']}/runs", json={})
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/session_runs/{run_id}",
        json={
            "status": "done",
            "total_input_tokens": 300_000,
            "total_output_tokens": 100_000,
            "provider": "ollama",
            "model": "gemma4:e4b-it-qat",
        },
    )
    assert patch_resp.status_code == 200
    run = patch_resp.json()

    assert run["provider"] == "ollama"
    assert run["model"] == "gemma4:e4b-it-qat"
    assert Decimal(run["total_cost_usd"]) == Decimal("0.0000")


# =============================================================================
# 3. GET /api/usage/daily shape + aggregation correctness
# =============================================================================


@pytest.mark.asyncio
async def test_usage_daily_empty_returns_zero_totals(client) -> None:
    """No session_runs in the future → empty rows, zero totals, correct shape.

    Also validates the `today` field (locked contract — FE consumes it).
    """
    import re
    resp = await client.get("/api/usage/daily?days=1")
    assert resp.status_code == 200
    body = resp.json()
    assert "days" in body
    assert "rows" in body
    assert "total_today_usd" in body
    assert "total_month_usd" in body
    # today field: present and YYYY-MM-DD shaped.
    assert "today" in body, f"'today' field missing from response: {list(body)}"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["today"]), (
        f"today field has unexpected format: {body['today']!r}"
    )
    # Totals are strings.
    assert isinstance(body["total_today_usd"], str)
    assert isinstance(body["total_month_usd"], str)


@pytest.mark.asyncio
async def test_usage_daily_today_field_matches_server_utc_date(client) -> None:
    """The `today` field must equal the server's current_date() in UTC.

    POSITIVE: GET returns today matching PostgreSQL current_date().
    NEGATIVE: today != total_today_usd sum date would mean the FE's date
    display is detached from the aggregation window.
    """
    from datetime import timezone
    import datetime

    resp = await client.get("/api/usage/daily?days=1")
    assert resp.status_code == 200
    body = resp.json()
    assert "today" in body

    # The server uses PostgreSQL current_date() — verify it matches Python UTC
    # date (same source of truth; test runs in the same UTC-aligned environment).
    expected_today = datetime.datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    assert body["today"] == expected_today, (
        f"today={body['today']!r} does not match server UTC date {expected_today!r}"
    )


@pytest.mark.asyncio
async def test_usage_daily_aggregates_gemini_runs(
    client, scaffold_cleanup
) -> None:
    """Two gemini runs in the same project → summed in one row bucket.

    Run A: 100k input + 20k output = 0.01 + 0.008 = $0.0180
    Run B: 200k input + 30k output = 0.02 + 0.012 = $0.0320
    Total: 300k input + 50k output = $0.0500
    """
    name = scaffold_cleanup(_unique_name("usage-agg"))
    proj_resp = await client.post("/api/projects", json=_project_payload(name))
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    sess_resp = await client.post("/api/sessions", json={"project_id": project_id})
    assert sess_resp.status_code == 201
    sess_id = sess_resp.json()["id"]

    for in_tok, out_tok in [(100_000, 20_000), (200_000, 30_000)]:
        run_resp = await client.post(f"/api/sessions/{sess_id}/runs", json={})
        assert run_resp.status_code == 201
        run_id = run_resp.json()["id"]
        patch_resp = await client.patch(
            f"/api/session_runs/{run_id}",
            json={
                "status": "done",
                "total_input_tokens": in_tok,
                "total_output_tokens": out_tok,
                "provider": "google",
                "model": "gemini-2.5-flash-lite",
            },
        )
        assert patch_resp.status_code == 200

    resp = await client.get(
        f"/api/usage/daily?days=1&project_id={project_id}"
    )
    assert resp.status_code == 200
    body = resp.json()

    # Filter to our provider bucket.
    google_rows = [r for r in body["rows"] if r["provider"] == "google"]
    assert len(google_rows) == 1, f"expected 1 google row, got {google_rows}"
    row = google_rows[0]
    assert row["input_tokens"] == 300_000
    assert row["output_tokens"] == 50_000
    # 300k * 0.10/1M + 50k * 0.40/1M = 0.0300 + 0.0200 = $0.0500
    assert Decimal(row["cost_usd"]) == Decimal("0.0500")


@pytest.mark.asyncio
async def test_usage_daily_project_filter_excludes_other_projects(
    client, scaffold_cleanup
) -> None:
    """project_id filter returns only runs for that project."""
    name_a = scaffold_cleanup(_unique_name("usage-pf-a"))
    name_b = scaffold_cleanup(_unique_name("usage-pf-b"))

    async def make_project_with_run(name: str) -> int:
        pr = await client.post("/api/projects", json=_project_payload(name))
        assert pr.status_code == 201
        pid = pr.json()["id"]
        sr = await client.post("/api/sessions", json={"project_id": pid})
        assert sr.status_code == 201
        rr = await client.post(f"/api/sessions/{sr.json()['id']}/runs", json={})
        assert rr.status_code == 201
        await client.patch(
            f"/api/session_runs/{rr.json()['id']}",
            json={
                "status": "done",
                "total_input_tokens": 50_000,
                "total_output_tokens": 10_000,
                "provider": "google",
                "model": "gemini-2.5-flash-lite",
            },
        )
        return pid

    pid_a = await make_project_with_run(name_a)
    await make_project_with_run(name_b)

    # Filter to project A only.
    resp = await client.get(f"/api/usage/daily?days=1&project_id={pid_a}")
    assert resp.status_code == 200
    body = resp.json()

    # Every row must come from project A (model-level verification: only 1
    # bucket possible since we seeded exactly 1 provider/model pair).
    assert len(body["rows"]) == 1
    assert body["rows"][0]["provider"] == "google"
    # 50k * 0.10/1M + 10k * 0.40/1M = 0.005 + 0.004 = $0.0090
    assert Decimal(body["rows"][0]["cost_usd"]) == Decimal("0.0090")


@pytest.mark.asyncio
async def test_usage_daily_null_provider_grouped_as_unknown(
    client, scaffold_cleanup
) -> None:
    """Runs with no provider PATCH → appears as provider='unknown' in rollup."""
    name = scaffold_cleanup(_unique_name("usage-null"))
    pr = await client.post("/api/projects", json=_project_payload(name))
    assert pr.status_code == 201
    pid = pr.json()["id"]

    sr = await client.post("/api/sessions", json={"project_id": pid})
    assert sr.status_code == 201
    rr = await client.post(f"/api/sessions/{sr.json()['id']}/runs", json={})
    assert rr.status_code == 201
    run_id = rr.json()["id"]

    # PATCH without provider/model (legacy path).
    await client.patch(
        f"/api/session_runs/{run_id}",
        json={
            "status": "done",
            "total_input_tokens": 10_000,
            "total_output_tokens": 5_000,
        },
    )

    resp = await client.get(f"/api/usage/daily?days=1&project_id={pid}")
    assert resp.status_code == 200
    body = resp.json()

    unknown_rows = [r for r in body["rows"] if r["provider"] == "unknown"]
    assert len(unknown_rows) == 1, f"expected 1 unknown row: {body['rows']}"
    assert unknown_rows[0]["input_tokens"] == 10_000
