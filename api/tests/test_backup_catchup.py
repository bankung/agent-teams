"""Unit tests for BackupRunner.catchup_if_stale() (Kanban #1474).

Covers the four required scenarios:
  (a) latest backup is 25h old  -> triggers run_once
  (b) latest backup is 2h old   -> does NOT trigger
  (c) no prior backup exists     -> does NOT trigger (fresh deploy no-op)
  (d) disabled config            -> no-op

S3 is mocked via moto. run_once is patched so no real upload occurs.
asyncio_mode=auto in pyproject.toml so async tests run without extra markers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import boto3
import pytest
from moto import mock_aws

from src.services.backup import BackupConfig, BackupResult, BackupRunner


# ---- helpers ---------------------------------------------------------------


def _gen_age_pubkey() -> str:
    import pyrage
    ident = pyrage.x25519.Identity.generate()
    return str(ident.to_public())


def _required_env(extras: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "BACKUP_S3_BUCKET": "test-bucket",
        "BACKUP_S3_ACCESS_KEY_ID": "AKIATESTKEY",
        "BACKUP_S3_SECRET_ACCESS_KEY": "secret",
        "BACKUP_AGE_PUBKEY": _gen_age_pubkey(),
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db:5432/agent_teams",
        "REPO_ROOT": "/repo",
    }
    if extras:
        env.update(extras)
    return env


def _key_at(dt: datetime, prefix: str = "agent-teams/") -> str:
    return (
        f"{prefix}{dt.strftime('%Y-%m-%d')}/"
        f"backup-{dt.strftime('%Y%m%dT%H%M%SZ')}.tar.gz.age"
    )


def _make_bucket(bucket: str) -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)


_OK_RESULT = BackupResult(ok=True, key="k", bytes_uploaded=100, pruned=0)


# ---- (a) stale backup -> triggers run_once ---------------------------------


async def test_catchup_fires_when_backup_is_stale(caplog) -> None:
    """Latest backup 25h old -> catchup_if_stale returns True + run_once called once."""
    cfg = BackupConfig.from_env(_required_env())
    runner = BackupRunner(cfg)

    stale_ts = datetime.now(timezone.utc) - timedelta(hours=25)
    stale_key = _key_at(stale_ts)

    run_once_mock = AsyncMock(return_value=_OK_RESULT)

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        boto3.client("s3", region_name="us-east-1").put_object(
            Bucket=cfg.s3_bucket, Key=stale_key, Body=b"x"
        )
        with patch.object(runner, "run_once", run_once_mock), \
             caplog.at_level(logging.WARNING):
            result = await runner.catchup_if_stale(max_age_hours=24)

    assert result is True
    run_once_mock.assert_awaited_once()
    assert any("triggering immediate catchup" in r.message for r in caplog.records)


# ---- (b) fresh backup -> does NOT trigger ----------------------------------


async def test_catchup_skips_when_backup_is_fresh(caplog) -> None:
    """Latest backup 2h old -> catchup_if_stale returns False + run_once NOT called."""
    cfg = BackupConfig.from_env(_required_env())
    runner = BackupRunner(cfg)

    fresh_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    fresh_key = _key_at(fresh_ts)

    run_once_mock = AsyncMock(return_value=_OK_RESULT)

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        boto3.client("s3", region_name="us-east-1").put_object(
            Bucket=cfg.s3_bucket, Key=fresh_key, Body=b"x"
        )
        with patch.object(runner, "run_once", run_once_mock), \
             caplog.at_level(logging.INFO):
            result = await runner.catchup_if_stale(max_age_hours=24)

    assert result is False
    run_once_mock.assert_not_awaited()
    assert any("no catchup needed" in r.message for r in caplog.records)


# ---- (c) no prior backup -> does NOT trigger (fresh deploy) ----------------


async def test_catchup_skips_when_no_prior_backup(caplog) -> None:
    """Empty bucket (fresh deploy) -> catchup_if_stale returns False without error."""
    cfg = BackupConfig.from_env(_required_env())
    runner = BackupRunner(cfg)

    run_once_mock = AsyncMock(return_value=_OK_RESULT)

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        # Bucket is empty — no backup objects exist.
        with patch.object(runner, "run_once", run_once_mock), \
             caplog.at_level(logging.INFO):
            result = await runner.catchup_if_stale(max_age_hours=24)

    assert result is False
    run_once_mock.assert_not_awaited()
    assert any("no prior canonical backup" in r.message for r in caplog.records)


# ---- (d) disabled config -> no-op ------------------------------------------


async def test_catchup_skips_when_disabled() -> None:
    """Disabled config (missing required env) -> returns False, no S3 call."""
    cfg = BackupConfig.from_env({})  # no env at all -> is_enabled=False
    assert cfg.is_enabled is False
    runner = BackupRunner(cfg)

    run_once_mock = AsyncMock(return_value=_OK_RESULT)
    s3_block = patch.object(
        runner, "_s3_client", side_effect=AssertionError("must not call S3")
    )

    with patch.object(runner, "run_once", run_once_mock), s3_block:
        result = await runner.catchup_if_stale(max_age_hours=24)

    assert result is False
    run_once_mock.assert_not_awaited()
