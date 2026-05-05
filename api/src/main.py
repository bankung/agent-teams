"""FastAPI app entrypoint.

Phase 2b.2: foundation + projects/tasks routers mounted under /api.
"""

from __future__ import annotations

from fastapi import FastAPI

from src.routers import projects as projects_router
from src.routers import tasks as tasks_router
from src.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="agent-teams API",
        version="0.1.0",
        description="Self-hosted Kanban backend (projects + tasks, audit trail via PG trigger).",
        debug=settings.app_debug,
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — does NOT touch the DB."""
        return {"status": "ok", "env": settings.app_env}

    app.include_router(projects_router.router, prefix="/api")
    app.include_router(tasks_router.router, prefix="/api")

    return app


app = create_app()
