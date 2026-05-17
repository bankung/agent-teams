"""Kanban #1115 (2026-05-17, L18 prevention) — payload-size cap tests.

Defends against hammer-test FINDING #10 (T-DOS-1: API accepted 10MB
description + 10000 AC items, no size guard at any layer).

Two layers tested:
1. Pydantic field-level max_length on TaskCreate / TaskUpdate (422)
2. Middleware Content-Length cap (413)
"""

from __future__ import annotations

import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


async def _make_project(client, scaffold_cleanup, prefix: str) -> int:
    name = _unique_name(prefix)
    scaffold_cleanup(name)
    proj = await client.post("/api/projects", json=_project_create_payload(name))
    assert proj.status_code in (200, 201), proj.text
    return proj.json()["id"]


# ---------------------------------------------------------------------------
# String field caps (AC 1 + AC 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_rejects_oversize_description(client, scaffold_cleanup):
    """AC 5: description > 20_000 chars → 422 with 'at most 20000 characters'."""
    project_id = await _make_project(client, scaffold_cleanup, "size-desc")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "oversize-desc",
        # 20_001 chars — exactly one over the cap. No need for 10MB to prove
        # the principle (and a 10MB body would also trip the middleware 413,
        # masking the 422 we want to assert).
        "description": "A" * 20_001,
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 20000 characters" in resp.text, resp.text


@pytest.mark.asyncio
async def test_post_task_accepts_max_size_description(client, scaffold_cleanup):
    """Boundary: description exactly at the 20_000 cap → 201."""
    project_id = await _make_project(client, scaffold_cleanup, "size-desc-ok")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "max-desc",
        "description": "A" * 20_000,
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_post_task_rejects_oversize_title(client, scaffold_cleanup):
    """AC 1: title > 200 chars → 422 with 'at most 200 characters'."""
    project_id = await _make_project(client, scaffold_cleanup, "size-title")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "A" * 201,
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 200 characters" in resp.text, resp.text


@pytest.mark.asyncio
async def test_post_task_rejects_oversize_halt_reason(client, scaffold_cleanup):
    """L16 (Kanban #1123): halt_reason > 1_000 chars → 422.

    Initially L18 (#1115) set the cap to 2000; L16 tightened it to 1000 as
    part of the prompt-injection prevention layer (smaller field = less room
    for attacker-controlled fluff even after sanitizer redaction).
    """
    project_id = await _make_project(client, scaffold_cleanup, "size-halt")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "oversize-halt",
        "halt_reason": "B" * 1_001,
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 1000 characters" in resp.text, resp.text


@pytest.mark.asyncio
async def test_post_task_rejects_oversize_status_change_reason(
    client, scaffold_cleanup
):
    """L16 (Kanban #1123): status_change_reason > 1_000 chars → 422.

    Tightened from L18's initial 2000 cap. See test above for the rationale.
    """
    project_id = await _make_project(client, scaffold_cleanup, "size-scr")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "oversize-scr",
        "status_change_reason": "C" * 1_001,
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 1000 characters" in resp.text, resp.text


# ---------------------------------------------------------------------------
# List size caps (AC 2 + AC 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_rejects_too_many_acceptance_criteria(
    client, scaffold_cleanup
):
    """AC 6: > 50 AC items → 422 with 'at most 50 items'."""
    project_id = await _make_project(client, scaffold_cleanup, "size-ac-count")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "too-many-acs",
        "acceptance_criteria": [
            {"text": f"criterion {i}", "status": "pending"} for i in range(51)
        ],
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 50 items" in resp.text, resp.text


@pytest.mark.asyncio
async def test_post_task_accepts_max_acceptance_criteria(client, scaffold_cleanup):
    """Boundary: exactly 50 AC items → 201."""
    project_id = await _make_project(client, scaffold_cleanup, "size-ac-ok")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "max-acs",
        "acceptance_criteria": [
            {"text": f"criterion {i}", "status": "pending"} for i in range(50)
        ],
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_post_task_rejects_too_many_subagent_models(client, scaffold_cleanup):
    """AC 2: > 200 subagent_models entries → 422."""
    project_id = await _make_project(client, scaffold_cleanup, "size-sm-count")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "too-many-sm",
        "subagent_models": [
            {
                "agent": f"agent-{i}",
                "model": "opus",
                "at": "2026-05-17T00:00:00Z",
            }
            for i in range(201)
        ],
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 200 items" in resp.text, resp.text


# ---------------------------------------------------------------------------
# AcceptanceCriterion.text cap (AC 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_rejects_oversize_ac_text(client, scaffold_cleanup):
    """AC 3: acceptance_criteria[].text > 1_000 chars → 422."""
    project_id = await _make_project(client, scaffold_cleanup, "size-ac-text")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "oversize-ac-text",
        "acceptance_criteria": [
            {"text": "D" * 1_001, "status": "pending"},
        ],
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "at most 1000 characters" in resp.text, resp.text


# ---------------------------------------------------------------------------
# Middleware request body cap (AC 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversize_request_body_returns_413(client, scaffold_cleanup):
    """AC 4: Content-Length > 2 MB cap → 413 'Request body too large'.

    We synthesize a payload large enough that even the JSON envelope alone
    pushes past 2 MB. The middleware short-circuits before Pydantic sees the
    body, so the response is 413 not 422.
    """
    project_id = await _make_project(client, scaffold_cleanup, "size-413")
    headers = {"X-Project-Id": str(project_id)}

    # 3 MB description string (will be rejected pre-parse by middleware on
    # the Content-Length header alone).
    payload = {
        "project_id": project_id,
        "title": "oversize-body",
        "description": "X" * (3 * 1024 * 1024),
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 413, resp.text
    assert "too large" in resp.text.lower(), resp.text


@pytest.mark.asyncio
async def test_normal_request_passes_middleware(client, scaffold_cleanup):
    """Sanity: normal-sized POST still works after middleware wired in."""
    project_id = await _make_project(client, scaffold_cleanup, "size-passthru")
    headers = {"X-Project-Id": str(project_id)}

    payload = {
        "project_id": project_id,
        "title": "normal-size",
        "description": "small body",
    }
    resp = await client.post("/api/tasks", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# TaskUpdate parity (PATCH path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_rejects_oversize_description(client, scaffold_cleanup):
    """PATCH path parity: oversize description on PATCH → 422."""
    project_id = await _make_project(client, scaffold_cleanup, "patch-size-desc")
    headers = {"X-Project-Id": str(project_id)}

    create = await client.post(
        "/api/tasks",
        json={"project_id": project_id, "title": "patch-victim"},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    resp = await client.patch(
        f"/api/tasks/{task_id}",
        json={"description": "E" * 20_001},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "at most 20000 characters" in resp.text, resp.text
