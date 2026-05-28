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

from src.constants import RecordStatus, TaskPriority, TaskRole, TaskStatus
from src.models.project import Project
from src.models.task import Task
from src.settings import get_settings


PROJECT_NAME = "agent-teams"
PROJECT_DESCRIPTION = "Self-hosted Kanban for managing dev team tasks (dogfood)"

DEMO_PROJECT_NAME = "demo-tour"
DEMO_PROJECT_DESCRIPTION = (
    "Sample tour — try the 3 tasks below. "
    "Delete this project when you have seen enough."
)


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


def _demo_project_kwargs() -> dict:
    return {
        "name": DEMO_PROJECT_NAME,
        "description": DEMO_PROJECT_DESCRIPTION,
        "team": "general",
        # working_path = None on purpose — uses default scaffold path.
        # paths_web/api/db are NOT NULL columns but have no min-length CHECK;
        # empty strings are intentional for this sample project that has no
        # real on-disk stack. Confirmed safe by 2026_05_04_2130_initial_schema.py,
        # which defines these columns as sa.Text() without any length constraint.
        # If a future migration adds a CHECK (length > 0), update these values.
        "paths_web": "",
        "paths_api": "",
        "paths_db": "",
        "is_active": True,
    }


def _demo_tasks(project_id: int) -> list[Task]:
    return [
        Task(
            project_id=project_id,
            title="[DEMO] Draft a small FastAPI hello-world endpoint with input validation",
            description=(
                "This is a sample task to show how agents do backend dev work.\n\n"
                "ASK: Draft a simple FastAPI POST endpoint at /api/hello that accepts "
                "{name: str} body, validates non-empty, returns {'message': f'Hello, {name}!'}. "
                "Save the code as a markdown snippet in your agent role-state folder "
                "(DO NOT modify the real api/ folder — this is a draft only).\n\n"
                "Click 'Run' above to start. Watch the task drawer to see the agent work."
            ),
            task_type="feature",
            task_kind="ai",
            process_status=TaskStatus.TODO,
            priority=TaskPriority.NORMAL,
            assigned_role=None,
            acceptance_criteria=[
                {"text": "Agent drafted endpoint code with input validation", "status": "pending"},
                {"text": "Code includes example request + response in markdown", "status": "pending"},
                {"text": "No modification to api/ folder (draft only)", "status": "pending"},
            ],
        ),
        Task(
            project_id=project_id,
            title="[DEMO] Draft 3 LinkedIn post variations about AI productivity",
            description=(
                "This is a sample task to show how agents do content work.\n\n"
                "ASK: Draft 3 short LinkedIn post variations (each ≤300 words) about how AI "
                "tools save time for knowledge workers. Vary the hook style: (1) statistic-driven, "
                "(2) story-driven, (3) provocation-driven. Save as markdown.\n\n"
                "Click 'Run' to see how the content team agents collaborate."
            ),
            task_type="feature",
            task_kind="ai",
            process_status=TaskStatus.TODO,
            priority=TaskPriority.NORMAL,
            assigned_role=None,
            acceptance_criteria=[
                {"text": "3 distinct post variations drafted (each ≤300 words)", "status": "pending"},
                {"text": "Hook styles differ: statistic / story / provocation", "status": "pending"},
                {"text": "Posts feel publishable (operator can edit, not rewrite from scratch)", "status": "pending"},
            ],
        ),
        Task(
            project_id=project_id,
            title="[DEMO] Summarize sample_sales.csv: top categories + 30-day trend",
            description=(
                "This is a sample task to show how agents do data analysis.\n\n"
                "ASK: There's a sample_sales.csv in your data/raw/ folder (will be created "
                "when you install the data team scaffold; or use any small CSV you have). "
                "Summarize: top 3 categories by revenue, simple 30-day trend chart, any "
                "anomalies you spot.\n\n"
                "Click 'Run' to see the bi-analyst agent work.\n\n"
                "Note: Requires data-team scaffold (D.4 task) for full sample CSV. Without it, "
                "agent will report 'no data found' and explain how to add one."
            ),
            task_type="feature",
            task_kind="ai",
            process_status=TaskStatus.TODO,
            priority=TaskPriority.NORMAL,
            assigned_role=None,
            acceptance_criteria=[
                {"text": "Summary covers top categories + trend", "status": "pending"},
                {"text": "At least 1 chart generated (PNG or markdown table)", "status": "pending"},
                {"text": "Anomalies (if found) noted with row context", "status": "pending"},
            ],
        ),
    ]


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
        # Intentionally NO status filter here: skip on ANY row (including soft-deleted).
        # Re-creating agent-teams under a NEW auto-increment id would break every
        # id=1-pinned reference (LANGGRAPH_PROJECT_ID=1 in docker-compose,
        # `_runtime/lead_project_id.txt`, operator-bound sessions). A true restore
        # would need to un-delete the existing row to preserve id=1 — that is a
        # separate future enhancement, not handled here.
        existing = await session.execute(
            select(Project).where(Project.name == PROJECT_NAME)
        )
        if existing.scalar_one_or_none() is not None:
            print(f"[seed] project {PROJECT_NAME!r} already exists — already seeded.")
        else:
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

    # === demo-tour seed (Kanban #1361 — pilot user 5-minute walkthrough) ===
    # Idempotency: check ACTIVE rows only (status=RecordStatus.ACTIVE). A
    # soft-deleted demo-tour (status=0) does NOT block re-creation — the
    # partial unique index `ux_projects_name_active` is scoped to status=1, so
    # the name is freed on soft-delete and a fresh ACTIVE row can be inserted.
    # This is the design intent (demo-tour is disposable; contrast with the
    # agent-teams block above which must preserve id=1).  (#1629 fix)
    async with SessionLocal() as session:
        demo_existing = (
            await session.execute(
                select(Project).where(
                    Project.name == DEMO_PROJECT_NAME,
                    Project.status == RecordStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if demo_existing is None:
            demo_project = Project(**_demo_project_kwargs())
            session.add(demo_project)
            await session.flush()  # populate demo_project.id
            for task in _demo_tasks(demo_project.id):
                session.add(task)
            await session.commit()
            await session.refresh(demo_project)
            print(
                f"[seed] inserted demo-tour project id={demo_project.id} + 3 demo tasks."
            )
        else:
            print(
                f"[seed] demo-tour project already exists (id={demo_existing.id}) — skipping."
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
