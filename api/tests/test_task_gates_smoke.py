"""Kanban #2564 — async-HITL `task_gates` contract-smoke tests.

First-pass contract smokes for the gate foundation (`async-hitl-gates.md` §4):
  - open a gate -> work-task halts (ps=8) + operator_gate set
  - open -> resolve happy path (answer folded into resume_context, ps flips back)
  - idempotent stale-reject on a non-open gate (409, not 5xx)
  - multi-gate concurrency: out-of-order resolve binds by gate_id; the task is
    actionable ONLY when open-gate-count -> 0
  - unified pending read unions legacy operator-HITL + open task_gates rows

DO NOT RUN IN-SESSION — the block-pytest hook denies it; the operator runs these
in a plain terminal. The comprehensive edge/regression matrix is dev-tester's
domain. These lock the wire contract per AC1-AC4 of #2564.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirror test_hitl_resolve_smoke.py's harness)
# ---------------------------------------------------------------------------


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"smoke fixture for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_work_task(client, project_id: int, title: str = "Gate work task") -> int:
    """Create a plain work-task in TODO (the thing a gate halts)."""
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json={"project_id": project_id, "title": title, "interaction_kind": "work"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _open_gate(
    client,
    project_id: int,
    task_id: int,
    *,
    kind: str = "decision",
    gate_tier: str = "decision",
    question: str = "Proceed?",
    options: list | None = None,
) -> dict:
    qp: dict = {"question": question}
    if options is not None:
        qp["options"] = options
    resp = await client.post(
        f"/api/tasks/{task_id}/gates",
        headers={"X-Project-Id": str(project_id)},
        json={"kind": kind, "gate_tier": gate_tier, "question_payload": qp},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# (1) Open a gate halts the work-task (AC1 lifecycle: INSERT + ps->8 + tier)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_gate_halts_work_task(client, scaffold_cleanup) -> None:
    """POSITIVE: opening a gate creates an OPEN gate row AND halts the work-task
    to ps=8 with operator_gate set to the gate's tier; halted_at stamps.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-open")
    tid = await _make_work_task(client, pid)

    gate = await _open_gate(
        client, pid, tid, gate_tier="hitl", options=[{"id": "y", "label": "Yes"}]
    )

    # POSITIVE: the gate row materialised correctly.
    assert gate["task_id"] == tid
    assert gate["seq"] == 1, "first gate on a task is seq=1"
    assert gate["status"] == "open"
    assert gate["gate_tier"] == "hitl"
    assert gate["answer"] is None
    assert gate["answered_at"] is None

    # The work-task is now halted on the operator (refetch — don't trust the
    # gate response alone).
    full = (
        await client.get(f"/api/tasks/{tid}", headers={"X-Project-Id": str(pid)})
    ).json()
    assert full["process_status"] == 8, "work-task must halt to ps=8"
    assert full["operator_gate"] == "hitl", "operator_gate set to the gate tier"
    assert full["halted_at"] is not None, "halted_at auto-stamps on ->8"


# ---------------------------------------------------------------------------
# (2) Open -> resolve happy path (AC2: answer folded + ps flips when count==0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_then_resolve_flips_task_actionable(client, scaffold_cleanup) -> None:
    """POSITIVE: resolving the only open gate writes the answer onto the gate,
    folds it into the work-task resume_context, and flips the task ps 8 -> TODO
    (actionable) because the remaining open-gate count is 0.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-resolve")
    tid = await _make_work_task(client, pid)
    gate = await _open_gate(
        client, pid, tid, options=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    )
    gid = gate["id"]

    resp = await client.post(
        f"/api/task-gates/{gid}/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "a", "provenance": "telegram", "answered_by": "op-123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # POSITIVE: response carries the resolution + the concurrency counter.
    assert body["gate_id"] == gid
    assert body["task_id"] == tid
    assert body["open_gate_count_remaining"] == 0
    assert body["process_status"] == 1, "ps flips to TODO (actionable) at count 0"
    # POSITIVE: the answer is folded into resume_context, keyed by gate id.
    rc = body["resume_context"]
    assert rc is not None
    assert str(gid) in rc["answered_gates"], rc
    assert rc["answered_gates"][str(gid)]["answer"] == "a"
    assert rc["answered_gates"][str(gid)]["answered_via"] == "telegram"
    assert rc["last_answered_gate_id"] == gid

    # NEGATIVE/refetch: gate is now 'answered' + the operator_gate lane cleared.
    full = (
        await client.get(f"/api/tasks/{tid}", headers={"X-Project-Id": str(pid)})
    ).json()
    assert full["process_status"] == 1, "task actionable after the only gate answered"
    assert full["operator_gate"] is None, "operator_gate cleared when count -> 0"


# ---------------------------------------------------------------------------
# (3) Idempotent stale-reject — resolve a non-open gate -> 409 (AC2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_non_open_gate_is_stale_reject_409(client, scaffold_cleanup) -> None:
    """POSITIVE of the stale-reject gate: a second resolve on an already-answered
    gate returns 409 (a clear 4xx, NOT a 5xx). This is the §9 structural need —
    a late/out-of-order answer must not re-bind to a closed gate.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-stale")
    tid = await _make_work_task(client, pid)
    gate = await _open_gate(client, pid, tid)
    gid = gate["id"]

    first = await client.post(
        f"/api/task-gates/{gid}/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "ok", "provenance": "web"},
    )
    assert first.status_code == 200, first.text

    # Second tap on the now-'answered' gate -> 409 stale-reject (idempotent).
    second = await client.post(
        f"/api/task-gates/{gid}/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "ok-again", "provenance": "web"},
    )
    assert second.status_code == 409, second.text
    assert "not open" in second.json()["detail"].lower(), second.text


@pytest.mark.asyncio
async def test_resolve_nonexistent_gate_returns_404(client, scaffold_cleanup) -> None:
    """NEGATIVE: resolving a gate id that doesn't exist -> 404."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-404")
    miss = await client.post(
        "/api/task-gates/999999999/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "x", "provenance": "web"},
    )
    assert miss.status_code == 404, miss.text


# ---------------------------------------------------------------------------
# (4) Concurrency — N open gates, out-of-order resolve, count-driven flip (AC3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_gate_out_of_order_resolve(client, scaffold_cleanup) -> None:
    """POSITIVE: two open gates on one task. Answering them OUT OF ORDER binds
    each answer by gate_id; the task stays HALTED (ps=8) until BOTH are
    answered, then flips actionable. Locks AC3 (concurrency).
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-concurrent")
    tid = await _make_work_task(client, pid)

    g1 = await _open_gate(client, pid, tid, question="First?")
    g2 = await _open_gate(client, pid, tid, question="Second?")
    assert g1["seq"] == 1 and g2["seq"] == 2, "seq increments per task"

    # Resolve the SECOND gate first (out of order).
    r2 = await client.post(
        f"/api/task-gates/{g2['id']}/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "second-ans", "provenance": "telegram"},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    # One gate still open -> task NOT yet actionable.
    assert body2["open_gate_count_remaining"] == 1, body2
    assert body2["process_status"] == 8, "still halted while a sibling gate is open"

    full_mid = (
        await client.get(f"/api/tasks/{tid}", headers={"X-Project-Id": str(pid)})
    ).json()
    assert full_mid["process_status"] == 8
    assert full_mid["operator_gate"] is not None, "operator_gate persists while open"

    # Now resolve the FIRST gate -> count hits 0 -> task flips actionable.
    r1 = await client.post(
        f"/api/task-gates/{g1['id']}/resolve",
        headers={"X-Project-Id": str(pid)},
        json={"answer": "first-ans", "provenance": "web"},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["open_gate_count_remaining"] == 0, body1
    assert body1["process_status"] == 1, "actionable once open-gate-count -> 0"

    # Both answers are bound by their own gate_id in resume_context.
    rc = body1["resume_context"]
    assert rc["answered_gates"][str(g1["id"])]["answer"] == "first-ans"
    assert rc["answered_gates"][str(g2["id"])]["answer"] == "second-ans"


# ---------------------------------------------------------------------------
# (5) Unified pending read unions legacy operator-HITL + open task_gates (AC4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unified_pending_read_unions_both_sources(client, scaffold_cleanup) -> None:
    """POSITIVE: GET /api/operator-gates/pending returns BOTH an open task_gate
    row (source='task_gate') AND a legacy operator-gated task
    (source='legacy_operator') in one shape. Locks AC4 (one reader, two writers).
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-unified")
    headers = {"X-Project-Id": str(pid)}

    # (a) A task with an OPEN gate (the new flow).
    gate_task = await _make_work_task(client, pid, title="Has a gate")
    gate = await _open_gate(client, pid, gate_task, gate_tier="commit")

    # (b) A separate task on the LEGACY operator-gate lane (task-level marker).
    legacy_task = await _make_work_task(client, pid, title="Legacy operator gate")
    patch = await client.patch(
        f"/api/tasks/{legacy_task}",
        headers=headers,
        json={"operator_gate": "key", "operator_gate_note": "needs an API key"},
    )
    assert patch.status_code == 200, patch.text

    resp = await client.get("/api/operator-gates/pending", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()

    by_source: dict[str, list] = {"task_gate": [], "legacy_operator": []}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)

    # POSITIVE: the open gate appears under source='task_gate' with its gate_id.
    tg_items = [it for it in by_source["task_gate"] if it["task_id"] == gate_task]
    assert len(tg_items) == 1, tg_items
    assert tg_items[0]["gate_id"] == gate["id"]
    assert tg_items[0]["gate_tier"] == "commit"
    assert tg_items[0]["seq"] == 1

    # POSITIVE: the legacy task appears under source='legacy_operator', gate_id NULL.
    lg_items = [it for it in by_source["legacy_operator"] if it["task_id"] == legacy_task]
    assert len(lg_items) == 1, lg_items
    assert lg_items[0]["gate_id"] is None
    assert lg_items[0]["gate_tier"] == "key"

    # NEGATIVE: an ANSWERED gate must NOT appear in the pending read.
    await client.post(
        f"/api/task-gates/{gate['id']}/resolve",
        headers=headers,
        json={"answer": "approve", "provenance": "telegram"},
    )
    resp2 = await client.get("/api/operator-gates/pending", headers=headers)
    items2 = resp2.json()
    still_listed = [
        it
        for it in items2
        if it["source"] == "task_gate" and it["task_id"] == gate_task
    ]
    assert still_listed == [], "answered gate must drop off the pending read"


@pytest.mark.asyncio
async def test_task_with_open_gate_appears_exactly_once(client, scaffold_cleanup) -> None:
    """H1 dedup regression: a task that has an open gate (open_gate() also sets
    task.operator_gate) must appear EXACTLY ONCE in the pending read as
    source='task_gate' and NEVER as source='legacy_operator'.

    Without the H1 fix the legacy branch's `operator_gate IS NOT NULL` predicate
    matches the same task, producing a duplicate entry.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "gates-h1-dedup")
    headers = {"X-Project-Id": str(pid)}

    tid = await _make_work_task(client, pid, title="Dedup test task")
    gate = await _open_gate(client, pid, tid, gate_tier="key")

    # Confirm open_gate() set operator_gate (the precondition for the H1 bug).
    task_row = (
        await client.get(f"/api/tasks/{tid}", headers=headers)
    ).json()
    assert task_row["operator_gate"] is not None, "precondition: operator_gate must be set"

    resp = await client.get("/api/operator-gates/pending", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()

    # POSITIVE: appears exactly once as task_gate.
    tg = [it for it in items if it["task_id"] == tid and it["source"] == "task_gate"]
    assert len(tg) == 1, f"expected exactly 1 task_gate entry, got {tg}"
    assert tg[0]["gate_id"] == gate["id"]

    # NEGATIVE: must NOT also appear as legacy_operator.
    leg = [it for it in items if it["task_id"] == tid and it["source"] == "legacy_operator"]
    assert leg == [], f"task must not appear under legacy_operator when open gate exists: {leg}"
