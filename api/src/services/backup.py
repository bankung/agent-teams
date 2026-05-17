"""Off-site encrypted backup runner (Kanban #959).

Nightly job that snapshots:
- A full `pg_dump` of the agent_teams DB (schema + data, custom-fmt SQL).
- A tarball of `/repo/context/` (per-project shared state, decisions, agents'
  role notes — all the soft state that lives in git but isn't in the DB).
- A tarball of `/repo/.claude/` (agent prompts, hooks, teams, settings).

The three files are bundled into one `.tar.gz`, encrypted with age (single
public-key recipient — private key stays OFFLINE with the operator), and
uploaded to an S3-compatible bucket. A single `boto3` client covers AWS S3,
Backblaze B2, Cloudflare R2, and Wasabi via `endpoint_url` configuration.

Retention: keep the last N daily snapshots + the last-of-each-month for the
previous M months (defaults 30 daily + 12 monthly, env-configurable). Prune
runs at the end of each successful run.

The runner is wired into the existing `AsyncIOScheduler` in `src.main` via a
`CronTrigger`. When required env vars are unset, `BackupConfig.is_enabled`
returns False and `src.main` logs a WARNING + skips scheduling the job — the
service degrades cleanly when the operator has not yet provisioned a bucket.

Audit trail: stdout via the standard `src.*` logger pattern. No DB row per
backup — keep it simple; the log line + the object key + the destination
bucket's own audit trail are the audit surface.

Recovery: see `context/projects/agent-teams/shared/backup-recovery.md` for
the decrypt + restore drill.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Skip patterns excluded from the context/ + .claude/ tarballs. Caches, build
# artifacts, transient scratch — none of which would survive a restore anyway.
_TAR_SKIP_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".git",
    "_scratch",
}


class BackupConfig(BaseModel):
    """Env-driven runtime config for the backup runner.

    `is_enabled` is False whenever any REQUIRED field is empty/None so the
    scheduler can refuse to schedule cleanly in default deployments where
    nobody has provisioned a bucket yet.
    """

    # Required — runner refuses to run if any of these are blank.
    s3_bucket: str = Field(default="")
    s3_access_key_id: str = Field(default="")
    s3_secret_access_key: str = Field(default="")
    age_pubkey: str = Field(default="")

    # Optional — sensible defaults.
    s3_region: str = Field(default="us-east-1")
    s3_endpoint: str | None = Field(default=None)
    s3_prefix: str = Field(default="agent-teams/")
    cron_rule: str = Field(default="0 3 * * *")
    timezone: str = Field(default="UTC")
    keep_daily: int = Field(default=30, ge=1)
    keep_monthly: int = Field(default=12, ge=0)
    dry_run: bool = Field(default=False)

    # Where to read repo files from (`/repo` inside the api container).
    repo_root: Path = Field(default=Path("/repo"))

    # DB connection — defaults to whatever the api container already has.
    # The runner shells out to `pg_dump`, so we need a libpq-style URL. We
    # convert the SQLAlchemy asyncpg DSN (postgresql+asyncpg://...) to plain
    # `postgresql://...` if needed.
    database_url: str = Field(default="")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "BackupConfig":
        """Read all BACKUP_* vars + DATABASE_URL from the environment.

        Kanban #1113 (2026-05-17, L8 defense-in-depth) — when DATABASE_URL is
        populated AND its db name is not in DB_NAME_ALLOWLIST, refuse to
        construct the config. A backup of a rogue DB would corrupt the backup
        history (encrypted snapshots of the wrong data overwriting the daily
        retention slot). See incident
        context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.
        """
        e = env if env is not None else os.environ
        db_url = e.get("DATABASE_URL", "")
        if db_url:
            from sqlalchemy.engine.url import make_url
            db_name = make_url(db_url).database or ""
            raw_allow = e.get(
                "DB_NAME_ALLOWLIST", "agent_teams,agent_teams_test",
            )
            allowed = {
                part for part in raw_allow.replace(" ", "").split(",") if part
            }
            if db_name not in allowed:
                raise RuntimeError(
                    f"BackupRunner: DATABASE_URL db {db_name!r} not in allowlist "
                    f"{sorted(allowed)}. Refusing to construct config — backup of "
                    "a rogue DB would corrupt the backup history. To add a new "
                    "allowed DB set DB_NAME_ALLOWLIST env (csv). See "
                    "context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md."
                )
        return cls(
            s3_bucket=e.get("BACKUP_S3_BUCKET", ""),
            s3_access_key_id=e.get("BACKUP_S3_ACCESS_KEY_ID", ""),
            s3_secret_access_key=e.get("BACKUP_S3_SECRET_ACCESS_KEY", ""),
            age_pubkey=e.get("BACKUP_AGE_PUBKEY", ""),
            s3_region=e.get("BACKUP_S3_REGION", "us-east-1"),
            s3_endpoint=(e.get("BACKUP_S3_ENDPOINT") or None),
            s3_prefix=_normalize_prefix(e.get("BACKUP_S3_PREFIX", "agent-teams/")),
            cron_rule=e.get("BACKUP_CRON_RULE", "0 3 * * *"),
            timezone=e.get("BACKUP_TIMEZONE", "UTC"),
            keep_daily=int(e.get("BACKUP_KEEP_DAILY", "30")),
            keep_monthly=int(e.get("BACKUP_KEEP_MONTHLY", "12")),
            dry_run=e.get("BACKUP_DRY_RUN", "false").lower() in ("1", "true", "yes"),
            repo_root=Path(e.get("REPO_ROOT", "/repo")),
            database_url=db_url,
        )

    @property
    def is_enabled(self) -> bool:
        """All four required fields populated?"""
        return bool(
            self.s3_bucket
            and self.s3_access_key_id
            and self.s3_secret_access_key
            and self.age_pubkey
        )


def _normalize_prefix(p: str) -> str:
    """Ensure the prefix ends with `/` and never starts with `/`."""
    p = p.lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def _pg_dump_url(sa_url: str) -> str:
    """Convert SQLAlchemy DSN to libpq URL.

    `postgresql+asyncpg://u:p@h:port/db` -> `postgresql://u:p@h:port/db`.
    """
    if not sa_url:
        return ""
    if "+" in sa_url.split("://", 1)[0]:
        scheme, rest = sa_url.split("://", 1)
        scheme = scheme.split("+", 1)[0]
        return f"{scheme}://{rest}"
    return sa_url


@dataclass
class BackupResult:
    """Summary of a single run_once() invocation."""

    ok: bool
    key: str | None
    bytes_uploaded: int
    pruned: int
    error: str | None = None


class BackupRunner:
    """Orchestrates the dump -> archive -> encrypt -> upload -> prune cycle.

    One instance lives for the lifetime of the api container. APScheduler
    invokes `run_once()` on its cron schedule. The method is idempotent at the
    object-key level (timestamp ensures uniqueness) and exception-safe — any
    failure logs + returns a failure result without crashing the scheduler.
    """

    def __init__(self, cfg: BackupConfig):
        self.cfg = cfg

    # -- Public entrypoint -------------------------------------------------

    async def run_once(self) -> BackupResult:
        """Run the full backup cycle. Safe to call from a scheduler thread.

        The synchronous body runs in a thread executor so we don't block the
        event loop on pg_dump / tarball / upload IO. Caller (the scheduler)
        receives a result envelope.
        """
        if not self.cfg.is_enabled:
            logger.warning("backup.run_once: disabled (missing required env)")
            return BackupResult(ok=False, key=None, bytes_uploaded=0, pruned=0,
                                error="disabled")
        return await asyncio.to_thread(self._run_once_sync)

    # -- Internals ---------------------------------------------------------

    def _run_once_sync(self) -> BackupResult:
        """Synchronous core. Runs inside a thread executor."""
        now = datetime.now(timezone.utc)
        key = self._make_key(now)
        logger.info(
            "backup.start key=%s bucket=%s dry_run=%s",
            key, self.cfg.s3_bucket, self.cfg.dry_run,
        )

        import tempfile
        try:
            with tempfile.TemporaryDirectory(prefix="agent-teams-backup-") as work_dir_s:
                work_dir = Path(work_dir_s)
                logger.info("backup.stage=dump start")
                db_dump = self._dump_db_to_tarball(work_dir)
                logger.info("backup.stage=dump done size=%d bytes", db_dump.stat().st_size)

                logger.info("backup.stage=archive start")
                tarball = self._archive_filesystem(work_dir, db_dump)
                logger.info("backup.stage=archive done size=%d bytes", tarball.stat().st_size)

                logger.info("backup.stage=encrypt start")
                encrypted = self._encrypt(tarball, work_dir)
                size = encrypted.stat().st_size
                logger.info("backup.stage=encrypt done size=%d bytes", size)

                logger.info("backup.stage=upload start key=%s", key)
                self._upload(encrypted, key)
                logger.info("backup.stage=upload done size=%d bytes", size)

            logger.info("backup.stage=prune start")
            pruned = self._prune()
            logger.info("backup.stage=prune done deleted=%d objects", pruned)

            logger.info(
                "backup.summary ok=True key=%s bytes=%d pruned=%d",
                key, size, pruned,
            )
            return BackupResult(ok=True, key=key, bytes_uploaded=size, pruned=pruned)
        except Exception as exc:
            logger.exception("backup.summary ok=False key=%s error=%s", key, exc)
            return BackupResult(
                ok=False, key=key, bytes_uploaded=0, pruned=0, error=str(exc),
            )

    def _make_key(self, now: datetime) -> str:
        """Object-key shape: `<prefix>[_dryrun/]<YYYY-MM-DD>/backup-<TS>.tar.gz.age`."""
        date = now.strftime("%Y-%m-%d")
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        sub = "_dryrun/" if self.cfg.dry_run else ""
        return f"{self.cfg.s3_prefix}{sub}{date}/backup-{ts}.tar.gz.age"

    def _dump_db_to_tarball(self, work_dir: Path) -> Path:
        """Run pg_dump, write the SQL to `work_dir/db-dump.sql`.

        Uses the plain-SQL format so the restore path is `psql ... < db-dump.sql`
        (no need for pg_restore tooling). Custom format would be smaller but
        adds a tool dependency for recovery.
        """
        url = _pg_dump_url(self.cfg.database_url)
        if not url:
            raise RuntimeError("backup._dump_db: DATABASE_URL is empty")

        out = work_dir / "db-dump.sql"
        # --no-owner / --no-privileges so the dump replays cleanly into a
        # fresh DB without needing the same role grants. --clean adds DROP
        # statements so a re-restore over an existing DB doesn't conflict.
        cmd = [
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            "--clean",
            "--if-exists",
            "-f", str(out),
            url,
        ]
        # Don't leak the URL into normal logs (it contains the password).
        logger.info("backup._dump_db: pg_dump -> %s", out)
        result = subprocess.run(  # noqa: S603 — controlled args
            cmd, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pg_dump failed (rc={result.returncode}): {result.stderr[-500:]}"
            )
        return out

    def _archive_filesystem(self, work_dir: Path, db_dump: Path) -> Path:
        """Bundle db_dump + context/ + .claude/ into a gzip tarball.

        Skips caches / build artifacts / VCS metadata via `_TAR_SKIP_NAMES`.
        """
        tarball = work_dir / "backup.tar.gz"

        def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            # Drop any path whose components match the skip set.
            for part in Path(info.name).parts:
                if part in _TAR_SKIP_NAMES:
                    return None
            return info

        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(db_dump, arcname="db-dump.sql")
            ctx = self.cfg.repo_root / "context"
            if ctx.exists():
                tar.add(ctx, arcname="context", filter=_filter)
            else:
                logger.warning("backup._archive: %s does not exist — skipped", ctx)
            claude = self.cfg.repo_root / ".claude"
            if claude.exists():
                tar.add(claude, arcname=".claude", filter=_filter)
            else:
                logger.warning("backup._archive: %s does not exist — skipped", claude)
        return tarball

    def _encrypt(self, tarball: Path, work_dir: Path) -> Path:
        """Encrypt the tarball with age (single recipient) -> `.tar.gz.age`."""
        import pyrage

        recipient = pyrage.x25519.Recipient.from_str(self.cfg.age_pubkey.strip())
        encrypted = work_dir / "backup.tar.gz.age"
        plaintext = tarball.read_bytes()
        ciphertext = pyrage.encrypt(plaintext, [recipient])
        encrypted.write_bytes(ciphertext)
        # Best-effort: scrub the plaintext tarball from disk now that we have
        # the encrypted copy. The temp dir gets nuked by the context manager
        # anyway, but minimizing the window helps.
        try:
            tarball.unlink()
        except OSError:
            pass
        return encrypted

    def _s3_client(self):
        """Lazy boto3 client construction — keeps the import out of cold start."""
        import boto3
        kwargs = {
            "aws_access_key_id": self.cfg.s3_access_key_id,
            "aws_secret_access_key": self.cfg.s3_secret_access_key,
            "region_name": self.cfg.s3_region,
        }
        if self.cfg.s3_endpoint:
            kwargs["endpoint_url"] = self.cfg.s3_endpoint
        return boto3.client("s3", **kwargs)

    def _upload(self, encrypted: Path, key: str) -> None:
        """Single-part upload (typical backup is < 1 GB)."""
        client = self._s3_client()
        with encrypted.open("rb") as fh:
            client.put_object(Bucket=self.cfg.s3_bucket, Key=key, Body=fh.read())

    def _prune(self) -> int:
        """Delete objects under prefix that are outside the retention window.

        Retention set:
            - the LATEST `keep_daily` distinct calendar dates seen in the prefix.
            - PLUS the latest object from each of the previous `keep_monthly`
              distinct calendar months.

        Anything not in the union of those two sets gets deleted. Returns the
        number of objects deleted.

        Dry-run: lists + logs the planned deletions but does NOT call delete.
        """
        client = self._s3_client()
        # We prune the canonical prefix only — _dryrun/ snapshots are NOT
        # pruned (let the operator clean them up by hand if desired). Listing
        # under self.cfg.s3_prefix would include the _dryrun/ subtree which
        # is why we list specifically without the dryrun suffix.
        prefix = self.cfg.s3_prefix
        keys: list[tuple[str, datetime]] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.cfg.s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                # Skip dry-run objects from retention math.
                if k.startswith(prefix + "_dryrun/"):
                    continue
                dt = _parse_key_timestamp(k)
                if dt is None:
                    # Unrecognized key shape — leave it alone.
                    continue
                keys.append((k, dt))

        keep = _compute_retention_set(
            keys, self.cfg.keep_daily, self.cfg.keep_monthly,
        )
        to_delete = [k for (k, _) in keys if k not in keep]

        if not to_delete:
            return 0

        if self.cfg.dry_run:
            for k in to_delete:
                logger.info("backup._prune dry_run would_delete=%s", k)
            return 0

        # delete_objects accepts up to 1000 keys per request.
        deleted = 0
        for chunk in _chunk(to_delete, 1000):
            resp = client.delete_objects(
                Bucket=self.cfg.s3_bucket,
                Delete={"Objects": [{"Key": k} for k in chunk]},
            )
            for d in resp.get("Deleted", []):
                logger.info("backup._prune deleted=%s", d.get("Key"))
                deleted += 1
            for err in resp.get("Errors", []):
                logger.warning(
                    "backup._prune delete_error key=%s code=%s msg=%s",
                    err.get("Key"), err.get("Code"), err.get("Message"),
                )
        return deleted


# -- Free helpers -----------------------------------------------------------


def _chunk(seq: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _parse_key_timestamp(key: str) -> datetime | None:
    """Recover the UTC timestamp from a `...backup-<YYYYMMDDTHHMMSSZ>.tar.gz.age` key.

    Returns None if the key doesn't match the expected shape (so foreign
    objects under the same prefix are left alone — never deleted by the
    pruner).
    """
    # Look for the canonical suffix.
    name = key.rsplit("/", 1)[-1]
    if not name.startswith("backup-") or not name.endswith(".tar.gz.age"):
        return None
    ts = name[len("backup-") : -len(".tar.gz.age")]
    try:
        return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _compute_retention_set(
    keys: list[tuple[str, datetime]], keep_daily: int, keep_monthly: int,
) -> set[str]:
    """Return the set of object keys to KEEP.

    Math:
      - For each distinct calendar date present, pick the LATEST object of
        that date. The most recent `keep_daily` such dates form the "daily"
        retention set.
      - For each distinct calendar month present, pick the LATEST object of
        that month. The most recent `keep_monthly` such months form the
        "monthly" retention set.
      - Return the UNION (a key in both sets counts once).

    A "date" here is the UTC calendar date of the key's timestamp. "Month"
    is (year, month). Sort is by timestamp DESC so "latest" is well-defined.
    """
    if not keys:
        return set()

    # Sort by timestamp DESC.
    keys_sorted = sorted(keys, key=lambda kv: kv[1], reverse=True)

    # Daily: walk DESC, take the first object of each calendar date until we
    # have `keep_daily` distinct dates.
    daily_keep: set[str] = set()
    seen_dates: set[tuple[int, int, int]] = set()
    for k, dt in keys_sorted:
        date_tuple = (dt.year, dt.month, dt.day)
        if date_tuple in seen_dates:
            continue
        seen_dates.add(date_tuple)
        daily_keep.add(k)
        if len(seen_dates) >= keep_daily:
            break

    # Monthly: walk DESC, take the first object of each calendar month.
    monthly_keep: set[str] = set()
    seen_months: set[tuple[int, int]] = set()
    if keep_monthly > 0:
        for k, dt in keys_sorted:
            month_tuple = (dt.year, dt.month)
            if month_tuple in seen_months:
                continue
            seen_months.add(month_tuple)
            monthly_keep.add(k)
            if len(seen_months) >= keep_monthly:
                break

    return daily_keep | monthly_keep
