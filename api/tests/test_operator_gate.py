"""Kanban #2127 — queryable "blocked-on-operator" operator-gate marker.

Migration `0064_operator_gate` adds two nullable TEXT columns
(`tasks.operator_gate` 5-enum rollup + `tasks.operator_gate_note` advisory) and
a GIN index `ix_tasks_ac_gin` on `acceptance_criteria` (jsonb_path_ops opclass)
so the AC-level filter predicate (`@>` containment) is indexable.

OPERATOR-CONFIRMED DESIGN (locked 2026-06-11):
- Gate enum (5 values): key | commit | decision | hitl | external.
- AC-level = source of truth: acceptance_criteria items gain OPTIONAL gate
  ('operator') + gate_kind (5-enum). An AC gates ONLY while status=='pending';
  passed/na clears it automatically.
- Task-level rollup: tasks.operator_gate + operator_gate_note set DIRECTLY by
  the Lead (NO auto-derivation / trigger / sweep).
- Filter rule (OR): GET /api/tasks?operator_gate=<any|...> matches a task iff
  the task-level column IS NOT NULL [and == the value when not 'any'] OR >=1 AC
  item has gate='operator' AND status='pending' [and gate_kind == the value when
  not 'any']. A task whose gate ACs are all passed/na AND task-level NULL is NOT
  returned.
- PATCH semantics: both fields key-absent=unchanged, explicit-null=clear,
  value=set; operator_gate_note settable independently of operator_gate;
  clearing operator_gate does NOT cascade-clear the note. Bad enum -> 422.

First-pass contract-smoke (dev-sr-backend scope — the comprehensive suite is
dev-tester's domain). Runs against `agent_teams_test` per conftest.py. Live
`agent_teams` row count MUST NOT drift across the session (conftest invariant);
cleanup is DELETE /api/projects/{id} on the way out (cascades child tasks).
"""

from __future__ import annotations

import uuid

import pytest


# ---- helpers ---------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k2127 operator-gate fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k2127"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, **extra):
    body: dict = {
        "project_id": project_id,
        "title": "k2127 fixture task",
        "description": "k2127 operator-gate test task",
    }
    body.update(extra)
    return await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=body,
    )


def _h(project_id: int) -> dict:
    return {"X-Project-Id": str(project_id)}


def _ac(text: str, status: str = "pending", **extra) -> dict:
    body = {"text": text, "status": status}
    body.update(extra)
    return body


# ============================================================================
# AC schema — old-shape validates, gate fields, gate-without-gate_kind legal,
# unknown extra key still 422.
# ============================================================================


@pytest.mark.asyncio
async def test_ac_old_shape_validates_and_gate_fields_default_null(
    client, scaffold_cleanup
) -> None:
    """OLD-shaped AC arrays (no gate keys) still validate; gate/gate_kind default
    to None on read (extra='forbid' kept, Optional defaults)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client, pid, acceptance_criteria=[_ac("plain criterion")]
        )
        assert resp.status_code == 201, resp.text
        ac0 = resp.json()["acceptance_criteria"][0]
        assert ac0["gate"] is None and ac0["gate_kind"] is None, ac0
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_ac_gate_fields_round_trip(client, scaffold_cleanup) -> None:
    """AC items carrying gate='operator' + gate_kind round-trip through TaskRead."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client,
            pid,
            acceptance_criteria=[
                _ac("needs key", gate="operator", gate_kind="key"),
            ],
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]
        ac0 = resp.json()["acceptance_criteria"][0]
        assert ac0["gate"] == "operator" and ac0["gate_kind"] == "key", ac0

        get_resp = await client.get(f"/api/tasks/{task_id}", headers=_h(pid))
        assert get_resp.status_code == 200, get_resp.text
        ac0g = get_resp.json()["acceptance_criteria"][0]
        assert ac0g["gate"] == "operator" and ac0g["gate_kind"] == "key", ac0g
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_ac_gate_without_gate_kind_is_legal(client, scaffold_cleanup) -> None:
    """gate='operator' with gate_kind absent/null is LEGAL (counts under `any`
    only). Documented decision: a gate with no kind is allowed."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client, pid, acceptance_criteria=[_ac("gated, no kind", gate="operator")]
        )
        assert resp.status_code == 201, resp.text
        ac0 = resp.json()["acceptance_criteria"][0]
        assert ac0["gate"] == "operator" and ac0["gate_kind"] is None, ac0
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_ac_unknown_extra_key_still_422(client, scaffold_cleanup) -> None:
    """extra='forbid' is preserved — an unknown AC key is still rejected 422."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client, pid, acceptance_criteria=[_ac("x", bogus_key=1)]
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.parametrize("bad_kind", ["KEY", "credential", "approval", "", "secret"])
@pytest.mark.asyncio
async def test_ac_bad_gate_kind_422(client, scaffold_cleanup, bad_kind) -> None:
    """A non-enum gate_kind → 422 (OperatorGateLiteral at the boundary)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client,
            pid,
            acceptance_criteria=[_ac("x", gate="operator", gate_kind=bad_kind)],
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_ac_bad_gate_value_422(client, scaffold_cleanup) -> None:
    """gate's only legal value is 'operator' — any other → 422."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client, pid, acceptance_criteria=[_ac("x", gate="agent")]
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")


# ============================================================================
# Task-level column — POST / PATCH set / clear / unchanged; bad enum 422.
# ============================================================================


@pytest.mark.asyncio
async def test_create_task_omits_operator_gate_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST without operator_gate → NULL (= not gated); note also NULL."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(client, pid)
        assert resp.status_code == 201, resp.text
        assert resp.json()["operator_gate"] is None, resp.json()
        assert resp.json()["operator_gate_note"] is None, resp.json()
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.parametrize("gate", ["key", "commit", "decision", "hitl", "external"])
@pytest.mark.asyncio
async def test_create_task_all_five_gates_round_trip(
    client, scaffold_cleanup, gate
) -> None:
    """Each of the 5 gate values is accepted + round-trips, with the note."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(
            client, pid, operator_gate=gate, operator_gate_note=f"need {gate}"
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]
        assert resp.json()["operator_gate"] == gate, resp.json()
        assert resp.json()["operator_gate_note"] == f"need {gate}", resp.json()

        get_resp = await client.get(f"/api/tasks/{task_id}", headers=_h(pid))
        assert get_resp.json()["operator_gate"] == gate, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.parametrize("bad_gate", ["KEY", "approval", "blocked", "", "operator"])
@pytest.mark.asyncio
async def test_create_task_bad_gate_422(client, scaffold_cleanup, bad_gate) -> None:
    """POST with a non-enum operator_gate → 422; loc points at the field."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await _create_task(client, pid, operator_gate=bad_gate)
        assert resp.status_code == 422, resp.text
        matches = [
            err
            for err in resp.json()["detail"]
            if err["loc"][:2] == ["body", "operator_gate"]
        ]
        assert matches, f"expected loc=['body','operator_gate',...]; got {resp.json()}"
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_set_then_clear_operator_gate(client, scaffold_cleanup) -> None:
    """PATCH set gate (positive lock), then explicit-null CLEARS it (negative
    lock — null does NOT leave the prior value in place)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        create = await _create_task(client, pid)
        task_id = create.json()["id"]
        assert create.json()["operator_gate"] is None

        # set
        patch1 = await client.patch(
            f"/api/tasks/{task_id}",
            headers=_h(pid),
            json={"operator_gate": "decision", "operator_gate_note": "pick A or B"},
        )
        assert patch1.status_code == 200, patch1.text
        assert patch1.json()["operator_gate"] == "decision", patch1.json()
        assert patch1.json()["operator_gate_note"] == "pick A or B", patch1.json()

        # explicit-null clears (POSITIVE: was 'decision'; NEGATIVE: now None not 'decision')
        patch2 = await client.patch(
            f"/api/tasks/{task_id}",
            headers=_h(pid),
            json={"operator_gate": None},
        )
        assert patch2.status_code == 200, patch2.text
        assert patch2.json()["operator_gate"] is None, patch2.json()
        # note NOT cascade-cleared by clearing the gate (locked)
        assert patch2.json()["operator_gate_note"] == "pick A or B", patch2.json()
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_absent_key_leaves_gate_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH that omits operator_gate leaves it unchanged (POSITIVE: still the
    set value; NEGATIVE: a same-PATCH unrelated field did not stomp it)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        create = await _create_task(client, pid, operator_gate="hitl")
        task_id = create.json()["id"]
        assert create.json()["operator_gate"] == "hitl"

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers=_h(pid),
            json={"priority": 3},  # unrelated field; operator_gate absent
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["operator_gate"] == "hitl", patch.json()  # unchanged
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_note_without_gate_is_legal(client, scaffold_cleanup) -> None:
    """operator_gate_note is settable independently of operator_gate (note set,
    gate stays NULL)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        create = await _create_task(client, pid)
        task_id = create.json()["id"]

        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers=_h(pid),
            json={"operator_gate_note": "advisory only, not gated yet"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["operator_gate"] is None, patch.json()
        assert (
            patch.json()["operator_gate_note"] == "advisory only, not gated yet"
        ), patch.json()
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_patch_bad_gate_422(client, scaffold_cleanup) -> None:
    """PATCH with a non-enum operator_gate → 422."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        create = await _create_task(client, pid)
        task_id = create.json()["id"]
        patch = await client.patch(
            f"/api/tasks/{task_id}",
            headers=_h(pid),
            json={"operator_gate": "nope"},
        )
        assert patch.status_code == 422, patch.text
    finally:
        await client.delete(f"/api/projects/{pid}")


# ============================================================================
# Filter — task-level (any / specific), AC-level pending (any / specific),
# passed/na NOT matched, combined with other filters, bad value 422.
# ============================================================================


def _ids(resp) -> set[int]:
    return {t["id"] for t in resp.json()}


@pytest.mark.asyncio
async def test_filter_any_matches_task_level_and_pending_ac(
    client, scaffold_cleanup
) -> None:
    """operator_gate=any matches a task-level-gated task AND a pending-AC-gated
    task; an un-gated task is NOT returned."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        t_task_level = (await _create_task(client, pid, operator_gate="key")).json()[
            "id"
        ]
        t_ac_level = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[_ac("needs key", gate="operator", gate_kind="key")],
            )
        ).json()["id"]
        t_ungated = (await _create_task(client, pid)).json()["id"]

        resp = await client.get(
            "/api/tasks", headers=_h(pid), params={"operator_gate": "any", "limit": 500}
        )
        assert resp.status_code == 200, resp.text
        ids = _ids(resp)
        assert t_task_level in ids, ids
        assert t_ac_level in ids, ids
        assert t_ungated not in ids, ids  # NEGATIVE: ungated excluded
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_filter_specific_value_task_and_ac_level(
    client, scaffold_cleanup
) -> None:
    """operator_gate=commit matches only commit-gated tasks (task-level OR
    pending AC with gate_kind=commit); a 'key'-gated task is excluded."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        t_commit_task = (
            await _create_task(client, pid, operator_gate="commit")
        ).json()["id"]
        t_commit_ac = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[
                    _ac("humans-only write", gate="operator", gate_kind="commit")
                ],
            )
        ).json()["id"]
        t_key_task = (await _create_task(client, pid, operator_gate="key")).json()[
            "id"
        ]
        t_key_ac = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[_ac("needs key", gate="operator", gate_kind="key")],
            )
        ).json()["id"]

        resp = await client.get(
            "/api/tasks",
            headers=_h(pid),
            params={"operator_gate": "commit", "limit": 500},
        )
        assert resp.status_code == 200, resp.text
        ids = _ids(resp)
        assert t_commit_task in ids and t_commit_ac in ids, ids
        assert t_key_task not in ids and t_key_ac not in ids, ids  # NEGATIVE
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_filter_passed_or_na_ac_not_matched(client, scaffold_cleanup) -> None:
    """An AC with gate='operator' whose status is passed/na no longer gates;
    if the task-level column is NULL the task is NOT returned (stale-positive
    prevention — the whole point of AC-level source-of-truth)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        t_passed = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[
                    _ac("done by op", status="passed", gate="operator", gate_kind="key")
                ],
            )
        ).json()["id"]
        t_na = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[
                    _ac("deferred", status="na", gate="operator", gate_kind="hitl")
                ],
            )
        ).json()["id"]
        # control: a genuinely-pending gated AC IS matched
        t_pending = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[
                    _ac("still on op", status="pending", gate="operator", gate_kind="key")
                ],
            )
        ).json()["id"]

        resp = await client.get(
            "/api/tasks", headers=_h(pid), params={"operator_gate": "any", "limit": 500}
        )
        assert resp.status_code == 200, resp.text
        ids = _ids(resp)
        assert t_passed not in ids, ids  # NEGATIVE: passed gate cleared
        assert t_na not in ids, ids  # NEGATIVE: na gate cleared
        assert t_pending in ids, ids  # POSITIVE: pending still gates
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_filter_specific_excludes_gate_without_kind(
    client, scaffold_cleanup
) -> None:
    """A pending AC with gate='operator' but NO gate_kind counts under `any`
    only — a specific-value filter does NOT match it (documented)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        t_nokind = (
            await _create_task(
                client,
                pid,
                acceptance_criteria=[_ac("gated no kind", gate="operator")],
            )
        ).json()["id"]

        # `any` matches it
        any_resp = await client.get(
            "/api/tasks", headers=_h(pid), params={"operator_gate": "any", "limit": 500}
        )
        assert t_nokind in _ids(any_resp), any_resp.json()

        # specific 'key' does NOT
        key_resp = await client.get(
            "/api/tasks", headers=_h(pid), params={"operator_gate": "key", "limit": 500}
        )
        assert t_nokind not in _ids(key_resp), key_resp.json()  # NEGATIVE
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_filter_composes_with_pending_process_status_done_lane(
    client, scaffold_cleanup
) -> None:
    """The operator_gate filter composes with pending=true + process_status +
    order=done_lane without a 500, and the keyset ordering is intact
    (updated_at DESC, id DESC)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        # two gated DONE tasks for the done_lane ordering check
        d1 = (
            await _create_task(client, pid, operator_gate="key")
        ).json()["id"]
        d2 = (
            await _create_task(client, pid, operator_gate="key")
        ).json()["id"]
        for tid in (d1, d2):
            r = await client.patch(
                f"/api/tasks/{tid}", headers=_h(pid), json={"process_status": 5}
            )
            assert r.status_code == 200, r.text

        # composed with order=done_lane + process_status=5 — no 500, ordering intact
        dl = await client.get(
            "/api/tasks",
            headers=_h(pid),
            params={
                "operator_gate": "any",
                "process_status": 5,
                "order": "done_lane",
                "limit": 500,
            },
        )
        assert dl.status_code == 200, dl.text
        rows = dl.json()
        ids = {t["id"] for t in rows}
        assert d1 in ids and d2 in ids, ids
        # ordering: updated_at DESC, id DESC — verify non-increasing updated_at
        ups = [t["updated_at"] for t in rows]
        assert ups == sorted(ups, reverse=True), ups

        # composed with pending=true — DONE tasks excluded (pending = ps != 5)
        pend = await client.get(
            "/api/tasks",
            headers=_h(pid),
            params={"operator_gate": "any", "pending": "true", "limit": 500},
        )
        assert pend.status_code == 200, pend.text
        pend_ids = {t["id"] for t in pend.json()}
        assert d1 not in pend_ids and d2 not in pend_ids, pend_ids  # NEGATIVE
    finally:
        await client.delete(f"/api/projects/{pid}")


@pytest.mark.asyncio
async def test_filter_non_enum_value_422(client, scaffold_cleanup) -> None:
    """A non-enum operator_gate query value → 422 (Literal at the boundary)."""
    project = await _create_project(client, scaffold_cleanup)
    pid = project["id"]
    try:
        resp = await client.get(
            "/api/tasks", headers=_h(pid), params={"operator_gate": "bogus"}
        )
        assert resp.status_code == 422, resp.text
    finally:
        await client.delete(f"/api/projects/{pid}")
