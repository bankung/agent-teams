"""Application settings (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment / .env file at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Async DSN — used by FastAPI runtime + Alembic env.py
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_teams",
        alias="DATABASE_URL",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")

    # Filesystem root of the agent-teams repo — used by project auto-scaffold
    # to locate context/projects/<name>/. REQUIRED — pydantic-settings raises
    # ValidationError at startup if REPO_ROOT is unset/empty. Inside docker-compose
    # the api service sets this to /repo (the bind mount target). For local uvicorn
    # runs, set it explicitly in .env (no implicit parents[2] fallback — refactor-brittle).
    repo_root: Path = Field(alias="REPO_ROOT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — call from app/Alembic env.py."""
    return Settings()
