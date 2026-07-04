"""Kanban #2780 (Telegram Phase 3) — the two GAP endpoints:
    POST /api/tasks/{id}/run-now  — make task X the Mode-A engine's next pick.
    POST /api/tasks/{id}/halt     — cooperatively halt a RUNNING task.

First-pass contract-smoke for the new surfaces (the rigorous edge/regression
matrix is dev-tester's domain). Coverage:

  run-now:
    (1) happy path — manual/TODO row -> run_mode='auto_pickup', ps stays TODO,
        halt_reason + scheduled_at cleared; verified via independent GET.
    (2) picker proof — /next-autorun then surfaces the primed task as next_task
        (the WHOLE point: it was invisible as run_mode='manual').
    (3) clears the schedule/halt brakes — a future scheduled_at + a halt_reason
        are both cleared so the task fires NOW.
    (4) refuse — DONE(5)/CANCELLED(6) -> 409; already-running(2) -> 409;
        missing -> 404.
    (5) idempotent — a second run-now on an already-primed task is a 200 no-op.
    (6) kill/pause gate — run-now on a paused project -> 423 (pause is not
        operator-gated, so it is the live-testable gate here).

  halt:
    (7) happy path — ps=2 -> ps=8 + halt_reason='operator_halt' + halted_at
        stamped; verified via independent GET.
    (8) refuse — a NON-running (ps != 2) task -> 409 (covers TODO + the
        already-halted ps=8 case, which is the idempotency guard: a second
        halt naturally 409s "not running").
    (9) missing -> 404.

Mirrors the helper/fixture pattern in test_tasks_halted_pending_user.py /
test_tasks_next_autorun.py — HTTP against the isolated agent_teams_test DB via
the `client` fixture. DO NOT RUN IN-SESSION (block-pytest hook); the operator
runs this in a terminal.
"""

from __future__ import annotations

import uuid

import pytest

from src.constants import TaskRunMode, TaskStatus


# ---------------------------------------------------------------------------
# Helpers (mirror test_tasks_halted_pending_user.py)
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


async def _patch_task(client, project_id: int, task_id: int, **fields) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.patch(f"/api/tasks/{task_id}", json=fields, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_task(client, project_id: int, task_id: int) -> dict:
    headers = {"X-Project-Id": str(project_id)}
    resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _run_now(client, project_id: int, task_id: int):
    return await client.post(
        f"/api/tasks/{task_id}/run-now", headers={"X-Project-Id": str(project_id)}
    )


async def _halt(client, project_id: int, task_id: int):
    return await client.post(
        f"/api/tasks/{task_id}/halt", headers={"X-Project-Id": str(project_id)}
    )


# ---------------------------------------------------------------------------
# run-now — happy path + picker proof
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_now_primes_manual_task_to_auto_pickup(client, scaffold_cleanup) -> None:
    """POSITIVE: run-now flips a manual/TODO task to run_mode='auto_pickup'.
    NEGATIVE: process_status is explicitly asserted STILL TODO(1) (run-now
    primes the picker, it does NOT itself move the task to in_progress) and
    run_mode is asserted == 'auto_pickup' exactly (not 'auto_headless', which
    would need project consent). Verified via an independent GET, not the
    response body alone.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-happy")
    task = await _make_task(client, pid, "run-now target")
    assert task["run_mode"] == TaskRunMode.MANUAL  # baseline: invisible to picker
    assert task["process_status"] == TaskStatus.TODO

    resp = await _run_now(client, pid, task["id"])
    assert resp.status_code == 200, resp.text

    got = await _get_task(client, pid, task["id"])
    assert got["run_mode"] == TaskRunMode.AUTO_PICKUP, got
    assert got["process_status"] == TaskStatus.TODO, got  # NEGATIVE: not moved to 2
    assert got["halt_reason"] is None
    assert got["scheduled_at"] is None


@pytest.mark.asyncio
async def test_run_now_surfaces_task_in_next_autorun(client, scaffold_cleanup) -> None:
    """POSITIVE (the whole point): after run-now, GET /next-autorun returns the
    task as `next_task`. NEGATIVE: a control run BEFORE run-now (manual) proves
    it was invisible — the transition is what made it selectable, not luck."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-picker")
    task = await _make_task(client, pid, "picker target")
    headers = {"X-Project-Id": str(pid)}

    # Control: a manual task is NOT the next_task.
    before = await client.get("/api/tasks/next-autorun", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["next_task"] is None, before.json()

    await _run_now(client, pid, task["id"])

    after = await client.get("/api/tasks/next-autorun", headers=headers)
    assert after.status_code == 200, after.text
    nxt = after.json()["next_task"]
    assert nxt is not None and nxt["id"] == task["id"], after.json()


@pytest.mark.asyncio
async def test_run_now_clears_schedule_and_halt_brakes(client, scaffold_cleanup) -> None:
    """POSITIVE: run-now clears a FUTURE scheduled_at AND a halt_reason so the
    task fires now. NEGATIVE: both fields asserted None afterwards (not merely
    'run_mode changed') — these are the brakes run-now explicitly owns."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-brakes")
    task = await _make_task(client, pid, "braked target")
    await _patch_task(
        client, pid, task["id"],
        scheduled_at="2099-01-01T00:00:00Z",
        halt_reason="parked for later",
    )

    resp = await _run_now(client, pid, task["id"])
    assert resp.status_code == 200, resp.text

    got = await _get_task(client, pid, task["id"])
    assert got["scheduled_at"] is None, got
    assert got["halt_reason"] is None, got
    assert got["run_mode"] == TaskRunMode.AUTO_PICKUP


# ---------------------------------------------------------------------------
# run-now — refuse cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_ps", [TaskStatus.DONE, TaskStatus.CANCELLED])
async def test_run_now_refuses_terminal_task(client, scaffold_cleanup, terminal_ps) -> None:
    """NEGATIVE: run-now on a DONE(5)/CANCELLED(6) task -> 409 (nothing to run)
    — a terminal task must not be silently re-queued."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-terminal")
    task = await _make_task(client, pid, "terminal target")
    await _patch_task(client, pid, task["id"], process_status=terminal_ps)

    resp = await _run_now(client, pid, task["id"])
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_run_now_refuses_already_running(client, scaffold_cleanup) -> None:
    """NEGATIVE: run-now on an IN_PROGRESS(2) task -> 409. A cooperative
    'pick this next' signal is nonsensical for a task the worker already holds."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-running")
    task = await _make_task(client, pid, "running target")
    await _patch_task(client, pid, task["id"], process_status=TaskStatus.IN_PROGRESS)

    resp = await _run_now(client, pid, task["id"])
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_run_now_missing_task_404(client, scaffold_cleanup) -> None:
    """NEGATIVE: run-now on a non-existent id -> 404 (never 500)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-404")
    resp = await _run_now(client, pid, 999_999_999)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_run_now_is_idempotent(client, scaffold_cleanup) -> None:
    """POSITIVE: a SECOND run-now on an already-primed task is a harmless 200
    no-op. NEGATIVE: state after the second call is byte-identical run_mode +
    ps to after the first — the re-entrant target state does not drift."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-idem")
    task = await _make_task(client, pid, "idem target")

    r1 = await _run_now(client, pid, task["id"])
    assert r1.status_code == 200, r1.text
    after1 = await _get_task(client, pid, task["id"])

    r2 = await _run_now(client, pid, task["id"])
    assert r2.status_code == 200, r2.text
    after2 = await _get_task(client, pid, task["id"])

    assert after2["run_mode"] == after1["run_mode"] == TaskRunMode.AUTO_PICKUP
    assert after2["process_status"] == after1["process_status"] == TaskStatus.TODO


@pytest.mark.asyncio
async def test_run_now_refuses_on_paused_project(client, scaffold_cleanup) -> None:
    """NEGATIVE (kill/pause gate): run-now on a task in a PAUSED project -> 423
    Locked. Pause is not operator-gated, so it is the live-testable half of the
    shared kill/pause gate; run-now must NOT drive work into a paused project
    (and does NOT expose create_task's allow_during_pause hatch)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "runnow-paused")
    task = await _make_task(client, pid, "paused-project target")

    pause = await client.post(
        f"/api/projects/{pid}/pause",
        json={"reason": "phase3 run-now pause-gate test"},
    )
    assert pause.status_code == 200, pause.text

    resp = await _run_now(client, pid, task["id"])
    assert resp.status_code == 423, resp.text


# ---------------------------------------------------------------------------
# halt — happy path + refuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_running_task_sets_ps8_and_reason(client, scaffold_cleanup) -> None:
    """POSITIVE: halt on a ps=2 task flips it to ps=8 (HALTED_PENDING_USER) +
    halt_reason='operator_halt' + stamps halted_at. NEGATIVE: process_status is
    asserted == 8 exactly and halt_reason == 'operator_halt' exactly (not
    merely 'changed'), and halted_at is non-null — the cooperative-halt state
    the executor observes at its next boundary. Verified via independent GET."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "halt-happy")
    task = await _make_task(client, pid, "halt target")
    await _patch_task(client, pid, task["id"], process_status=TaskStatus.IN_PROGRESS)

    resp = await _halt(client, pid, task["id"])
    assert resp.status_code == 200, resp.text

    got = await _get_task(client, pid, task["id"])
    assert got["process_status"] == TaskStatus.HALTED_PENDING_USER, got
    assert got["halt_reason"] == "operator_halt", got
    assert got["halted_at"] is not None, got


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_ps",
    [TaskStatus.TODO, TaskStatus.DONE, TaskStatus.HALTED_PENDING_USER],
)
async def test_halt_refuses_non_running_task(client, scaffold_cleanup, start_ps) -> None:
    """NEGATIVE: halt on a task that is NOT running (ps != 2) -> 409. Covers
    TODO (never started), DONE (terminal), and the already-halted ps=8 case —
    the last is the idempotency guard: halting an already-halted task 409s
    'not running' rather than double-writing (no ps=8 re-stamp)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "halt-notrunning")
    task = await _make_task(client, pid, "not-running target")
    if start_ps != TaskStatus.TODO:
        # Move to ps=2 first (halted_at requires the ->8 path); DONE/HALTED are
        # reached from the running state for a realistic transition.
        await _patch_task(client, pid, task["id"], process_status=TaskStatus.IN_PROGRESS)
        await _patch_task(client, pid, task["id"], process_status=start_ps)

    resp = await _halt(client, pid, task["id"])
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_halt_missing_task_404(client, scaffold_cleanup) -> None:
    """NEGATIVE: halt on a non-existent id -> 404 (never 500)."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "halt-404")
    resp = await _halt(client, pid, 999_999_999)
    assert resp.status_code == 404, resp.text
