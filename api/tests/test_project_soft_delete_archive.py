"""Kanban #1124 (2026-05-17, L19 prevention) — DELETE /api/projects/{id}
archives the on-disk scaffolded folder to `.deleted/<name>-<ts>/` instead of
leaving it to accumulate.

Companion to test_scaffold_rate_limit.py (rate limit side) — this side
covers the disk-side cleanup so a sustained churn of create/soft-delete
doesn't fill `context/projects/` with orphan dirs.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _project_create_payload(name: str) -> dict:
    return {
        "name": name,
        "description": f"archive-test fixture for {name}",
        "paths": {"web": "/tmp/x/web", "api": "/tmp/x/api", "db": "/tmp/x/db"},
        "stack": {"web": "nextjs", "api": "fastapi", "db": "postgres"},
        "config": {},
        "is_active": False,
        "team": "dev",
    }


def _repo_root() -> Path:
    from src.settings import get_settings

    return Path(get_settings().repo_root)


@pytest.fixture
def archive_cleanup():
    """Track names we expect to land under `.deleted/<name>-<ts>/` and remove
    those archive dirs after the test.

    Without this, the `.deleted/` dir accumulates archive folders per test
    run — visible churn in the working tree.
    """
    names: list[str] = []

    def register(name: str) -> str:
        names.append(name)
        return name

    yield register

    deleted_root = _repo_root() / "context" / "projects" / ".deleted"
    if not deleted_root.exists():
        return
    for name in names:
        for archived in deleted_root.glob(f"{name}-*"):
            shutil.rmtree(archived, ignore_errors=True)


@pytest.mark.asyncio
async def test_soft_delete_moves_folder_to_deleted_archive(
    client, scaffold_cleanup, archive_cleanup
) -> None:
    """POST /api/projects → folder exists. DELETE /api/projects/{id} →
    folder is GONE from `context/projects/<name>/` AND PRESENT under
    `context/projects/.deleted/<name>-<timestamp>/`.

    The archive directory format is `<name>-YYYYMMDDTHHMMSSZ` (utc).
    """
    name = _unique_name("archive-test")
    scaffold_cleanup(name)  # safety net if delete-side cleanup fails
    archive_cleanup(name)

    create_resp = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    assert create_resp.status_code == 201, create_resp.text
    pid = create_resp.json()["id"]

    repo_root = _repo_root()
    src = repo_root / "context" / "projects" / name
    assert src.exists(), f"scaffold did not create {src}"

    delete_resp = await client.delete(f"/api/projects/{pid}")
    assert delete_resp.status_code == 204, delete_resp.text

    # Original folder is gone
    assert not src.exists(), (
        f"DELETE should have moved the folder away from {src}, but it is still there"
    )

    # Archive folder exists under .deleted/ with the timestamp suffix
    deleted_root = repo_root / "context" / "projects" / ".deleted"
    assert deleted_root.exists(), f"archive root {deleted_root} should exist"
    archived = list(deleted_root.glob(f"{name}-*"))
    assert len(archived) == 1, (
        f"expected exactly 1 archived dir under {deleted_root} for {name}, got {archived}"
    )

    # Archive directory still carries the scaffolded structure (sanity-check
    # — we MOVED the dir, didn't delete it).
    archive_dir = archived[0]
    assert (archive_dir / "shared").is_dir(), (
        f"archived dir should preserve shared/: {archive_dir}"
    )


@pytest.mark.asyncio
async def test_soft_delete_when_folder_already_missing_returns_204(
    client, scaffold_cleanup, archive_cleanup
) -> None:
    """If the scaffolded folder was manually removed (or never created),
    DELETE still returns 204 — the archive side is best-effort and must not
    fail the request. The DB row is the source of truth for the soft-delete.
    """
    name = _unique_name("archive-missing")
    scaffold_cleanup(name)
    archive_cleanup(name)

    create_resp = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    assert create_resp.status_code == 201, create_resp.text
    pid = create_resp.json()["id"]

    # Pre-emptively yank the folder so the archive branch finds nothing
    src = _repo_root() / "context" / "projects" / name
    if src.exists():
        shutil.rmtree(src, ignore_errors=True)

    delete_resp = await client.delete(f"/api/projects/{pid}")
    assert delete_resp.status_code == 204, delete_resp.text

    # No archive should have been created (src didn't exist) — sanity check.
    deleted_root = _repo_root() / "context" / "projects" / ".deleted"
    if deleted_root.exists():
        archived = list(deleted_root.glob(f"{name}-*"))
        assert archived == [], f"unexpected archive(s) for missing src: {archived}"


@pytest.mark.asyncio
async def test_idempotent_soft_delete_does_not_double_archive(
    client, scaffold_cleanup, archive_cleanup
) -> None:
    """First DELETE archives the folder. Second DELETE on an already-deleted
    project is a no-op (early return path); it MUST NOT attempt a second
    archive (the src is already gone) and MUST NOT 500.
    """
    name = _unique_name("archive-idem")
    scaffold_cleanup(name)
    archive_cleanup(name)

    create_resp = await client.post(
        "/api/projects", json=_project_create_payload(name)
    )
    assert create_resp.status_code == 201, create_resp.text
    pid = create_resp.json()["id"]

    # First delete — archives the folder
    r1 = await client.delete(f"/api/projects/{pid}")
    assert r1.status_code == 204, r1.text

    deleted_root = _repo_root() / "context" / "projects" / ".deleted"
    assert len(list(deleted_root.glob(f"{name}-*"))) == 1

    # Second delete — early-return, no further archive
    r2 = await client.delete(f"/api/projects/{pid}")
    assert r2.status_code == 204, r2.text

    # Still exactly one archive (idempotent)
    assert len(list(deleted_root.glob(f"{name}-*"))) == 1
