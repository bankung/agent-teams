# Tier-1 smoke matrix — agent-teams

> **Project-specific Tier-1 config.** Lead is the only writer.
> **Cross-project methodology** (probe shape, decision matrix, POSITIVE+NEGATIVE rule, restoration discipline, output convention, worked example) lives in `context/teams/dev/smoke-methodology.md`. Read both — methodology defines the rules, this file defines the agent-teams specifics.

## Endpoints / hosts

- **API:** `http://localhost:8456` — FastAPI on the `api` service in `docker-compose.yml`.
- **Web:** `http://localhost:3000` — Next.js on the `web` service.
- **DB:** `agent_teams` on the `db` service. Direct probes are NOT Tier-1; use API endpoints.

## Project-specific Tier-1 trigger paths (overrides / additions)

The methodology default already covers `api/src/{routers,schemas,models,templates,main.py}/**`, `api/alembic/versions/**`, `api/scripts/**`, `docker-compose.yml`, env files. agent-teams adds:

- `api/src/services/project_scaffold.py` — auto-scaffold side-effect on `POST /api/projects` is Tier-1 critical (a bug here corrupts every new project's folder structure).
- `api/src/templates/project_shared/**` — scaffold template content; touched files copy into every new project's `shared/`. Probe by POSTing a throwaway `_smoke-<ts>` project, GET-listing the scaffolded files, DELETE-ing the row.

## Canonical seed values (for restoration discipline)

If a probe mutates the seeded `agent-teams` project (id=1), restore from `api/scripts/seed.py`. Key fields used in probes:

- `name = "agent-teams"`
- `description = "Self-hosted Kanban for managing dev team tasks (dogfood)"`
- `paths_web = "/repo/web"`, `paths_api = "/repo/api"`, `paths_db = "/repo/api/alembic/versions"`
- `team = "dev"`
- `is_active = true`

## Notable past escapes (cross-reference)

- **Kanban #76** — `updated_at` no-op skip, vacuous-shape M9 test escape. The canonical worked example in `context/teams/dev/smoke-methodology.md` "Worked example" section is this incident.
- **Kanban #81** — first Tier-2 dry-run; backfill report at `shared/backfill-2026-05-08.md`. Probe artifact discipline (the `_` prefix convention) was pinned here after `backfill-<ts>` folders polluted the working tree.
- **Kanban #120** — tasks-router `updated_at` parity bug, caught in the #81 backfill smoke matrix; led to `tasks_history` 'U' write on DELETE.
