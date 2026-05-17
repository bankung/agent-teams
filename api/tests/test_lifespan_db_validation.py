"""L8 prevention: api lifespan DB-name allowlist gate (Kanban #1113).

Tests the refuse-to-start guard in `src.main._validate_db_url` and the
defense-in-depth gate in `BackupConfig.from_env()`. Sibling to L6 (purge
fixture) and L7 (langgraph DATABASE_URI). See
context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

All tests are mock-only — no DB connection required. The `_validate_db_url`
function takes a string URL so we can pass synthetic DSNs without monkeypatching
the live module-level engine.

Lifespan-level coverage: we monkeypatch the IMPORTED `engine` reference inside
`src.main` (so the `from src.db import engine` line in lifespan picks up the
fake) and drive the lifespan async context — assert RuntimeError fires BEFORE
`start_listener` is called.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import _allowed_db_names, _validate_db_url


# ---------------------------------------------------------------------------
# AC-1 / AC-3 — _validate_db_url accepts canonical DBs, rejects rogue ones
# ---------------------------------------------------------------------------


def test_validate_accepts_canonical_live_db() -> None:
    """The live `agent_teams` DB is in the default allowlist."""
    _validate_db_url("postgresql+asyncpg://u:p@h:5432/agent_teams")  # no raise


def test_validate_accepts_canonical_test_db() -> None:
    """The `agent_teams_test` DB is in the default allowlist."""
    _validate_db_url("postgresql+asyncpg://u:p@h:5432/agent_teams_test")  # no raise


def test_validate_rejects_rogue_db_with_message_naming_db_and_env() -> None:
    """AC-3: error message names the rejected db_name + DB_NAME_ALLOWLIST env."""
    with pytest.raises(RuntimeError) as excinfo:
        _validate_db_url("postgresql+asyncpg://u:p@h:5432/rogue_db")
    msg = str(excinfo.value)
    assert "REFUSE TO START" in msg
    assert "rogue_db" in msg, "error must name the rejected DB"
    assert "DB_NAME_ALLOWLIST" in msg, "error must mention the env var to extend"
    assert "agent_teams" in msg, "error must list the current allowlist"


def test_validate_rejects_empty_db_name() -> None:
    """A DSN with no database (unresolved env) must be refused — '' not in allowlist."""
    with pytest.raises(RuntimeError, match="REFUSE TO START"):
        _validate_db_url("postgresql+asyncpg://u:p@h:5432/")


def test_validate_rejects_production_like_name() -> None:
    """Any non-allowlisted db name must be refused."""
    for name in ("agent_teams_prod", "agent_teams_staging", "postgres"):
        with pytest.raises(RuntimeError, match="REFUSE TO START"):
            _validate_db_url(f"postgresql+asyncpg://u:p@h:5432/{name}")


# ---------------------------------------------------------------------------
# Allowlist env parsing — DB_NAME_ALLOWLIST respected at call time
# ---------------------------------------------------------------------------


def test_allowed_db_names_defaults() -> None:
    """When DB_NAME_ALLOWLIST is unset, defaults to live + test DBs."""
    assert _allowed_db_names({}) == {"agent_teams", "agent_teams_test"}


def test_allowed_db_names_env_csv_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override is CSV; whitespace stripped; empties dropped."""
    monkeypatch.setenv(
        "DB_NAME_ALLOWLIST", " agent_teams , agent_teams_test , staging_db , ",
    )
    names = _allowed_db_names()
    assert names == {"agent_teams", "agent_teams_test", "staging_db"}


def test_validate_respects_extended_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding staging_db to DB_NAME_ALLOWLIST lets it pass at call time."""
    monkeypatch.setenv(
        "DB_NAME_ALLOWLIST", "agent_teams,agent_teams_test,staging_db",
    )
    _validate_db_url("postgresql+asyncpg://u:p@h:5432/staging_db")  # no raise


def test_validate_accepts_explicit_allowed_override() -> None:
    """The `allowed` parameter override path (used by callers passing custom sets)."""
    _validate_db_url(
        "postgresql+asyncpg://u:p@h:5432/custom_db",
        allowed={"custom_db", "other"},
    )


# ---------------------------------------------------------------------------
# AC-2 / AC-5 — lifespan rejects rogue DB BEFORE scheduler / SSE broker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_rejects_rogue_db_before_listener_or_scheduler() -> None:
    """End-to-end lifespan drive: monkeypatch the in-module engine ref to a
    fake bound to 'rogue_db', enter the async lifespan ctx, assert:

      a) RuntimeError raised with 'REFUSE TO START' and 'rogue_db'.
      b) start_listener was NEVER awaited (gate fires first).
      c) AsyncIOScheduler was NEVER constructed (gate fires first).
    """
    from src import main as src_main

    fake_engine = SimpleNamespace(url="postgresql+asyncpg://u:p@h:5432/rogue_db")

    # The lifespan does `from src.db import engine` — patch the symbol on
    # src.db so the fresh import inside lifespan picks up our fake.
    with patch("src.db.engine", fake_engine), \
         patch("src.main.start_listener", new=AsyncMock()) as mock_listener, \
         patch("src.main.AsyncIOScheduler") as mock_sched_cls:
        app = MagicMock()
        with pytest.raises(RuntimeError, match="REFUSE TO START.*rogue_db"):
            async with src_main.lifespan(app):
                pytest.fail("lifespan body must not be reached on rogue DB")
        mock_listener.assert_not_awaited()
        mock_sched_cls.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_accepts_canonical_db_and_proceeds() -> None:
    """Sanity: when DB is canonical (agent_teams_test — the live conftest DSN),
    the gate passes and the SSE-broker startup is reached.

    APP_SCHEDULER_DISABLE=true is set by conftest, so the scheduler branch is
    skipped — we only need to assert start_listener was awaited (proves the
    gate didn't fire and execution proceeded past the validation line).
    """
    from src import main as src_main

    # Stub start_listener / stop_listener so we don't hit the real LISTEN/NOTIFY
    # connection path inside a unit test.
    with patch("src.main.start_listener", new=AsyncMock()) as mock_start, \
         patch("src.main.stop_listener", new=AsyncMock()) as mock_stop:
        app = MagicMock()
        async with src_main.lifespan(app):
            pass
        mock_start.assert_awaited_once()
        mock_stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC-4 — BackupConfig.from_env() defense-in-depth gate
# ---------------------------------------------------------------------------


def test_backup_config_from_env_rejects_rogue_db() -> None:
    """BackupConfig.from_env raises if DATABASE_URL points at a non-allowed DB."""
    from src.services.backup import BackupConfig

    rogue_env = {
        "BACKUP_S3_BUCKET": "test-bucket",
        "BACKUP_S3_ACCESS_KEY_ID": "AKIA",
        "BACKUP_S3_SECRET_ACCESS_KEY": "secret",
        "BACKUP_AGE_PUBKEY": "age1k6jm85h4ffkvq4w6wjm5j8jeht5yt3xc6me5k4hzx7karmffvavqt78yay",
        "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/rogue_db",
    }
    with pytest.raises(RuntimeError) as excinfo:
        BackupConfig.from_env(rogue_env)
    msg = str(excinfo.value)
    assert "BackupRunner" in msg
    assert "rogue_db" in msg
    assert "DB_NAME_ALLOWLIST" in msg


def test_backup_config_from_env_accepts_canonical_db() -> None:
    """Sanity: canonical DB passes — existing test_backup.py fixtures still work."""
    from src.services.backup import BackupConfig

    ok_env = {
        "BACKUP_S3_BUCKET": "test-bucket",
        "BACKUP_S3_ACCESS_KEY_ID": "AKIA",
        "BACKUP_S3_SECRET_ACCESS_KEY": "secret",
        "BACKUP_AGE_PUBKEY": "age1k6jm85h4ffkvq4w6wjm5j8jeht5yt3xc6me5k4hzx7karmffvavqt78yay",
        "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/agent_teams",
    }
    cfg = BackupConfig.from_env(ok_env)
    assert cfg.is_enabled is True
    assert cfg.database_url == ok_env["DATABASE_URL"]


def test_backup_config_from_env_no_database_url_skips_gate() -> None:
    """An empty DATABASE_URL is allowed — gate only fires when a URL is set
    (matches existing pg_dump behaviour: empty URL raises later at dump time).
    """
    from src.services.backup import BackupConfig

    no_db_env = {
        "BACKUP_S3_BUCKET": "test-bucket",
        "BACKUP_S3_ACCESS_KEY_ID": "AKIA",
        "BACKUP_S3_SECRET_ACCESS_KEY": "secret",
        "BACKUP_AGE_PUBKEY": "age1k6jm85h4ffkvq4w6wjm5j8jeht5yt3xc6me5k4hzx7karmffvavqt78yay",
    }
    cfg = BackupConfig.from_env(no_db_env)
    assert cfg.database_url == ""


def test_backup_config_from_env_respects_extended_allowlist() -> None:
    """When DB_NAME_ALLOWLIST adds a name, BackupConfig accepts it."""
    from src.services.backup import BackupConfig

    env = {
        "BACKUP_S3_BUCKET": "test-bucket",
        "BACKUP_S3_ACCESS_KEY_ID": "AKIA",
        "BACKUP_S3_SECRET_ACCESS_KEY": "secret",
        "BACKUP_AGE_PUBKEY": "age1k6jm85h4ffkvq4w6wjm5j8jeht5yt3xc6me5k4hzx7karmffvavqt78yay",
        "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/staging_db",
        "DB_NAME_ALLOWLIST": "agent_teams,agent_teams_test,staging_db",
    }
    cfg = BackupConfig.from_env(env)  # no raise
    assert cfg.database_url == env["DATABASE_URL"]
