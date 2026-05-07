# Dev Team Orchestrator

A multi-agent dev team + **self-hosted Kanban** for the **Next.js + FastAPI + PostgreSQL** stack. One **Lead** spawns **specialist subagents** on demand through Claude Code's subagent system (no tmux).

Instead of driving the AI step by step, you create tasks in the Kanban UI or hand them to Lead in plain language. Lead analyzes the task → spawns the role-specific agents needed → integrates the results → reports back. Agents are ephemeral (spawn-per-task, terminate when done) — important state is persisted separately.

**Multi-project ready** — the Kanban UI manages every project (paths, stack, standards mapping). Lead keeps per-project knowledge isolated while sharing **cross-project standards** that all projects can pull from.

## Storage architecture (three buckets)

| Bucket | Storage | Examples | Writer |
|---|---|---|---|
| **1. Project config + tasks** | PostgreSQL DB | name, paths, stack, standards mapping; Kanban tasks (status/priority/role) | UI via Kanban + Lead via API |
| **2. Cross-project standards** | MD files (`context/standards/<framework>/`) | coding conventions, Kanban schema codes | humans only |
| **3. Per-project knowledge** | MD files (`context/projects/<p>/`) | decisions, api-contracts, db-schema, role state | Lead writes shared/, role writes own folder |

## Architecture

```
                    ┌─────────────┐
                    │    User     │
                    └──────┬──────┘
                           │ (1) talks to Lead / (2) creates task in Kanban UI
              ┌────────────┼────────────┐
              │                         │
         ┌────▼─────┐          ┌────────▼────────┐
         │   Lead   │◄─curl────│  Kanban UI      │
         │          │          │  (Next.js)      │
         └────┬─────┘          └────────┬────────┘
              │                         │
              │                  REST API│
              │                         ▼
              │                ┌────────────────┐
              │                │   FastAPI      │
              │                │   (api/)       │
              │                └────────┬───────┘
              │                         │
              │                         ▼
              │                ┌────────────────┐
              │                │  PostgreSQL    │  ← Bucket 1
              │                │  projects      │
              │                │  tasks         │
              │                │  tasks_history │
              │                └────────────────┘
              │
              │ Agent tool (subagent_type)
       ┌──────┼──────┬───────┬──────────┐
       │      │      │       │          │
   ┌───▼──┬───▼──┬───▼───┬──▼──┬────────▼────┐
   │front │back  │devops │ qa  │  reviewer   │
   │ end  │ end  │       │     │ (read-only) │
   └───┬──┴───┬──┴───┬───┴──┬──┴──────┬──────┘
       │      │      │      │         │
       └──────┴──────┼──────┴─────────┘
                     │
              ┌──────▼─────────────────────────┐
              │  context/                      │
              │  ├── standards/  ← Bucket 2    │
              │  └── projects/<p>/  ← Bucket 3 │
              └────────────────────────────────┘
```

- The user drives Lead through Claude Code (CLI / IDE / Web) **or** creates tasks in the Kanban UI.
- Lead resolves the active project from the **API** (`GET /api/projects/active`) — there is no `projects.json`.
- Lead **does not edit code itself** — it delegates to subagents through the `Agent` tool.
- Subagents do the work → write state back to their own `context/projects/<active>/<role>/` → return a summary → terminate.
- Cross-role decisions / API contracts / DB schema live in `context/projects/<active>/shared/` (Lead writes only) — **per-project**.
- Cross-project coding conventions + Kanban schema codes live in `context/standards/<framework>/` — **humans only**.

## Why no tmux?

The earlier design used tmux panes so multiple agents could run side by side — but Claude Code has a built-in subagent system (`Agent` tool + `subagent_type`) that spawns parallel jobs without screen-watching, paste-buffer issues, or installing tmux/jq. It also runs natively on Windows.

Trade-off: subagents are ephemeral (gone when the task ends) — so persistent context (DB + MD files) is what lets the next round pick up where the last one left off.

## Team roster

| Role | Stack / scope | Owns (writes only here) |
|---|---|---|
| **frontend** | Next.js (App Router), React, TypeScript | `context/projects/<active>/frontend/` |
| **backend**  | FastAPI, Pydantic, SQLAlchemy/Alembic | `context/projects/<active>/backend/` |
| **devops**   | Docker, CI/CD, env, deploy, apply migrations | `context/projects/<active>/devops/` |
| **qa**       | Vitest/Jest/Playwright, pytest, edge cases | `context/projects/<active>/qa/` |
| **reviewer** | Code review (read-only — quality, security, perf) | `context/projects/<active>/reviewer/` |

Per-role definitions: [.claude/agents/](.claude/agents/).

## Prerequisites

| Requirement | Install |
|---|---|
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | `npm i -g @anthropic-ai/claude-code` then `claude login` |
| Docker Desktop | runs PostgreSQL + FastAPI in containers |
| Node + Python toolchains for the target project | as required by the project itself |

## Quick start

```bash
# 1. Clone
git clone <this-repo> agent-teams
cd agent-teams

# 2. Copy env template (defaults work as-is; edit if needed)
cp .env.example .env

# 3. Start PostgreSQL + FastAPI backend
docker compose up --build
# - PG on port ${POSTGRES_PORT:-5432}
# - FastAPI on port ${API_PORT:-8456}
# (Add -d to detach; foreground is recommended on first run for the build log.)

# 4. In another shell, after api logs print "Application startup complete":
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed
# Seed creates the default agent-teams project + sample tasks.

# 5. Smoke test
curl http://localhost:8456/api/projects/active

# 6. (optional) Open the Kanban UI — Phase 3
# cd web && pnpm dev
# Open http://localhost:3000

# 7. Open Claude Code at the agent-teams repo root
claude
# Lead resolves the active project by curling localhost:8456.
```

CLAUDE.md is loaded automatically — Claude is ready to act as Lead.

> **First time Lead curls the API**, Claude Code prompts for permission — pick "Yes and don't ask again for this command" to allowlist.

### Run with Docker — details

| Service | Container | Port | Notes |
|---|---|---|---|
| `db` | `agent-teams-db` | `${POSTGRES_PORT:-5432}` | Postgres 16, named volume `agent-teams-pgdata` |
| `api` | `agent-teams-api` | `${API_PORT:-8456}` | bind-mounts the repo at `/repo` so newly scaffolded projects are writable |
| `web` | (Phase 3) | `3000` | placeholder in `docker-compose.yml` |

`docker-compose.yml` sets the api's `DATABASE_URL` to the `db` service hostname automatically — host `.env` only matters when running `uvicorn` outside compose.

## Day-to-day usage

### Through the Kanban UI

1. Open http://localhost:3000.
2. **Create a project** → fill in name, paths (web/api/db), stack, standards.
3. **Create a task** → role, description, priority.
4. **Trigger Lead** → click "Start" on a task → Lead picks it up, spawns the right subagent, updates status.

### Natural language through Claude Code

```
add a login feature with API
```

Lead will:
1. resolve the active project via `curl http://localhost:8456/api/projects/active`,
2. (optional) create a parent task with `POST /api/tasks` for Kanban tracking,
3. read `context/projects/<active>/shared/*` (decisions, api-contracts, db-schema),
4. choose which standards to inject per the lane mapping,
5. spawn `backend` → apply api-contracts → spawn `frontend` → spawn `qa` → spawn `reviewer`,
6. update task status in the DB as it goes,
7. report a summary back to you.

### Naming roles directly

```
have frontend and backend work on feature X in parallel
```

### Switching project

```
switch to project myapp: add the /users endpoint
```

Lead resolves it via `GET /api/projects/by-name/myapp` and uses `projects/myapp/` as context instead.

### Common command shapes

| You say | Lead does |
|---|---|
| "add endpoint X" | spawn backend → apply shared updates |
| "user dashboard page" | spawn frontend (reading existing api-contracts) |
| "compose file for dev" | spawn devops |
| "e2e tests for the login flow" | spawn qa |
| "review the current PR" | spawn reviewer |
| "feature complete: post comments" | backend → frontend → qa → reviewer |

## Permission model

[.claude/settings.json](.claude/settings.json) enforces:

| Tool | Behavior |
|---|---|
| `Read`, `Glob`, `Grep` | auto-allow |
| `Write`, `Edit`, `Bash` | **prompt every time** |

Subagents inherit the same policy. `--dangerously-skip-permissions` is never used.

**Commands Lead runs frequently** — worth allowlisting on first prompt:
- `curl http://localhost:8456/api/*` (resolve project, update task status)
- `git status`, `git diff` (verify subagent work)

## Bootstrap fallback

If Lead can't reach the API:
1. Lead tries the seed: `docker compose exec api python -m scripts.seed`.
2. If the seed fails (DB down, script error), Lead reports the error and asks you to:
   - `docker compose ps` (PG running?)
   - `docker compose logs api` (FastAPI started?)
3. After you fix it, tell Lead to retry.

## Context persistence

```
context/
├── standards/                            ← Bucket 2: cross-project, humans only
│   ├── README.md
│   ├── general.md                        ← rules + Kanban schema codes (status/priority/role)
│   ├── nextjs/  react/  typescript/  tailwind/
│   ├── fastapi/  python/  pydantic/  sqlalchemy/
│   └── postgresql/  docker/
│
└── projects/                             ← Bucket 3: per-project knowledge
    └── <project>/
        ├── shared/                       ← Lead writes only (committed)
        │   ├── decisions.md
        │   ├── api-contracts.md
        │   └── db-schema.md
        └── <role>/                       ← role-owned (gitignored except .gitkeep)
            ├── current-state.md
            └── session-<date>-<slug>.md
```

(Bucket 1 = DB inside Postgres; see `api/`, not the filesystem.)

**Rules:**
- Subagents **read** `context/projects/<p>/shared/*` but **never write** — proposals go back to Lead.
- Subagents **write freely** in their own `context/projects/<p>/<role>/`.
- Subagents **read** `context/standards/*` but **never write** — insights go in the "Standards insights" section of the final report.
- Every subagent updates `current-state.md` before returning.
- DB writes go through FastAPI endpoints only — Lead and subagents never run direct SQL.

**Why standards/ and shared/ are committed but role/ is gitignored:**

| Path | Commit? | Reason |
|---|---|---|
| `context/standards/` | ✅ | Cross-project knowledge — the team needs the same view |
| `context/projects/<p>/shared/` | ✅ | Per-project contract — the team needs the same view |
| `context/projects/<p>/<role>/` | ❌ | Per-machine state — private memory per workstation |

## Standards lane mapping

When spawning role X, Lead resolves standards from `projects.config.standards` (returned by the API):

| Role | Lanes injected |
|---|---|
| frontend | `standards.web` |
| backend | `standards.api` + `standards.db` |
| devops | every lane |
| qa | every lane |
| reviewer | every lane |

`context/standards/general.md` is injected into every role regardless of lane (it includes the Kanban schema codes used when updating task status).

## File structure

```
agent-teams/
├── CLAUDE.md                       # Lead's playbook (auto-loaded)
├── README.md                       # this file
├── docker-compose.yml              # PG + FastAPI services
├── .env.example                    # env var template
├── api/                            # FastAPI + SQLAlchemy + Alembic
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/versions/
│   ├── src/
│   │   ├── main.py
│   │   ├── db.py
│   │   ├── models/                 # SQLAlchemy
│   │   ├── routers/                # FastAPI endpoints
│   │   └── schemas/                # Pydantic
│   ├── scripts/
│   │   └── seed.py                 # initial seed (agent-teams project + sample tasks)
│   └── tests/
├── web/                            # Next.js Kanban UI (Phase 3)
├── context/
│   ├── standards/                  # Bucket 2 (committed)
│   └── projects/
│       └── agent-teams/            # Bucket 3 (shared committed, role gitignored)
└── .claude/
    ├── agents/                     # 5 role definitions
    ├── docs/                       # Lead's reference docs (loaded on demand)
    └── settings.json               # permission policy
```

## Customizing agents

Each role lives in `.claude/agents/<role>.md` — edit it directly to:
- expand or shrink the stack the role knows,
- adjust the report structure,
- add role-specific constraints.

Framework-specific conventions belong in `context/standards/<framework>/<topic>.md` — they apply to every project that picks that framework.

## Workflow examples

### Example 1: single-agent task

```
You: add a <UserAvatar> component in web

Lead:
  → curl http://localhost:8456/api/projects/active → {name: "agent-teams", paths: {...}, standards: {...}}
  → Read context/projects/agent-teams/shared/decisions.md
  → Read context/projects/agent-teams/frontend/current-state.md
  → Read context/standards/{general,nextjs,react,typescript,tailwind}/*.md
  → Spawn Agent({subagent_type: "frontend", prompt: "...add UserAvatar..." + context})

Subagent (frontend):
  → Read package.json, existing components
  → Write src/components/user-avatar.tsx [user approves]
  → Update context/projects/agent-teams/frontend/current-state.md [user approves]
  → Return: {summary, files modified}

Lead:
  → Verify the file exists
  → Report to user
```

### Example 2: multi-role feature with Kanban tracking

```
You: full login feature (email + password)

Lead:
  → curl POST http://localhost:8456/api/tasks (create parent task)
  → Plan: backend → apply contract → frontend → qa → reviewer
  → curl PATCH /api/tasks/<id> {status: 2, started_at: now}  # in_progress
  → Spawn backend("create POST /auth/login + User model + migration")

Backend subagent:
  → Generate Alembic migration
  → Write Pydantic models, endpoint, password hashing
  → Update context/projects/agent-teams/backend/current-state.md
  → Return: {summary, proposed api-contracts.md update, proposed db-schema.md update,
             handoff: devops-apply-migration, frontend-consume-contract}

Lead:
  → Apply proposed shared updates [user approves]
  → Spawn devops → apply migration
  → Spawn frontend → consume contract
  → Spawn qa + reviewer in parallel
  → curl PATCH /api/tasks/<id> {status: 5, completed_at: now}  # done
  → Report to user
```

### Example 3: read-only review

```
You: review branch feature/payments

Lead:
  → Spawn reviewer with the full standards inject

Reviewer subagent:
  → git diff main...feature/payments [user approves]
  → Read changed files
  → Write context/projects/agent-teams/reviewer/review-2026-05-04-payments.md
  → Return: {summary, blockers: 1, major: 3, minor: 5}

Lead:
  → Report blockers + path to the review file
```

## Troubleshooting

### A subagent stopped because the user denied permission
**Cause:** the user pressed deny on a Claude Code prompt.
**Fix:** Lead reports which step blocked — tell Lead to skip it, or allow and retry.

### Lead can't reach the API via curl
**Cause:** FastAPI is not up / PG is not up / wrong port.
**Fix:**
1. `docker compose ps` — are containers running?
2. `docker compose logs api` — any FastAPI startup error?
3. If the DB is empty: `docker compose exec api python -m scripts.seed`.

### API can't reach DB (`api` is up but can't connect)
**Common causes:** the db container isn't `healthy` yet / password mismatch / `DATABASE_URL` points at the wrong host.
**Fix:**
1. `docker compose ps` — `db` must be `healthy`.
2. `docker compose logs db` — check startup errors.
3. The api should use `host=db` (compose sets that), not `localhost`.

### Migration fails
**Fix:**
1. `docker compose exec api alembic current` — what revision are we on?
2. (DEV ONLY — wipes data) reset:
   ```bash
   docker compose exec api alembic downgrade base
   docker compose exec api alembic upgrade head
   ```
3. PL/pgSQL trigger errors → `docker compose logs db` for migration syntax issues.

### Reset everything (DEV ONLY)
Drop containers + volume + DB content:
```bash
docker compose down -v
```
`-v` removes the named volume `agent-teams-pgdata` — Postgres re-initializes on the next `up`.

### A subagent claims it edited shared/ or standards/
**Check:** `git status` / `git diff` against `context/projects/*/shared/` and `context/standards/`.
**Fix:** if there's a diff Lead didn't write, revert it and have Lead rewrite from the proposal.

### Context file too large
**Fix:**
- Tell Lead to paste only the relevant section.
- Delete session notes that have been consolidated into `current-state.md`.
- Split `api-contracts.md` per domain.
- Split a framework's standards across more files.

### Project switch carried over old context
**Fix:** tell Lead "re-resolve the active project and re-read `context/projects/<new>/shared/` from scratch."

## Further reading

- [CLAUDE.md](CLAUDE.md) — Lead's playbook (golden rules, roster, lifecycle, anti-patterns)
- [.claude/agents/](.claude/agents/) — per-role definitions and report structures
- [.claude/docs/](.claude/docs/) — Lead's reference docs (spawn template, context layout, new project flow, lessons)
- [context/standards/README.md](context/standards/README.md) — the standards system
- [context/standards/general.md](context/standards/general.md) — Kanban schema codes
- [context/projects/agent-teams/shared/](context/projects/agent-teams/shared/) — starter templates
