"""Contract-smoke tests for the demo-tour seed block (Kanban #1361).

Covers:
  - demo-tour project + 3 tasks created on a fresh DB
  - idempotency: second seed run does not duplicate
  - required field shapes (title prefix, task_type, task_kind, status, AC count)
  - project.team == 'general'
  - #1629 regression: soft-deleted demo-tour does NOT block re-seed from
    creating a fresh ACTIVE row

Test isolation: these tests run against `agent_teams_test` (via the conftest
session fixture that drops/creates/migrates the test DB before the session
starts). The _setup_test_database fixture runs seed() once; tests below call
_seed() again where they need to verify idempotency. The existing db_session
fixture (conftest.py) provides a direct AsyncSession into the test DB without
touching the live agent_teams DB.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from scripts.seed import DEMO_PROJECT_NAME, _demo_tasks, _seed
from src.constants import RecordStatus, TaskStatus
from src.models.project import Project
from src.models.task import Task


@pytest.mark.asyncio
async def test_seed_creates_demo_tour_project_when_absent(db_session) -> None:
    """The _setup_test_database fixture already ran _seed() once before this
    test. Verify demo-tour project exists with 3 tasks — confirming the seed
    block ran successfully on a fresh (migrated) DB.
    """
    project = (
        await db_session.execute(
            select(Project).where(Project.name == DEMO_PROJECT_NAME)
        )
    ).scalar_one_or_none()

    assert project is not None, "demo-tour project must exist after seed"
    assert project.status == 1, "demo-tour project must be active (status=1)"

    task_count = (
        await db_session.execute(
            select(func.count()).where(Task.project_id == project.id)
        )
    ).scalar_one()

    assert task_count == 3, (
        f"Expected 3 demo tasks under demo-tour, got {task_count}"
    )


@pytest.mark.asyncio
async def test_seed_skips_demo_tour_when_present(db_session) -> None:
    """Running _seed() a second time must not duplicate the demo-tour tasks."""
    # Run seed again — demo-tour already exists from _setup_test_database.
    await _seed()

    project = (
        await db_session.execute(
            select(Project).where(Project.name == DEMO_PROJECT_NAME)
        )
    ).scalar_one_or_none()

    assert project is not None

    task_count = (
        await db_session.execute(
            select(func.count()).where(Task.project_id == project.id)
        )
    ).scalar_one()

    assert task_count == 3, (
        f"Re-running seed must not duplicate demo tasks; found {task_count} (expected 3)"
    )


@pytest.mark.asyncio
async def test_seed_demo_tour_tasks_have_required_fields(db_session) -> None:
    """All 3 demo tasks must have: title starting '[DEMO]', task_type='feature',
    task_kind='ai', process_status=TODO, acceptance_criteria with 3 pending items.
    """
    project = (
        await db_session.execute(
            select(Project).where(Project.name == DEMO_PROJECT_NAME)
        )
    ).scalar_one()

    tasks = (
        await db_session.execute(
            select(Task)
            .where(Task.project_id == project.id)
            .order_by(Task.id)
        )
    ).scalars().all()

    assert len(tasks) == 3

    for task in tasks:
        assert task.title.startswith("[DEMO]"), (
            f"Demo task title must start with '[DEMO]'; got {task.title!r}"
        )
        assert task.task_type == "feature", (
            f"Demo task must have task_type='feature'; got {task.task_type!r}"
        )
        assert task.task_kind == "ai", (
            f"Demo task must have task_kind='ai'; got {task.task_kind!r}"
        )
        assert task.process_status == TaskStatus.TODO, (
            f"Demo task must have process_status=TODO(1); got {task.process_status!r}"
        )
        assert isinstance(task.acceptance_criteria, list), (
            f"acceptance_criteria must be a list; got {type(task.acceptance_criteria)}"
        )
        assert len(task.acceptance_criteria) == 3, (
            f"Each demo task must have 3 AC items; got {len(task.acceptance_criteria)}"
        )
        for ac in task.acceptance_criteria:
            assert ac.get("status") == "pending", (
                f"All AC items must be 'pending' at seed time; got {ac!r}"
            )


@pytest.mark.asyncio
async def test_seed_demo_tour_team_is_general(db_session) -> None:
    """demo-tour project must carry team='general'."""
    project = (
        await db_session.execute(
            select(Project).where(Project.name == DEMO_PROJECT_NAME)
        )
    ).scalar_one()

    assert project.team == "general", (
        f"demo-tour project must have team='general'; got {project.team!r}"
    )


@pytest.mark.asyncio
async def test_seed_creates_fresh_demo_tour_after_soft_delete(
    client, db_session
) -> None:
    """Regression for #1629: a soft-deleted demo-tour row must NOT block re-seed.

    Pre-fix behaviour: the existence check had no status filter, so a
    soft-deleted row (status=0) matched and seed printed "already exists —
    skipping", leaving GET /api/projects/by-name/demo-tour returning 404.

    Soft-delete method: flip `status` directly on the ORM row via db_session
    (avoids DELETE endpoint scaffold-folder side effects in the test environment
    while still exercising the same DB state the bug required).

    Steps:
      1. Confirm demo-tour is ACTIVE and by-name returns 200; capture id.
      2. Soft-delete via db_session (status=DELETED).
      3. Confirm by-name returns 404.
      4. Re-run _seed().
      5. Assert by-name returns 200 with a DIFFERENT id (fresh ACTIVE row).
    """
    # 1. Confirm demo-tour is active after the conftest seed.
    resp = await client.get("/api/projects/by-name/demo-tour")
    assert resp.status_code == 200, (
        f"Expected 200 for demo-tour before soft-delete; got {resp.status_code}"
    )
    original_id = resp.json()["id"]

    # 2. Soft-delete the demo-tour row directly via db_session.
    project = (
        await db_session.execute(
            select(Project).where(
                Project.name == DEMO_PROJECT_NAME,
                Project.status == RecordStatus.ACTIVE,
            )
        )
    ).scalar_one()
    project.status = RecordStatus.DELETED
    project.is_active = False
    await db_session.commit()

    # 3. by-name must now 404 (soft-deleted row is invisible).
    resp = await client.get("/api/projects/by-name/demo-tour")
    assert resp.status_code == 404, (
        f"Expected 404 after soft-delete; got {resp.status_code}"
    )

    # 4. Re-run seed — the ACTIVE-scoped check must not match the deleted row.
    await _seed()

    # 5. by-name must return 200 with a brand-new id (the fix created a fresh row).
    resp = await client.get("/api/projects/by-name/demo-tour")
    assert resp.status_code == 200, (
        f"Expected 200 after re-seed (post-fix); got {resp.status_code}. "
        "Pre-fix: seed skipped because soft-deleted row matched the no-status-filter check."
    )
    new_id = resp.json()["id"]
    assert new_id != original_id, (
        f"Re-seeded demo-tour must have a NEW id; both are {original_id}. "
        "This indicates the old row was reactivated rather than a fresh row inserted."
    )
