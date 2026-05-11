# Docker Compose — project conventions

**Scope:** how this repo's `docker-compose.yml` is structured for dev. Operational PG specifics (healthcheck, named volume, bring-up sequence, DATABASE_URL split) live in `postgresql/operations.md` — cross-reference, don't restate.

## Service organization

- **One service per process.** `db`, `api`, future `web` — never run two daemons in one container.
- **`agent-teams-` prefix on every container.** Set `container_name: agent-teams-<service>` explicitly (`agent-teams-db`, `agent-teams-api`, `agent-teams-web`). Don't rely on the auto-generated `<project>_<service>_1` form — explicit names make `docker exec`, `docker logs`, and operator scripts portable across hosts where the compose project name differs.

## Env handling

- **`${VAR:-default}` for every host-controllable knob** (port, password, env flag). Defaults make `docker compose up` work out of the box; users override via `.env`.
- **`.env` is gitignored; `.env.example` is committed** as the canonical list of knobs with safe defaults and inline comments.
- The same `.env` is consumed by **both** compose substitution AND pydantic-settings inside the FastAPI app on the host — keep it the single source of truth for local config.

## `depends_on` — wait on health, never on start

```yaml
depends_on:
  db:
    condition: service_healthy
```

Never `condition: service_started`. `service_started` returns immediately after `docker run`; the dependent boots while Postgres is still initializing and crash-loops on first connect. Cross-ref `postgresql/operations.md`.

## Healthcheck convention

- **Every long-running service must have one.** No exceptions — dependents rely on `service_healthy` to gate startup, and a missing healthcheck silently degrades that contract to `service_started`, which boots through PG init and crash-loops on first connect.
- `db` uses `pg_isready -U postgres -d agent_teams` over the local unix socket inside the container — no creds passed on the command line. See `docker-compose.yml:26-30`.
- `api` uses `curl -fsS http://localhost:8456/health` against the FastAPI liveness endpoint. `curl` is installed in `api/Dockerfile` specifically for this check; `start_period: 10s` gives uvicorn time to import + bind before failures count. See `docker-compose.yml:58-66`.
- `/health` is a liveness probe — no DB touch. See `standards/fastapi/runtime.md`.
- New services (Phase 3 `web`, future workers) inherit this rule on day one — land the service and its healthcheck in the same change.

## Port mapping — N:N convention

**Always publish `host:container` with the same port number.**

```yaml
ports:
  - "${API_PORT:-8456}:8456"
  - "${POSTGRES_PORT:-5432}:5432"
```

See `docker-compose.yml:23` and `docker-compose.yml:51`. The Dockerfile's `EXPOSE` and `CMD --port` must match (`api/Dockerfile:28` `EXPOSE 8456`, `api/Dockerfile:32` `--port 8456`).

**Why:** one number means logs, in-container healthchecks, code references (`http://localhost:8456`), reverse-proxy configs, and operator muscle memory all use the same value — no NAT-style translation to keep in your head. Asymmetric mappings (`8456:8000`) are reserved for the rare case where an upstream image you don't control hardcodes a port; not applicable to anything we build.

## Bind-mount strategy — current

```yaml
volumes:
  - .:/repo
```

The whole repo is bind-mounted read-write at `/repo`. `WORKDIR` is `/repo/api`. **Broad on purpose for MVP:**

- The project auto-scaffold (`POST /api/projects`) writes `context/projects/<name>/` from inside the container — needs `/repo/context` to be writable.
- `uvicorn --reload` watches the api source — needs `/repo/api/src` to mirror host edits live.
- In-container tests read `pytest.ini`, fixtures, etc. — needs `/repo/api` mounted, not just installed.

A narrower mount (e.g., `./api:/repo/api` + `./context:/repo/context`) is the future shape but adds list-maintenance overhead (every new top-level path needs a mount entry) without solving a real risk in single-developer dogfood.

## Bind-mount narrowing — when to revisit

Trigger conditions to switch to a narrower mount list:

- **Before any non-local environment.** Staging or shared dev = narrow first.
- **Before adding sensitive files outside `./api` and `./context`.** Today the only files we don't want exposed (`.env`) are at the repo root and the container reads its own `DATABASE_URL` from compose `environment` instead, so practical risk is low. New top-level secrets-bearing folders flip this.
- **Before a second developer joins** who might commit secrets to other folders by mistake.

The change itself is a 1-line edit to `docker-compose.yml` + `docker compose down && up -d --build`. Risk is low; the trap is forgetting to do it as the project grows.

## Named volumes for stateful data

```yaml
volumes:
  agent-teams-pgdata:
```

`db` mounts `agent-teams-pgdata:/var/lib/postgresql/data`. **Never bind-mount Postgres data directories** — host filesystem permission models and case-sensitivity (Windows!) break PG's assumptions. Named volumes live in Docker-managed storage and are immune.

## Reset (DEV ONLY)

`docker compose down -v` wipes named volumes including `agent-teams-pgdata`. Cross-ref `postgresql/operations.md` for the full bring-up / reset sequence.

## DATABASE_URL split

The api service reads `DATABASE_URL` from its compose `environment` block (`host=db`, the service name on the compose network); the host-side `uvicorn` reads `.env`'s `DATABASE_URL` (`host=localhost:${POSTGRES_PORT:-5432}`). Two DSNs, same database. Full explanation: `postgresql/operations.md`.

## Phase 3 web service

A commented placeholder block lives in `docker-compose.yml:59-73`. When uncommented, the wiring is:

```yaml
web:
  build:
    context: ./web
    dockerfile: Dockerfile
  container_name: agent-teams-web
  depends_on:
    api:
      condition: service_healthy
  environment:
    NEXT_PUBLIC_API_URL: http://api:8456
  ports:
    - "${WEB_PORT:-5431}:3000"
```

Notes:
- `http://api:8456` uses **service-name DNS inside the compose network** — `api` resolves to the api container, not `localhost`.
- `${WEB_PORT:-5431}:3000` follows the N:N rule. Host-side default is a **project-scoped port** (5431 for agent-teams, mirroring api=8456) rather than the Next.js stack default of 3000 — avoids collision when multiple projects run side-by-side on the same workstation. Container-side stays 3000 because the Dockerfile EXPOSE + `next dev -p 3000` are framework-native and stack-invariant. **Pick a project-scoped host port at scaffolding time;** retrofitting later is cheap but tedious (Kanban #762 was the agent-teams retrofit).
- `condition: service_healthy` works against the api `/health` healthcheck (already wired — see Healthcheck convention).
- `web` itself must ship with its own healthcheck on day one (per Healthcheck convention).

## No production hardening yet

The current compose stack is **dev-grade only** — multi-stage Dockerfile, non-root user, dropping `--reload`, gunicorn workers, secret management, image scanning are all deferred. Cross-ref `postgresql/operations.md` Gaps for the DB-side equivalents. **Don't deploy this stack as-is.** Hardening lands in a separate Phase before any non-local environment.
