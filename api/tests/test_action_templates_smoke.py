"""Kanban #1006 — action-template library contract smoke tests.

Covers the happy path of:
  (1) GET /api/templates/actions returns all 7 templates with the locked field shape
  (2) POST /api/tasks with action_template_id="approve-pr" and NO explicit
      task_kind/task_type/priority pre-fills correctly from the template
  (3) POST /api/tasks with action_template_id="approve-pr" AND explicit
      task_kind="ai" respects the caller's explicit choice (override-safe)
  (4) The created task's resume_context.action_template.{id,version} records
      the template provenance (AC6)

Test isolation: templates are loaded from a tmp directory controlled by the
ACTION_TEMPLATES_DIR env override so the test suite is not coupled to
.claude/templates/actions/ being promoted by Lead yet.

These are first-pass contract-smoke tests only.  The comprehensive edge-case
suite (unknown template_id 422, malformed YAML warning, partial AC merge,
cache-invalidation, etc.) is delegated to dev-tester.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_APPROVE_PR_YAML = """\
name: approve-pr
version: "1.0.0"
description: "Review a pull request diff and merge it to the target branch."
default_task_type: chore
default_task_kind: human
default_priority: 2
ac_outline:
  - "PR diff reviewed against the spec"
  - "Tests pass on CI"
  - "Merge to main without rebase conflicts"
hints:
  - "Skim the diff for new env vars or dependencies"
suggested_attachments:
  - "PR URL"
  - "Spec doc link"
"""

_MINIMAL_YAML = """\
name: minimal-template
version: "0.1.0"
description: "Minimal template for testing."
default_task_type: feature
default_task_kind: ai
default_priority: 3
ac_outline: []
"""


@pytest.fixture(autouse=True)
def _reset_template_cache():
    """Clear the loader cache before each test so env overrides take effect.

    Mutates the module-private `_CACHE` directly per `feedback_test_surface_pollution`
    memory — production code intentionally does NOT expose a public `reset_cache()`
    helper; tests reach into the module's private state.
    """
    from src.services import action_templates
    action_templates._CACHE = None
    yield
    action_templates._CACHE = None


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create a tmp directory with two YAML template files."""
    (tmp_path / "approve-pr.yaml").write_text(_APPROVE_PR_YAML, encoding="utf-8")
    (tmp_path / "minimal-template.yaml").write_text(_MINIMAL_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def set_templates_dir(templates_dir: Path):
    """Set ACTION_TEMPLATES_DIR to the tmp templates directory."""
    original = os.environ.get("ACTION_TEMPLATES_DIR")
    os.environ["ACTION_TEMPLATES_DIR"] = str(templates_dir)
    yield templates_dir
    if original is None:
        os.environ.pop("ACTION_TEMPLATES_DIR", None)
    else:
        os.environ["ACTION_TEMPLATES_DIR"] = original


# ---------------------------------------------------------------------------
# Project helper (mirrors decision_payload_smoke pattern)
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


# ---------------------------------------------------------------------------
# (1) GET /api/templates/actions returns loaded templates with locked shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_action_templates_shape(client, set_templates_dir) -> None:
    """GET /api/templates/actions returns the locked field shape for each template.

    Two templates are loaded from the tmp dir.  Each must carry:
      id, name, version, description, default_task_type, default_task_kind,
      default_priority, ac_outline (list), hints (list), suggested_attachments (list).
    """
    resp = await client.get("/api/templates/actions")
    assert resp.status_code == 200, resp.text

    items = resp.json()
    assert isinstance(items, list), items
    assert len(items) == 2, items

    # Find approve-pr in the response
    approve = next((t for t in items if t["name"] == "approve-pr"), None)
    assert approve is not None, f"approve-pr not in {[t['name'] for t in items]}"

    # Locked field shape (AC1)
    assert approve["id"] == "approve-pr"
    assert approve["version"] == "1.0.0"
    assert isinstance(approve["description"], str) and len(approve["description"]) > 0
    assert approve["default_task_type"] == "chore"
    assert approve["default_task_kind"] == "human"
    assert approve["default_priority"] == 2
    assert isinstance(approve["ac_outline"], list) and len(approve["ac_outline"]) == 3
    assert isinstance(approve["hints"], list) and len(approve["hints"]) >= 1
    assert isinstance(approve["suggested_attachments"], list)


# ---------------------------------------------------------------------------
# (2) POST /api/tasks with action_template_id pre-fills from template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_with_template_prefills_defaults(
    client, scaffold_cleanup, set_templates_dir
) -> None:
    """POST /api/tasks with action_template_id="approve-pr" and NO explicit
    task_kind/task_type/priority → fields pre-filled from the template.

    Verifies:
      - task_kind = "human" (from approve-pr template, not the default "ai")
      - task_type = "chore"  (from approve-pr)
      - priority  = 2        (from approve-pr)
      - acceptance_criteria has 3 entries (from ac_outline)
      - resume_context.action_template.id == "approve-pr" (AC6)
      - resume_context.action_template.version == "1.0.0"  (AC6)
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "tmpl-smoke-a")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": pid,
            "title": "Review the PR for feature X",
            "action_template_id": "approve-pr",
            # Explicitly NOT supplying task_kind, task_type, priority, acceptance_criteria
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Template defaults applied (approve-pr is human/chore/priority=2)
    assert body["task_kind"] == "human", body
    assert body["task_type"] == "chore", body
    assert body["priority"] == 2, body

    # acceptance_criteria populated from ac_outline
    acs = body.get("acceptance_criteria") or []
    assert len(acs) == 3, f"expected 3 AC items from ac_outline, got {len(acs)}: {acs}"
    for item in acs:
        assert item["status"] == "pending", item
        assert len(item["text"]) > 0, item

    # AC6: resume_context carries action_template provenance
    rc = body.get("resume_context")
    assert rc is not None, "resume_context should be set when action_template_id is used"
    at = rc.get("action_template")
    assert at is not None, f"resume_context missing action_template key: {rc}"
    assert at["id"] == "approve-pr", at
    assert at["version"] == "1.0.0", at


# ---------------------------------------------------------------------------
# (3) POST with explicit task_kind="ai" overrides the template default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_task_explicit_field_overrides_template(
    client, scaffold_cleanup, set_templates_dir
) -> None:
    """POST /api/tasks with action_template_id="approve-pr" AND explicit
    task_kind="ai" — caller's value takes precedence over the template default.

    The template default for approve-pr is task_kind="human".  The caller
    supplies task_kind="ai" explicitly — the stored value must be "ai".
    """
    pid = await _make_fresh_project(client, scaffold_cleanup, "tmpl-smoke-b")
    headers = {"X-Project-Id": str(pid)}

    resp = await client.post(
        "/api/tasks",
        headers=headers,
        json={
            "project_id": pid,
            "title": "Review the PR for feature Y",
            "action_template_id": "approve-pr",
            "task_kind": "ai",  # explicit override — must win
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Explicit caller value must win over template default
    assert body["task_kind"] == "ai", (
        f"Caller-supplied task_kind='ai' should override template default 'human'; "
        f"got {body['task_kind']}"
    )

    # task_type and priority still come from the template (not explicitly set)
    assert body["task_type"] == "chore", body
    assert body["priority"] == 2, body

    # AC6 still records template provenance even on a partial override
    rc = body.get("resume_context") or {}
    assert rc.get("action_template", {}).get("id") == "approve-pr"
