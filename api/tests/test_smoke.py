"""Smoke tests — confirm the app boots and non-DB endpoints respond.

These tests do NOT require a running Postgres. They exercise:
- Import the FastAPI app cleanly (Project + Task ORM models register on Base.metadata).
- /health returns 200 with the expected envelope.
- /api/projects/by-name/{name} is the canonical bootstrap endpoint
  (post Kanban #694 Phase 2; /api/projects/active now returns 410 Gone).

Full integration tests (DB-backed) arrive in the QA phase once docker-compose
is up.
"""

from __future__ import annotations

import pytest


def test_app_imports() -> None:
    """Importing src.main must succeed — proves models + routers wire up."""
    from src.main import app

    assert app.title == "agent-teams API"
    # Confirm both routers mounted under /api.
    paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths
    assert any(p.startswith("/api/projects") for p in paths)
    assert any(p.startswith("/api/tasks") for p in paths)


def test_orm_metadata_has_all_tables() -> None:
    """Base.metadata must include projects, tasks, tasks_history."""
    from src.models import Base

    table_names = set(Base.metadata.tables.keys())
    assert {"projects", "tasks", "tasks_history"}.issubset(table_names)


def test_constants_align_with_general_md() -> None:
    """Sanity-check that integer codes match the standards doc."""
    from src.constants import TaskPriority, TaskRole, TaskStatus

    assert TaskStatus.ALL == (1, 2, 3, 4, 5)
    assert TaskPriority.ALL == (1, 2, 3, 4)
    assert TaskRole.ALL == (1, 2, 3, 4, 5)


@pytest.mark.asyncio
async def test_health_endpoint(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "env" in body
