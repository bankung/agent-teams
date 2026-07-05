"""Kanban #2566 — async-HITL gate-resume integration into /api/tasks/next-autorun.

Wires the gate lifecycle (Tasks A #2564 / B #2565) into the Mode-A auto-run
picker. When a gate is fully answered, resolve_gate flips the work-task ps
8->TODO with halt_reason=None — which WITHOUT this change matches next_task_stmt
and is picked up as if FRESH, losing the resume signal. The fix surfaces
gate-resumed tasks on a clean separate predicate (NextAutorunResponse.
gate_resume_tasks) and keeps them OUT of next_task. Design lock:
`async-hitl-gates.md` §7 + §8.

Pattern mirrors test_task_gates_smoke.py: HTTP-client over fresh isolated
projects (no live agent_teams DB rows). Gates are opened/resolved via the real
wire endpoints so the ps=8 -> resolve -> ps=TODO transitions are exercised
end-to-end.

DO NOT RUN IN-SESSION — the block-pytest hook denies it; the operator runs these
in a plain terminal.

Coverage (predicate-level — CODE-VERIFIABLE NOW; the live "fresh runner actually
resumes from resume_context" path is ZommmBeeean runner #2531, not this task):
  (AC2) answered gate (0 open) + auto + ps=TODO -> in gate_resume_tasks, NOT next_task
  (AC3) >=1 OPEN gate (ps=8) -> in NONE; after answering -> in gate_resume_tasks
  (AC4) blocked_by (blocker not DONE) + answered gate -> absent; blocker DONE -> present;
        blocker DONE but a gate still open -> absent
  (AC1) resolve MERGES the answer without clobbering a pre-existing resume_context snapshot
  (regression) plain fresh TODO (no gates) -> in next_task, NOT gate_resume_tasks
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror test_task_gates_smoke.py's harness)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"gate-resume fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_auto_task(
    client, project_id: int, title: str, **extras
) -> dict:
    """Create an auto_pickup AI work-task (the lane the picker reads)."""
    body = {
        "project_id": project_id,
        "title": title,
        "interaction_kind": "work",
        "run_mode": "auto_pickup",
        "task_kind": "ai",
        **extras,
    }
    resp = await client.post(
        "/api/tasks", headers={"X-Project-Id": str(project_id)}, json=body
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _patch_task(client, project_id: int, task_id: int, **fields) -> dict:
    resp = await client.patch(
        f"/api/tasks/{task_id}",
        headers={"X-Project-Id": str(project_id)},
        json=fields,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _open_gate(
    client,
    project_id: int,
    task_id: int,
    *,
    kind: str = "decision",
    gate_tier: str = "decision",
    question: str = "Proceed?",
) -> dict:
    resp = await client.post(
        f"/api/tasks/{task_id}/gates",
        headers={"X-Project-Id": str(project_id)},
        json={
            "kind": kind,
            "gate_tier": gate_tier,
            "question_payload": {"question": question},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _resolve_gate(
    client, project_id: int, gate_id: int, *, answer: str = "ok", provenance: str = "telegram"
) -> dict:
    resp = await client.post(
        f"/api/task-gates/{gate_id}/resolve",
        headers={"X-Project-Id": str(project_id)},
        json={"answer": answer, "provenance": provenance},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _next_autorun(client, project_id: int) -> dict:
    resp = await client.get(
        "/api/tasks/next-autorun", headers={"X-Project-Id": str(project_id)}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ids(rows: list[dict]) -> list[int]:
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# (AC2) answered gate (0 open) -> gate_resume_tasks; NOT next_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answered_gate_surfaces_in_gate_resume_not_next_task(
    client, scaffold_cleanup
) -> None:
    """A task whose only gate is answered (0 open, ps flipped 8->TODO, auto
    run_mode) appears in gate_resume_tasks AND is absent from next_task — the
    core §7 partition (resume, do not start fresh)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2566-ac2")
    task = await _make_auto_task(client, pid, "gate-resumed task")

    gate = await _open_gate(client, pid, task["id"])
    # Opening a gate halts the task to ps=8 — so it must NOT be next_task here.
    mid = await _next_autorun(client, pid)
    assert task["id"] not in _ids([mid["next_task"]] if mid["next_task"] else []), mid
    assert task["id"] not in _ids(mid["gate_resume_tasks"]), (
        "an OPEN gate (ps=8) must not appear in gate_resume_tasks yet"
    )

    # Resolve the only gate -> 0 open -> ps flips 8->TODO.
    res = await _resolve_gate(client, pid, gate["id"])
    assert res["open_gate_count_remaining"] == 0
    assert res["process_status"] == 1, "ps flips to TODO at count 0"

    body = await _next_autorun(client, pid)
    # POSITIVE: it surfaces on the gate-resume lane.
    assert task["id"] in _ids(body["gate_resume_tasks"]), (
        f"answered-gate task {task['id']} must be in gate_resume_tasks: {body}"
    )
    # NEGATIVE (the whole point): it is NOT offered as a fresh pickup.
    fresh_id = body["next_task"]["id"] if body["next_task"] else None
    assert fresh_id != task["id"], (
        f"answered-gate task {task['id']} must NOT be picked up fresh as next_task: {body}"
    )


# ---------------------------------------------------------------------------
# (AC3) open gate -> NONE; after answering -> gate_resume_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_gate_in_no_lane_then_resumes_after_answer(
    client, scaffold_cleanup
) -> None:
    """A task with >=1 OPEN gate (ps=8) appears in NONE of next_task /
    resume_tasks / pending_questions / gate_resume_tasks; once answered (0 open,
    ps->TODO) it appears in gate_resume_tasks."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2566-ac3")
    task = await _make_auto_task(client, pid, "open-gate task")
    gate = await _open_gate(client, pid, task["id"])

    body = await _next_autorun(client, pid)
    tid = task["id"]
    assert (body["next_task"] or {}).get("id") != tid, body
    assert tid not in _ids(body["resume_tasks"]), body
    assert tid not in _ids(body["pending_questions"]), body
    assert tid not in _ids(body["gate_resume_tasks"]), (
        f"open-gate task {tid} (ps=8) must be in no lane: {body}"
    )

    # Answer it.
    await _resolve_gate(client, pid, gate["id"])
    body2 = await _next_autorun(client, pid)
    assert tid in _ids(body2["gate_resume_tasks"]), (
        f"after answering, task {tid} must surface in gate_resume_tasks: {body2}"
    )
    assert (body2["next_task"] or {}).get("id") != tid, (
        "still must not be a fresh pickup"
    )


# ---------------------------------------------------------------------------
# (AC4) combinatorial edge — legacy blocked_by AND a gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_resume_requires_blocker_terminal(client, scaffold_cleanup) -> None:
    """A task with BOTH a blocked_by (blocker NOT done) AND an answered gate is
    ABSENT from gate_resume_tasks; flipping the blocker to DONE makes it appear.
    §7 combinatorial edge: actionable only when blocker terminal AND 0 open
    gates."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2566-ac4")

    blocker = await _make_auto_task(
        client, pid, "blocker", run_mode="manual", task_kind="human"
    )
    task = await _make_auto_task(
        client, pid, "blocked + gated task", blocked_by=blocker["id"]
    )
    gate = await _open_gate(client, pid, task["id"])
    await _resolve_gate(client, pid, gate["id"])  # 0 open, but blocker still TODO

    body = await _next_autorun(client, pid)
    assert task["id"] not in _ids(body["gate_resume_tasks"]), (
        f"answered-gate task {task['id']} with a live blocker must be ABSENT: {body}"
    )

    # Flip the blocker DONE -> now both conditions hold.
    await _patch_task(client, pid, blocker["id"], process_status=5)
    body2 = await _next_autorun(client, pid)
    assert task["id"] in _ids(body2["gate_resume_tasks"]), (
        f"with blocker DONE + 0 open gates, task {task['id']} must appear: {body2}"
    )


@pytest.mark.asyncio
async def test_blocker_done_but_gate_open_still_absent(client, scaffold_cleanup) -> None:
    """The mirror edge: blocker DONE but a gate still OPEN (ps=8) -> absent from
    gate_resume_tasks (and every other lane)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2566-ac4b")

    blocker = await _make_auto_task(
        client, pid, "done blocker", run_mode="manual", task_kind="human"
    )
    await _patch_task(client, pid, blocker["id"], process_status=5)
    task = await _make_auto_task(
        client, pid, "blocker-done but gate-open task", blocked_by=blocker["id"]
    )
    await _open_gate(client, pid, task["id"])  # ps -> 8, gate stays open

    body = await _next_autorun(client, pid)
    assert task["id"] not in _ids(body["gate_resume_tasks"]), (
        f"open gate (ps=8) must keep task {task['id']} out of gate_resume even with a DONE blocker: {body}"
    )
    assert (body["next_task"] or {}).get("id") != task["id"], body


# ---------------------------------------------------------------------------
# (AC1) resolve MERGES the answer without clobbering a pre-existing snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_preserves_preexisting_resume_context(
    client, scaffold_cleanup
) -> None:
    """§8 self-sufficiency: the gate-OPENER persists a "where I was" halt snapshot
    into resume_context; resolve_gate MERGES the answer in WITHOUT clobbering it,
    so a fresh runner reads the merged whole. This proves a runner halt-snapshot
    survives the answer-fold (the live fresh-resume drain-run is runner #2531).
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2566-ac1")
    task = await _make_auto_task(client, pid, "merge-preservation task")

    # Pre-seed a halt snapshot (simulates the opener's PATCH-based capture).
    await _patch_task(
        client, pid, task["id"], resume_context={"step": "drafting", "note": "where I was"}
    )

    gate = await _open_gate(client, pid, task["id"])
    res = await _resolve_gate(client, pid, gate["id"], answer="approve")

    rc = res["resume_context"]
    assert rc is not None, res
    # POSITIVE: the pre-existing snapshot survives the answer-fold.
    assert rc["step"] == "drafting", f"pre-existing snapshot clobbered: {rc}"
    assert rc["note"] == "where I was", f"pre-existing snapshot clobbered: {rc}"
    # POSITIVE: the new gate-answer keys are present alongside it.
    assert str(gate["id"]) in rc["answered_gates"], rc
    assert rc["answered_gates"][str(gate["id"])]["answer"] == "approve"
    assert rc["last_answered_gate_id"] == gate["id"]

    # Refetch the row (don't trust the response alone) — the merge is durable.
    full = (
        await client.get(
            f"/api/tasks/{task['id']}", headers={"X-Project-Id": str(pid)}
        )
    ).json()
    assert full["resume_context"]["step"] == "drafting", full
    assert str(gate["id"]) in full["resume_context"]["answered_gates"], full


# ---------------------------------------------------------------------------
# (regression) plain fresh TODO (no gates) -> next_task, NOT gate_resume_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_todo_task_unaffected_by_partition(client, scaffold_cleanup) -> None:
    """A plain auto_pickup TODO task with NO gates still appears in next_task and
    NEVER in gate_resume_tasks — proves the partition clause cannot regress
    gate-free tasks (zero task_gates rows -> EXISTS false -> ~ true)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "k2566-reg")
    task = await _make_auto_task(client, pid, "plain fresh task")

    body = await _next_autorun(client, pid)
    assert body["next_task"] is not None, body
    assert body["next_task"]["id"] == task["id"], (
        f"plain TODO task {task['id']} must still be next_task: {body}"
    )
    assert task["id"] not in _ids(body["gate_resume_tasks"]), (
        f"gate-free task {task['id']} must NOT appear in gate_resume_tasks: {body}"
    )
