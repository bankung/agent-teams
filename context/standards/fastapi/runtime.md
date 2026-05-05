# FastAPI — runtime conventions

**Scope:** how the app boots, where settings come from, what the liveness probe does, and how it's served. Routing-level rules live in `fastapi/routing.md`.

## Process

- **Dev:** `uvicorn src.main:app --host 0.0.0.0 --port 8456 --reload`. `--reload` is **dev only** — it watches the source tree and rebuilds workers on every file change. Adequate for one developer; useless and unsafe under concurrent prod traffic.
- **Prod (deferred until production phase):** drop `--reload`, run uvicorn workers under gunicorn (`gunicorn src.main:app -k uvicorn.workers.UvicornWorker -w <N>`). Not configured in this repo yet — flagged here so it's not forgotten.
- `--reload` reloads on **source change only**. It does **not** reload on `.env` edits — Pydantic settings are read once at startup. Restart the process / container after flipping env vars.

## App object

- **No app factory.** `app = FastAPI(...)` is module-level in `api/src/main.py` (the `create_app()` function exists but is called eagerly at import). Justification: single-tenant dogfood; no test harness needs to spin up alternate-config apps. Tests use the singleton via `httpx.AsyncClient(transport=ASGITransport(app=app))`. Revisit if multi-config tests appear.
- **Routers are mounted with `prefix="/api"` in `main.py`** — see `fastapi/routing.md`. The router files themselves carry only the resource segment (e.g., `prefix="/projects"`).

## /health endpoint

- **MUST NOT touch the DB.** `/health` is a liveness probe — it answers "is the process up?", not "can it serve traffic?". Coupling liveness to DB readiness causes cascading restarts: a DB hiccup flaps the orchestrator, which kills app pods that would otherwise have recovered when the DB returned. Returns `{"status": "ok", "env": <app_env>}`. See `api/src/main.py`.
- A separate `/ready` (DB ping) can be added when the deployment story needs it. Liveness ≠ readiness.

## Settings

- **`pydantic-settings` via `src.settings.get_settings()` (LRU-cached size=1).** Loaded once on first call from `.env` + environment. See `api/src/settings.py`.
- **Read at startup. Never re-read mid-request.** Re-reading defeats the cache and re-parses `.env` on every call. If a setting must be runtime-configurable, redesign it (DB-backed config row) — don't punch through `get_settings()`.
- Test/Alembic accessors call `get_settings()` directly — same singleton, same parse.

## Ports

- **API port is `8456`** (changed from `8000` on 2026-05-05 to avoid collisions with common Python webserver defaults). Same number host-side and container-side: compose maps `8456:8456`. See `context/standards/docker/compose.md` for the convention rationale.
- **Where to update on a port change** (single source of truth — keep this list current):
  1. `.env` and `.env.example` (`API_PORT=...`)
  2. `docker-compose.yml` (port mapping line + uvicorn `--port` in `command`)
  3. `api/Dockerfile` (`EXPOSE` + default `CMD --port`)
  4. `context/projects/agent-teams/shared/api-contracts.md` (Base URL)
  5. `context/projects/agent-teams/shared/decisions.md` (add a dated entry)
  6. `context/standards/fastapi/runtime.md` (this file — the literal port reference above)
  7. `README.md` and `CLAUDE.md` (any curl examples)
  8. `.claude/settings.json` (allowlisted curl entries)
  9. `api/src/templates/project_shared/api-contracts.md` (Base URL example for new projects)

  Frontend constants will join this list when Phase 3 lands.
