"""Application settings (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# parents[0] = src/, parents[1] = api/, parents[2] = repo root
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


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
    app_debug: bool = Field(default=True, alias="APP_DEBUG")

    # Filesystem root of the agent-teams repo — used by project auto-scaffold
    # to locate context/projects/<name>/. Override with REPO_ROOT env var when
    # the API runs outside the repo (e.g., in a container with the repo bind-mounted).
    repo_root: Path = Field(default=_DEFAULT_REPO_ROOT, alias="REPO_ROOT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — call from app/Alembic env.py."""
    return Settings()
