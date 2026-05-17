"""Kanban #957 — `projects.approval_policies` JSONB column wire-up.

Migration `0033_projects_approval_policies` adds the column as nullable
JSONB. PATCH semantics mirror `tools_config` (key-absent leaves unchanged;
explicit dict REPLACES; explicit null CLEARS to NULL).

Tests pin:
- POST creates a project without approval_policies → DB stores NULL.
- PATCH with a valid dict → 200 + GET reflects.
- PATCH with explicit null → CLEAR to NULL on subsequent GET.
- PATCH with an unrelated field does NOT clobber approval_policies.
- PATCH with forward-compat shape (unknown predicate keys) accepted —
  shape validation lives in the worker's evaluator, not at the API
  boundary (operator can stage rules ahead of evaluator updates).
- ProjectRead surfaces the field as-is (None when null).
"""

from __future__ import annotations

import uuid

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str, *, team: str = "dev") -> dict:
    return {
        "name": name,
        "description": f"k957 approval_policies fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


_SAMPLE_POLICY = {
    "rules": [
        {
            "name": "auto-approve small llm spend",
            "match": {"text_contains": "llm", "amount_usd_lt": 5.0},
            "action": "auto_approve",
            "default_answer": "accept",
        },
        {
            "name": "auto-deny rm -rf",
            "match": {"text_contains": "rm -rf"},
            "action": "auto_deny",
        },
    ]
}


# ---------------------------------------------------------------------------
# 1. POST default → approval_policies is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_create_default_approval_policies_is_null(
    client, scaffold_cleanup
) -> None:
    name = scaffold_cleanup(_unique_name("k957-default"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["approval_policies"] is None, resp.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["approval_policies"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 2. PATCH with a valid policies dict → 200 + GET reflects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_update_approval_policies_accepts_valid(
    client, scaffold_cleanup
) -> None:
    name = scaffold_cleanup(_unique_name("k957-patch-ok"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"approval_policies": _SAMPLE_POLICY},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["approval_policies"] == _SAMPLE_POLICY, patch.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["approval_policies"] == _SAMPLE_POLICY, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 3. PATCH with explicit null → CLEAR to NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_update_approval_policies_null_clears(
    client, scaffold_cleanup
) -> None:
    name = scaffold_cleanup(_unique_name("k957-patch-null"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # Seed with a real policy first.
        seed = await client.patch(
            f"/api/projects/{project_id}",
            json={"approval_policies": _SAMPLE_POLICY},
        )
        assert seed.status_code == 200, seed.text
        # Then clear.
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"approval_policies": None}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["approval_policies"] is None, patch.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.json()["approval_policies"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 4. PATCH key-absent leaves approval_policies unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_update_unrelated_field_preserves_approval_policies(
    client, scaffold_cleanup
) -> None:
    """PATCHing only `description` must not touch `approval_policies`."""
    name = scaffold_cleanup(_unique_name("k957-patch-other"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # Seed policies.
        seed = await client.patch(
            f"/api/projects/{project_id}",
            json={"approval_policies": _SAMPLE_POLICY},
        )
        assert seed.status_code == 200, seed.text
        # Update an unrelated field.
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"description": "updated desc"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["approval_policies"] == _SAMPLE_POLICY, patch.json()
        assert patch.json()["description"] == "updated desc", patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 5. PATCH accepts forward-compat shapes (unknown predicate keys)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_update_approval_policies_accepts_forward_compat_shape(
    client, scaffold_cleanup
) -> None:
    """Operator may stage rules with predicates the evaluator doesn't know yet.

    The API does not 422 — the worker's evaluator falls back to
    REQUIRE_ATTENTION on unknown predicates (fail closed).
    """
    name = scaffold_cleanup(_unique_name("k957-patch-fwd"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        forward_compat = {
            "rules": [
                {
                    "name": "future predicate",
                    "match": {"role_equals": "tester"},  # not yet implemented
                    "action": "auto_approve",
                }
            ]
        }
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"approval_policies": forward_compat},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["approval_policies"] == forward_compat, patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---------------------------------------------------------------------------
# 6. ProjectUpdate Pydantic — direct unit test (no HTTP)
# ---------------------------------------------------------------------------


def test_project_update_pydantic_approval_policies_optional() -> None:
    """ProjectUpdate.approval_policies is optional — empty PATCH body validates."""
    from src.schemas.project import ProjectUpdate

    # Empty body validates fine.
    update = ProjectUpdate()
    assert "approval_policies" not in update.model_dump(exclude_unset=True)

    # Explicit dict.
    update = ProjectUpdate(approval_policies={"rules": []})
    dumped = update.model_dump(exclude_unset=True)
    assert dumped["approval_policies"] == {"rules": []}

    # Explicit null.
    update = ProjectUpdate(approval_policies=None)
    dumped = update.model_dump(exclude_unset=True)
    assert "approval_policies" in dumped
    assert dumped["approval_policies"] is None
