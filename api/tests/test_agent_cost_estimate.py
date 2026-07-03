"""Contract-smoke tests for GET /api/agents/{name}/cost-estimate (Kanban #1020).

Response: {avg_cost_per_spawn, spawn_count_last_30d, projected_monthly_usd,
vs_project_budget_pct, traffic_light}. Estimated-basis (tasks.estimated_cost_usd,
the #944 done-flip heuristic) — see routers/agent_gallery.py's docstring for why.

`estimated_cost_usd` is SERVER-computed on done-flip (never settable via the
POST/PATCH body — see services/task_cost_estimator.py), so tests seed real
tasks via the public API (title/description sized to guarantee a NON-ZERO
heuristic TOKEN count) exactly like test_agent_gallery.py's `_create_done_task`,
then read the resulting cost back before asserting the percentage/threshold
math — this avoids hard-coding a literal dollar figure that would drift if the
env-resolved model ever changes.

Provider env matters for a non-zero DOLLAR cost, not just tokens: the #944
estimator resolves (provider, model) from LANGGRAPH_LLM_PROVIDER /
ANTHROPIC_MODEL (services/task_cost_estimator.resolve_provider_model), and
`ollama` prices at exactly $0 by definition (services/cost_tracker.PRICING) —
this dev container's ambient env IS ollama, confirmed live
(services/task_cost_estimator.resolve_provider_model() -> ("ollama", "local")
in this container as of 2026-07-03). Any test that needs a non-zero
projected_monthly_usd therefore MUST `monkeypatch.delenv` both vars to force
the anthropic-opus default (mirrors the existing precedent in
test_task_cost_estimator.py's `test_resolve_provider_model_defaults_to_anthropic_opus`)
— padding the description alone is NOT sufficient in this environment.

Every assertion pairs a POSITIVE check with the NEGATIVE lock it is guarding.
"""

from __future__ import annotations

import uuid

import pytest

# A long-enough description to make the #944 heuristic (chars // chars_per_token)
# produce a NON-ZERO input-TOKEN count. Whether that becomes a non-zero DOLLAR
# cost also depends on the env-resolved provider — see module docstring; tests
# that need a real dollar figure additionally force the provider via `_force_paid_provider`.
_LONG_DESCRIPTION = "cost estimate fixture task body. " * 40


def _force_paid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the #944 estimator's default (anthropic, claude-opus-4-8) — a
    real, non-zero price-card entry — regardless of this container's ambient
    LANGGRAPH_LLM_PROVIDER (confirmed live: `ollama`, which prices at exactly
    $0 by definition). Mirrors the existing
    `test_resolve_provider_model_defaults_to_anthropic_opus` precedent in
    test_task_cost_estimator.py.
    """
    monkeypatch.delenv("LANGGRAPH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_payload(name: str, **overrides: object) -> dict:
    payload = {
        "name": name,
        "description": f"cost-estimate test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }
    payload.update(overrides)
    return payload


async def _create_done_task(
    client, project_id: int, title: str, subagent_models: list, description: str = ""
) -> dict:
    create_resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={
            "project_id": project_id,
            "title": title,
            "description": description,
            "subagent_models": subagent_models,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    task_id = create_resp.json()["id"]
    patch_resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(project_id)},
        json={"process_status": 5},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return patch_resp.json()


async def _make_project(client, scaffold_cleanup, prefix: str, **overrides: object) -> dict:
    from src import db as _db

    await _db.engine.dispose()
    name = scaffold_cleanup(_unique_name(prefix))
    resp = await client.post("/api/projects", json=_project_payload(name, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# =============================================================================
# 1. Unknown agent / bad name → 404 (mirrors the detail route's gate)
# =============================================================================


async def test_unknown_agent_name_404(client, scaffold_cleanup):
    project = await _make_project(client, scaffold_cleanup, "cost-unknown-agent")
    resp = await client.get(
        f"/api/agents/does-not-exist-agent/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 404


async def test_bad_regex_name_404(client, scaffold_cleanup):
    # Traversal-shaped input is rejected by the regex gate before any lookup —
    # same posture as GET /api/agents/{name}.
    project = await _make_project(client, scaffold_cleanup, "cost-bad-regex")
    resp = await client.get(
        f"/api/agents/..%2fetc/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 404


# =============================================================================
# 2. Unknown project_id → 404
# =============================================================================


async def test_unknown_project_id_404(client):
    resp = await client.get(
        "/api/agents/dev-backend/cost-estimate?project_id=999999999"
    )
    assert resp.status_code == 404


async def test_soft_deleted_project_id_404(client, scaffold_cleanup):
    """dev-reviewer #1020 fix 3: get_active_project_or_404 must reject a
    soft-deleted project (status != ACTIVE), not just a nonexistent id."""
    project = await _make_project(client, scaffold_cleanup, "cost-soft-deleted")
    del_resp = await client.delete(f"/api/projects/{project['id']}")
    assert del_resp.status_code in (200, 204), del_resp.text

    resp = await client.get(
        f"/api/agents/dev-backend/cost-estimate?project_id={project['id']}"
    )
    # POSITIVE: a soft-deleted project 404s exactly like a nonexistent one.
    assert resp.status_code == 404
    # NEGATIVE lock: must NOT resolve as if the project were still live (200).
    assert resp.status_code != 200


# =============================================================================
# 3. horizon=weekly → 422 (Literal rejects anything but "monthly")
# =============================================================================


async def test_horizon_weekly_rejected_422(client, scaffold_cleanup):
    project = await _make_project(client, scaffold_cleanup, "cost-bad-horizon")
    resp = await client.get(
        f"/api/agents/dev-backend/cost-estimate"
        f"?project_id={project['id']}&horizon=weekly"
    )
    assert resp.status_code == 422


# =============================================================================
# 4. No spawn history → 0 count, both cost fields null, green
# =============================================================================


async def test_no_history_returns_zero_and_nulls_green(client, scaffold_cleanup):
    project = await _make_project(client, scaffold_cleanup, "cost-no-history")
    resp = await client.get(
        f"/api/agents/dev-backend/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # POSITIVE: no spawns at all for this fresh project.
    assert body["spawn_count_last_30d"] == 0
    assert body["avg_cost_per_spawn"] is None
    assert body["projected_monthly_usd"] is None
    assert body["vs_project_budget_pct"] is None
    assert body["traffic_light"] == "green"
    # NEGATIVE lock: a null-history response must NOT report a positive count.
    assert body["spawn_count_last_30d"] != 1


# =============================================================================
# 5. Happy path — seeded spawns produce a real count + non-null costs,
#    and a budget carefully sized off the OBSERVED cost forces a KNOWN light.
# =============================================================================


async def test_happy_path_seeded_spawns_null_budget_green(
    client, scaffold_cleanup, monkeypatch
):
    _force_paid_provider(monkeypatch)
    project = await _make_project(client, scaffold_cleanup, "cost-happy")
    agent = "dev-backend"

    await _create_done_task(
        client, project["id"], "spawn one",
        [{"agent": agent, "model": "sonnet", "at": "2026-07-01T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )
    await _create_done_task(
        client, project["id"], "spawn two",
        [{"agent": agent, "model": "opus", "at": "2026-07-02T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )
    # A control spawn of a DIFFERENT agent must not be counted.
    await _create_done_task(
        client, project["id"], "other agent spawn",
        [{"agent": "dev-frontend", "model": "haiku", "at": "2026-07-02T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )

    resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: exactly our 2 dev-backend spawns counted (not 3 — the
    # dev-frontend control spawn is excluded).
    assert body["spawn_count_last_30d"] == 2
    # avg_cost_per_spawn is non-null and > 0 (the long description + forced
    # paid provider guarantee a non-zero heuristic cost on done-flip).
    assert body["avg_cost_per_spawn"] is not None
    assert body["avg_cost_per_spawn"] > 0
    # projected_monthly_usd IS the total cost, and avg is DERIVED from it
    # (avg == round(projected / count, 4)) — self-consistent by construction
    # (dev-reviewer #1020 fix 2), not two independently-rounded numbers.
    assert body["avg_cost_per_spawn"] == round(body["projected_monthly_usd"] / 2, 4)
    # No budget configured on this project -> pct null -> green (never red on a
    # null budget, regardless of spend).
    assert body["vs_project_budget_pct"] is None
    assert body["traffic_light"] == "green"
    # NEGATIVE lock: the dev-frontend control spawn's cost must not leak in as
    # a THIRD averaged data point (count stays 2, not 3).
    assert body["spawn_count_last_30d"] != 3


# =============================================================================
# 5b. dev-reviewer #1020 fix 2 — an agent listed TWICE on the SAME task counts
#     as 2 spawns, but the task's cost is attributed ONCE (not doubled).
# =============================================================================


async def test_agent_listed_twice_on_same_task_counts_two_spawns_one_cost(
    client, scaffold_cleanup, monkeypatch
):
    _force_paid_provider(monkeypatch)
    project = await _make_project(client, scaffold_cleanup, "cost-double-listed")
    agent = "dev-backend"

    task = await _create_done_task(
        client, project["id"], "double-listed spawn",
        [
            {"agent": agent, "model": "sonnet", "at": "2026-07-01T00:00:00Z"},
            {"agent": agent, "model": "opus", "at": "2026-07-02T00:00:00Z"},
        ],
        description=_LONG_DESCRIPTION,
    )
    task_cost = float(task["estimated_cost_usd"])
    assert task_cost > 0, "fixture must produce a non-zero cost to be a useful lock"

    resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: ONE task listing the agent twice counts as 2 spawns (per-element
    # count), matching the two subagent_models entries.
    assert body["spawn_count_last_30d"] == 2
    # POSITIVE: projected_monthly_usd equals the task's cost ONCE — not doubled
    # even though the agent appears twice on it (task-level cost attribution).
    assert body["projected_monthly_usd"] == pytest.approx(task_cost, rel=1e-6)
    # NEGATIVE lock: the cost must NOT have been summed per-element (which
    # would double it to ~2x task_cost).
    assert body["projected_monthly_usd"] != pytest.approx(task_cost * 2, rel=1e-6)
    # avg_cost_per_spawn = projected / count = task_cost / 2 (self-consistent).
    assert body["avg_cost_per_spawn"] == round(task_cost / 2, 4)


# =============================================================================
# 6. Budget configured, sized so projected spend is DEEP under the green
#    ceiling (10%) -> green, and pct is a small positive number (not null).
# =============================================================================


async def test_generous_budget_stays_green_with_nonnull_pct(
    client, scaffold_cleanup, monkeypatch
):
    _force_paid_provider(monkeypatch)
    project = await _make_project(
        client, scaffold_cleanup, "cost-green-budget",
        budget_monthly_usd="100000.00",
    )
    agent = "dev-backend"
    await _create_done_task(
        client, project["id"], "spawn one",
        [{"agent": agent, "model": "sonnet", "at": "2026-07-01T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )

    resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: a huge budget relative to one task's heuristic cost -> pct is a
    # real (non-null) but tiny number, well under the 10% default green ceiling.
    assert body["vs_project_budget_pct"] is not None
    assert body["vs_project_budget_pct"] < 10
    assert body["traffic_light"] == "green"
    # NEGATIVE lock: green must NOT be reported as red/yellow here.
    assert body["traffic_light"] != "red"
    assert body["traffic_light"] != "yellow"


# =============================================================================
# 7. Red case — budget sized SMALLER than the observed projected spend forces
#    vs_project_budget_pct > 30 (default yellow ceiling) -> red.
# =============================================================================


async def test_tiny_budget_forces_red(client, scaffold_cleanup, monkeypatch):
    _force_paid_provider(monkeypatch)
    agent = "dev-backend"

    # First pass with NO budget to learn the real projected_monthly_usd this
    # fixture produces (server-computed; we don't hard-code the dollar figure).
    probe_project = await _make_project(client, scaffold_cleanup, "cost-red-probe")
    await _create_done_task(
        client, probe_project["id"], "probe spawn",
        [{"agent": agent, "model": "opus", "at": "2026-07-01T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )
    probe_resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={probe_project['id']}"
    )
    assert probe_resp.status_code == 200, probe_resp.text
    projected = probe_resp.json()["projected_monthly_usd"]
    assert projected is not None and projected > 0

    # Now a REAL project whose budget is 1/1000th of that observed projection —
    # guarantees vs_project_budget_pct >> 30.
    tiny_budget = round(projected / 1000, 2) or 0.01
    red_project = await _make_project(
        client, scaffold_cleanup, "cost-red-actual",
        budget_monthly_usd=str(tiny_budget),
    )
    await _create_done_task(
        client, red_project["id"], "red spawn",
        [{"agent": agent, "model": "opus", "at": "2026-07-01T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )
    resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={red_project['id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: pct is far past the 30% default yellow ceiling -> red.
    assert body["vs_project_budget_pct"] > 30
    assert body["traffic_light"] == "red"
    # NEGATIVE lock: must not be misreported as green.
    assert body["traffic_light"] != "green"


# =============================================================================
# 8. config.agent_cost_thresholds override changes the light for the SAME pct
# =============================================================================


async def test_config_threshold_override_changes_light(
    client, scaffold_cleanup, monkeypatch
):
    _force_paid_provider(monkeypatch)
    agent = "dev-backend"

    # Probe run (no budget) to learn the real projected spend.
    probe_project = await _make_project(client, scaffold_cleanup, "cost-override-probe")
    await _create_done_task(
        client, probe_project["id"], "probe spawn",
        [{"agent": agent, "model": "sonnet", "at": "2026-07-01T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )
    probe_resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={probe_project['id']}"
    )
    projected = probe_resp.json()["projected_monthly_usd"]
    assert projected is not None and projected > 0

    # Budget sized so pct lands at roughly 20% under DEFAULT thresholds
    # (10/30) -> default verdict is "yellow" (>=10, <30).
    budget_for_20pct = round(projected / 0.20, 2)
    project = await _make_project(
        client, scaffold_cleanup, "cost-override-actual",
        budget_monthly_usd=str(budget_for_20pct),
        # Raise the green ceiling to 25% -> the SAME ~20% pct now reads green.
        config={"agent_cost_thresholds": {"green_max_pct": 25, "yellow_max_pct": 50}},
    )
    await _create_done_task(
        client, project["id"], "override spawn",
        [{"agent": agent, "model": "sonnet", "at": "2026-07-01T00:00:00Z"}],
        description=_LONG_DESCRIPTION,
    )
    resp = await client.get(
        f"/api/agents/{agent}/cost-estimate?project_id={project['id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: with the override raising green_max_pct to 25, a ~20% spend is
    # green (would have been yellow under the 10/30 defaults).
    assert body["vs_project_budget_pct"] < 25
    assert body["traffic_light"] == "green"
    # NEGATIVE lock: confirm the override actually mattered — the SAME pct
    # against the DEFAULT thresholds (10/30) would NOT be green (it's >= 10).
    assert body["vs_project_budget_pct"] >= 10


# =============================================================================
# 9. dev-reviewer #1020 fix 1 — malformed config.agent_cost_thresholds values
#    must NOT 500 the endpoint; each malformed key falls back independently.
# =============================================================================


@pytest.mark.parametrize(
    "malformed_thresholds",
    [
        {"green_max_pct": "abc"},
        {"green_max_pct": None},
        {"green_max_pct": {"nested": "object"}},
        {"yellow_max_pct": "not-a-number"},
        {"green_max_pct": "abc", "yellow_max_pct": "also-bad"},
    ],
)
async def test_malformed_threshold_config_falls_back_not_500(
    client, scaffold_cleanup, malformed_thresholds
):
    project = await _make_project(
        client, scaffold_cleanup, "cost-malformed-threshold",
        config={"agent_cost_thresholds": malformed_thresholds},
    )
    resp = await client.get(
        f"/api/agents/dev-backend/cost-estimate?project_id={project['id']}"
    )
    # POSITIVE: 200, not 500 — the malformed key falls back to its own default.
    assert resp.status_code == 200, resp.text
    # NEGATIVE lock: must not have crashed (no history on this fresh project ->
    # deterministically green under ANY valid threshold fallback).
    assert resp.status_code != 500
    assert resp.json()["traffic_light"] == "green"
