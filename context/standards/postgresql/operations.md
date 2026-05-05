# PostgreSQL — Operational conventions

**Scope:** how the project runs Postgres in dev (compose service, healthcheck, bring-up, reset) and the policy gaps we are knowingly carrying. Applies to anyone running `docker compose` against this repo or wiring a similar setup in a sibling project.

## Compose service

- **Image:** `postgres:16-alpine` named `agent-teams-db`. Pin the major version (16) — bumping requires a deliberate `pg_dump`/`pg_restore` plan because the named volume's data files are version-tied.
- **Healthcheck:** `pg_isready -U postgres -d agent_teams` (no creds needed; `pg_isready` connects via the local unix socket inside the container). Interval 5s, retries 5. See `docker-compose.yml` `db.healthcheck`.
- **Dependents wait on health, not start:** `depends_on: db: condition: service_healthy` (NOT `service_started`). `service_started` returns immediately after `docker run` and Postgres is not ready to accept connections at that point — the api would crash-loop on first boot.

## Persistence

- **Named volume:** `agent-teams-pgdata` mounted at `/var/lib/postgresql/data`. Named (not bind-mount) so it survives `docker compose down` and isn't tangled in host filesystem permissions.
- **Reset (DEV ONLY):** `docker compose down -v` — the `-v` flag wipes the named volume. Never run on shared/staging/prod environments. There is no production hardening yet (see "Gaps").

## Bring-up sequence

```
cp .env.example .env
docker compose up --build
# in a second shell, once api logs show "Application startup complete":
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed
```

The migration step is intentionally manual — keeps schema changes visible in the operator log rather than buried inside container start. Seed populates the active project row so `GET /api/projects/active` returns 200 immediately.

## DATABASE_URL split

Two distinct DSN values target the same database from different network locations:

- **Inside compose** (api container → db container): host is the service name `db`. Set in the compose `api.environment.DATABASE_URL` block, NOT inherited from `.env`. See `docker-compose.yml`.
- **From the host** (running `cd api && uvicorn ... --reload` directly): host is `localhost:${POSTGRES_PORT:-5432}`. This is what `.env`'s `DATABASE_URL` is for; it is ignored when running inside compose.

Mixing them up produces a confusing "could not translate host name" error from asyncpg — check which side you are running on first.

## Hard DELETE policy

Per the project soft-delete policy, application code never issues `DELETE`. Hard DELETE is reserved for **manual operator cleanup via psql** — typically removing a row created in error before any downstream consumer saw it. When you do this, the audit trigger records `operation = 'D'` so the action is still traceable. See `postgresql/audit-trail.md` for the operation-code semantics.

## Gaps (known, deferred)

The current setup is **dev-grade only**:

- No backup / restore strategy (no `pg_dump` cron, no PITR/WAL archiving).
- No replication or HA.
- No SSL — connections are plaintext on the loopback / compose network.
- No role-based access control beyond the default `postgres` superuser.
- Default password lives in `.env.example` (`postgres`) — fine for dogfood, unacceptable elsewhere.

Document this here so future-us doesn't deploy this compose stack as-is. Hardening lands in a separate Phase before any non-local environment.
