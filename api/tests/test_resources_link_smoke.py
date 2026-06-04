"""Kanban #1309 — resources API smoke tests that run NOW (no multipart needed).

The LINK create path, GET detail/preview, DELETE-to-trash (link has no file),
the operator-gate 403, the 404s, and same-project task_id 422 for links all use
JSON bodies — they do NOT require python-multipart, so they run BEFORE the
container rebuild. The multipart FILE-upload cases live in
test_resources_integration.py (skip-guarded until the rebuild).

The link HEAD probe is best-effort + swallows errors; these tests don't assert a
specific head_status (network-dependent) — only that the field is present and the
create succeeds.
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
            "description": f"resources link smoke for {name}",
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
# LINK create -> detail -> preview -> delete round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_create_detail_preview_delete(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-link")

    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={
            "kind": "link",
            "url": "https://example.com/spec.pdf",
            "label": "Spec",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    rid = body["id"]
    assert body["kind"] == "link"
    assert body["url"] == "https://example.com/spec.pdf"
    assert body["label"] == "Spec"
    # POSITIVE: verify-and-tag captured the URL syntax breakdown.
    assert body["tags"]["url_scheme"] == "https"
    assert body["tags"]["url_host"] == "example.com"
    # head_status key present (value may be None when offline — best-effort).
    assert "head_status" in body["tags"]

    # GET detail.
    resp = await client.get(f"/api/resources/{rid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == rid

    # GET preview (link -> no row/col but the endpoint still 200s).
    resp = await client.get(f"/api/resources/{rid}/preview")
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "link"

    # In list (newest first).
    resp = await client.get(f"/api/projects/{pid}/resources")
    assert resp.status_code == 200, resp.text
    assert any(r["id"] == rid for r in resp.json())

    # DELETE soft-deletes (link has no file -> trash move is a no-op).
    resp = await client.delete(f"/api/resources/{rid}")
    assert resp.status_code == 204, resp.text
    # NEGATIVE (the lock): gone from the active list.
    resp = await client.get(f"/api/projects/{pid}/resources")
    assert not any(r["id"] == rid for r in resp.json()), resp.json()


# ---------------------------------------------------------------------------
# Malformed URL -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_bad_url_422(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-badurl")
    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "not-a-real-url"},
    )
    assert resp.status_code == 422, resp.text
    assert "absolute http" in resp.json()["detail"], resp.json()


@pytest.mark.asyncio
async def test_link_missing_url_422(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-nourl")
    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link"},
    )
    assert resp.status_code == 422, resp.text
    assert "non-empty url" in resp.json()["detail"], resp.json()


# ---------------------------------------------------------------------------
# 404 on missing project / missing resource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_link_missing_project_404(client) -> None:
    resp = await client.post(
        "/api/projects/999999999/resources",
        json={"kind": "link", "url": "https://example.com"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_get_missing_resource_404(client) -> None:
    resp = await client.get("/api/resources/999999999")
    assert resp.status_code == 404, resp.text
    resp = await client.get("/api/resources/999999999/preview")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# same-project task_id 422 (link path, no multipart)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_cross_project_task_id_422(client, scaffold_cleanup) -> None:
    pid_a = await _make_fresh_project(client, scaffold_cleanup, "res-lxa")
    pid_b = await _make_fresh_project(client, scaffold_cleanup, "res-lxb")

    t = await client.post(
        "/api/tasks", headers={"X-Project-Id": str(pid_b)},
        json={"project_id": pid_b, "title": "b task"},
    )
    tid_b = t.json()["id"]

    resp = await client.post(
        f"/api/projects/{pid_a}/resources",
        json={"kind": "link", "url": "https://example.com", "task_id": tid_b},
    )
    assert resp.status_code == 422, resp.text
    assert "different project" in resp.json()["detail"], resp.json()


@pytest.mark.asyncio
async def test_link_same_project_task_id_ok(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-lok")
    t = await client.post(
        "/api/tasks", headers={"X-Project-Id": str(pid)},
        json={"project_id": pid, "title": "task"},
    )
    tid = t.json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com", "task_id": tid},
    )
    assert resp.status_code == 201, resp.text
    # POSITIVE: same-project task pin succeeds and round-trips.
    assert resp.json()["task_id"] == tid


# ---------------------------------------------------------------------------
# Operator gate (403 when ACTIVE without token); POST + DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_gate_blocks_create_and_delete(
    client, scaffold_cleanup, monkeypatch
) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-gate")

    # Create a link with the gate INACTIVE so we have a row to try deleting.
    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com"},
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]

    # ACTIVATE the gate -> create + delete now require X-Operator-Token.
    monkeypatch.setenv("OPERATOR_ACTION_KEY", "test-secret-key")

    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com/x"},
    )
    assert resp.status_code == 403, resp.text
    assert "operator_proof_required" in resp.json()["detail"], resp.json()

    resp = await client.delete(f"/api/resources/{rid}")
    assert resp.status_code == 403, resp.text

    # POSITIVE: with the correct token both succeed.
    hdr = {"X-Operator-Token": "test-secret-key"}
    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com/y"}, headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.delete(f"/api/resources/{rid}", headers=hdr)
    assert resp.status_code == 204, resp.text


# ---------------------------------------------------------------------------
# Fix #3: soft-deleted resource -> GET detail + preview both 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_deleted_resource_get_returns_404(client, scaffold_cleanup) -> None:
    """#1309 fix #3: GET detail + preview must 404 for soft-deleted rows."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-del404")

    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com/to-delete"},
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]

    # Soft-delete the resource.
    resp = await client.delete(f"/api/resources/{rid}")
    assert resp.status_code == 204, resp.text

    # NEGATIVE (the lock): GET detail + preview both return 404 after deletion.
    resp = await client.get(f"/api/resources/{rid}")
    assert resp.status_code == 404, f"expected 404 on detail, got {resp.status_code}: {resp.text}"

    resp = await client.get(f"/api/resources/{rid}/preview")
    assert resp.status_code == 404, f"expected 404 on preview, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Fix #4: stored_path absent from all responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stored_path_not_in_response(client, scaffold_cleanup) -> None:
    """#1309 fix #4: stored_path must never appear in any wire response."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-sp")

    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com/doc"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    rid = body["id"]

    # NEGATIVE (the lock): stored_path never in POST 201 response.
    assert "stored_path" not in body.get("tags", {}), body["tags"]

    # NEGATIVE: stored_path never in GET detail response.
    resp = await client.get(f"/api/resources/{rid}")
    assert resp.status_code == 200
    assert "stored_path" not in resp.json().get("tags", {}), resp.json()["tags"]

    # NEGATIVE: stored_path never in list response.
    resp = await client.get(f"/api/projects/{pid}/resources")
    assert resp.status_code == 200
    for r in resp.json():
        assert "stored_path" not in r.get("tags", {}), r["tags"]


# ---------------------------------------------------------------------------
# Fix #9: missing/incorrect kind gives clear 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_kind_gives_clear_422(client, scaffold_cleanup) -> None:
    """#1309 fix #9: JSON body missing kind gives a clear 422 not 'got kind=None'."""
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-kind422")

    # No kind at all.
    resp = await client.post(
        f"/api/projects/{pid}/resources",
        json={"url": "https://example.com"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    # POSITIVE: message is informative about valid kinds.
    assert "kind=" in detail or "kind" in detail, detail
    # NEGATIVE (the lock): must NOT just say "got kind=None" with no context.
    assert "JSON body must include kind" in detail or "kind='link'" in detail, detail
