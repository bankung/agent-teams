"""Kanban #1840 — `projects.auto_decision_policy` JSONB column wire-up.

Migration `0070_proj_auto_decision_policy` adds a nullable JSONB column
storing a declarative, per-project override for the full-auto Lead's hardcoded
top-5 decision matrix (context/teams/dev/full-auto.md). NULL = no policy (the
Lead uses the matrix verbatim).

First-pass contract-smoke (happy path + the AC-named negative cases):
- POST without `auto_decision_policy` → column lands NULL (omit-when-None).
- POST with a valid partial policy → stored verbatim, GET round-trips it.
- PATCH with a valid policy → 200 + GET reflects (round-trip).
- POST/PATCH with a bad Literal (reviewer_nit:"maybe") → 422.
- POST with an extra/unknown key (extra="forbid") → 422.

Shape mirrors `approval_policies` storage convention (nullable, no DB CHECK,
validation at the API boundary) but with a TYPED AutoDecisionPolicy model
(extra="forbid", all fields optional → partial policies allowed). Cleanup uses
scaffold_cleanup + DELETE /api/projects/{id} on the way out so the live-DB
row-count invariant in conftest stays happy.
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
        "description": f"k1840 auto_decision_policy fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


# ---- 1. POST without auto_decision_policy → NULL (omit-when-None) -----------


@pytest.mark.asyncio
async def test_create_project_omits_auto_decision_policy_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST without `auto_decision_policy` → column NULL (= no policy).

    Pins the router's OMIT-when-None branch + the nullable column default.
    A project with no policy returns auto_decision_policy=null (AC).
    """
    name = scaffold_cleanup(_unique_name("k1840-omit"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["auto_decision_policy"] is None, resp.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["auto_decision_policy"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. POST with a valid partial policy → stored verbatim, GET round-trips -


@pytest.mark.asyncio
async def test_create_project_explicit_auto_decision_policy_round_trip(
    client, scaffold_cleanup
) -> None:
    """POST with a valid partial policy → server stores it; GET round-trips.

    Uses the AC's example shape. `reviewer_warn` is partial (only fold_max_loc
    given) — the model fills `fold_requires_no_contract_change` with its True
    default, so the persisted+returned sub-shape carries BOTH keys. Unset
    top-level knobs (tester_standards_proposal etc.) are stripped by the POST
    path's exclude_none, so they do NOT appear as null in the stored JSONB.
    """
    name = scaffold_cleanup(_unique_name("k1840-explicit"))
    payload = _project_create_payload(name)
    payload["auto_decision_policy"] = {
        "reviewer_warn": {"fold_max_loc": 20},
        "reviewer_nit": "fold",
    }

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        expected = {
            "reviewer_warn": {
                "fold_max_loc": 20,
                "fold_requires_no_contract_change": True,
            },
            "reviewer_nit": "fold",
        }
        assert resp.json()["auto_decision_policy"] == expected, resp.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["auto_decision_policy"] == expected, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 3. PATCH with a valid policy → 200 + GET reflects (round-trip) ---------


@pytest.mark.asyncio
async def test_patch_project_auto_decision_policy_round_trip(
    client, scaffold_cleanup
) -> None:
    """PATCH a valid policy onto a no-policy project → 200, GET reflects it."""
    name = scaffold_cleanup(_unique_name("k1840-patch-ok"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # POSITIVE: started with no policy.
        assert create.json()["auto_decision_policy"] is None, create.json()

        new_policy = {
            "tester_standards_proposal": "log_only",
            "validator_ambiguity": "halt",
            "scope_creep": "halt",
        }
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"auto_decision_policy": new_policy},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["auto_decision_policy"] == new_policy, patch.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["auto_decision_policy"] == new_policy, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 4. PATCH explicit-null CLEARS to NULL (null-stays-null) ----------------


@pytest.mark.asyncio
async def test_patch_project_auto_decision_policy_null_clears_to_null(
    client, scaffold_cleanup
) -> None:
    """PATCH explicit `null` → column CLEARS to NULL (NOT coerced to {}).

    Mirrors notification_targets semantics: the "no policy" NULL state is
    distinct from "{} configured". POSITIVE: set a policy first and confirm it
    landed; NEGATIVE/lock: explicit-null wipes it back to None, NOT to {} and
    NOT leaving the prior policy in place.
    """
    name = scaffold_cleanup(_unique_name("k1840-patch-null"))
    payload = _project_create_payload(name)
    payload["auto_decision_policy"] = {"reviewer_nit": "defer"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # POSITIVE: the policy is really set on the row first.
        assert create.json()["auto_decision_policy"] == {"reviewer_nit": "defer"}, (
            create.json()
        )

        # NEGATIVE/lock: explicit-null clears to None — NOT {} and NOT the prior.
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"auto_decision_policy": None},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["auto_decision_policy"] is None, patch.json()
        assert patch.json()["auto_decision_policy"] != {}, patch.json()
        assert patch.json()["auto_decision_policy"] != {"reviewer_nit": "defer"}, (
            patch.json()
        )

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["auto_decision_policy"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5. PATCH absent key leaves the value unchanged ------------------------


@pytest.mark.asyncio
async def test_patch_project_auto_decision_policy_absent_key_unchanged(
    client, scaffold_cleanup
) -> None:
    """A PATCH that does NOT mention auto_decision_policy leaves it intact.

    Pins exclude_unset key-absent semantics: a PATCH touching only
    `description` must not wipe a previously-set policy.
    """
    name = scaffold_cleanup(_unique_name("k1840-patch-absent"))
    payload = _project_create_payload(name)
    payload["auto_decision_policy"] = {"scope_creep": "halt"}
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"description": "edited, no policy key"},
        )
        assert patch.status_code == 200, patch.text
        # Unchanged — still the original policy, NOT None and NOT {}.
        assert patch.json()["auto_decision_policy"] == {"scope_creep": "halt"}, (
            patch.json()
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 6. Bad Literal value → 422 (POST + PATCH) -----------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_bad_reviewer_nit_literal(
    client, scaffold_cleanup
) -> None:
    """POST with reviewer_nit:"maybe" (not in {defer,fold}) → 422.

    The typed AutoDecisionPolicy Literal rejects the value at the boundary; the
    422 loc points into body.auto_decision_policy.reviewer_nit.
    """
    name = scaffold_cleanup(_unique_name("k1840-badlit-create"))
    payload = _project_create_payload(name)
    payload["auto_decision_policy"] = {"reviewer_nit": "maybe"}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err
        for err in body["detail"]
        if err["loc"][:2] == ["body", "auto_decision_policy"]
    ]
    assert matches, f"expected loc=['body','auto_decision_policy',...]; got {body}"


@pytest.mark.asyncio
async def test_patch_project_rejects_bad_reviewer_nit_literal(
    client, scaffold_cleanup
) -> None:
    """PATCH with reviewer_nit:"maybe" → 422; valid project left untouched."""
    name = scaffold_cleanup(_unique_name("k1840-badlit-patch"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"auto_decision_policy": {"reviewer_nit": "maybe"}},
        )
        assert patch.status_code == 422, patch.text
        matches = [
            err
            for err in patch.json()["detail"]
            if err["loc"][:2] == ["body", "auto_decision_policy"]
        ]
        assert matches, f"expected auto_decision_policy 422; got {patch.json()}"
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 7. Unknown/extra key → 422 (extra="forbid") ---------------------------


@pytest.mark.asyncio
async def test_create_project_rejects_extra_policy_key(
    client, scaffold_cleanup
) -> None:
    """POST with an unknown policy key → 422 (extra="forbid").

    A typo'd knob (e.g. `reviwer_warn`) must fail loudly rather than silently
    persist as a no-op. The 422 loc points into body.auto_decision_policy.
    """
    name = scaffold_cleanup(_unique_name("k1840-extrakey-create"))
    payload = _project_create_payload(name)
    payload["auto_decision_policy"] = {"reviwer_warn": {"fold_max_loc": 5}}

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err
        for err in body["detail"]
        if err["loc"][:2] == ["body", "auto_decision_policy"]
    ]
    assert matches, f"expected loc=['body','auto_decision_policy',...]; got {body}"


@pytest.mark.asyncio
async def test_create_project_rejects_extra_nested_reviewer_warn_key(
    client, scaffold_cleanup
) -> None:
    """POST with an extra key INSIDE reviewer_warn → 422 (nested extra=forbid).

    Pins that the ReviewerWarnPolicy sub-model is also strict — a typo'd
    `fold_max_lines` (vs `fold_max_loc`) fails 422.
    """
    name = scaffold_cleanup(_unique_name("k1840-extranested-create"))
    payload = _project_create_payload(name)
    payload["auto_decision_policy"] = {
        "reviewer_warn": {"fold_max_loc": 10, "fold_max_lines": 99}
    }

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err
        for err in body["detail"]
        if err["loc"][:2] == ["body", "auto_decision_policy"]
    ]
    assert matches, f"expected loc=['body','auto_decision_policy',...]; got {body}"
