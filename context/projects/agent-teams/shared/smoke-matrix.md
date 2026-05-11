# Tier-1 smoke matrix — agent-teams

> **Project-specific Tier-1 config.** Lead is the only writer.
> **Cross-project methodology** (probe shape, decision matrix, POSITIVE+NEGATIVE rule, restoration discipline, output convention, worked example) lives in `context/teams/dev/smoke-methodology.md`. Read both — methodology defines the rules, this file defines the agent-teams specifics.

## Endpoints / hosts

- **API:** `http://localhost:8456` — FastAPI on the `api` service in `docker-compose.yml`.
- **Web:** `http://localhost:5431` — Next.js on the `web` service. **Symmetric:** host = container = 5431 (mirrors api 8456:8456). Host migration from 3000 → 5431 landed in Kanban #762 (2026-05-11); container-internal symmetry closed in Kanban #763 same day. `docker compose exec -T web wget http://localhost:5431` works identically to host `curl localhost:5431` — no inside-vs-outside gear-shift.
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
- `auto_run_consent_at = NULL` (post-Issue-3 cleanup; see `_scratch/cleanup-2026-05-09.sql`)

## X-Project-Id header convention (mandatory; Kanban #695, 2026-05-09)

All `/api/tasks*` endpoints (`GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks`, `PATCH /api/tasks/{id}`, `DELETE /api/tasks/{id}`) require `-H "X-Project-Id: <int>"`. Project endpoints (`/api/projects/*`, `/api/projects/{id}/grant-consent`) do NOT need the header — project IS the resource.

**Canonical seed value for agent-teams:** `X-Project-Id: 1`.

**Locked detail strings** (source-text-locked in `api/src/services/session_project.py`):
- Missing header: `"X-Project-Id header is required for task endpoints"`
- Resource mismatch (GET-by-id / PATCH / DELETE): `"task <task_id> does not belong to project_id <header_value>"`
- Body mismatch on POST: `"X-Project-Id header <header_value> does not match request body project_id <body_value>"` (header value FIRST, body value SECOND)

**Probe pattern for Tier-1 on task endpoints** — the canonical 3-leg shape after #695:
1. POSITIVE: correct header → 200/201 + expected shape
2. NEGATIVE: missing header → 400 + locked missing-header detail
3. NEGATIVE: wrong header → 400 + locked mismatch detail (with substituted ids byte-checked)

## Notable past escapes (cross-reference)

- **Kanban #76** — `updated_at` no-op skip, vacuous-shape M9 test escape. The canonical worked example in `context/teams/dev/smoke-methodology.md` "Worked example" section is this incident.
- **Kanban #81** — first Tier-2 dry-run; backfill report at `shared/backfill-2026-05-08.md`. Probe artifact discipline (the `_` prefix convention) was pinned here after `backfill-<ts>` folders polluted the working tree.
- **Kanban #120** — tasks-router `updated_at` parity bug, caught in the #81 backfill smoke matrix; led to `tasks_history` 'U' write on DELETE.
- **Kanban #483 / #690 / #695** — `auto_run_consent_at` consent-gate + cross-table validator + X-Project-Id header gate. Each shipped with Tier-1 smoke (8 / 3 / 5 probes respectively) verifying their wire contracts against live uvicorn. The 3-leg POSITIVE+missing+wrong probe pattern in the section above was crystallized by #695.
