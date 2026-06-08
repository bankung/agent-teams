"""Regression: Kanban #1885 — PATCH process_status=5 on audit_enabled project.

Original bug: PATCH /api/tasks/{id} with process_status=5 on a task belonging
to an audit_enabled project returned HTTP 500 (InFailedSQLTransactionError).
The GOV3 audit-flag pipeline side-effect fired inside the same transaction as
the DONE flip, failed, and left the session in an aborted state — the final
UPDATE hit InFailedSQLTransactionError.

Fix (api/src/routers/tasks.py ~2313-2364): the DONE flip is committed FIRST in
its own transaction; the audit-flag pipeline then runs inside a separate
try/except that calls session.rollback() on error — a pipeline failure cannot
abort the already-committed DONE flip.

Tests here:
  (AC3 / CORE)   test_regression_kanban_1885_done_flip_audit_enabled_project
      — PATCH process_status=5 on a plain task inside an audit_enabled project
        returns HTTP 200 AND completed_at is populated. Would have returned 500
        under the pre-fix code. Does NOT rely on the audit-flag pipeline — it
        guards the DONE-flip commit itself.

  (OPTIONAL — resilience)  test_regression_kanban_1885_audit_type_done_flip_stands
      — An audit-type task (task_type="audit") with an audit_report, inside an
        audit_enabled project, PATCH process_status=5 returns 200 + completed_at
        even when the flag-pipeline side-effect is forced to raise. Guards the
        "audit-task DONE flip stands but flag pipeline rolled back" invariant
        from the comment in tasks.py.

Runs against agent_teams_test (conftest.py rewrite). Live agent_teams rows MUST
NOT drift — _live_db_row_count_invariant in conftest.py asserts that.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — mirror test_gov3_pause_flag.py conventions exactly
# ---------------------------------------------------------------------------


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": "k1885 regression fixture",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
        # audit_enabled defaults to True on the schema — listed explicitly for
        # test-documentation purposes only.
        "audit_enabled": True,
    }


async def _create_project(client, scaffold_cleanup) -> dict:
    name = scaffold_cleanup(_unique_name("k1885"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_task(client, project_id: int, **overrides) -> dict:
    body: dict = {
        "project_id": project_id,
        "title": "k1885 regression task",
        "description": "regression fixture — DO NOT ARCHIVE",
        "process_status": 2,  # IN_PROGRESS
        "task_type": "feature",
    }
    body.update(overrides)
    resp = await client.post(
        "/api/tasks",
        headers={"X-Project-Id": str(project_id)},
        json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# AC3 / CORE regression test
# ---------------------------------------------------------------------------

# Regression: Kanban #1885
@pytest.mark.asyncio
async def test_regression_kanban_1885_done_flip_audit_enabled_project(
    client, scaffold_cleanup
) -> None:
    """PATCH process_status=5 on an audit_enabled project returns 200 + completed_at.

    Pre-fix: this returned 500 (InFailedSQLTransactionError) because the GOV3
    audit-flag side-effect aborted the transaction that the DONE flip tried to
    commit into.

    Post-fix: the DONE flip commits in its own transaction BEFORE the pipeline
    fires; a pipeline error cannot roll back the flip.

    POSITIVE assertion: completed_at is populated after the PATCH.
    NEGATIVE assertion: response is NOT 500 (explicit — the original failure mode).
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        task = await _create_task(client, project_id)
        task_id = task["id"]

        # Capture pre-PATCH state to guard against vacuous-equality.
        assert task["process_status"] != 5, (
            "Task should NOT already be DONE before the regression PATCH"
        )
        assert task["completed_at"] is None, (
            "completed_at must be NULL before DONE flip (vacuous-equality guard)"
        )

        resp = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={"process_status": 5},
        )

        # NEGATIVE: must NOT be 500 (the original bug's failure mode).
        assert resp.status_code != 500, (
            f"Regression: got 500 — InFailedSQLTransactionError likely back. "
            f"Body: {resp.text}"
        )
        # POSITIVE: must be 200.
        assert resp.status_code == 200, (
            f"Expected 200 from DONE flip on audit_enabled project, got "
            f"{resp.status_code}: {resp.text}"
        )

        body = resp.json()
        # POSITIVE: completed_at must be populated (the state mutation happened).
        assert body["completed_at"] is not None, (
            "completed_at must be set after DONE flip — mutation did not persist"
        )
        # POSITIVE: process_status is 5.
        assert body["process_status"] == 5, (
            f"process_status must be 5 after DONE flip, got {body['process_status']}"
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# OPTIONAL — resilience: audit-type task DONE flip survives pipeline error
# ---------------------------------------------------------------------------

# Regression: Kanban #1885
@pytest.mark.asyncio
async def test_regression_kanban_1885_audit_type_done_flip_stands_despite_pipeline_error(
    client, scaffold_cleanup
) -> None:
    """Audit-type task DONE flip returns 200 even when the flag pipeline raises.

    Locks the "audit-task DONE flip stands but flag pipeline rolled back"
    comment at tasks.py ~2351-2364.

    The audit_flag service is monkeypatched to raise an unexpected exception
    (simulating the pre-fix broken state where the side-effect corrupted the
    transaction). The PATCH must still return 200 + completed_at.

    POSITIVE assertion: completed_at is set (DONE flip committed).
    NEGATIVE assertion: response is NOT 500 despite the pipeline crash.
    """
    project = await _create_project(client, scaffold_cleanup)
    project_id = project["id"]
    try:
        # Create an audit-type task at IN_PROGRESS with an audit_report.
        task_body: dict = {
            "project_id": project_id,
            "title": "k1885 audit-type regression task",
            "description": "audit-type done-flip resilience check",
            "process_status": 2,  # IN_PROGRESS
            "task_type": "audit",
            "started_at": "2026-06-08T00:00:00Z",
        }
        create_resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=task_body,
        )
        assert create_resp.status_code == 201, create_resp.text
        task = create_resp.json()
        task_id = task["id"]

        # Stamp an audit_report (required for apply_flag_from_audit_report to fire).
        patch_report = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
            json={
                "audit_report": {
                    "verdict": "budget_over_limit",
                    "severity": "high",
                    "recommendation": "review",
                    "evidence": [{"summary": "k1885 regression evidence"}],
                }
            },
        )
        assert patch_report.status_code == 200, patch_report.text

        # Guard pre-flip state.
        assert task["completed_at"] is None

        # Force the flag pipeline to crash — simulates the corrupted side-effect.
        with patch(
            "src.services.audit_flag.apply_flag_from_audit_report",
            new=AsyncMock(side_effect=RuntimeError("k1885 injected pipeline crash")),
        ):
            done_resp = await client.patch(
                f"/api/tasks/{task_id}",
                headers={"X-Project-Id": str(project_id)},
                json={"process_status": 5},
            )

        # NEGATIVE: NOT 500 despite the pipeline crash.
        assert done_resp.status_code != 500, (
            f"Regression: got 500 with crashed pipeline — "
            f"DONE flip does NOT stand. Body: {done_resp.text}"
        )
        # POSITIVE: 200.
        assert done_resp.status_code == 200, (
            f"Expected 200 from audit-type DONE flip with crashed pipeline, "
            f"got {done_resp.status_code}: {done_resp.text}"
        )
        body = done_resp.json()
        # POSITIVE: completed_at set (mutation persisted despite pipeline crash).
        assert body["completed_at"] is not None, (
            "completed_at must be set — DONE flip commit must survive pipeline crash"
        )
        assert body["process_status"] == 5
    finally:
        await client.delete(f"/api/projects/{project_id}")
