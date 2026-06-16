"""Kanban #1839 — process_status=8 'halted-pending-user' + halted_at stamp.

Contract-smoke coverage for the new lifecycle code + companion timestamp.
The rigorous edge/regression suite is dev-tester's domain; this is the
first-pass wiring proof for the new surface.

Coverage:
  (1) PATCH {process_status: 8} -> 200 (accepted by the validator + CHECK).
  (2) PATCH {process_status: 7} -> 422 (7 is reserved/not in TaskStatus.ALL).
  (3) Stamp: PATCH ->8 on a halted_at-NULL row stamps halted_at; a second
      unrelated PATCH does NOT re-stamp; a client-supplied halted_at is
      respected (parity with started_at/completed_at).
  (4) halted_at is present in the TaskRead payload (GET /api/tasks/{id}).
  (5) AC3 no-regression: setting halt_reason still halts via the flag, a
      halt_reason PATCH does NOT change process_status, and the
      halt_reason-keyed resume path is unaffected by ps=8.
  (6) next_task does NOT return a ps=8 row (auto-pickup skip, AC1).

Mirrors the helper/fixture pattern in test_tasks_next_autorun.py — HTTP
against the isolated agent_teams_test DB via the `client` fixture.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.constants import TaskStatus

# Local constant mirror so the test reads intent, not a magic number.
_HALTED = TaskStatus.HALTED_PENDING_USER  # 8
_RESERVED_7 = 7


# ---------------------------------------------------------------------------
# Helpers (mirror test_tasks_next_autorun.py)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"test fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_task(client, project_id: int, title: str, **extras) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    body = {"project_id": project_id, "title": title, **extras}
    resp = await client.post("/api/tasks", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(client, project_id: int, task_id: int, **fields) -> tuple[int, dict]:
    """PATCH that does NOT assert status — returns (status_code, json) so
    negative cases (422) can inspect the envelope."""
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(f"/api/tasks/{task_id}", json=fields, headers=headers)
    return resp.status_code, (resp.json() if resp.content else {})


async def _get_task(client, project_id: int, task_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_next_autorun(client, project_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get("/api/tasks/next-autorun", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# (1) PATCH ps=8 accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_process_status_8_accepted(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-accept8")
    t = await _make_task(client, pid, "halt me", run_mode="auto_pickup", task_kind="ai")

    code, body = await _patch_task(client, pid, t["id"], process_status=_HALTED)
    assert code == 200, body
    assert body["process_status"] == _HALTED


# ---------------------------------------------------------------------------
# (2) PATCH ps=7 rejected (reserved)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_process_status_7_rejected(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-reject7")
    t = await _make_task(client, pid, "no seven", run_mode="auto_pickup", task_kind="ai")

    code, body = await _patch_task(client, pid, t["id"], process_status=_RESERVED_7)
    assert code == 422, body
    msgs = " | ".join(err["msg"] for err in body["detail"])
    # Message renders TaskStatus.ALL verbatim — 7 absent, 8 present.
    assert "process_status must be one of (1, 2, 3, 4, 5, 6, 8), got 7" in msgs


# ---------------------------------------------------------------------------
# (3) halted_at stamp semantics — stamp once, no re-stamp, client value respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halted_at_stamped_on_transition(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-stamp")
    t = await _make_task(client, pid, "stamp me", run_mode="auto_pickup", task_kind="ai")
    assert t["halted_at"] is None  # not stamped on create

    code, body = await _patch_task(client, pid, t["id"], process_status=_HALTED)
    assert code == 200, body
    assert body["halted_at"] is not None, "halted_at must be stamped on ->8 transition"


@pytest.mark.asyncio
async def test_halted_at_not_restamped_on_later_patch(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-norestamp")
    t = await _make_task(client, pid, "stamp once", run_mode="auto_pickup", task_kind="ai")

    code, halted = await _patch_task(client, pid, t["id"], process_status=_HALTED)
    assert code == 200, halted
    first_stamp = halted["halted_at"]
    assert first_stamp is not None

    # An unrelated PATCH (title change) must NOT touch halted_at.
    code, after = await _patch_task(client, pid, t["id"], title="renamed")
    assert code == 200, after
    assert after["halted_at"] == first_stamp, "unrelated PATCH must not re-stamp halted_at"

    # Move off 8 then back to 8 — halted_at PERSISTS (not auto-cleared) and is
    # NOT re-stamped (the field is already non-NULL). This is the locked
    # non-goal: re-stamp-on-each-halt is deliberately out of scope.
    code, todo = await _patch_task(client, pid, t["id"], process_status=TaskStatus.TODO)
    assert code == 200, todo
    assert todo["halted_at"] == first_stamp, "halted_at must persist off ps=8"

    code, rehalt = await _patch_task(client, pid, t["id"], process_status=_HALTED)
    assert code == 200, rehalt
    assert rehalt["halted_at"] == first_stamp, "re-halt must NOT re-stamp halted_at"


@pytest.mark.asyncio
async def test_halted_at_client_value_respected(client, scaffold_cleanup) -> None:
    """Parity with started_at/completed_at: a client-supplied halted_at on the
    ->8 PATCH is respected by the setdefault stamp logic (not overwritten)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-client")
    t = await _make_task(client, pid, "client stamp", run_mode="auto_pickup", task_kind="ai")

    explicit = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    code, body = await _patch_task(
        client, pid, t["id"], process_status=_HALTED, halted_at=explicit.isoformat()
    )
    assert code == 200, body
    # Server stores the client value, not func.now().
    returned = datetime.fromisoformat(body["halted_at"])
    if returned.tzinfo is None:
        returned = returned.replace(tzinfo=timezone.utc)
    assert returned == explicit, f"client halted_at not respected: {body['halted_at']!r}"


# ---------------------------------------------------------------------------
# (4) halted_at present in TaskRead payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halted_at_in_taskread_payload(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-payload")
    t = await _make_task(client, pid, "read me", run_mode="auto_pickup", task_kind="ai")

    read = await _get_task(client, pid, t["id"])
    assert "halted_at" in read, "halted_at must be a TaskRead field"
    assert read["halted_at"] is None  # NULL until halted


# ---------------------------------------------------------------------------
# (5) AC3 no-regression — halt_reason flag is fully orthogonal to ps=8
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_reason_patch_does_not_change_process_status(
    client, scaffold_cleanup
) -> None:
    """Setting halt_reason halts via the MVP flag (#785) and leaves
    process_status UNCHANGED — the two mechanisms are decoupled (AC3)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-ac3")
    t = await _make_task(client, pid, "flag halt", run_mode="auto_pickup", task_kind="ai")
    assert t["process_status"] == TaskStatus.TODO

    code, body = await _patch_task(
        client, pid, t["id"], halt_reason="waiting for decision #999"
    )
    assert code == 200, body
    assert body["halt_reason"] == "waiting for decision #999"
    # The hard invariant: a halt_reason PATCH does NOT auto-derive ps=8.
    assert body["process_status"] == TaskStatus.TODO, (
        "halt_reason must NOT change process_status (AC3 orthogonality)"
    )
    # And halted_at is NOT stamped by a halt_reason flag (only the ->8 transition).
    assert body["halted_at"] is None, "halt_reason must NOT stamp halted_at"


@pytest.mark.asyncio
async def test_halt_reason_resume_path_unaffected_by_ps8(client, scaffold_cleanup) -> None:
    """A halt_reason-flagged task whose blocker is DONE still surfaces in
    resume_tasks; a separate ps=8 task does NOT leak into that list (the
    resume query is halt_reason-keyed, not process_status-keyed)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-resume")

    # Plain blocker task, then mark it DONE (resume query keys on the blocker
    # being DONE, not on its interaction_kind — so a plain task suffices).
    blocker = await _make_task(
        client, pid, "blocker q", run_mode="auto_pickup", task_kind="ai",
    )
    code, _ = await _patch_task(client, pid, blocker["id"], process_status=TaskStatus.DONE)
    assert code == 200

    # Halted (flag) task blocked by the now-DONE blocker -> resumable.
    halted = await _make_task(
        client, pid, "halted flag", run_mode="auto_pickup", task_kind="ai",
        halt_reason="waiting", blocked_by=blocker["id"],
    )

    # A ps=8 task with NO halt_reason and NO blocker — must NOT appear in resume.
    ps8 = await _make_task(client, pid, "ps8 only", run_mode="auto_pickup", task_kind="ai")
    code, _ = await _patch_task(client, pid, ps8["id"], process_status=_HALTED)
    assert code == 200

    body = await _get_next_autorun(client, pid)
    resume_ids = {r["id"] for r in body["resume_tasks"]}
    assert halted["id"] in resume_ids, "halt_reason resume path regressed"
    assert ps8["id"] not in resume_ids, "ps=8 row must not leak into resume_tasks"


# ---------------------------------------------------------------------------
# (6) next_task auto-pickup skip — ps=8 is structurally excluded (TODO-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_task_skips_ps8_row(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "k1839-skip")
    t = await _make_task(client, pid, "halt then check", run_mode="auto_pickup", task_kind="ai")

    code, _ = await _patch_task(client, pid, t["id"], process_status=_HALTED)
    assert code == 200

    body = await _get_next_autorun(client, pid)
    # Only a TODO auto_pickup row qualifies; the ps=8 row is excluded.
    assert body["next_task"] is None, (
        f"ps=8 row must not be returned as next_task; got {body['next_task']!r}"
    )
