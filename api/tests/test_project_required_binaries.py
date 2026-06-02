"""Kanban #1800 / #1652 — `projects.required_binaries` JSONB column wire-up.

Migration `0055_required_binaries` adds a nullable JSONB column storing a
declared list of host-binary names the project's Mode-B (langgraph headless)
tools require on PATH, e.g. ["ffmpeg", "yt-dlp"]. NULL = no host-binary
requirements (today's behavior; the worker gate skips).

This file pins the API-layer contract (first-pass contract-smoke):
- POST without `required_binaries` → column lands NULL (omit-when-None)
- POST with explicit list → stored verbatim, GET reflects
- PATCH with a valid list → 200 + GET reflects (round-trip)
- PATCH explicit-null → CLEARS to NULL (null-stays-null, NOT coerced to [])
- PATCH absent key → leaves the existing value unchanged
- POST/PATCH with a bad binary name ("../etc", "a;b") → 422

Semantics mirror `notification_targets` EXACTLY (nullable, null-stays-null,
value-tolerant read). Cleanup uses scaffold_cleanup + DELETE /api/projects/{id}
on the way out so the live-DB row-count invariant in conftest stays happy.
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
        "description": f"k1800 required_binaries fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": team,
    }


# ---- 1. POST without required_binaries → NULL (omit-when-None) --------------


@pytest.mark.asyncio
async def test_create_project_omits_required_binaries_lands_null(
    client, scaffold_cleanup
) -> None:
    """POST without `required_binaries` → column NULL (= no requirements).

    Pins the router's OMIT-when-None branch + the nullable column default.
    """
    name = scaffold_cleanup(_unique_name("k1800-omit"))
    resp = await client.post("/api/projects", json=_project_create_payload(name))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["required_binaries"] is None, resp.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["required_binaries"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 2. POST with explicit list → stored verbatim --------------------------


@pytest.mark.asyncio
async def test_create_project_explicit_required_binaries_round_trip(
    client, scaffold_cleanup
) -> None:
    """POST with explicit `required_binaries` → server stores it verbatim."""
    name = scaffold_cleanup(_unique_name("k1800-explicit"))
    payload = _project_create_payload(name)
    payload["required_binaries"] = ["ffmpeg", "yt-dlp"]

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    try:
        assert resp.json()["required_binaries"] == ["ffmpeg", "yt-dlp"], resp.json()
        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["required_binaries"] == ["ffmpeg", "yt-dlp"], (
            get_resp.json()
        )
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 3. PATCH with a valid list → 200 + GET reflects -----------------------


@pytest.mark.asyncio
async def test_patch_project_required_binaries_round_trip(
    client, scaffold_cleanup
) -> None:
    """PATCH a valid list → 200, then GET returns the new value."""
    name = scaffold_cleanup(_unique_name("k1800-patch-ok"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        new_list = ["ffmpeg", "imagemagick", "pandoc"]
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"required_binaries": new_list}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["required_binaries"] == new_list, patch.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["required_binaries"] == new_list, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 4. PATCH explicit-null CLEARS to NULL (null-stays-null) ---------------


@pytest.mark.asyncio
async def test_patch_project_required_binaries_null_clears_to_null(
    client, scaffold_cleanup
) -> None:
    """PATCH explicit `null` → column CLEARS to NULL (NOT coerced to []).

    Mirrors notification_targets semantics: the "no requirements" NULL state is
    distinct from "[] configured". POSITIVE side: set a non-empty list first and
    confirm it landed; NEGATIVE/lock side: explicit-null wipes it back to None,
    NOT to [] and NOT leaving the prior list in place.
    """
    name = scaffold_cleanup(_unique_name("k1800-patch-null"))
    payload = _project_create_payload(name)
    payload["required_binaries"] = ["ffmpeg"]
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # POSITIVE: the list is really set on the row first.
        assert create.json()["required_binaries"] == ["ffmpeg"], create.json()

        # NEGATIVE/lock: explicit-null clears to None — NOT [] and NOT ["ffmpeg"].
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"required_binaries": None}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["required_binaries"] is None, patch.json()
        assert patch.json()["required_binaries"] != [], patch.json()
        assert patch.json()["required_binaries"] != ["ffmpeg"], patch.json()

        get_resp = await client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["required_binaries"] is None, get_resp.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 5. PATCH absent key leaves the value unchanged ------------------------


@pytest.mark.asyncio
async def test_patch_project_required_binaries_absent_key_unchanged(
    client, scaffold_cleanup
) -> None:
    """PATCH that does NOT mention required_binaries leaves the prior list intact.

    Pins exclude_unset key-absent semantics: a PATCH touching only
    `description` must not wipe a previously-set required_binaries list.
    """
    name = scaffold_cleanup(_unique_name("k1800-patch-absent"))
    payload = _project_create_payload(name)
    payload["required_binaries"] = ["ffmpeg", "yt-dlp"]
    create = await client.post("/api/projects", json=payload)
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        # PATCH an unrelated field — required_binaries key is ABSENT.
        patch = await client.patch(
            f"/api/projects/{project_id}", json={"description": "edited, no binaries key"}
        )
        assert patch.status_code == 200, patch.text
        # Unchanged — still the original list, NOT None and NOT [].
        assert patch.json()["required_binaries"] == ["ffmpeg", "yt-dlp"], patch.json()
    finally:
        await client.delete(f"/api/projects/{project_id}")


# ---- 6. Bad binary name → 422 (POST + PATCH) -------------------------------


@pytest.mark.parametrize("bad_name", ["../etc", "a;b", "rm -rf /", "/usr/bin/ffmpeg", "", "$(whoami)"])
@pytest.mark.asyncio
async def test_create_project_rejects_bad_binary_name(
    client, scaffold_cleanup, bad_name
) -> None:
    """POST with a binary name containing a path / shell metachar / empty → 422.

    The `_BINARY_NAME_RE` validator rejects the injection surface at the
    boundary; the 422 loc points at body.required_binaries.
    """
    name = scaffold_cleanup(_unique_name("k1800-badname-create"))
    payload = _project_create_payload(name)
    payload["required_binaries"] = ["ffmpeg", bad_name]

    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    matches = [
        err
        for err in body["detail"]
        if err["loc"][:2] == ["body", "required_binaries"]
    ]
    assert matches, f"expected loc=['body','required_binaries',...]; got {body}"


@pytest.mark.asyncio
async def test_patch_project_rejects_bad_binary_name(
    client, scaffold_cleanup
) -> None:
    """PATCH with a bad binary name ("a;b") → 422; valid project left untouched."""
    name = scaffold_cleanup(_unique_name("k1800-badname-patch"))
    create = await client.post("/api/projects", json=_project_create_payload(name))
    assert create.status_code == 201, create.text
    project_id = create.json()["id"]
    try:
        patch = await client.patch(
            f"/api/projects/{project_id}",
            json={"required_binaries": ["a;b"]},
        )
        assert patch.status_code == 422, patch.text
        matches = [
            err
            for err in patch.json()["detail"]
            if err["loc"][:2] == ["body", "required_binaries"]
        ]
        assert matches, f"expected required_binaries 422; got {patch.json()}"
    finally:
        await client.delete(f"/api/projects/{project_id}")
