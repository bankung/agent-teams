"""Tests for L12 prevention layer (Kanban #1120, 2026-05-17).

Covers the minimum-dump-size guard in `BackupRunner.run_once()` and the
"< 2 backups blocks prune" defense in `BackupRunner._prune()`. Both layers
protect the backup history from silent corruption when the api container
starts pointed at an empty/rogue DB.

Sibling test module: `test_backup.py` (Kanban #959). The shared moto/age/
pg_dump mock pattern is reused here verbatim.

Cross-ref:
  - incidents/2026-05-17-dev-db-wipe.md
  - Kanban #1113 (L8 — lifespan DB allowlist) which is the upstream gate;
    L12 here is the belt-and-suspenders if L8 is bypassed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tarfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import boto3
import pyrage
from moto import mock_aws

from src.services.backup import BackupConfig, BackupRunner


# ---- helpers (mirror test_backup.py to keep this module self-contained) ---


def _gen_age_keypair() -> tuple[str, str]:
    ident = pyrage.x25519.Identity.generate()
    return str(ident.to_public()), str(ident)


def _required_env(
    pub: str = "age1k6jm85h4ffkvq4w6wjm5j8jeht5yt3xc6me5k4hzx7karmffvavqt78yay",
    bucket: str = "test-bucket",
    extras: dict[str, str] | None = None,
) -> dict[str, str]:
    env = {
        "BACKUP_S3_BUCKET": bucket,
        "BACKUP_S3_ACCESS_KEY_ID": "AKIATESTKEY",
        "BACKUP_S3_SECRET_ACCESS_KEY": "secret",
        "BACKUP_AGE_PUBKEY": pub,
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db:5432/agent_teams",
        "REPO_ROOT": "/repo",
    }
    if extras:
        env.update(extras)
    return env


def _make_bucket(bucket: str) -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=bucket)


def _key_at(dt: datetime, prefix: str = "agent-teams/") -> str:
    return (
        f"{prefix}{dt.strftime('%Y-%m-%d')}/"
        f"backup-{dt.strftime('%Y%m%dT%H%M%SZ')}.tar.gz.age"
    )


# ---- L12 part 1: run_once refuses tiny pg_dump output ---------------------


def test_run_once_refuses_tiny_dump(tmp_path: Path, caplog) -> None:
    """AC #1, #2, #4: pg_dump produces 9 bytes -> run_once returns ok=False,
    error mentions 'suspiciously small', no upload, no retention prune.
    """
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    # Mock pg_dump to write a 9-byte stub (well below the 100KB default).
    def fake_run(cmd, **kwargs):  # noqa: ANN001 — test stub
        out_path = Path(cmd[cmd.index("-f") + 1])
        out_path.write_bytes(b"SELECT 1;")  # 9 bytes
        return MagicMock(returncode=0, stdout="", stderr="")

    # Patch _upload + _prune to assert they were never called on this path.
    upload_calls = []
    prune_calls = []

    def fake_upload(self, encrypted, key):  # noqa: ANN001
        upload_calls.append((encrypted, key))

    def fake_prune(self):  # noqa: ANN001
        prune_calls.append(True)
        return 0

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        with patch("src.services.backup.subprocess.run", side_effect=fake_run), \
             patch.object(BackupRunner, "_upload", fake_upload), \
             patch.object(BackupRunner, "_prune", fake_prune), \
             caplog.at_level(logging.ERROR):
            result = asyncio.run(runner.run_once())

    # AC #2: error envelope shape.
    assert result.ok is False
    assert result.key is None
    assert result.bytes_uploaded == 0
    assert result.pruned == 0
    assert result.error is not None
    assert "suspiciously small" in result.error
    assert "9B" in result.error
    assert "102400B" in result.error  # default min in bytes
    assert "refusing to upload empty-DB backup" in result.error

    # AC #4: no upload + no prune happened.
    assert upload_calls == [], "upload must not be called on too-small dumps"
    assert prune_calls == [], "prune must not be called on too-small dumps"

    # Logger captured the abort at ERROR level.
    assert any("suspiciously small" in r.message for r in caplog.records)


def test_run_once_refuses_dump_just_below_threshold(tmp_path: Path) -> None:
    """Boundary: a dump 1 byte below BACKUP_MIN_BYTES still aborts."""
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    just_below = 100 * 1024 - 1  # 102399 bytes

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        out_path = Path(cmd[cmd.index("-f") + 1])
        out_path.write_bytes(b"x" * just_below)
        return MagicMock(returncode=0, stdout="", stderr="")

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        with patch("src.services.backup.subprocess.run", side_effect=fake_run):
            result = asyncio.run(runner.run_once())

    assert result.ok is False
    assert result.error is not None
    assert f"{just_below}B" in result.error


def test_run_once_accepts_dump_at_or_above_threshold(tmp_path: Path) -> None:
    """Positive control: a dump exactly at the threshold proceeds to upload.

    Proves the gate is at the boundary, not above it, and that the happy
    path still works end-to-end with mocked S3.
    """
    pub, priv = _gen_age_keypair()

    # Minimal repo on disk so _archive_filesystem has something to bundle.
    repo = tmp_path / "repo"
    (repo / "context").mkdir(parents=True)
    (repo / ".claude").mkdir(parents=True)

    cfg = BackupConfig.from_env(_required_env(pub=pub, extras={"REPO_ROOT": str(repo)}))
    runner = BackupRunner(cfg)

    at_threshold = 100 * 1024  # 102400 bytes — exactly the default

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        out_path = Path(cmd[cmd.index("-f") + 1])
        out_path.write_bytes(b"x" * at_threshold)
        return MagicMock(returncode=0, stdout="", stderr="")

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        with patch("src.services.backup.subprocess.run", side_effect=fake_run):
            result = asyncio.run(runner.run_once())

    assert result.ok is True, f"run_once failed: {result.error}"
    assert result.key is not None
    assert result.bytes_uploaded > 0


def test_run_once_respects_min_bytes_env_override(
    tmp_path: Path, monkeypatch
) -> None:
    """AC #5 runtime side: BACKUP_MIN_BYTES env var overrides the default."""
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    # Override threshold to 10MB; a 1KB dump should now fail (which would
    # have passed on the default 100KB? no, 1KB < 100KB — pick a value that
    # would pass at default but fail at override).
    # 200KB dump > 100KB default would normally pass, but < 10MB override.
    monkeypatch.setenv("BACKUP_MIN_BYTES", str(10 * 1024 * 1024))
    dump_size = 200 * 1024  # 200KB

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        out_path = Path(cmd[cmd.index("-f") + 1])
        out_path.write_bytes(b"x" * dump_size)
        return MagicMock(returncode=0, stdout="", stderr="")

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        with patch("src.services.backup.subprocess.run", side_effect=fake_run):
            result = asyncio.run(runner.run_once())

    assert result.ok is False
    assert result.error is not None
    assert "suspiciously small" in result.error
    assert f"{dump_size}B" in result.error
    assert f"{10 * 1024 * 1024}B" in result.error


# ---- L12 part 2: prune refuses if < 2 backups -----------------------------


def test_prune_refuses_with_zero_backups(tmp_path: Path, caplog) -> None:
    """AC #3: no objects in the prefix -> prune is a no-op."""
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        with caplog.at_level(logging.WARNING):
            deleted = runner._prune()

    assert deleted == 0
    assert any(
        "only 0 backup(s) exist" in r.message for r in caplog.records
    ), "expected the < 2 guard warning to fire"


def test_prune_refuses_with_one_backup(tmp_path: Path, caplog) -> None:
    """AC #3: exactly one backup in the prefix -> prune is a no-op, the lone
    backup survives even if normal retention math would slate it.
    """
    pub, _ = _gen_age_keypair()
    # Force keep_daily=0 + keep_monthly=0 so normal retention would delete
    # the single object — proving the guard runs ahead of that math.
    cfg = BackupConfig.from_env(_required_env(
        pub=pub,
        extras={"BACKUP_KEEP_DAILY": "1", "BACKUP_KEEP_MONTHLY": "0"},
    ))
    runner = BackupRunner(cfg)

    only_key = _key_at(datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc))

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        client = boto3.client("s3", region_name="us-east-1")
        client.put_object(Bucket=cfg.s3_bucket, Key=only_key, Body=b"x")

        with caplog.at_level(logging.WARNING):
            deleted = runner._prune()

        # Survivor check.
        survivors = set()
        for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=cfg.s3_bucket, Prefix=cfg.s3_prefix,
        ):
            for o in page.get("Contents", []):
                survivors.add(o["Key"])

    assert deleted == 0
    assert survivors == {only_key}
    assert any(
        "only 1 backup(s) exist" in r.message for r in caplog.records
    )


def test_prune_proceeds_with_two_or_more_backups(tmp_path: Path) -> None:
    """Positive control: with >= 2 backups, retention math runs normally."""
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    # 60 days of one-per-day backups; with default keep_daily=30 + keep_monthly=12
    # the prune should delete some of them (proving the guard didn't trigger).
    base = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    keys = [_key_at(base - timedelta(days=i)) for i in range(60)]

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        client = boto3.client("s3", region_name="us-east-1")
        for k in keys:
            client.put_object(Bucket=cfg.s3_bucket, Key=k, Body=b"x")

        deleted = runner._prune()

    # At least one object should have been deleted; the < 2 guard did NOT
    # short-circuit on this (>= 2 backups) input.
    assert deleted > 0, "prune should have run normal retention math"


def test_prune_ignores_foreign_keys_in_count(tmp_path: Path, caplog) -> None:
    """The < 2 guard counts only backup-shaped keys (parseable timestamp).

    A bucket with one real backup + several foreign objects under the same
    prefix should still trip the guard (count == 1).
    """
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    backup_key = _key_at(datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc))
    foreign_keys = [
        "agent-teams/README.md",
        "agent-teams/random-uploaded.tar.gz",
    ]

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        client = boto3.client("s3", region_name="us-east-1")
        client.put_object(Bucket=cfg.s3_bucket, Key=backup_key, Body=b"x")
        for k in foreign_keys:
            client.put_object(Bucket=cfg.s3_bucket, Key=k, Body=b"x")

        with caplog.at_level(logging.WARNING):
            deleted = runner._prune()

    # Only 1 backup-shaped key -> guard fires, nothing deleted.
    assert deleted == 0
    assert any("only 1 backup(s) exist" in r.message for r in caplog.records)
