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

import logging

import pytest


def test_src_logger_emits_info_after_main_import(caplog) -> None:
    """Kanban #739 v2 — `src.main` import attaches a stdout StreamHandler
    directly to the `src` umbrella logger (level=INFO) so scheduler INFO
    lines (and any future `src.services.*` INFO) reach uvicorn's stdout.
    The v1 attempt used `basicConfig` which attaches to stderr and is not
    forwarded by uvicorn's `--reload` worker subprocess to docker logs.

    Propagation stays on (root has no handler in prod since we dropped
    `basicConfig`, so no duplicate-emit risk), which keeps pytest's caplog
    working at the root level.
    """
    # Force the import — installs the surgical stdout StreamHandler on `src`.
    import src.main  # noqa: F401

    src_logger = logging.getLogger("src.services.recurrence")
    # Effective level walks up the parent chain; `src` was set to INFO.
    assert src_logger.getEffectiveLevel() <= logging.INFO

    # Verify the umbrella `src` logger has the expected handler config.
    src_umbrella = logging.getLogger("src")
    assert any(
        isinstance(h, logging.StreamHandler) for h in src_umbrella.handlers
    ), "src must have a StreamHandler attached (Kanban #739 v2)"

    with caplog.at_level(logging.INFO, logger="src.services.recurrence"):
        src_logger.info("kanban-739 logging smoke check")

    assert any(
        rec.name == "src.services.recurrence"
        and rec.levelno == logging.INFO
        and "kanban-739 logging smoke check" in rec.getMessage()
        for rec in caplog.records
    )


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

    assert TaskStatus.ALL == (1, 2, 3, 4, 5, 6)
    assert TaskPriority.ALL == (1, 2, 3, 4)
    assert TaskRole.ALL == (1, 2, 3, 4, 5)


@pytest.mark.asyncio
async def test_health_endpoint(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "env" in body
