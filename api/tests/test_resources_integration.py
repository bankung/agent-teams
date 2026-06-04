"""Kanban #1309 — resources API HTTP integration smoke tests.

These exercise the FULL wire contract: multipart file upload, the 520 MB cap
(413 via a monkeypatched small cap — NO giant file), link kind, list filters,
the preview endpoint, delete-to-trash, same-project task_id 422, and the
operator-gate 403.

DEFERRED-UNTIL-REBUILD: multipart upload needs `python-multipart`, added to
pyproject.toml by #1309 but NOT yet in the running container. This whole module
is SKIPPED (collected, reported as skipped) until the dep is importable, then
runs fully after Lead/devops rebuilds the api image. The link / 403 / 422 cases
that do NOT use multipart are isolated in test_resources_link_smoke.py so they
run NOW. See the #1309 report.
"""

from __future__ import annotations

import importlib.util
import uuid

import pytest

# Skip the whole module until python-multipart is installed (post-rebuild).
_HAS_MULTIPART = importlib.util.find_spec("multipart") is not None
pytestmark = pytest.mark.skipif(
    not _HAS_MULTIPART,
    reason="python-multipart not installed yet (Kanban #1309 needs container rebuild)",
)


async def _make_fresh_project(client, scaffold_cleanup, slug: str) -> int:
    name = scaffold_cleanup(f"{slug}-{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        "/api/projects",
        json={
            "name": name,
            "description": f"resources smoke for {name}",
            "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
            "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
            "config": {},
            "is_active": False,
            "team": "dev",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _csv_1000x8() -> bytes:
    header = ",".join(f"col{i}" for i in range(8))
    rows = [header]
    for r in range(1000):
        rows.append(",".join(f"v{r}_{c}" for c in range(8)))
    return ("\n".join(rows) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# AC#7 Tier-1 smoke: upload sample_sales.csv (1000 rows, 8 cols) -> tags -> delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_csv_tags_then_delete(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-csv")

    data = _csv_1000x8()
    resp = await client.post(
        f"/api/projects/{pid}/resources",
        files={"file": ("sample_sales.csv", data, "text/csv")},
        data={"kind": "file"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    rid = body["id"]
    assert body["kind"] == "file"
    assert body["filename"] == "sample_sales.csv"
    assert body["size_bytes"] == len(data)
    # POSITIVE: verify-and-tag landed the right metadata.
    assert body["tags"]["row_count"] == 1000, body["tags"]
    assert body["tags"]["col_count"] == 8, body["tags"]
    assert len(body["tags"]["preview"]) == 10, body["tags"]
    assert body["tags"]["format_detected"] == "csv"
    assert "hash" in body["tags"]
    assert body["tags"]["est_cost_if_full"]["usd"] is not None

    # preview endpoint reads from tags (no full re-read).
    resp = await client.get(f"/api/resources/{rid}/preview")
    assert resp.status_code == 200, resp.text
    prev = resp.json()
    assert prev["row_count"] == 1000
    assert prev["col_count"] == 8
    assert len(prev["preview"]) == 10

    # DELETE soft-deletes + moves the file to trash.
    resp = await client.delete(f"/api/resources/{rid}")
    assert resp.status_code == 204, resp.text

    # NEGATIVE (the lock): the deleted resource no longer appears in the list.
    resp = await client.get(f"/api/projects/{pid}/resources")
    assert resp.status_code == 200, resp.text
    assert not any(r["id"] == rid for r in resp.json()), resp.json()

    # Idempotent re-delete still 204.
    resp = await client.delete(f"/api/resources/{rid}")
    assert resp.status_code == 204, resp.text


# ---------------------------------------------------------------------------
# 520 MB cap -> 413 (monkeypatched small cap; no row created)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_over_cap_413_no_row(client, scaffold_cleanup, monkeypatch) -> None:
    from src.services import resource_storage as rs

    monkeypatch.setattr(rs, "MAX_UPLOAD_BYTES", 100)  # 100-byte cap

    pid = await _make_fresh_project(client, scaffold_cleanup, "res-413")
    big = b"x" * 500  # > 100

    resp = await client.post(
        f"/api/projects/{pid}/resources",
        files={"file": ("big.csv", big, "text/csv")},
        data={"kind": "file"},
    )
    assert resp.status_code == 413, resp.text

    # NEGATIVE (the lock): no row was created.
    resp = await client.get(f"/api/projects/{pid}/resources")
    assert resp.status_code == 200
    assert resp.json() == [], resp.json()


# ---------------------------------------------------------------------------
# list filters (?kind, ?task_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters(client, scaffold_cleanup) -> None:
    pid = await _make_fresh_project(client, scaffold_cleanup, "res-filter")
    headers = {"X-Project-Id": str(pid)}

    # a task to pin one resource to.
    t = await client.post(
        "/api/tasks", headers=headers, json={"project_id": pid, "title": "t"}
    )
    tid = t.json()["id"]

    # file resource pinned to the task.
    await client.post(
        f"/api/projects/{pid}/resources",
        files={"file": ("a.csv", b"x,y\n1,2\n", "text/csv")},
        data={"kind": "file", "task_id": str(tid)},
    )
    # link resource, unpinned.
    await client.post(
        f"/api/projects/{pid}/resources",
        json={"kind": "link", "url": "https://example.com/doc"},
    )

    # ?kind=link returns only the link.
    resp = await client.get(f"/api/projects/{pid}/resources?kind=link")
    assert resp.status_code == 200, resp.text
    kinds = {r["kind"] for r in resp.json()}
    assert kinds == {"link"}, kinds

    # ?task_id returns only the pinned file.
    resp = await client.get(f"/api/projects/{pid}/resources?task_id={tid}")
    assert resp.status_code == 200, resp.text
    assert all(r["task_id"] == tid for r in resp.json())
    assert {r["kind"] for r in resp.json()} == {"file"}


# ---------------------------------------------------------------------------
# same-project task_id rejection (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_project_task_id_422(client, scaffold_cleanup) -> None:
    pid_a = await _make_fresh_project(client, scaffold_cleanup, "res-xa")
    pid_b = await _make_fresh_project(client, scaffold_cleanup, "res-xb")

    # task in project B.
    t = await client.post(
        "/api/tasks", headers={"X-Project-Id": str(pid_b)},
        json={"project_id": pid_b, "title": "b task"},
    )
    tid_b = t.json()["id"]

    # upload to project A referencing B's task -> 422.
    resp = await client.post(
        f"/api/projects/{pid_a}/resources",
        files={"file": ("a.csv", b"x\n1\n", "text/csv")},
        data={"kind": "file", "task_id": str(tid_b)},
    )
    assert resp.status_code == 422, resp.text
    assert "different project" in resp.json()["detail"], resp.json()
