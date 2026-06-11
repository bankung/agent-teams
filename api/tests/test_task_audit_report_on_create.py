"""Kanban #1261 — `audit_report` accepted on TaskCreate (POST /api/tasks).

Previously, sending `audit_report` in a POST body was silently ignored by
Pydantic because the field was absent from TaskCreate (it existed only on
TaskUpdate + the ORM). This slice adds it so the auditor engine can write a
new audit task with its report in a single POST call — no PATCH required.

First-pass contract-smoke (dev-backend scope):
- POST with `audit_report` dict → 201 + round-trip value matches (NO PATCH).
- POST without `audit_report` → column lands None (field is optional).

Positive assertion: audit_report value is actually stored and readable.
Negative assertion: omitting the field leaves None, NOT some garbage default.

Runs against `agent_teams_test` per conftest.py. Live `agent_teams` row count
MUST NOT drift (conftest invariant asserts it); cleanup via DELETE /api/projects.
"""

from __future__ import annotations

import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"k1261 audit_report fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _task_create_payload(project_id: int, **extra) -> dict:
    body: dict = {
        "project_id": project_id,
        "title": "k1261 audit_report smoke task",
        "task_type": "audit",
    }
    body.update(extra)
    return body


# ---- 1. POST with audit_report → persisted in one call (no PATCH) ----------


@pytest.mark.asyncio
async def test_post_task_with_audit_report_round_trips(
    client, scaffold_cleanup
) -> None:
    """POST with audit_report dict → 201 + value stored verbatim (no PATCH).

    POSITIVE: the dict is readable on the returned body and via GET.
    """
    name = scaffold_cleanup(_unique_name("k1261"))
    proj_resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert proj_resp.status_code == 201, proj_resp.text
    project_id = proj_resp.json()["id"]
    try:
        report = {"verdict": "clean", "severity": "low", "evidence": ["no issues"]}
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(project_id, audit_report=report),
        )
        assert resp.status_code == 201, resp.text
        # POSITIVE: round-trip — value matches what we sent.
        assert resp.json()["audit_report"] == report, resp.json()

        # Confirm via GET as well.
        task_id = resp.json()["id"]
        get_resp = await client.get(
            f"/api/tasks/{task_id}",
            headers={"X-Project-Id": str(project_id)},
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["audit_report"] == report, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. POST without audit_report → None (optional field) ------------------


@pytest.mark.asyncio
async def test_post_task_omit_audit_report_lands_none(
    client, scaffold_cleanup
) -> None:
    """POST without audit_report → column is None (field is optional).

    NEGATIVE/lock: audit_report is None — NOT some garbage default, NOT an
    empty dict. Ensures the default is exactly None and the field is truly
    optional.
    """
    name = scaffold_cleanup(_unique_name("k1261"))
    proj_resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert proj_resp.status_code == 201, proj_resp.text
    project_id = proj_resp.json()["id"]
    try:
        resp = await client.post(
            "/api/tasks",
            headers={"X-Project-Id": str(project_id)},
            json=_task_create_payload(project_id),  # no audit_report key
        )
        assert resp.status_code == 201, resp.text
        # NEGATIVE/lock: None — NOT {}, NOT any non-null default.
        assert resp.json()["audit_report"] is None, resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")
