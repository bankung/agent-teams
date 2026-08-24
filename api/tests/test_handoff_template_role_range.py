"""Kanban #2819 — regression lock: handoff_templates.default_assigned_role
DB CHECK vs Pydantic validator range alignment.

Background: `ck_handoff_templates_default_assigned_role_range` (migration
0045_handoff_templates) hardcoded <= 50. Kanban #2812 bumped
`TaskRole.RANGE_MAX` 50 -> 60 (social team codes 51-57), so
`schemas/handoff_template.py::_validate_role_range` now accepts 1..60 at the
Pydantic boundary — but until migration
0077_drop_handoff_role_check is APPLIED, the DB CHECK still
rejects 51..60 with an IntegrityError (surfaced by the router's generic
IntegrityError handler as HTTP 400 "violates a database constraint").

This file locks the fix: in-range codes must succeed end-to-end (POST 201),
while the first out-of-range code must still be rejected at the Pydantic
boundary (422) — proving RANGE_MAX is the enforced ceiling, not an accidental
widening.

Kanban #2871 (2026-08-24) bumped RANGE_MAX 60 -> 70 for the mobile team
(codes 61-62), so the ceiling probe moved 61 -> 71 and 61 joined the
positive set. The lock's INTENT is unchanged: whatever RANGE_MAX is, the
code just past it must 422.

NOTE: `test_handoff_template_role_in_range_succeed` FAILS until migration
0077 is applied (the migration is build-only per the #2819 spawn brief,
reviewed by Lead before `alembic upgrade`) — this is the intended
"fails before / passes after" lock, not a bug in the test.
"""

from __future__ import annotations

import uuid

import pytest


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


def _role_payload(name: str, role: int) -> dict:
    return {
        "name": name,
        "title_pattern": "Follow-up: {parent_title}",
        "task_kind": "human",
        "task_type": "chore",
        "default_assigned_role": role,
    }


@pytest.mark.asyncio
async def test_handoff_template_role_in_range_succeed(
    client, scaffold_cleanup
) -> None:
    """In-range codes must round-trip to the DB: social 51/60 (Kanban #2812)
    and mobile 61/70 (Kanban #2871).

    POSITIVE lock for #2819: once migration 0077 drops the stale <=50 DB
    CHECK, these succeed (201) instead of the 400 the router's
    IntegrityError handler returns today.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "handoff-role-range")
    headers = {"X-Project-Id": str(pid)}

    for role in (51, 60, 61, 70):
        resp = await client.post(
            "/api/handoff-templates",
            headers=headers,
            json=_role_payload(f"role-{role}-{uuid.uuid4().hex[:6]}", role),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["default_assigned_role"] == role


@pytest.mark.asyncio
async def test_handoff_template_role_71_rejected(client, scaffold_cleanup) -> None:
    """71 stays out of range (TaskRole.RANGE_MAX=70) — Pydantic 422 at the
    request boundary, never reaching the DB.

    NEGATIVE lock: the range gate must stay a real ceiling — dropping the DB
    CHECK must not silently widen what the app layer accepts.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "handoff-role-range")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/handoff-templates",
        headers=headers,
        json=_role_payload(f"role-71-{uuid.uuid4().hex[:6]}", 71),
    )
    assert resp.status_code == 422, resp.text
