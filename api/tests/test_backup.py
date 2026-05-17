"""Unit tests for the off-site encrypted backup runner (Kanban #959).

Covers `BackupConfig` env parsing + enablement gate, the encrypt/decrypt
round-trip via pyrage, object-key shape, mocked-S3 upload + dry-run +
retention pruner, tarball-skip discipline, pg_dump command shape, and one
end-to-end run_once() smoke that proves the full cycle works with all the
pieces wired together.

S3 is mocked via `moto[s3]` (`mock_aws` context manager) — no network, no
real credentials, deterministic. pg_dump is mocked at the `subprocess.run`
boundary so we never need a live Postgres for the unit tests.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tarfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import boto3
import pyrage
import pytest
from moto import mock_aws

from src.services.backup import (
    BackupConfig,
    BackupResult,
    BackupRunner,
    _compute_retention_set,
    _normalize_prefix,
    _parse_key_timestamp,
    _pg_dump_url,
)


# ---- helpers --------------------------------------------------------------


def _gen_age_keypair() -> tuple[str, str]:
    """Return (public_str, private_str) for a fresh x25519 age keypair."""
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


# ---- config / enablement --------------------------------------------------


@pytest.mark.parametrize(
    "drop",
    [
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_ACCESS_KEY_ID",
        "BACKUP_S3_SECRET_ACCESS_KEY",
        "BACKUP_AGE_PUBKEY",
    ],
)
def test_config_disabled_when_any_required_env_missing(drop: str) -> None:
    env = _required_env()
    env.pop(drop)
    cfg = BackupConfig.from_env(env)
    assert cfg.is_enabled is False, f"expected disabled when {drop} missing"


def test_config_enabled_with_all_env_set() -> None:
    cfg = BackupConfig.from_env(_required_env())
    assert cfg.is_enabled is True
    assert cfg.s3_bucket == "test-bucket"
    assert cfg.s3_region == "us-east-1"
    assert cfg.s3_endpoint is None
    assert cfg.s3_prefix == "agent-teams/"
    assert cfg.cron_rule == "0 3 * * *"
    assert cfg.timezone == "UTC"
    assert cfg.keep_daily == 30
    assert cfg.keep_monthly == 12
    assert cfg.dry_run is False


def test_endpoint_override_for_b2_r2_wasabi() -> None:
    env = _required_env(
        extras={"BACKUP_S3_ENDPOINT": "https://s3.us-west-001.backblazeb2.com"}
    )
    cfg = BackupConfig.from_env(env)
    assert cfg.s3_endpoint == "https://s3.us-west-001.backblazeb2.com"
    # Boto3 client construction should pick up the endpoint.
    runner = BackupRunner(cfg)
    with mock_aws():
        client = runner._s3_client()
        # botocore stamps the endpoint on the meta of the constructed client.
        # The endpoint URL field is normalized — assert containment, not equality.
        assert "backblazeb2.com" in client.meta.endpoint_url


def test_dry_run_env_parsing() -> None:
    for truthy in ("1", "true", "TRUE", "yes"):
        cfg = BackupConfig.from_env(_required_env(extras={"BACKUP_DRY_RUN": truthy}))
        assert cfg.dry_run is True
    for falsy in ("0", "false", "no", ""):
        cfg = BackupConfig.from_env(_required_env(extras={"BACKUP_DRY_RUN": falsy}))
        assert cfg.dry_run is False


def test_normalize_prefix_idempotent() -> None:
    assert _normalize_prefix("foo") == "foo/"
    assert _normalize_prefix("foo/") == "foo/"
    assert _normalize_prefix("/foo/") == "foo/"
    assert _normalize_prefix("") == ""


def test_pg_dump_url_strips_asyncpg_driver() -> None:
    assert (
        _pg_dump_url("postgresql+asyncpg://u:p@h:5432/db")
        == "postgresql://u:p@h:5432/db"
    )
    # A plain libpq URL passes through.
    assert (
        _pg_dump_url("postgresql://u:p@h/db")
        == "postgresql://u:p@h/db"
    )
    assert _pg_dump_url("") == ""


# ---- encrypt / decrypt round-trip -----------------------------------------


def test_encrypt_then_decrypt_round_trip(tmp_path: Path) -> None:
    pub, priv = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    plaintext = b"the quick brown fox jumps over the lazy dog\n" * 100
    tarball = tmp_path / "backup.tar.gz"
    tarball.write_bytes(plaintext)

    encrypted = runner._encrypt(tarball, tmp_path)
    assert encrypted.exists()
    assert encrypted.name == "backup.tar.gz.age"
    # Plaintext should have been scrubbed.
    assert not tarball.exists()

    # Decrypt with the matching identity.
    ident = pyrage.x25519.Identity.from_str(priv)
    decrypted = pyrage.decrypt(encrypted.read_bytes(), [ident])
    assert decrypted == plaintext


# ---- object-key shape -----------------------------------------------------


def test_object_key_shape() -> None:
    cfg = BackupConfig.from_env(_required_env())
    runner = BackupRunner(cfg)
    now = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    key = runner._make_key(now)
    assert key == "agent-teams/2026-05-16/backup-20260516T030000Z.tar.gz.age"


def test_object_key_shape_with_custom_prefix() -> None:
    cfg = BackupConfig.from_env(_required_env(extras={"BACKUP_S3_PREFIX": "backups/prod"}))
    runner = BackupRunner(cfg)
    now = datetime(2026, 1, 1, 12, 30, 45, tzinfo=timezone.utc)
    key = runner._make_key(now)
    assert key == "backups/prod/2026-01-01/backup-20260101T123045Z.tar.gz.age"


def test_object_key_shape_dryrun() -> None:
    cfg = BackupConfig.from_env(_required_env(extras={"BACKUP_DRY_RUN": "true"}))
    runner = BackupRunner(cfg)
    now = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    key = runner._make_key(now)
    assert key == "agent-teams/_dryrun/2026-05-16/backup-20260516T030000Z.tar.gz.age"


def test_parse_key_timestamp_round_trip() -> None:
    dt = _parse_key_timestamp("agent-teams/2026-05-16/backup-20260516T030000Z.tar.gz.age")
    assert dt == datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    # Foreign key shape — ignored.
    assert _parse_key_timestamp("agent-teams/random.txt") is None
    assert _parse_key_timestamp("agent-teams/backup-bad.tar.gz.age") is None


# ---- archive (tarball) skip discipline ------------------------------------


def test_archive_skips_python_caches_and_node_modules(tmp_path: Path) -> None:
    # Build a fake repo with cruft we want excluded.
    repo = tmp_path / "repo"
    (repo / "context" / "projects" / "p1").mkdir(parents=True)
    (repo / "context" / "projects" / "p1" / "decisions.md").write_text("hello")
    (repo / "context" / "__pycache__").mkdir(parents=True)
    (repo / "context" / "__pycache__" / "junk.pyc").write_bytes(b"bytecode")
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "spec.md").write_text("agent")
    (repo / ".claude" / "node_modules").mkdir(parents=True)
    (repo / ".claude" / "node_modules" / "foo.js").write_text("// junk")
    (repo / ".claude" / "_scratch").mkdir(parents=True)
    (repo / ".claude" / "_scratch" / "tmp.txt").write_text("nope")

    cfg = BackupConfig.from_env(_required_env(extras={"REPO_ROOT": str(repo)}))
    runner = BackupRunner(cfg)
    db_dump = tmp_path / "db-dump.sql"
    db_dump.write_text("-- SQL")

    tarball = runner._archive_filesystem(tmp_path, db_dump)
    with tarfile.open(tarball, "r:gz") as tar:
        names = tar.getnames()

    # Wanted entries.
    assert "db-dump.sql" in names
    assert any("context/projects/p1/decisions.md" in n for n in names)
    assert any(".claude/agents/spec.md" in n for n in names)

    # Forbidden entries.
    assert not any("__pycache__" in n for n in names)
    assert not any("node_modules" in n for n in names)
    assert not any("_scratch" in n for n in names)


def test_archive_missing_dirs_logged_not_fatal(tmp_path: Path, caplog) -> None:
    """If context/ or .claude/ is absent, log a warning and continue."""
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    cfg = BackupConfig.from_env(_required_env(extras={"REPO_ROOT": str(empty_repo)}))
    runner = BackupRunner(cfg)
    db_dump = tmp_path / "db-dump.sql"
    db_dump.write_text("-- SQL")
    with caplog.at_level(logging.WARNING):
        tarball = runner._archive_filesystem(tmp_path, db_dump)
    assert tarball.exists()
    assert any("does not exist" in r.message for r in caplog.records)


# ---- pg_dump command shape (mocked subprocess) ----------------------------


def test_pg_dump_command_shape(tmp_path: Path) -> None:
    cfg = BackupConfig.from_env(_required_env())
    runner = BackupRunner(cfg)

    captured: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001 — test stub
        captured["cmd"] = cmd
        # Simulate a successful pg_dump by writing a stub file.
        out_path = Path(cmd[cmd.index("-f") + 1])
        out_path.write_text("-- fake dump")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("src.services.backup.subprocess.run", side_effect=fake_run):
        result = runner._dump_db_to_tarball(tmp_path)

    assert result.name == "db-dump.sql"
    assert result.read_text() == "-- fake dump"

    cmd = captured["cmd"]
    assert cmd[0] == "pg_dump"
    # No-owner / no-priv + clean restore semantics.
    assert "--no-owner" in cmd
    assert "--no-privileges" in cmd
    assert "--clean" in cmd
    assert "--if-exists" in cmd
    # The URL was converted from asyncpg DSN to libpq form.
    assert cmd[-1].startswith("postgresql://")
    assert "+asyncpg" not in cmd[-1]


def test_pg_dump_failure_raises(tmp_path: Path) -> None:
    cfg = BackupConfig.from_env(_required_env())
    runner = BackupRunner(cfg)
    with patch(
        "src.services.backup.subprocess.run",
        return_value=MagicMock(returncode=1, stderr="boom: connection refused"),
    ):
        with pytest.raises(RuntimeError, match="pg_dump failed"):
            runner._dump_db_to_tarball(tmp_path)


def test_pg_dump_missing_database_url_raises(tmp_path: Path) -> None:
    env = _required_env()
    env.pop("DATABASE_URL")
    cfg = BackupConfig.from_env(env)
    runner = BackupRunner(cfg)
    with pytest.raises(RuntimeError, match="DATABASE_URL is empty"):
        runner._dump_db_to_tarball(tmp_path)


# ---- mocked S3 upload + dry run -------------------------------------------


def _make_bucket(bucket: str) -> None:
    """Create the moto bucket so PutObject doesn't 404."""
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=bucket)


def test_upload_to_mocked_s3_succeeds(tmp_path: Path) -> None:
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)
    encrypted = tmp_path / "enc.bin"
    encrypted.write_bytes(b"\x00\x01\x02ciphertext")
    key = "agent-teams/2026-05-16/backup-20260516T030000Z.tar.gz.age"
    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        runner._upload(encrypted, key)
        # Verify the object landed.
        client = boto3.client("s3", region_name="us-east-1")
        body = client.get_object(Bucket=cfg.s3_bucket, Key=key)["Body"].read()
        assert body == b"\x00\x01\x02ciphertext"


# ---- retention math -------------------------------------------------------


def _key_at(dt: datetime, prefix: str = "agent-teams/") -> str:
    return (
        f"{prefix}{dt.strftime('%Y-%m-%d')}/"
        f"backup-{dt.strftime('%Y%m%dT%H%M%SZ')}.tar.gz.age"
    )


def test_retention_keeps_last_30_daily_plus_12_monthly() -> None:
    """Synthesize 18 months of one-per-day keys, run the retention selector,
    assert the retained set is exactly "last 30 distinct dates UNION last-of-month
    for the previous 12 months".
    """
    base = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    keys: list[tuple[str, datetime]] = []
    # 18 months back, one snapshot per day.
    for i in range(18 * 31):
        dt = base - timedelta(days=i)
        keys.append((_key_at(dt), dt))

    keep = _compute_retention_set(keys, keep_daily=30, keep_monthly=12)

    # Daily set: last 30 distinct calendar dates. Since each day has exactly one
    # object, the count is 30.
    daily_dates = {dt.date() for (_, dt) in keys}
    daily_dates_sorted = sorted(daily_dates, reverse=True)[:30]
    daily_keys_expected = {
        _key_at(datetime.combine(d, base.time(), tzinfo=timezone.utc))
        for d in daily_dates_sorted
    }
    # Monthly set: latest object of each of the last 12 calendar months.
    # Since the latest object of each month is the day with the largest day-of-month,
    # we walk the keys DESC and pick the first for each (year, month).
    monthly_keys_expected: set[str] = set()
    seen_months: set[tuple[int, int]] = set()
    for k, dt in sorted(keys, key=lambda kv: kv[1], reverse=True):
        m = (dt.year, dt.month)
        if m in seen_months:
            continue
        seen_months.add(m)
        monthly_keys_expected.add(k)
        if len(seen_months) >= 12:
            break

    expected = daily_keys_expected | monthly_keys_expected
    assert keep == expected
    # Sanity: keep set is well within the input set.
    assert keep.issubset({k for (k, _) in keys})
    # Sanity: daily-retained dates are the 30 most recent ones.
    kept_dates = {
        _parse_key_timestamp(k).date() for k in keep
        if _parse_key_timestamp(k) is not None
    }
    assert max(kept_dates) == base.date()
    # And the to-delete count is non-zero (we synthesized way more than retention).
    assert len(keys) > len(keep)


def test_retention_empty_input() -> None:
    assert _compute_retention_set([], 30, 12) == set()


def test_retention_keep_monthly_zero() -> None:
    base = datetime(2026, 5, 16, tzinfo=timezone.utc)
    keys = [(_key_at(base - timedelta(days=i)), base - timedelta(days=i)) for i in range(60)]
    keep = _compute_retention_set(keys, keep_daily=10, keep_monthly=0)
    assert len(keep) == 10


def test_prune_keeps_last_30_daily_plus_12_monthly_in_moto(tmp_path: Path) -> None:
    """End-to-end pruner: pre-populate moto S3 with 18 months of synthetic
    objects, run the pruner, assert the surviving set matches the retention math.
    """
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)

    base = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    expected_keys = []
    for i in range(18 * 31):
        dt = base - timedelta(days=i)
        expected_keys.append(_key_at(dt))

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        client = boto3.client("s3", region_name="us-east-1")
        for k in expected_keys:
            client.put_object(Bucket=cfg.s3_bucket, Key=k, Body=b"x")

        deleted = runner._prune()

        # Verify surviving set matches the retention math.
        survivors = set()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=cfg.s3_bucket, Prefix=cfg.s3_prefix):
            for o in page.get("Contents", []):
                survivors.add(o["Key"])

    pairs = [(k, _parse_key_timestamp(k)) for k in expected_keys]
    pairs = [(k, dt) for (k, dt) in pairs if dt is not None]
    expected_keep = _compute_retention_set(pairs, keep_daily=30, keep_monthly=12)

    assert survivors == expected_keep
    assert deleted == len(expected_keys) - len(expected_keep)


def test_prune_dry_run_no_op(tmp_path: Path) -> None:
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub, extras={"BACKUP_DRY_RUN": "true"}))
    runner = BackupRunner(cfg)
    base = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    keys = [_key_at(base - timedelta(days=i)) for i in range(60)]
    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        client = boto3.client("s3", region_name="us-east-1")
        for k in keys:
            client.put_object(Bucket=cfg.s3_bucket, Key=k, Body=b"x")

        deleted = runner._prune()
        # Verify nothing was actually deleted.
        survivors = set()
        for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=cfg.s3_bucket, Prefix=cfg.s3_prefix
        ):
            for o in page.get("Contents", []):
                survivors.add(o["Key"])

    assert deleted == 0
    assert survivors == set(keys)


def test_prune_ignores_foreign_keys_in_same_prefix(tmp_path: Path) -> None:
    """Objects under the prefix that don't match the backup naming convention
    are left untouched — never deleted by the pruner.
    """
    pub, _ = _gen_age_keypair()
    cfg = BackupConfig.from_env(_required_env(pub=pub))
    runner = BackupRunner(cfg)
    base = datetime(2026, 5, 16, 3, 0, 0, tzinfo=timezone.utc)
    backup_keys = [_key_at(base - timedelta(days=i)) for i in range(60)]
    foreign_keys = [
        "agent-teams/README.md",
        "agent-teams/random-uploaded-by-operator.tar.gz",
    ]
    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        client = boto3.client("s3", region_name="us-east-1")
        for k in backup_keys + foreign_keys:
            client.put_object(Bucket=cfg.s3_bucket, Key=k, Body=b"x")
        runner._prune()
        # Foreign objects still present.
        for k in foreign_keys:
            client.head_object(Bucket=cfg.s3_bucket, Key=k)  # 404 would raise


# ---- run_once integration -------------------------------------------------


def test_run_once_disabled_returns_clean_failure(tmp_path: Path) -> None:
    cfg = BackupConfig.from_env({})  # no env at all
    assert cfg.is_enabled is False
    runner = BackupRunner(cfg)
    import asyncio
    result = asyncio.run(runner.run_once())
    assert result.ok is False
    assert result.error == "disabled"


def test_warning_logged_when_disabled(caplog) -> None:
    cfg = BackupConfig.from_env({})
    runner = BackupRunner(cfg)
    import asyncio
    with caplog.at_level(logging.WARNING):
        asyncio.run(runner.run_once())
    assert any("disabled" in r.message for r in caplog.records)


def test_run_once_full_smoke_with_moto(tmp_path: Path) -> None:
    """End-to-end smoke: full run_once() with mocked pg_dump + moto S3.

    Verifies the encrypted object lands at the expected key, can be downloaded,
    decrypted with the matching age private key, extracted, and the recovered
    files match what we put in.
    """
    pub, priv = _gen_age_keypair()

    # Synthesize a small repo on disk so the archive step has real content.
    repo = tmp_path / "repo"
    (repo / "context" / "projects" / "p1").mkdir(parents=True)
    (repo / "context" / "projects" / "p1" / "decisions.md").write_text("kanban-959 decisions")
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "test.md").write_text("agent prompt")

    cfg = BackupConfig.from_env(_required_env(pub=pub, extras={"REPO_ROOT": str(repo)}))
    runner = BackupRunner(cfg)

    # Mock pg_dump. Padded above the L12 min-size threshold (#1120, 100KB
    # default) with a long comment block so the run_once smoke exercises the
    # full upload+prune path. The signal content the round-trip asserts on
    # ("CREATE TABLE projects") stays at the top.
    def fake_run(cmd, **kwargs):  # noqa: ANN001
        out_path = Path(cmd[cmd.index("-f") + 1])
        padding = "-- pad " + ("x" * 200 + "\n") * 600  # ~120KB of comments
        out_path.write_text(
            "-- pg_dump fake content\n"
            "CREATE TABLE projects (id int);\n"
            + padding
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with mock_aws():
        _make_bucket(cfg.s3_bucket)
        with patch("src.services.backup.subprocess.run", side_effect=fake_run):
            import asyncio
            result = asyncio.run(runner.run_once())

        assert result.ok is True, f"run_once failed: {result.error}"
        assert result.key is not None
        assert result.key.startswith("agent-teams/")
        assert result.key.endswith(".tar.gz.age")
        assert result.bytes_uploaded > 0

        # Round-trip: download + decrypt + extract.
        client = boto3.client("s3", region_name="us-east-1")
        ciphertext = client.get_object(
            Bucket=cfg.s3_bucket, Key=result.key,
        )["Body"].read()
        ident = pyrage.x25519.Identity.from_str(priv)
        plaintext = pyrage.decrypt(ciphertext, [ident])
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:gz") as tar:
            names = tar.getnames()
            assert "db-dump.sql" in names
            assert any("context/projects/p1/decisions.md" in n for n in names)
            assert any(".claude/agents/test.md" in n for n in names)
            # Verify the SQL content survived intact.
            sql_member = tar.getmember("db-dump.sql")
            sql_bytes = tar.extractfile(sql_member).read()
            assert b"CREATE TABLE projects" in sql_bytes
