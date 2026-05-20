"""Kanban #1004 — handoff templates contract smoke tests.

Covers the happy path of:
  (1) CRUD: POST + GET + PATCH + DELETE /api/handoff-templates round-trip
      lands a template, soft-deletes it, and re-list excludes it by default.
  (2) DONE-flip spawn: a task carrying handoff_template_id, PATCHed to
      process_status=5, atomically spawns a child task with:
        - title interpolated from template.title_pattern + parent.title,
        - acceptance_criteria built from template.ac_outline,
        - assigned_role / task_kind / task_type / priority copied,
        - resume_context.handoff records template_id + parent_task_id.
  (3) Loop guard (AC6): the spawned child has handoff_template_id=NULL —
      a further PATCH to DONE on the child does NOT chain-spawn.

The rigorous suite (edge cases — global-template scope, malformed
title_pattern 422, project-scope cross-tenant 422, soft-deleted template
spawn no-op WARNING, idempotent re-PATCH of an already-DONE row, etc.)
is dev-tester's domain.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Project helper (mirrors decision_payload_smoke / action_templates_smoke)
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


def _handoff_template_payload(
    name: str,
    *,
    project_id: int | None = None,
    title_pattern: str = "Review + merge: {parent_title}",
    ac_outline: list[str] | None = None,
    carry_context_to_comment: bool = False,
    default_assigned_role: int | None = 5,
) -> dict:
    return {
        "name": name,
        "description": "smoke fixture handoff template",
        "title_pattern": title_pattern,
        "task_kind": "human",
        "task_type": "chore",
        "default_priority": 2,
        "default_assigned_role": default_assigned_role,
        "ac_outline": ac_outline
        if ac_outline is not None
        else [
            "Diff reviewed against spec",
            "CI green",
            "Merged without rebase conflicts",
        ],
        "carry_context_to_comment": carry_context_to_comment,
        **({"project_id": project_id} if project_id is not None else {}),
    }


# ---------------------------------------------------------------------------
# (1) CRUD round-trip — POST, GET (detail + list), PATCH, DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_template_crud_happy_round_trip(
    client, scaffold_cleanup
) -> None:
    """Full CRUD cycle on a project-scoped handoff template.

    Verifies:
      - POST creates the template (201 + Read shape).
      - GET detail returns the stored row.
      - GET list (with X-Project-Id) includes it; without header, excludes it
        (project-scoped templates only surface to their project's listings).
      - PATCH updates the description.
      - DELETE soft-deletes (204); subsequent default GET list excludes the row,
        and include_deleted=true brings it back.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "handoff-crud")
    headers = {"X-Project-Id": str(pid)}

    name = f"approve-and-merge-{uuid.uuid4().hex[:6]}"

    # POST
    resp = await client.post(
        "/api/handoff-templates",
        headers=headers,
        json=_handoff_template_payload(name),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    tmpl_id = body["id"]
    assert body["name"] == name
    assert body["project_id"] == pid
    assert body["title_pattern"] == "Review + merge: {parent_title}"
    assert body["task_kind"] == "human"
    assert body["task_type"] == "chore"
    assert body["default_priority"] == 2
    assert body["default_assigned_role"] == 5
    assert body["ac_outline"] == [
        "Diff reviewed against spec",
        "CI green",
        "Merged without rebase conflicts",
    ]
    assert body["carry_context_to_comment"] is False

    # GET detail
    resp = await client.get(f"/api/handoff-templates/{tmpl_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == tmpl_id

    # GET list scoped to the project — must include our template.
    resp = await client.get("/api/handoff-templates", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert any(t["id"] == tmpl_id for t in items), items

    # GET list without project context — must NOT include our project-scoped template.
    resp = await client.get("/api/handoff-templates")
    assert resp.status_code == 200, resp.text
    items_no_scope = resp.json()
    assert not any(t["id"] == tmpl_id for t in items_no_scope), items_no_scope

    # PATCH the description.
    resp = await client.patch(
        f"/api/handoff-templates/{tmpl_id}",
        json={"description": "updated by smoke test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "updated by smoke test"

    # DELETE soft-deletes.
    resp = await client.delete(f"/api/handoff-templates/{tmpl_id}")
    assert resp.status_code == 204, resp.text

    # Default list excludes the soft-deleted row.
    resp = await client.get("/api/handoff-templates", headers=headers)
    assert resp.status_code == 200, resp.text
    assert not any(t["id"] == tmpl_id for t in resp.json()), resp.json()

    # include_deleted=true brings it back.
    resp = await client.get(
        "/api/handoff-templates?include_deleted=true", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert any(t["id"] == tmpl_id for t in resp.json()), resp.json()


# ---------------------------------------------------------------------------
# (2) DONE-flip spawn — parent → child via handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_done_flip_spawns_child_atomically(
    client, scaffold_cleanup
) -> None:
    """PATCH parent.process_status=5 spawns a child derived from the template.

    Verifies the locked AC2 contract:
      - Child row exists post-PATCH with parent_task_id pointing at parent.
      - child.title = template.title_pattern.format(parent_title=parent.title)
      - child.task_kind / task_type / priority / assigned_role copied from template.
      - child.acceptance_criteria has one entry per ac_outline item,
        each {text, status='pending', verified_*=None}.
      - child.description carries parent context (carry_context_to_comment=true).
      - child.resume_context.handoff records template_id + parent_task_id.
      - child.handoff_template_id IS NULL (loop guard set up for AC6 test).
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "handoff-spawn")
    headers = {"X-Project-Id": str(pid)}

    # Create the template — with carry_context_to_comment=true so we can verify
    # the description is rendered.
    resp = await client.post(
        "/api/handoff-templates",
        headers=headers,
        json=_handoff_template_payload(
            f"merge-after-{uuid.uuid4().hex[:6]}",
            title_pattern="Merge follow-up: {parent_title}",
            ac_outline=["Rebased onto main", "Pushed to origin"],
            carry_context_to_comment=True,
            default_assigned_role=2,
        ),
    )
    assert resp.status_code == 201, resp.text
    tmpl = resp.json()
    tmpl_id = tmpl["id"]

    # Create the parent — TODO with handoff_template_id set.
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": pid,
            "title": "Implement feature X",
            "handoff_template_id": tmpl_id,
            "task_kind": "ai",
            "task_type": "feature",
        },
    )
    assert resp.status_code == 201, resp.text
    parent = resp.json()
    parent_id = parent["id"]
    assert parent["handoff_template_id"] == tmpl_id

    # PATCH parent to DONE — should atomically spawn the child.
    resp = await client.patch(
        f"/api/tasks/{parent_id}",
        headers=headers,
        json={
            "process_status": 5,
            "status_change_reason": "merged via PR #999",
        },
    )
    assert resp.status_code == 200, resp.text
    parent_done = resp.json()
    assert parent_done["process_status"] == 5
    # Parent retains its template pointer (the spawn hook never clears it).
    assert parent_done["handoff_template_id"] == tmpl_id

    # Find the child via the task list with parent_task_id filter.
    resp = await client.get(
        f"/api/tasks?parent_task_id={parent_id}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    children = resp.json()
    assert len(children) == 1, f"expected exactly one child, got {len(children)}: {children}"
    child = children[0]

    # Title rendered from the template's pattern + parent's title.
    assert child["title"] == "Merge follow-up: Implement feature X", child
    # Template fields copied.
    assert child["task_kind"] == "human"
    assert child["task_type"] == "chore"
    assert child["priority"] == 2
    assert child["assigned_role"] == 2
    # parent_task_id wires the child to the parent.
    assert child["parent_task_id"] == parent_id
    # Loop guard: child's own handoff_template_id is NULL.
    assert child["handoff_template_id"] is None, child

    # acceptance_criteria built from ac_outline (each entry {text, status='pending'}).
    acs = child.get("acceptance_criteria") or []
    assert len(acs) == 2, acs
    assert acs[0]["text"] == "Rebased onto main"
    assert acs[0]["status"] == "pending"
    assert acs[1]["text"] == "Pushed to origin"
    assert acs[1]["status"] == "pending"

    # carry_context_to_comment=true → description carries parent context.
    desc = child.get("description") or ""
    assert f"#{parent_id}" in desc, desc
    assert "Implement feature X" in desc, desc
    assert "merged via PR #999" in desc, desc

    # resume_context records the handoff provenance.
    rc = child.get("resume_context") or {}
    handoff_meta = rc.get("handoff") or {}
    assert handoff_meta.get("template_id") == tmpl_id, handoff_meta
    assert handoff_meta.get("parent_task_id") == parent_id, handoff_meta


# ---------------------------------------------------------------------------
# (3) Loop guard — child does NOT chain-spawn (AC6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_no_chain_loop(client, scaffold_cleanup) -> None:
    """Flipping the SPAWNED child to DONE does not spawn a grandchild.

    The router copies the template's task_kind / task_type / priority / etc.
    to the child but explicitly sets the child's `handoff_template_id` to
    NULL (services/handoff_spawn.py). Re-PATCHing the child to DONE therefore
    has nothing to spawn — the chain terminates after one level.
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "handoff-loop")
    headers = {"X-Project-Id": str(pid)}

    # Template that would chain if the guard weren't in place.
    resp = await client.post(
        "/api/handoff-templates",
        headers=headers,
        json=_handoff_template_payload(
            f"chain-attempt-{uuid.uuid4().hex[:6]}",
            title_pattern="Followup of: {parent_title}",
            ac_outline=["Done"],
            default_assigned_role=None,
        ),
    )
    assert resp.status_code == 201, resp.text
    tmpl_id = resp.json()["id"]

    # Create the parent.
    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": pid,
            "title": "Root task",
            "handoff_template_id": tmpl_id,
        },
    )
    assert resp.status_code == 201, resp.text
    parent_id = resp.json()["id"]

    # PATCH parent to DONE → spawns child #1.
    resp = await client.patch(
        f"/api/tasks/{parent_id}",
        headers=headers,
        json={"process_status": 5},
    )
    assert resp.status_code == 200, resp.text

    # Pick the child.
    resp = await client.get(
        f"/api/tasks?parent_task_id={parent_id}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    children = resp.json()
    assert len(children) == 1, children
    child = children[0]
    child_id = child["id"]

    # Positive: the child exists and lacks a template pointer (this is the
    # state the guard establishes — necessary precondition for the negative).
    assert child["handoff_template_id"] is None, child

    # PATCH the child to DONE — should NOT spawn a grandchild.
    resp = await client.patch(
        f"/api/tasks/{child_id}",
        headers=headers,
        json={"process_status": 5},
    )
    assert resp.status_code == 200, resp.text

    # Negative assertion: no grandchildren exist under the child.
    resp = await client.get(
        f"/api/tasks?parent_task_id={child_id}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    grandchildren = resp.json()
    assert grandchildren == [], (
        f"loop guard failed: child #{child_id} spawned a grandchild: {grandchildren}"
    )
