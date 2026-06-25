"""Kanban #2700 — GET /api/audit/flags cross-project audit-flag endpoint.

Locks three JSONB-filter cases for `question_payload->>'is_audit_flag'`:
  1. present + value 'true'  → included
  2. present + value 'false' → excluded
  3. key missing (NULL)      → excluded

Setup mirrors test_audit_rollup.py: ORM-side inserts via SessionLocal so
the no-raw-SQL-DML rule is respected. Uses the `empty_db` fixture imported
from that module to avoid duplicating the purge/teardown logic.
"""

from __future__ import annotations

import uuid

import pytest

from src.constants import RecordStatus, TaskStatus
from src.db import SessionLocal
from src.models.project import Project
from src.models.task import Task

# Re-use the empty_db fixture (purge + reseed) from the sibling module.
from tests.test_audit_rollup import empty_db  # noqa: F401


def _project_name(slug: str) -> str:
    return f"k2700-{slug}-{uuid.uuid4().hex[:6]}"


async def _make_project(name: str | None = None) -> Project:
    async with SessionLocal() as session:
        project = Project(
            name=name or _project_name("p"),
            description="audit flags test fixture",
            paths_web="/tmp/x/web",
            paths_api="/tmp/x/api",
            paths_db="/tmp/x/db",
            stack_web="nextjs",
            stack_api="fastapi",
            stack_db="postgres",
            config={},
            is_active=False,
            team="dev",
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def _make_flag_task(
    project_id: int,
    *,
    question_payload: dict | None,
    process_status: int = TaskStatus.BLOCKED,
    title: str = "audit flag fixture",
) -> Task:
    """Insert a question-kind task with the given question_payload."""
    async with SessionLocal() as session:
        task = Task(
            project_id=project_id,
            title=title,
            process_status=process_status,
            interaction_kind="question",
            question_payload=question_payload,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


# ---- JSONB filter: present-true included, present-false + missing excluded ---


@pytest.mark.asyncio
async def test_jsonb_filter_present_true_included(client, empty_db) -> None:  # noqa: F811
    """is_audit_flag=true row MUST appear; false and missing rows MUST NOT."""
    proj = await _make_project()

    # Case 1: key present, value true → should be included.
    task_true = await _make_flag_task(
        proj.id, question_payload={"is_audit_flag": True, "prompt": "flag me"}
    )
    # Case 2: key present, value false → excluded.
    await _make_flag_task(
        proj.id, question_payload={"is_audit_flag": False, "prompt": "not a flag"}
    )
    # Case 3: key missing entirely → excluded (NULL != 'true').
    await _make_flag_task(proj.id, question_payload={"prompt": "no flag key"})

    resp = await client.get("/api/audit/flags")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body) == 1, body
    item = body[0]
    assert item["flag"]["id"] == task_true.id, item
    assert item["project"]["id"] == proj.id, item


@pytest.mark.asyncio
async def test_empty_db_returns_empty_list(client, empty_db) -> None:  # noqa: F811
    """No tasks → endpoint returns [] with status 200."""
    resp = await client.get("/api/audit/flags")
    assert resp.status_code == 200, resp.text
    assert resp.json() == [], resp.text


@pytest.mark.asyncio
async def test_auto_archived_excluded(client, empty_db) -> None:  # noqa: F811
    """Kanban #1240 parity: is_audit_flag=true task with is_active=False is excluded."""
    proj = await _make_project()

    # Auto-archived row (is_active=False) — must be excluded even though flag is set.
    async with SessionLocal() as session:
        task = Task(
            project_id=proj.id,
            title="archived flag fixture",
            process_status=TaskStatus.BLOCKED,
            interaction_kind="question",
            question_payload={"is_audit_flag": True},
            is_active=False,
        )
        session.add(task)
        await session.commit()

    resp = await client.get("/api/audit/flags")
    assert resp.status_code == 200, resp.text
    assert resp.json() == [], resp.text


@pytest.mark.asyncio
async def test_done_and_cancelled_excluded(client, empty_db) -> None:  # noqa: F811
    """Pending parity: DONE (ps=5) and CANCELLED (ps=6) rows are excluded."""
    proj = await _make_project()

    # DONE — should be excluded (operator resolved the flag).
    await _make_flag_task(
        proj.id,
        question_payload={"is_audit_flag": True},
        process_status=TaskStatus.DONE,
    )
    # CANCELLED — excluded by include_cancelled=false default.
    await _make_flag_task(
        proj.id,
        question_payload={"is_audit_flag": True},
        process_status=TaskStatus.CANCELLED,
    )
    # BLOCKED — should be included (open flag).
    task_blocked = await _make_flag_task(
        proj.id,
        question_payload={"is_audit_flag": True},
        process_status=TaskStatus.BLOCKED,
    )

    resp = await client.get("/api/audit/flags")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body) == 1, body
    assert body[0]["flag"]["id"] == task_blocked.id, body
