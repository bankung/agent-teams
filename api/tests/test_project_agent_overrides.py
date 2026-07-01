"""Kanban #1018 — GET/PATCH /api/projects/{id}/agent-overrides.

ADDITIVE to the #777 `agent_overrides` tier-map (untouched shape/validators/
spawn-precedence). New per-project enable/notes state lives in a NEW subkey
`config.agent_settings: {"<agent-name>": {"enabled": bool, "notes": str|null}}`.

Contract pinned here:
  - GET assembles by UNIONING agent names present in `agent_overrides` (tier)
    and `config.agent_settings` (enabled/notes); absent-from-both = absent
    from the response array. `lead_overrides` is always `{}` (reserved, #1024).
  - PATCH is a per-agent UPSERT (merge, not whole-dict replace): each field
    (`enabled`/`model_override`/`notes`) is independently omittable —
    omitted = leave unchanged; `model_override` explicit null = clear the
    tier override; `enabled`/`notes` present = set in `config.agent_settings`.
  - Gate order (LOCKED by task brief, deliberately distinct from the
    `/progress-stats` 404-on-mismatch precedent): 400 missing X-Project-Id
    header -> 404 unknown/soft-deleted project -> 400 header/path mismatch.
  - Unknown agent name -> 422 (validated via the SAME `list_agents()` scan
    that backs GET /api/agents — never hardcoded).
  - Bad `model_override` enum -> 422 via the `AgentModelLiteral` Pydantic gate.
  - GET is value-tolerant on `agent_overrides` (Kanban #1018 M2, code review):
    an out-of-enum tier already sitting in the DB (legacy row / hand-edit /
    direct migration — `agent_overrides` has NO DB CHECK on its values)
    normalizes to `model_override: null` on read instead of 500ing. Locked by
    `test_get_agent_overrides_out_of_enum_tier_normalizes_to_null` below,
    seeded via a direct ORM write that bypasses the strict PATCH validator.

Cleanup uses `scaffold_cleanup` + DELETE /api/projects/{id} so the live-DB
row-count invariant in conftest stays happy.
"""

from __future__ import annotations

import uuid

import pytest

from src.models.project import Project


# ---- helpers ---------------------------------------------------------------

# Real, currently-valid agent names (per `.claude/agents/*.md`) — reused across
# tests so the PATCH-body name-existence gate (backed by the live filesystem
# scan) always passes for the "happy" cases.
_AGENT_A = "dev-backend"
_AGENT_B = "dev-frontend"
_AGENT_UNKNOWN = "definitely-not-a-real-agent-xyz"


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev", config: dict | None = None) -> dict:
    return {
        "name": name,
        "description": f"k1018 agent-overrides fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": config or {},
        "is_active": False,
        "team": team,
    }


async def _make_project(
    client, scaffold_cleanup, *, slug: str = "k1018", config: dict | None = None,
    agent_overrides: dict | None = None,
) -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    payload = _project_create_payload(name, config=config)
    if agent_overrides is not None:
        payload["agent_overrides"] = agent_overrides
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _hdr(project_id: int) -> dict:
    return {"X-Project-Id": str(project_id)}


# ---- 1. GET backfill: no overrides at all -> agents: [] --------------------


@pytest.mark.asyncio
async def test_get_agent_overrides_no_overrides_returns_empty_array(
    client, scaffold_cleanup
) -> None:
    """A freshly-created project has neither `agent_overrides` nor
    `config.agent_settings` populated -> `agents` is [] (not an error, not a
    full backfilled roster) and `lead_overrides` is the reserved `{}`.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k1018-empty")
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/agent-overrides", headers=_hdr(pid)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agents"] == [], body
        assert body["lead_overrides"] == {}, body
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 2. GET backfill: legacy tier-only row -> enabled defaults true --------


@pytest.mark.asyncio
async def test_get_agent_overrides_legacy_tier_only_backfills_enabled_true(
    client, scaffold_cleanup
) -> None:
    """A project with a #777 `agent_overrides` tier entry but NO
    `config.agent_settings` entry for that agent -> the assembled row still
    appears (unioned from `agent_overrides`), `enabled` backfills to `true`,
    `model_override` carries the tier, `notes` is null.
    """
    project = await _make_project(
        client,
        scaffold_cleanup,
        slug="k1018-legacy",
        agent_overrides={_AGENT_A: "opus"},
    )
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/agent-overrides", headers=_hdr(pid)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agents"] == [
            {
                "name": _AGENT_A,
                "enabled": True,
                "model_override": "opus",
                "notes": None,
            }
        ], body
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 2b. GET value-tolerant: out-of-enum tier normalizes to null (M2) ------


@pytest.mark.asyncio
async def test_get_agent_overrides_out_of_enum_tier_normalizes_to_null(
    client, scaffold_cleanup, db_session
) -> None:
    """Kanban #1018 M2 (code review) — `agent_overrides` (#777 tier map) is
    value-tolerant storage with NO DB CHECK on its values; the PATCH validator
    (`AgentModelLiteral`) only guards the STRICT write path. A tier string
    that predates a Literal tightening, or was hand-edited / written by a
    direct migration, can sit in the DB outside the current enum.

    Seed it via a DIRECT ORM write (`db_session`) — bypassing the strict API
    PATCH entirely, since the API itself would 422 on "gpt4" and could never
    produce this row shape through its own write path. This is the only way
    to reach the value-tolerant-read code path under test.

    POSITIVE: GET still returns 200 (not 500) and the OTHER, valid-tier agent
    passes through unaffected. NEGATIVE this locks: the bogus agent's
    `model_override` is `null`, not the raw "gpt4" string leaking onto the
    wire (which would violate the strict `AgentModelLiteral` response
    contract) and not a crash.
    """
    project = await _make_project(client, scaffold_cleanup, slug="k1018-m2")
    pid = project["id"]
    try:
        row = await db_session.get(Project, pid)
        assert row is not None, f"project id={pid} not found"
        # Direct ORM write — the strict Pydantic PATCH boundary never sees
        # "gpt4"; this row shape is only reachable by bypassing the API.
        row.agent_overrides = {_AGENT_A: "gpt4", _AGENT_B: "opus"}
        await db_session.commit()

        resp = await client.get(
            f"/api/projects/{pid}/agent-overrides", headers=_hdr(pid)
        )
        assert resp.status_code == 200, resp.text
        by_name = {a["name"]: a for a in resp.json()["agents"]}
        assert by_name[_AGENT_A]["model_override"] is None, by_name  # normalized, not "gpt4"
        assert by_name[_AGENT_A]["enabled"] is True, by_name  # backfill unaffected
        assert by_name[_AGENT_B]["model_override"] == "opus", by_name  # valid sibling untouched
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 3. PATCH enabled=false round-trips -------------------------------------


@pytest.mark.asyncio
async def test_patch_agent_overrides_enabled_false_round_trips(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1018-enabled")
    pid = project["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        assert patch.status_code == 200, patch.text
        row = next(a for a in patch.json()["agents"] if a["name"] == _AGENT_A)
        assert row["enabled"] is False, row
        assert row["model_override"] is None, row  # untouched (omitted)

        # GET round-trip confirms persistence (POSITIVE: mutation did happen).
        get_resp = await client.get(
            f"/api/projects/{pid}/agent-overrides", headers=_hdr(pid)
        )
        assert get_resp.status_code == 200, get_resp.text
        row2 = next(a for a in get_resp.json()["agents"] if a["name"] == _AGENT_A)
        assert row2["enabled"] is False, row2

        # NEGATIVE this locks: re-enabling flips it back (not a one-way/stuck value).
        re_enable = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": True}]},
        )
        assert re_enable.status_code == 200, re_enable.text
        row3 = next(a for a in re_enable.json()["agents"] if a["name"] == _AGENT_A)
        assert row3["enabled"] is True, row3
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 4. PATCH model_override set / clear ------------------------------------


@pytest.mark.asyncio
async def test_patch_agent_overrides_model_override_set_then_clear(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1018-tier")
    pid = project["id"]
    try:
        # Set.
        set_resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "model_override": "haiku"}]},
        )
        assert set_resp.status_code == 200, set_resp.text
        row = next(a for a in set_resp.json()["agents"] if a["name"] == _AGENT_A)
        assert row["model_override"] == "haiku", row

        # Belt-and-suspenders: the underlying #777 agent_overrides column
        # itself carries the tier (untouched storage/shape).
        get_project = await client.get(f"/api/projects/{pid}")
        assert get_project.json()["agent_overrides"] == {_AGENT_A: "haiku"}, get_project.json()

        # Clear via explicit null.
        clear_resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "model_override": None}]},
        )
        assert clear_resp.status_code == 200, clear_resp.text
        # Agent no longer has ANY override (no tier, no agent_settings entry
        # was ever set) -> absent from the array entirely (NEGATIVE: not a
        # lingering row with model_override=None).
        names = [a["name"] for a in clear_resp.json()["agents"]]
        assert _AGENT_A not in names, clear_resp.json()

        get_project2 = await client.get(f"/api/projects/{pid}")
        assert get_project2.json()["agent_overrides"] == {}, get_project2.json()
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 5. PATCH unknown agent -> 422 ------------------------------------------


@pytest.mark.asyncio
async def test_patch_agent_overrides_unknown_agent_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1018-unknown")
    pid = project["id"]
    try:
        resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_UNKNOWN, "enabled": False}]},
        )
        assert resp.status_code == 422, resp.text
        assert _AGENT_UNKNOWN in resp.json()["detail"], resp.json()

        # NEGATIVE this locks: nothing was written (partial-batch rejected
        # wholesale, not partially applied) — a mixed batch with one bad name
        # must not silently apply the good one.
        mixed = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={
                "agents": [
                    {"name": _AGENT_A, "enabled": False},
                    {"name": _AGENT_UNKNOWN, "enabled": False},
                ]
            },
        )
        assert mixed.status_code == 422, mixed.text
        get_resp = await client.get(
            f"/api/projects/{pid}/agent-overrides", headers=_hdr(pid)
        )
        names = [a["name"] for a in get_resp.json()["agents"]]
        assert _AGENT_A not in names, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 6. PATCH bad enum -> 422 -----------------------------------------------


@pytest.mark.asyncio
async def test_patch_agent_overrides_bad_model_enum_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1018-badenum")
    pid = project["id"]
    try:
        resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "model_override": "gpt5"}]},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        matches = [
            err
            for err in body["detail"]
            if "model_override" in err["loc"] and err["type"] == "literal_error"
        ]
        assert matches, f"expected model_override literal_error; got {body}"
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 7. other config keys preserved after PATCH -----------------------------


@pytest.mark.asyncio
async def test_patch_agent_overrides_preserves_other_config_keys(
    client, scaffold_cleanup
) -> None:
    """PATCH /agent-overrides read-modify-writes `config` — a PRE-EXISTING
    `standards` / `enabled_roles` key must survive untouched (no whole-dict
    clobber). This is the load-bearing negative for the read-modify-write
    discipline the brief mandates.
    """
    seed_config = {
        "standards": {"api": ["fastapi/general.md"], "web": [], "db": []},
        "enabled_roles": [1, 2, 6],
    }
    project = await _make_project(
        client, scaffold_cleanup, slug="k1018-preserve", config=seed_config
    )
    pid = project["id"]
    try:
        # Sanity: seed actually landed.
        pre = await client.get(f"/api/projects/{pid}")
        assert pre.json()["config"]["standards"] == seed_config["standards"], pre.json()
        assert pre.json()["config"]["enabled_roles"] == [1, 2, 6], pre.json()

        patch = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_B, "notes": "flaky on large diffs"}]},
        )
        assert patch.status_code == 200, patch.text

        post = await client.get(f"/api/projects/{pid}")
        cfg = post.json()["config"]
        # Both pre-existing keys intact (POSITIVE: nothing dropped).
        assert cfg["standards"] == seed_config["standards"], cfg
        assert cfg["enabled_roles"] == [1, 2, 6], cfg
        # New subkey landed alongside them (POSITIVE: the write did happen).
        assert cfg["agent_settings"][_AGENT_B]["notes"] == "flaky on large diffs", cfg
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 8. cross-project -> 400 ------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_overrides_cross_project_returns_400(
    client, scaffold_cleanup
) -> None:
    """Header bound to a DIFFERENT project than the path -> 400 (LOCKED gate
    order per task brief — deliberately NOT the 404 that /progress-stats uses
    for the same mismatch shape; this endpoint's contract is 400).
    """
    project_b = await _make_project(client, scaffold_cleanup, slug="k1018-cross")
    pid_b = project_b["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid_b}/agent-overrides",
            headers={"X-Project-Id": "1"},
        )
        assert resp.status_code == 400, resp.text
    finally:
        await client.delete(f"/api/projects/{pid_b}")


@pytest.mark.asyncio
async def test_patch_agent_overrides_cross_project_returns_400(
    client, scaffold_cleanup
) -> None:
    project_b = await _make_project(client, scaffold_cleanup, slug="k1018-crosspatch")
    pid_b = project_b["id"]
    try:
        resp = await client.patch(
            f"/api/projects/{pid_b}/agent-overrides",
            headers={"X-Project-Id": "1"},
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        assert resp.status_code == 400, resp.text
    finally:
        await client.delete(f"/api/projects/{pid_b}")


# ---- 9. unknown project -> 404 ----------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_overrides_missing_project_returns_404(client) -> None:
    resp = await client.get(
        "/api/projects/9999999/agent-overrides",
        headers={"X-Project-Id": "9999999"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Project id=9999999 not found"


@pytest.mark.asyncio
async def test_patch_agent_overrides_missing_project_returns_404(client) -> None:
    resp = await client.patch(
        "/api/projects/9999999/agent-overrides",
        headers={"X-Project-Id": "9999999"},
        json={"agents": [{"name": _AGENT_A, "enabled": False}]},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Project id=9999999 not found"


# ---- 10. missing X-Project-Id header -> 400 ---------------------------------


@pytest.mark.asyncio
async def test_get_agent_overrides_missing_header_returns_400(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k1018-nohdr")
    pid = project["id"]
    try:
        resp = await client.get(f"/api/projects/{pid}/agent-overrides")
        assert resp.status_code == 400, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")
