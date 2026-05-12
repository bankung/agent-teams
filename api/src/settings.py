"""Application settings (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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

    # CORS allow-list — Kanban #805. Browser preflight OPTIONS requests must
    # see Access-Control-Allow-Origin echoing the request Origin or FE
    # `jsonFetch` lands as TypeError "Failed to fetch". Env-driven via
    # CORS_ALLOW_ORIGINS — accept either a JSON list (pydantic-settings default
    # for list[str]) OR a comma-separated string for ops convenience
    # (`CORS_ALLOW_ORIGINS=http://localhost:5431,https://app.example.com`).
    # Default covers local Next.js dev — port matches docker-compose `web` host
    # port mapping (WEB_PORT default 5431, see docker-compose.yml).
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5431"],
        alias="CORS_ALLOW_ORIGINS",
    )

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv_origins(cls, v: object) -> object:
        """Accept comma-separated string from env vars in addition to JSON list."""
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return ["http://localhost:5431"]
            # Pydantic's default list[str] env parser only understands JSON.
            # If it doesn't start with '[' treat it as comma-separated.
            if not stripped.startswith("["):
                return [item.strip() for item in stripped.split(",") if item.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — call from app/Alembic env.py."""
    return Settings()
