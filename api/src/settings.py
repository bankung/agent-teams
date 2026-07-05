"""Application settings (pydantic-settings)."""

from __future__ import annotations

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

    # Kanban #955.A — Web Push (VAPID) keys for the notify_web_push.py adapter.
    # Operator generates a fresh keypair ONCE via api/scripts/generate_vapid_keys.py
    # and pastes the output into .env. The private key MUST NOT be committed.
    # Subject is `mailto:...` or `https://...` per RFC 8292 (default in
    # .env.example is `mailto:admin@example.com` — operator must override in .env).
    #
    # Defaults are empty so an unconfigured deployment surfaces the
    # `missing_env_VAPID_*` adapter detail (router falls through cleanly) — same
    # posture as TELEGRAM_BOT_TOKEN gating.
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field(default="", alias="VAPID_SUBJECT")

    # Gmail SMTP + digest env vars are read directly via os.environ.get in
    # notify_email.py (matches notify_telegram.py pattern); intentionally not parsed into Settings.

    # Kanban #2565 — Telegram async-HITL channel (Mode-A automation, 0.8 #5).
    # BOTH are read at CALL TIME via os.environ inside notify_telegram.py and
    # scripts/telegram_poller.py (mirrors the notify_telegram
    # env-at-call-time pattern), so these fields are DECLARATIVE — they document
    # the canonical names + supply a default-empty so an unconfigured deployment
    # soft-fails (the adapter surfaces `missing_env_TELEGRAM_BOT_TOKEN` and the
    # router falls through; the poller logs + idles). NEVER hardcode real values.
    #   TELEGRAM_BOT_TOKEN        — the bot's API token (outbound send + poller).
    #   TELEGRAM_OPERATOR_CHAT_ID — the ONLY chat_id the poller will act on; a
    #                               callback/message from any other from.id is
    #                               silently ignored (poller chat-id lock).
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_operator_chat_id: str = Field(default="", alias="TELEGRAM_OPERATOR_CHAT_ID")

    # Kanban #1437 — signed-token opt-out for digest emails (itsdangerous
    # URLSafeTimedSerializer). Tokens are HMAC-signed; the key must be stable
    # across API restarts so tokens remain valid for up to 90 days. In
    # docker-compose the api service sets this from ${SECRET_KEY:-dev-default}.
    # The dev-default is intentionally weak — production MUST rotate via .env.
    # Salt is hardcoded per action ("digest-optout-v1") so the same key can be
    # reused for future token types without cross-action forgery risk.
    secret_key: str = Field(
        default="dev-secret-NOT-FOR-PROD-change-in-dotenv",
        alias="SECRET_KEY",
    )

    # Kanban #1011 (2026-05-20): HITL aging nudge cron cadence.
    # How frequently the nudge scanner runs in minutes. Default 30.
    # Range 5..240 — below 5 is too aggressive; above 240 (4h) defeats the
    # sub-hour nudge precision. Validated via Field(ge=5, le=240).
    hitl_nudge_interval_minutes: int = Field(
        default=30,
        ge=5,
        le=240,
        alias="HITL_NUDGE_INTERVAL_MINUTES",
    )

    # Kanban #1240 (2026-06-07): TTL (in days) for the daily audit-archive
    # sweep. A COMPLETED audit task (task_type='audit') whose completed_at is
    # older than this many days is flipped is_active=false by
    # services/audit_archive.py (projects with audit_enabled=false are skipped).
    # Default 30. ge=1 — a zero/negative TTL would archive freshly-completed
    # audit tasks the same day, defeating the point. The sweep service reads the
    # value via os.environ at tick time (mirrors recurrence / hitl_nudge) so a
    # test monkeypatch / .env change applies without a restart-only Settings
    # singleton; this field documents + validates the canonical default.
    audit_archive_days: int = Field(
        default=30,
        ge=1,
        alias="AUDIT_ARCHIVE_DAYS",
    )

    # Kanban #2356 (2026-06-15): default billing-cycle cut-off day for the
    # monthly usage rollup (GET /api/usage/monthly). A cycle covers
    # [day D of month M, day D of month M+1). Capped at 28 (deliberate): every
    # month has a day <= 28, so cycle boundaries never hit the Feb/30/31 edge.
    # Overridable per-request via ?cycle_day=; the resolved value is echoed in
    # the response.
    cost_cycle_day: int = Field(
        default=1,
        ge=1,
        le=28,
        alias="COST_CYCLE_DAY",
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


def get_settings() -> Settings:
    """Settings accessor — call from app / Alembic env.py / scripts.

    INTENTIONALLY NOT cached (`@lru_cache` removed 2026-05-17 per the
    dev-DB-wipe incident L3 fix). Settings construction is microsecond-cheap;
    caching the singleton lets the FIRST `get_settings()` call's
    DATABASE_URL bind to the module-level engine in `src.db` PERMANENTLY,
    even when conftest later rewrites `os.environ["DATABASE_URL"]` to the
    test DB. Re-reading env on each call costs nothing measurable and
    eliminates a whole class of test-isolation poisoning.

    See `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md`.
    """
    return Settings()
