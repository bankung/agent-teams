"""Idempotent seed script.

Usage:
    python -m scripts.seed

Inserts the dogfood `agent-teams` project + 3 sample tasks if the project does
not already exist. Does NOT scaffold the on-disk folder — agent-teams already
has hand-written shared docs we must not overwrite.

Exits 0 on success or "already seeded"; non-zero on any DB / connection error.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from src.constants import TaskPriority, TaskRole, TaskStatus
from src.db import SessionLocal, engine
from src.models.project import Project
from src.models.task import Task
from src.settings import get_settings


PROJECT_NAME = "agent-teams"
PROJECT_DESCRIPTION = "Self-hosted Kanban for managing dev team tasks (dogfood)"


def _project_kwargs() -> dict:
    settings = get_settings()
    repo = settings.repo_root
    return {
        "name": PROJECT_NAME,
        "description": PROJECT_DESCRIPTION,
        "paths_web": str(repo / "web"),
        "paths_api": str(repo / "api"),
        "paths_db": str(repo / "api" / "alembic" / "versions"),
        "stack_web": "Next.js 14 + TypeScript + Tailwind",
        "stack_api": "FastAPI + Pydantic + SQLAlchemy + Alembic",
        "stack_db": "PostgreSQL 16",
        "config": {
            "standards": {
                "web": ["nextjs", "react", "typescript", "tailwind"],
                "api": ["fastapi", "python", "pydantic", "sqlalchemy"],
                "db": ["postgresql"],
            },
        },
        "is_active": True,
    }


def _sample_tasks(project_id: int) -> list[Task]:
    return [
        Task(
            project_id=project_id,
            title="Phase 2b.1 — api scaffold (foundation)",
            description="Skeleton: settings, db, base/Project model, alembic init.",
            process_status=TaskStatus.DONE,
            priority=TaskPriority.HIGH,
            assigned_role=TaskRole.BACKEND,
        ),
        Task(
            project_id=project_id,
            title="Phase 2b.2 — api endpoints + seed",
            description="Task model, migration, schemas, routers, seed, smoke test.",
            process_status=TaskStatus.IN_PROGRESS,
            priority=TaskPriority.HIGH,
            assigned_role=TaskRole.BACKEND,
        ),
        Task(
            project_id=project_id,
            title="Phase 3 — kanban UI scaffold",
            description="Next.js 14 app, kanban board, project switcher.",
            process_status=TaskStatus.TODO,
            priority=TaskPriority.NORMAL,
            assigned_role=TaskRole.FRONTEND,
        ),
    ]


async def _seed() -> int:
    async with SessionLocal() as session:
        existing = await session.execute(
            select(Project).where(Project.name == PROJECT_NAME)
        )
        if existing.scalar_one_or_none() is not None:
            print(f"[seed] project {PROJECT_NAME!r} already exists — already seeded.")
            return 0

        project = Project(**_project_kwargs())
        session.add(project)
        await session.flush()  # populate project.id without committing

        for task in _sample_tasks(project.id):
            session.add(task)

        await session.commit()
        await session.refresh(project)
        print(
            f"[seed] inserted project id={project.id} name={project.name!r} + 3 sample tasks."
        )
        return 0


async def _main() -> int:
    try:
        return await _seed()
    finally:
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
