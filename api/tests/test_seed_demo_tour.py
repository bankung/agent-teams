"""Contract-smoke tests for the demo-tour seed block (Kanban #1361).

Covers:
  - demo-tour project + 3 tasks created on a fresh DB
  - idempotency: second seed run does not duplicate
  - required field shapes (title prefix, task_type, task_kind, status, AC count)
  - project.team == 'general'

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
from src.constants import TaskStatus
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
