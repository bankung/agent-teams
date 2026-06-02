# Off-site backup — recovery drill (Kanban #959)

Step-by-step playbook for restoring agent-teams from an encrypted nightly
snapshot. Tested with the `pyrage` (Rust `age`) encryption and the `boto3`
upload path the runner uses in production.

## What's in a snapshot

Each `.tar.gz.age` object contains:

- `db-dump.sql` — full `pg_dump` of the `agent_teams` Postgres database
  (plain SQL with `--clean --if-exists --no-owner --no-privileges`; restores
  via `psql` without role-grant gymnastics).
- `context/` — every per-project `shared/` + role state directory.
- `.claude/` — agent prompts, hooks, teams, settings.

Build artifacts and caches (`__pycache__`, `node_modules`, `.git`, `_scratch`,
etc.) are excluded by the runner's tarball filter.

## Prerequisites

1. The age **private** key (the one matching `BACKUP_AGE_PUBKEY`). It should
   NOT live anywhere on the host or in git — keep it on a hardware token, a
   password manager, or a separate offline drive. If the private key is lost,
   the backup is unrecoverable.
2. `age` CLI installed locally (`apt install age` / `brew install age` / Rust
   port via `cargo install rage` — `rage -d` accepts the same files).
3. `postgresql-client` (provides `psql`).
4. AWS CLI or `boto3` for downloading from the bucket (or `rclone` / vendor
   CLI for B2/R2/Wasabi).

## 1. Download the encrypted snapshot

Using AWS S3:

```bash
aws s3 cp \
  s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}YYYY-MM-DD/backup-YYYYMMDDTHHMMSSZ.tar.gz.age \
  ./backup.tar.gz.age
```

For Backblaze B2 / Cloudflare R2 / Wasabi, set `--endpoint-url` to match
whatever `BACKUP_S3_ENDPOINT` the runner used (see container env).

## 2. Decrypt with the age private key

```bash
age -d -i ~/path/to/private-key.txt backup.tar.gz.age > backup.tar.gz
```

Or via `rage`:

```bash
rage -d -i ~/path/to/private-key.txt backup.tar.gz.age > backup.tar.gz
```

If decryption fails: confirm the private key matches the `BACKUP_AGE_PUBKEY`
the runner was using at the time of the snapshot. Public + private must be a
matched pair.

## 3. Extract

```bash
tar -xzf backup.tar.gz
```

Produces:

```
db-dump.sql
context/
.claude/
```

## 4. Restore the DB

Stand up an empty Postgres + create the DB:

```bash
docker compose up -d db
docker compose exec -T db psql -U postgres -c "CREATE DATABASE agent_teams;"
docker compose exec -T db psql -U postgres -d agent_teams < db-dump.sql
```

The dump includes `DROP TABLE ... IF EXISTS` clauses (`--clean --if-exists`),
so re-running over an existing DB is also safe — though restoring into a
fresh DB is the cleanest path.

## 5. Place `context/` + `.claude/`

Copy the extracted directories over a fresh `agent-teams` checkout, replacing
the in-repo defaults. Both directories are tracked in git in normal
operation, but the snapshot is the source of truth at the moment of capture
(captures the user's per-project decisions / role state / agent
customizations).

```bash
rm -rf /path/to/agent-teams/context /path/to/agent-teams/.claude
cp -r context .claude /path/to/agent-teams/
```

## 6. Verify

```bash
docker compose -p agent-teams up -d
curl http://localhost:8456/api/projects/by-name/agent-teams
```

Expected: 200 with the project metadata (id, team, status). If the DB
restored cleanly the task counts should match the snapshot moment.

For a deeper smoke, list a handful of tasks and confirm the audit history
came along:

```bash
curl -H "X-Project-Id: 1" "http://localhost:8456/api/tasks?limit=5"
```

## Caveats

- **Full snapshot, no PITR.** Granularity is whatever the cron rule sets
  (defaults to 03:00 UTC daily). Loss window = at most 24 hours.
- **LangGraph checkpoints come along.** They live in the `langgraph` schema
  inside the same DB, so the `pg_dump` includes them.
- **Foreign objects in the bucket prefix are NOT pruned.** The retention
  selector only deletes keys matching `backup-<TS>.tar.gz.age` — anything
  else under the prefix stays put.
- **`_dryrun/` snapshots are not pruned.** Clean them up by hand when you're
  done verifying the runner.
- **Image rebuild required.** The api Dockerfile installs `postgresql-client`
  so `pg_dump` is on the PATH. A running container without that package
  cannot run the backup; rebuild with `docker compose -p agent-teams build api`.

## Operator setup checklist

When provisioning the backup target for the first time:

1. Generate an age keypair: `age-keygen -o backup-key.txt` (the file contains
   the SECRET key; the public recipient is in a comment at the top, prefix
   `age1...`).
2. Move `backup-key.txt` OFF the host — hardware token, password manager,
   etc. Verify it's not in git.
3. Create the bucket. For B2/R2/Wasabi, also note the endpoint URL.
4. Generate access keys with **write-only-no-delete** scope if the backend
   supports it (defense in depth — if a host is compromised, the attacker
   cannot wipe historical backups). The runner only needs `PutObject`,
   `ListBucket`, `DeleteObject` (delete is used for retention). If you split
   the keys: one write-only key for the runner, one separate
   read+delete key for the retention pruner.
5. Populate the api container env (`docker-compose.yml` or `.env`):
   ```
   BACKUP_S3_BUCKET=...
   BACKUP_S3_ACCESS_KEY_ID=...
   BACKUP_S3_SECRET_ACCESS_KEY=...
   BACKUP_AGE_PUBKEY=age1...   # public part only
   BACKUP_S3_ENDPOINT=...      # optional — set for B2/R2/Wasabi
   BACKUP_S3_REGION=us-east-1  # default; backend-dependent
   BACKUP_CRON_RULE=0 3 * * *  # default 03:00 daily
   BACKUP_TIMEZONE=UTC         # default
   BACKUP_KEEP_DAILY=30
   BACKUP_KEEP_MONTHLY=12
   BACKUP_DRY_RUN=true         # set true for the first few runs to verify
   ```
6. Restart the api container; verify the lifespan log shows
   "backup scheduled: ...".
7. Wait 24 hours (or temporarily lower the cron); verify the object lands in
   the bucket under `<prefix>/_dryrun/<YYYY-MM-DD>/...`.
8. Run THIS recovery drill against the dry-run object end-to-end. If it
   passes, unset `BACKUP_DRY_RUN` and restart.
9. Schedule a quarterly recovery-drill reminder. A backup you've never
   restored from is not a backup.

## Startup catchup (Kanban #1474, 2026-06-02)

### What it does

On every api container start, if backup is enabled and a prior canonical backup
exists in the bucket, the runner checks the age of the most-recent backup. If it
is older than the threshold, it fires one immediate `run_once()` before the
scheduler's first cron fire. This covers the "desktop was OFF during the cron
window" gap that produced the 2026-05-20/21/23 missing snapshots (the desktop is
not a server — APScheduler with `coalesce=True` silently discards fires that were
never observed because the container was down).

The default cron was also moved from `0 3 * * *` (03:00 UTC = 10:00 ICT) to
`0 14 * * *` (14:00 UTC = 21:00 ICT, evening Bangkok) — a consistently
high-uptime window for the desktop.

### Configuration

| Env var | Default | Description |
|---|---|---|
| `BACKUP_CRON_RULE` | `0 14 * * *` | Cron schedule (was `0 3 * * *` before 2026-06-02). |
| `BACKUP_CATCHUP_MAX_AGE_HOURS` | `24` | Age threshold (hours); a latest backup older than this triggers a catchup on startup. |

### No-op cases (by design)

- Backup disabled (any required env var missing) → no catchup, no S3 call.
- No prior canonical backup found → no catchup; the first snapshot is produced
  by the cron schedule (avoids spam on a fresh deploy).
- Latest backup younger than the threshold → no catchup, info log only.

### Troubleshooting

- **"backup catchup task scheduled" then no run** → latest backup is within the
  window; normal. Look for `backup.catchup: latest backup is X.Xh old …`.
- **"triggering immediate catchup run"** → catchup fired; a new S3 key should
  land within minutes, followed by `backup.summary ok=True`.
- **Activation** → the reschedule + catchup take effect on the next api restart
  (the cron is registered at startup; the catchup runs on lifespan enter).
