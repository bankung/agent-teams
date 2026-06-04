"""Kanban #1302 (X.1) — project_resources SCHEMA contract-smoke tests.

This slice was SCHEMA + ORM + Pydantic ONLY. The #1309 upload endpoint slice
WIDENED `tags` from a `list[str]` to a metadata OBJECT (dict) — the table is
brand-new with zero rows / no consumers, so the shape change is data-safe. The
`tags` assertions below were updated from list-shape to dict-shape accordingly
(#1309). These first-pass contract-smoke tests assert the DB-level and
schema-level contract DIRECTLY (via the `db_session` fixture + the Pydantic
models), NOT via HTTP.

Coverage (the ACs that land in this slice):
  - AC2 (Pydantic): ResourceCreate / ResourceRead happy round-trip + the
    per-kind required-field validator (422 path).
  - AC3 (DB CHECK): insert kind='file' with url-but-no-filename FAILS at the DB
    (IntegrityError on ck_project_resources_kind_fields). Paired POSITIVE: a
    valid 'file' row (with filename) and a valid 'link' row (with url) DO insert.
  - AC5 (FK cascade): deleting a project CASCADE-deletes its resources; deleting
    a task SET-NULLs the resource's task_id (the resource SURVIVES).

The rigorous suite (the upload endpoint's HTTP contract, tag-element negatives,
size/label boundary cases, soft-delete-aware listing, content_type round-trips)
is dev-tester's domain once #1309 ships the router.

Runs against `agent_teams_test` per conftest.py rewrite. All raw DML below
targets the test DB only (the pytest_runner role is SELECT-only on live
`agent_teams`; the `_live_db_row_count_invariant` guard asserts no live drift).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.models.project import Project
from src.models.project_resource import ProjectResource
from src.models.task import Task
from src.schemas.project_resource import ResourceCreate, ResourceRead


# ---------------------------------------------------------------------------
# Helpers — create throwaway project / task rows in the TEST DB via db_session.
# ---------------------------------------------------------------------------


async def _make_project(db_session) -> int:
    proj = Project(
        name=f"k1302-{uuid.uuid4().hex[:8]}",
        description="k1302 schema fixture",
        paths_web="/tmp/x/web",
        paths_api="/tmp/x/api",
        paths_db="/tmp/x/db",
        team="dev",
    )
    db_session.add(proj)
    await db_session.commit()
    await db_session.refresh(proj)
    return proj.id


async def _make_task(db_session, project_id: int) -> int:
    task = Task(project_id=project_id, title="k1302 fixture task")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task.id


# ---------------------------------------------------------------------------
# AC2 — Pydantic schema happy round-trip + per-kind validator
# ---------------------------------------------------------------------------


def test_resource_create_link_happy() -> None:
    """ResourceCreate accepts a valid 'link' row and round-trips its fields.

    #1309: `tags` is now a metadata OBJECT (dict), not a list of strings.
    """
    rc = ResourceCreate(
        project_id=1,
        kind="link",
        url="https://example.com/spec.pdf",
        label="Spec",
        tags={"url_scheme": "https", "title": "Spec"},
    )
    # POSITIVE: fields survive validation verbatim.
    assert rc.kind == "link"
    assert rc.url == "https://example.com/spec.pdf"
    assert rc.tags == {"url_scheme": "https", "title": "Spec"}
    # filename stays None for a link.
    assert rc.filename is None


def test_resource_create_file_happy() -> None:
    """ResourceCreate accepts a valid 'file' row (filename present)."""
    rc = ResourceCreate(
        project_id=1,
        kind="file",
        filename="diagram.png",
        content_type="image/png",
        size_bytes=2048,
    )
    assert rc.kind == "file"
    assert rc.filename == "diagram.png"
    # #1309: tags defaults to {} (the verify-and-tag metadata container).
    assert rc.tags == {}


def test_resource_create_file_without_filename_422() -> None:
    """AC2 (validator): kind='file' with url-but-no-filename is rejected by the
    Pydantic boundary BEFORE it can reach the DB (the friendlier 422 path)."""
    with pytest.raises(ValidationError) as exc:
        ResourceCreate(
            project_id=1,
            kind="file",
            url="https://example.com/x",  # url present, filename absent
        )
    assert "requires a non-empty filename" in str(exc.value)


def test_resource_create_link_without_url_422() -> None:
    """Symmetric: kind='link' with no url is rejected by the validator."""
    with pytest.raises(ValidationError) as exc:
        ResourceCreate(project_id=1, kind="link", filename="x")
    assert "requires a non-empty url" in str(exc.value)


def test_resource_create_bad_kind_422() -> None:
    """The Literal gates the discriminator value — 'folder' is rejected."""
    with pytest.raises(ValidationError):
        ResourceCreate(project_id=1, kind="folder", url="https://x")


def test_resource_read_from_orm_attributes() -> None:
    """ResourceRead serializes from ORM-style attributes (from_attributes)."""

    class _Row:
        id = 7
        project_id = 1
        task_id = None
        kind = "link"
        filename = None
        url = "https://example.com"
        content_type = None
        size_bytes = None
        label = "Doc"
        tags = {"url_scheme": "https", "head_status": 200}
        from datetime import datetime, timezone

        created_at = datetime(2026, 6, 4, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 4, tzinfo=timezone.utc)

    out = ResourceRead.model_validate(_Row())
    assert out.id == 7
    assert out.kind == "link"
    # #1309: tags is the metadata OBJECT (dict).
    assert out.tags == {"url_scheme": "https", "head_status": 200}


# ---------------------------------------------------------------------------
# AC3 — DB CHECK: kind='file' with url-but-no-filename FAILS at the DB level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_check_file_without_filename_rejected(db_session) -> None:
    """AC3: insert kind='file' carrying a url but NO filename → IntegrityError
    on ck_project_resources_kind_fields (DB-level, independent of Pydantic)."""
    pid = await _make_project(db_session)

    bad = ProjectResource(
        project_id=pid,
        kind="file",
        url="https://example.com/x",  # url present
        filename=None,  # filename ABSENT — violates the CHECK
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError) as exc:
        await db_session.commit()
    # The named CHECK is the one that fired (the lock — not some other error).
    assert "ck_project_resources_kind_fields" in str(exc.value)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_db_check_valid_file_and_link_insert(db_session) -> None:
    """POSITIVE pair for AC3: a valid 'file' row (filename present) and a valid
    'link' row (url present) BOTH insert successfully — proving the CHECK is not
    vacuously rejecting everything."""
    pid = await _make_project(db_session)

    good_file = ProjectResource(
        project_id=pid, kind="file", filename="diagram.png", size_bytes=10
    )
    good_link = ProjectResource(
        project_id=pid, kind="link", url="https://example.com/doc"
    )
    db_session.add_all([good_file, good_link])
    await db_session.commit()
    await db_session.refresh(good_file)
    await db_session.refresh(good_link)

    # POSITIVE: both rows persisted with ids + the JSONB tags default landed {}.
    # #1309: the ORM Python-side default is now `dict` (was `list`); an
    # INSERT-without-explicit-tags row reads back {}.
    assert good_file.id is not None
    assert good_link.id is not None
    assert good_file.tags == {}
    assert good_link.tags == {}
    assert good_file.status == 1  # RecordStatus.ACTIVE default

    # Cleanup throwaway project (cascade removes both resources in the test DB).
    await db_session.execute(
        text("DELETE FROM projects WHERE id = :pid"), {"pid": pid}
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_db_check_bad_kind_rejected(db_session) -> None:
    """An out-of-enum discriminator ('folder') is rejected at the DB level.

    Note: a non-enum kind also fails ck_project_resources_kind_fields (neither
    the 'file' nor the 'link' branch can be satisfied), so PostgreSQL may report
    EITHER named CHECK as the first violation. The lock is that the row is
    rejected by a CHECK constraint — not which one fires first.
    """
    pid = await _make_project(db_session)
    bad = ProjectResource(project_id=pid, kind="folder", url="https://x")
    db_session.add(bad)
    with pytest.raises(IntegrityError) as exc:
        await db_session.commit()
    msg = str(exc.value)
    assert "CheckViolation" in msg, msg
    # One of the two project_resources CHECKs must be the culprit (proves the
    # rejection is OUR constraint, not an unrelated error).
    assert (
        "ck_project_resources_kind_valid" in msg
        or "ck_project_resources_kind_fields" in msg
    ), msg
    await db_session.rollback()


# ---------------------------------------------------------------------------
# AC5 — FK cascade (project) + SET NULL (task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fk_project_cascade_deletes_resource(db_session) -> None:
    """AC5: deleting the parent project CASCADE-deletes its resources."""
    pid = await _make_project(db_session)
    res = ProjectResource(project_id=pid, kind="link", url="https://example.com")
    db_session.add(res)
    await db_session.commit()
    await db_session.refresh(res)
    res_id = res.id
    # POSITIVE: the resource exists before the project delete.
    assert res_id is not None

    # Hard-delete the project (test DB only) → ON DELETE CASCADE.
    await db_session.execute(
        text("DELETE FROM projects WHERE id = :pid"), {"pid": pid}
    )
    await db_session.commit()
    # Expire the identity map so the re-SELECT reads post-cascade DB state.
    db_session.expire_all()

    # NEGATIVE (the lock): the resource row is GONE (cascade).
    remaining = (
        await db_session.execute(
            select(ProjectResource).where(ProjectResource.id == res_id)
        )
    ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_fk_task_set_null_resource_survives(db_session) -> None:
    """AC5: deleting a pinned task SET-NULLs the resource's task_id — the
    resource SURVIVES (it just detaches)."""
    pid = await _make_project(db_session)
    tid = await _make_task(db_session, pid)

    res = ProjectResource(
        project_id=pid, task_id=tid, kind="link", url="https://example.com"
    )
    db_session.add(res)
    await db_session.commit()
    await db_session.refresh(res)
    res_id = res.id
    # POSITIVE: the resource is pinned to the task before the delete.
    assert res.task_id == tid

    # Hard-delete the task (test DB only) → ON DELETE SET NULL.
    await db_session.execute(
        text("DELETE FROM tasks WHERE id = :tid"), {"tid": tid}
    )
    await db_session.commit()
    # The raw DELETE bypassed the ORM, so the session's identity map still holds
    # the stale resource with task_id set. Expire it so the next SELECT reads the
    # post-cascade DB state rather than the cached instance.
    db_session.expire_all()

    # NEGATIVE (the lock): the resource STILL EXISTS but task_id is now NULL.
    survivor = (
        await db_session.execute(
            select(ProjectResource).where(ProjectResource.id == res_id)
        )
    ).scalar_one_or_none()
    assert survivor is not None, "resource must survive task deletion (SET NULL)"
    assert survivor.task_id is None

    # Cleanup the throwaway project (cascade removes the survivor).
    await db_session.execute(
        text("DELETE FROM projects WHERE id = :pid"), {"pid": pid}
    )
    await db_session.commit()
