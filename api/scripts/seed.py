"""Idempotent seed script.

Usage:
    python -m scripts.seed

Inserts the dogfood `agent-teams` project + 3 sample tasks if the project does
not already exist. Does NOT scaffold the on-disk folder — agent-teams already
has hand-written shared docs we must not overwrite.

Exits 0 on success or "already seeded"; non-zero on any DB / connection error.

NOTE on imports (2026-05-17 incident L3 fix): `from src.db import ...` is
INTENTIONALLY deferred into the `_seed()` / `_main()` function bodies. Putting
it at module top would resolve `get_settings()` (and the module-level engine
build inside src.db) at import time — and `scripts.seed` is itself imported
from inside pytest's conftest setup fixture, which means the engine could
bind to whatever DATABASE_URL was visible BEFORE conftest's in-process rewrite.
The defensive `endswith("_test")` gate at the top of `_seed()` is the
belt-and-suspenders that catches the bug class even if the lazy import races.
See `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md`.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from src.constants import TaskPriority, TaskRole, TaskStatus
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
    # Resolve engine + SessionLocal at call time, not module-import time.
    # Module-level import would cache the URL via get_settings()'s @lru_cache
    # before conftest's in-process DATABASE_URL rewrite runs — see 2026-05-17
    # incident postmortem at
    # context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
    from src.db import SessionLocal, engine

    # Defensive gate — refuse to seed a non-test DB unless explicitly
    # acknowledged. Catches the lru_cache-poisoning class of bug even if
    # everything upstream (conftest rewrite, harness hook, etc.) misbehaves.
    # endswith("_test") rejects 'agent_teams' but accepts 'agent_teams_test'
    # (does NOT accept e.g. 'agent_teams_test_subname' — only true _test suffix).
    url_str = str(engine.url)
    db_name = engine.url.database or ""
    if not db_name.endswith("_test"):
        if os.environ.get("SEED_TARGET") != "production":
            raise RuntimeError(
                f"_seed(): refusing to seed against URL {url_str!r} — "
                f"DB name {db_name!r} does not end with '_test'. "
                "If this IS intended (initial production seed), set "
                "env SEED_TARGET=production and re-run. "
                "See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md."
            )

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
    # Lazy-import engine for the same reason as _seed() above — see header NOTE.
    from src.db import engine

    try:
        return await _seed()
    finally:
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
