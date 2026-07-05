"""Kanban #2768 — audit trail for per-project agent_overrides / agent_settings
changes.

Reuses `projects_audit` (Kanban #1209/#1211's kill/revive/pause ledger)
rather than a new table — the 6th action `agent_config` rides the existing
`drain_summary` JSONB column as a generic change-delta payload:
    {"changes": {"<agent>": {"<field>": {"from": X, "to": Y}}}}

Coverage:
- enabled-flip PATCH writes one row with the right delta.
- tier (model_override) set + clear each captured with from/to.
- notes change captured.
- a no-op PATCH (values already match, or nothing effectively changes)
  writes NO row.
- `X-Actor` header stamps `projects_audit.actor` (default 'operator').
- GET /{id}/audit-log returns rows newest-first, `action` filter works,
  an unknown action value 422s, and rows are scoped to their own project
  (never leak across projects).
- Existing #1018 agent-overrides tests (test_project_agent_overrides.py)
  are untouched and still importable — this file adds audit coverage
  alongside them, it does not replace them.

Tests run against `agent_teams_test`. Live `agent_teams` row count must NOT
drift across the session — the `_live_db_row_count_invariant` fixture in
conftest.py asserts that.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.models.projects_audit import ProjectsAudit

# Real, currently-valid agent names (per `.claude/agents/*.md`) — mirrors
# test_project_agent_overrides.py's convention so the PATCH-body
# name-existence gate (backed by the live filesystem scan) always passes.
_AGENT_A = "dev-backend"
_AGENT_B = "dev-frontend"


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k2768 audit fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


async def _make_project(client, scaffold_cleanup, *, slug: str = "k2768") -> dict:
    name = scaffold_cleanup(_unique_name(slug))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _hdr(project_id: int) -> dict:
    return {"X-Project-Id": str(project_id)}


# ---- 1. enabled-flip writes a row with the right delta ---------------------


@pytest.mark.asyncio
async def test_patch_enabled_flip_writes_audit_row_with_delta(
    client, scaffold_cleanup, db_session
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-enabled")
    pid = project["id"]
    try:
        resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        assert resp.status_code == 200, resp.text

        rows = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
            )
        ).scalars().all()
        assert len(rows) == 1, rows
        row = rows[0]
        assert row.actor == "operator"
        assert row.reason is None
        assert row.drain_summary == {
            "changes": {_AGENT_A: {"enabled": {"from": True, "to": False}}}
        }, row.drain_summary
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 2. tier set + clear each captured with from/to -------------------------


@pytest.mark.asyncio
async def test_patch_tier_set_then_clear_each_captured(
    client, scaffold_cleanup, db_session
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-tier")
    pid = project["id"]
    try:
        set_resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "model_override": "haiku"}]},
        )
        assert set_resp.status_code == 200, set_resp.text

        clear_resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "model_override": None}]},
        )
        assert clear_resp.status_code == 200, clear_resp.text

        rows = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
                .order_by(ProjectsAudit.created_at.asc())
            )
        ).scalars().all()
        assert len(rows) == 2, rows
        set_row, clear_row = rows
        assert set_row.drain_summary == {
            "changes": {_AGENT_A: {"tier": {"from": None, "to": "haiku"}}}
        }, set_row.drain_summary
        assert clear_row.drain_summary == {
            "changes": {_AGENT_A: {"tier": {"from": "haiku", "to": None}}}
        }, clear_row.drain_summary
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 3. notes change captured ------------------------------------------------


@pytest.mark.asyncio
async def test_patch_notes_change_captured(
    client, scaffold_cleanup, db_session
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-notes")
    pid = project["id"]
    try:
        resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_B, "notes": "flaky on large diffs"}]},
        )
        assert resp.status_code == 200, resp.text

        row = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
            )
        ).scalar_one()
        assert row.drain_summary == {
            "changes": {
                _AGENT_B: {"notes": {"from": None, "to": "flaky on large diffs"}}
            }
        }, row.drain_summary
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 4. no-op PATCH writes NO row -------------------------------------------


@pytest.mark.asyncio
async def test_patch_noop_writes_no_audit_row(
    client, scaffold_cleanup, db_session
) -> None:
    """Re-sending the SAME value that's already effective (enabled=True,
    which is the backfill default with no prior agent_settings entry) must
    not write an audit row — the delta reflects EFFECTIVE change, not
    request shape (POSITIVE: a real change still writes a row afterward,
    proving the no-op result isn't a broken write path).
    """
    project = await _make_project(client, scaffold_cleanup, slug="k2768-noop")
    pid = project["id"]
    try:
        noop = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": True}]},
        )
        assert noop.status_code == 200, noop.text

        rows_after_noop = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
            )
        ).scalars().all()
        assert rows_after_noop == [], rows_after_noop  # NEGATIVE: no-op wrote nothing

        real_change = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        assert real_change.status_code == 200, real_change.text
        rows_after_change = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
            )
        ).scalars().all()
        assert len(rows_after_change) == 1, rows_after_change  # POSITIVE: write path works
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 4b. mixed batch: delta only includes the agent that actually changed ---


@pytest.mark.asyncio
async def test_patch_mixed_batch_delta_only_includes_changed_agent(
    client, scaffold_cleanup, db_session
) -> None:
    """A single PATCH touching TWO agents where only one (A) effectively
    changes and the other (B) re-sends its current no-op value must still
    write exactly ONE audit row, and that row's `changes` dict must contain
    ONLY A's key — B must not appear even as an empty/no-op entry.

    Setup: B is first given a real `enabled=False` so its later re-send of
    `enabled=False` in the mixed batch is a genuine "already effective"
    no-op (not merely the absent-key backfill-default shortcut that
    `test_patch_noop_writes_no_audit_row` already covers for a fresh agent).
    """
    project = await _make_project(client, scaffold_cleanup, slug="k2768-mixed")
    pid = project["id"]
    try:
        seed = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_B, "enabled": False}]},
        )
        assert seed.status_code == 200, seed.text

        mixed = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={
                "agents": [
                    {"name": _AGENT_A, "enabled": False},  # real change: True -> False
                    {"name": _AGENT_B, "enabled": False},  # no-op: already False
                ]
            },
        )
        assert mixed.status_code == 200, mixed.text

        rows = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
                .order_by(ProjectsAudit.created_at.asc())
            )
        ).scalars().all()
        # 1 row from the seed PATCH (B: True->False) + 1 row from the mixed
        # PATCH (A only) = 2 total; the mixed-batch row is the one under test.
        assert len(rows) == 2, rows
        mixed_row = rows[-1]
        assert mixed_row.drain_summary == {
            "changes": {_AGENT_A: {"enabled": {"from": True, "to": False}}}
        }, mixed_row.drain_summary  # POSITIVE: A present with correct delta
        assert _AGENT_B not in mixed_row.drain_summary["changes"], mixed_row.drain_summary  # NEGATIVE: B (no-op) absent
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 5. X-Actor header stamped -----------------------------------------------


@pytest.mark.asyncio
async def test_patch_honors_x_actor_header(
    client, scaffold_cleanup, db_session
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-actor")
    pid = project["id"]
    try:
        headers = _hdr(pid)
        headers["X-Actor"] = "project-auditor"
        resp = await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=headers,
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        assert resp.status_code == 200, resp.text

        row = (
            await db_session.execute(
                select(ProjectsAudit)
                .where(ProjectsAudit.project_id == pid)
                .where(ProjectsAudit.action == "agent_config")
            )
        ).scalar_one()
        assert row.actor == "project-auditor"
    finally:
        await client.delete(f"/api/projects/{pid}")


# ---- 6. GET audit-log: newest-first + action filter + scoping ---------------


@pytest.mark.asyncio
async def test_audit_log_returns_rows_newest_first(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-log")
    pid = project["id"]
    try:
        await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_B, "enabled": False}]},
        )

        resp = await client.get(f"/api/projects/{pid}/audit-log")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 2, rows
        # Newest first: the SECOND patch (agent B) is rows[0].
        assert rows[0]["drain_summary"]["changes"].get(_AGENT_B) is not None, rows
        assert rows[1]["drain_summary"]["changes"].get(_AGENT_A) is not None, rows
        assert rows[0]["created_at"] >= rows[1]["created_at"], rows
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_audit_log_action_filter(client, scaffold_cleanup) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-filter")
    pid = project["id"]
    try:
        await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        # A row of a DIFFERENT action ('kill' has its own required-reason
        # POST; simplest way to get a second action value on this project
        # without a second endpoint call is to filter for one that yields
        # zero rows — proves the WHERE clause is actually applied, not a
        # no-op query param.
        only_agent_config = await client.get(
            f"/api/projects/{pid}/audit-log", params={"action": "agent_config"}
        )
        assert only_agent_config.status_code == 200, only_agent_config.text
        assert len(only_agent_config.json()) == 1, only_agent_config.json()

        only_kill = await client.get(
            f"/api/projects/{pid}/audit-log", params={"action": "kill"}
        )
        assert only_kill.status_code == 200, only_kill.text
        assert only_kill.json() == [], only_kill.json()  # NEGATIVE: filter excludes non-matching rows
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_audit_log_unknown_action_returns_422(
    client, scaffold_cleanup
) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-badaction")
    pid = project["id"]
    try:
        resp = await client.get(
            f"/api/projects/{pid}/audit-log", params={"action": "not_a_real_action"}
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_audit_log_scoped_to_own_project(
    client, scaffold_cleanup
) -> None:
    """Project A's audit-log GET never surfaces project B's rows, and vice
    versa — pure row-level `WHERE project_id = :id` scoping (this endpoint
    has no X-Project-Id header gate, unlike the sibling agent-overrides
    GET/PATCH; scoping is enforced entirely by the path project_id).
    """
    project_a = await _make_project(client, scaffold_cleanup, slug="k2768-scope-a")
    project_b = await _make_project(client, scaffold_cleanup, slug="k2768-scope-b")
    pid_a, pid_b = project_a["id"], project_b["id"]
    try:
        await client.patch(
            f"/api/projects/{pid_a}/agent-overrides",
            headers=_hdr(pid_a),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        await client.patch(
            f"/api/projects/{pid_b}/agent-overrides",
            headers=_hdr(pid_b),
            json={"agents": [{"name": _AGENT_B, "enabled": False}]},
        )

        log_a = await client.get(f"/api/projects/{pid_a}/audit-log")
        log_b = await client.get(f"/api/projects/{pid_b}/audit-log")
        assert log_a.status_code == 200, log_a.text
        assert log_b.status_code == 200, log_b.text

        names_a = {
            name
            for row in log_a.json()
            for name in row["drain_summary"]["changes"]
        }
        names_b = {
            name
            for row in log_b.json()
            for name in row["drain_summary"]["changes"]
        }
        assert names_a == {_AGENT_A}, names_a  # POSITIVE: own row present
        assert names_b == {_AGENT_B}, names_b  # POSITIVE: own row present
        assert _AGENT_B not in names_a, names_a  # NEGATIVE: no cross-project leak
        assert _AGENT_A not in names_b, names_b  # NEGATIVE: no cross-project leak
    finally:
        await client.delete(f"/api/projects/{pid_a}")
        await client.delete(f"/api/projects/{pid_b}")


@pytest.mark.asyncio
async def test_audit_log_missing_project_returns_404(client) -> None:
    resp = await client.get("/api/projects/9999999/audit-log")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Project id=9999999 not found"


@pytest.mark.asyncio
async def test_audit_log_limit_param(client, scaffold_cleanup) -> None:
    project = await _make_project(client, scaffold_cleanup, slug="k2768-limit")
    pid = project["id"]
    try:
        await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_A, "enabled": False}]},
        )
        await client.patch(
            f"/api/projects/{pid}/agent-overrides",
            headers=_hdr(pid),
            json={"agents": [{"name": _AGENT_B, "enabled": False}]},
        )

        resp = await client.get(
            f"/api/projects/{pid}/audit-log", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == 1, resp.json()

        too_high = await client.get(
            f"/api/projects/{pid}/audit-log", params={"limit": 201}
        )
        assert too_high.status_code == 422, too_high.text
    finally:
        await client.delete(f"/api/projects/{pid}")
