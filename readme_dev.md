# agent-teams for developers

**For setup and FAQ, start with [README.md](README.md).**

This guide covers architecture, storage, team rosters, configuration, and internal subsystems. It's aimed at people developing agent-teams itself or deeply customizing agents for their project.

---

## What is agent-teams?

agent-teams is a **meta-orchestration product** — a Kanban backend plus an agent harness (Lead playbook, role definitions, hooks, standards) that lives alongside every project you manage. Each project gets the same orchestration layer placed under its working directory in seconds, not 20 minutes of manual file-shuffling.

Once the agent-teams stack is running (see [Run the agent-teams stack](#run-the-agent-teams-stack) below), the `bin/agent-teams-init.ps1` CLI registers a new project in the Kanban DB and scaffolds the harness into the target folder in one command:

```powershell
# Clone agent-teams, start the stack (docker compose up -d), then:
.\bin\agent-teams-init.ps1 `
    -Name myapp `
    -WorkingPath C:\code\myapp `
    -Team dev
```

Output:

```
Created project id=571

Scaffolded C:\code\myapp
  copied : 46
  skipped: 0
  errors : 0
```

After the first run, `C:\code\myapp` contains `CLAUDE.md`, `.claude/agents/*`, `.claude/hooks/*`, `.claude/settings.json` (auto-filtered for this project's name/id), `context/standards/*`, and `context/teams/<team>/*`. Open the folder in Claude Code and the Lead bootstrap protocol takes over.

Re-running on the same target is idempotent — existing files are reported as `skipped` and never overwritten. To force a clobber, delete the target file first (the `-Force` flag is reserved; not yet wired up).

### Parameters

| Name | Required | Description |
|---|---|---|
| `-Name` | yes | Project name. Pattern `^[a-zA-Z0-9_-]{1,64}$`. Looked up via `GET /api/projects/by-name/<name>`; created on 404. |
| `-WorkingPath` | yes | Absolute Windows path where the harness lands. Created if missing. |
| `-Team` | yes | `dev`, `novel`, `general`, `content`, `netops`, `seo`, `data-analytics`, or `sem` — picks the agent roster + standards subset shipped in the manifest. |
| `-ApiUrl` | no | Default `http://localhost:8456`. Override for a non-local agent-teams instance. |
| `-Force` | no | Reserved for future overwrite mode; currently a no-op. |
| `-Verbose` | no | Lists every `copied` / `skipped` rel_path under the summary block. |

### Exit codes

- `0` — at least one file copied or skipped, zero errors.
- `1` — argument validation failed, API call failed, manifest empty, or one or more per-file writes threw.

### Troubleshooting

- **422 on POST /api/projects** — the agent-teams API enforces the `^[a-zA-Z0-9_-]{1,64}$` pattern for `name`. Spaces / dots / unicode are rejected.
- **404 on GET /api/scaffold/...** — verify `team=dev` or `team=novel` and that the agent-teams stack is the version with MVP-D (Kanban #795) deployed.
- **Connection refused** — `docker compose ps` against the agent-teams repo; the API binds `localhost:8456` by default.

---

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
              │                │  PostgreSQL    │  ← DB zone
              │                │  projects      │
              │                │  tasks         │
              │                │  tasks_history │
              │                └────────────────┘
              │
              │ Agent tool (subagent_type — names per active team)
       ┌──────┼──────┬───────┬──────────┐
       │      │      │       │          │
   ┌───▼──┬───▼──┬───▼───┬──▼──┬────────▼────┐
   │front │back  │devops │test │  reviewer   │   (dev team spawns dev-* roles)
   │ end  │ end  │       │ er  │ (read-only) │   (novel team spawns novel-* roles)
   └───┬──┴───┬──┴───┬───┴──┬──┴──────┬──────┘
       │      │      │      │         │
       └──────┴──────┼──────┴─────────┘
                     │
              ┌──────▼────────────────────────────────┐
              │  context/                             │
              │  ├── standards/  ← Standards zone     │
              │  ├── teams/      ← Team-methodology   │
              │  └── projects/<p>/                    │
              │       ├── shared/ ← Project-shared    │
              │       └── <role>/ ← Role-state        │
              └───────────────────────────────────────┘
```

- The user drives Lead through Claude Code (CLI / IDE / Web) **or** creates tasks in the Kanban UI.
- Lead binds each session to a user-named project via `GET /api/projects/by-name/<name>` — there is no `projects.json`. (The legacy `/api/projects/active` is `410 Gone` since #694; use `?status=1` to list live projects or `by-name/<name>` for direct lookup.)
- Lead **does not edit code itself** — it delegates to subagents through the `Agent` tool.
- Subagents do the work → write state back to their own `context/projects/<active>/<role>/` → return a summary → terminate.
- Cross-role decisions / API contracts / DB schema live in `context/projects/<active>/shared/` (Lead writes only) — **per-project**.
- Cross-project coding conventions + Kanban schema codes live in `context/standards/<framework>/` — **humans only**.

---

## Storage architecture (three buckets)

| Bucket | Storage | Examples | Writer |
|---|---|---|---|
| **1. Project config + tasks** | PostgreSQL DB | name, paths, stack, standards mapping; Kanban tasks (status/priority/role) | UI via Kanban + Lead via API |
| **2. Cross-project standards** | MD files (`context/standards/<framework>/`) | coding conventions, Kanban schema codes | humans only |
| **3. Per-project knowledge** | MD files (`context/projects/<p>/`) | decisions, api-contracts, db-schema, role state | Lead writes shared/, role writes own folder |

---

## Multi-domain teams

Every project picks **one team** at creation time (`projects.team`). Each team is a *playbook* (not a subagent — Claude Code subagents can't spawn nested subagents) that the main session (Lead persona) loads after resolving the active project. Different teams own different rosters and lifecycles.

| Team | Domain | Playbook | Roster prefix |
|---|---|---|---|
| `dev` | software development | [.claude/teams/dev.md](.claude/teams/dev.md) | `dev-*` |
| `novel` | novel writing (skeleton — demonstrates the multi-domain pattern) | [.claude/teams/novel.md](.claude/teams/novel.md) | `novel-*` |
| `general` | multi-domain fallback | [.claude/teams/general.md](.claude/teams/general.md) | `general-*` |
| `content` | content production | [.claude/teams/content.md](.claude/teams/content.md) | `content-*` |
| `netops` | network operations & infrastructure | [.claude/teams/netops.md](.claude/teams/netops.md) | `netops-*` |
| `seo` | SEO strategy & technical audit | [.claude/teams/seo.md](.claude/teams/seo.md) | `seo-*` |
| `data-analytics` | BI / analytics | [.claude/teams/data-analytics.md](.claude/teams/data-analytics.md) | `data-analytics-*` |
| `sem` | paid media (Google Ads, Meta, etc.) | [.claude/teams/sem.md](.claude/teams/sem.md) | `sem-*` |

**To add a new team:** (1) Add the team value to `ProjectTeam` enum in `api/src/constants.py`; (2) add the team's roster to `TEAM_ROSTERS` dict in the same file; (3) author `.claude/teams/<name>.md` playbook; (4) author `.claude/agents/<role>.md` for any new roles. **No migration or DB constraint edits required** — the API validates teams at the application layer from the constants.

---

## Team roster — `dev` team

The agent-teams repo itself uses `team='dev'`.

| Role | Stack / scope | Owns (writes only here) |
|---|---|---|
| **dev-frontend** | Next.js (App Router), React, TypeScript | `context/projects/<active>/dev-frontend/` |
| **dev-backend**  | FastAPI, Pydantic, SQLAlchemy/Alembic | `context/projects/<active>/dev-backend/` |
| **dev-devops**   | Docker, CI/CD, env, deploy, apply migrations | `context/projects/<active>/dev-devops/` |
| **dev-tester**   | Vitest/Jest/Playwright, pytest, edge cases | `context/projects/<active>/dev-tester/` |
| **dev-reviewer** | Code review (read-only — quality, security, perf) | `context/projects/<active>/dev-reviewer/` |

Per-role definitions: [.claude/agents/](.claude/agents/) (`dev-*.md` files).

---

## Prerequisites

| Requirement | Install |
|---|---|
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | `npm i -g @anthropic-ai/claude-code` then `claude login` |
| Docker Desktop | runs PostgreSQL + FastAPI in containers |
| Node + Python toolchains for the target project | as required by the project itself |

---

## Run the agent-teams stack

This is the one-time setup for the agent-teams repo itself (the orchestration backend). To onboard a target project against a running stack, see [Quick Start](README.md#get-started-in-2-steps) above.

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

# 5. Smoke test — list live projects (replaces legacy /active endpoint)
curl http://localhost:8456/api/projects?status=1
# Or look up the seeded project directly:
curl http://localhost:8456/api/projects/by-name/agent-teams

# 6. (optional) Open the Kanban UI
cd web && pnpm dev
# Open http://localhost:5431

# 7. Open Claude Code at the agent-teams repo root
claude
# Lead resolves the active project by curling localhost:8456.
```

CLAUDE.md is loaded automatically — Claude is ready to act as Lead.

> **First time Lead curls the API**, Claude Code prompts for permission — pick "Yes and don't ask again for this command" to allowlist.

### Run with Docker — details

| Service | Container | Port | Notes |
|---|---|---|---|
| `db` | `agent-teams-db` | `${POSTGRES_PORT:-5432}` | Postgres 16, UTF8 encoding (full Unicode incl. emoji), named volume `agent-teams-pgdata` |
| `api` | `agent-teams-api` | `${API_PORT:-8456}` | bind-mounts the repo at `/repo`; runs apscheduler in-process for the recurrence subsystem |
| `web` | `agent-teams-web` | `${WEB_PORT:-5431}` | Next.js 14 App Router; Linear-style minimalist Kanban board (V2 read-only landed; V3 project switcher pending). Container-internal port is 3000; host mapping defaults to 5431 to avoid Next.js default-port collision with other projects. |

`docker-compose.yml` sets the api's `DATABASE_URL` to the `db` service hostname automatically — host `.env` only matters when running `uvicorn` outside compose. The api container also runs an `AsyncIOScheduler` background job (60s default tick) — see "Built-in subsystems" below.

### Security-sensitive env vars

All entries below are part of the 2026-05-17 hardening sprint (22/27 prevention layers shipped). Full incident postmortem + per-layer details: `context/projects/agent-teams/shared/incidents/2026-05-17-resume-handoff.md`.

| Var | Default | Effect |
|---|---|---|
| `DB_NAME_ALLOWLIST` | `agent_teams,agent_teams_test` | api lifespan refuses to start if `engine.url.database` is not in this csv list (L8 / Kanban #1113). Also enforced by `BackupConfig.from_env()` so a rogue-DB backup cannot corrupt backup history. Defense against silent runtime DB-pointer drift. |
| `LANGGRAPH_DB_NAME_ALLOWLIST` | `agent_teams,agent_teams_test` | langgraph lifespan refuses to start if `DATABASE_URI`'s extracted db name is not in this csv list AND URI must contain `search_path=langgraph` (L7 / Kanban #1112). Closes the gap L8 didn't cover — langgraph uses a separate env var. |
| `MIGRATION_TARGET` | unset (= refuse non-_test DBs) | Set to `live` to apply `alembic upgrade head` against the live `agent_teams` DB (L10 / Kanban #1117). Without it, env.py raises `RuntimeError` and no DDL runs. See [Live migration procedure](#live-migration-procedure-production--dev-db) below. |
| `PYTEST_DB_PASSWORD` | dev fallback `pytest_runner_dev_only_NOT_FOR_PROD`, **MUST be rotated in prod** | DB-engine-layer gate (L4 / Kanban #1109). conftest binds pytest sessions to the `pytest_runner` postgres role which has SELECT-only on live `agent_teams` and full DDL/DML on `agent_teams_test`. Even if every software layer fails, postgres itself refuses destructive ops on live. Verified live with bypass-all-defenses test → `permission denied for table tasks`. |
| `BYPASS_LIVE_DB_PYTEST_HOOK` | unset | PowerShell hook `block-pytest-on-live-db.ps1` (L1 + L1.5 / Kanban #1119) honours `=1` as escape valve, emits `[BYPASS] ...` marker to stderr for audit. Applies to all 4 DENY paths (parent-shell env, inline bash env, docker exec pytest, python -c bypass). |
| `DOCKER_PYTEST_VERIFIED` | unset (= refuse docker exec pytest) | Narrower attestation than the blanket bypass — set to `=1` in the same shell ONLY after running `docker compose exec api printenv DATABASE_URL` and confirming the container env points at `_test` DB. Targets the `docker compose exec ... pytest` path the hook can't otherwise verify from outside the container (L1.5 / Kanban #1119). |
| `HITL_DEMO_ENABLED` | `1` in dev compose, **unset in prod** | Set to `"1"` to enable the `HITL demo —` title-prefix branch in `langgraph/nodes.py` (Kanban #1073 demo path). Leave unset/empty in production — without the gate, any authenticated user with task-create permission can trigger the hardcoded `request_user_input` interrupt branch just by prefixing their task title with `HITL demo —` (CWE-489 / OWASP A05, fixed in Kanban #1107). |
| `BACKUP_MIN_BYTES` | `102400` (100 KB) | Defense against silent backup corruption when an empty/rogue DB gets dumped (L12 / Kanban #1120) — runner aborts before upload if the pg_dump output is below this threshold. Retention pruner also refuses to delete anything when < 2 backups exist. |
| `REQUEST_MAX_BYTES` | `2097152` (2 MB) | FastAPI middleware short-circuits with 413 on Content-Length above this (L18 / Kanban #1115). Defense-in-depth for the Pydantic field-level caps (title 200, description 20_000, halt_reason / status_change_reason 1_000, AC list 50). |
| `RATE_LIMIT_PROJECTS_POST` | `5/minute` | slowapi rate limit on POST /api/projects per IP (L19 / Kanban #1124). Defense against scaffold DOS (FINDING #11: 20 POST in <5s = 20 folder creates). Soft-delete handler also moves `context/projects/<name>/` → `context/projects/.deleted/<name>-<ts>/` for archival vs orphan accumulation. |
| `MAX_ACTIVE_CHILDREN_DEFAULT` | `100` | Recurrence template safety cap (L21 / Kanban #1125) — `fire_template` halts the template (BLOCKED + `halt_reason='max_active_children_reached'`) when active children reach the cap. Per-template override via `tasks.max_active_children` column. Closes FINDING #13 (runaway `* * * * *` template spawning 1440 children/day). |

---

## Notification channels

The api supports two parallel notification channels: email digest and push notifications (ntfy.sh). Both are optional and independently gated by env vars. Delivery attempts are logged to `tasks_history` with operation code `'N'` for audit.

### Configuration

All notification vars live in the root `.env` file and are mapped via `docker-compose.yml` into the api container.

**Email digest (Gmail SMTP relay):**

| Env var | Default | Effect |
|---|---|---|
| `GMAIL_SMTP_HOST` | unset | Gmail SMTP server (`smtp.gmail.com`) |
| `GMAIL_SMTP_PORT` | unset | Gmail SMTP port (`587` for TLS) |
| `GMAIL_SMTP_USER` | unset | Gmail address (your.email@gmail.com) |
| `GMAIL_SMTP_APP_PASSWORD` | unset | [App Password](https://support.google.com/accounts/answer/185833), not your main Gmail password |
| `GMAIL_SMTP_FROM` | unset | From-address in the email (often same as `_USER`) |
| `DIGEST_EMAIL_RECIPIENT` | unset | Recipient email (where the daily digest goes) |
| `DIGEST_EMAIL_ENABLED` | `1` | Set to `"0"` to disable; `"1"` to enable. Unsubscribe link in email allows per-account opt-out. |

**Push notifications (ntfy.sh):**

| Env var | Default | Effect |
|---|---|---|
| `NTFY_BASE_URL` | unset | Base URL (`https://ntfy.sh` for public; `http://<host>:8080` for self-hosted) |
| `NTFY_TOPIC` | unset | Topic name (alphanumeric + dashes; e.g., `agent-teams-abc123`). Topics are **world-readable** — use an obscure name. |
| `NTFY_ACCESS_TOKEN` | unset | Optional Bearer token for private self-hosted instances |
| `PUSH_ENABLED` | `1` | Set to `"0"` to disable; `"1"` to enable |

**Deep linking + opt-out tokens:**

| Env var | Default | Effect |
|---|---|---|
| `WEB_BASE_URL` | unset | Root URL for task links in emails/notifications (e.g., `http://localhost:5431`) |
| `SECRET_KEY` | unset | Used to sign opt-out tokens in email footer links. Set to any random 32-char string; keep it secret. |

### Endpoints

**Fire the daily digest manually:**
```bash
curl -X POST http://localhost:8456/api/digest/fire \
  -H "X-Project-Id: 1" \
  -H "Content-Type: application/json"
```
Sends digest email (if `DIGEST_EMAIL_ENABLED=1`) and push notification (if `PUSH_ENABLED=1`) with a summary of all tasks from the past 24 hours. Runs atomically — if either channel fails, both are retried on the next scheduled tick (or manual trigger). [Task #1217]

**Fire an ad-hoc push notification:**
```bash
curl -X POST http://localhost:8456/api/push/fire \
  -H "X-Project-Id: 1" \
  -H "Content-Type: application/json" \
  -d '{"title": "Build failed", "message": "3 tests broke in api/"}'
```
[Task #1192]

**Operator unsubscribe from digest:**
```
GET /api/notifications/digest-optout?token=<signed-token>
```
The email footer includes this link with a pre-signed token. Clicking it disables `DIGEST_EMAIL_ENABLED` for that operator's project. Re-enable via API:
```bash
curl -X PATCH http://localhost:8456/api/projects/1 \
  -H "X-Project-Id: 1" \
  -H "Content-Type: application/json" \
  -d '{"config": {"digest_email_enabled": true}}'
```
[Task #1450]

### Setup walkthrough

1. **Gmail App Password** (required for email; ignore if digest disabled):
   - Enable 2-step verification on your Google Account.
   - Go to [Google Account → App Passwords](https://myaccount.google.com/apppasswords).
   - Select "Mail" and "Windows Computer" (or your platform).
   - Google issues a 16-char password. Copy it.
   - Add to `.env`: `GMAIL_SMTP_APP_PASSWORD=<16-char-password>`

2. **ntfy topic** (required for push; ignore if push disabled):
   - Pick an alphanumeric + dashes string at least 8 chars long (e.g., `agent-teams-a7k9j2`).
   - Add to `.env`: `NTFY_TOPIC=agent-teams-a7k9j2`

3. **Restart the api:**
   ```bash
   docker compose restart api
   ```

4. **Test:**
   ```bash
   curl -X POST http://localhost:8456/api/digest/fire -H "X-Project-Id: 1"
   ```

See [context/projects/agent-teams/shared/runbooks/env-var-setup.md](context/projects/agent-teams/shared/runbooks/env-var-setup.md) for detailed troubleshooting and self-hosted ntfy instructions.

---

## Built-in subsystems

The api ships several background subsystems beyond CRUD task storage:

| Subsystem | Wired by | What it does |
|---|---|---|
| **Audit trigger** | migration `0001_initial_schema` | `tasks_audit_trg AFTER UPDATE OR DELETE` writes every mutation into `tasks_history` (newly INSERTed rows are audited only on first mutation, by design). |
| **Soft-delete** | universal `status` flag | `DELETE` flips `status=0`; idempotent re-DELETE returns 204; subtask + lineage references stay queryable. |
| **Recurrence (T1+T2)** | migration `0007` + `services/recurrence.py` + `apscheduler` | Two fire paths run every `APP_SCHEDULER_TICK_SECONDS` (default 60s): templates spawn child rows + advance `next_fire_at`; `scheduled_at` one-shots transition in place. Single-fire on resume (no replay storms). `POST /api/tasks/{id}/fire-now` for manual trigger. |
| **Cross-table validators** | `services/run_mode.py` + `services/task_kind.py` | Pure-function gates fire BEFORE DB-hitting checks (cheaper short-circuit on the failure path). Resolved-final pattern catches PATCH-induced state violations across direction-A and direction-B. |
| **Context-management (CTX 1–4)** | migrations `0008` / `0009` + `services/session_store.py` / `token_counter.py` / `cost_tracker.py` / `compact_runner.py` | Per-session context store: hybrid DB row + filesystem (`_sessions/<id>/`). Activity append, heartbeat per-card log, prompt-ready string, soft-warn token budget, 4-bucket ceiling model, server-authoritative cost (Anthropic pricing table), Haiku 4.5 LLM compactor with full forensic archive (prior history + original activity + LLM summary). Compactor returns 503 until `ANTHROPIC_API_KEY` is provisioned. |
| **Project consent gate** | migration `0005` + `services/run_mode.py` | `tasks.run_mode='auto_headless'` requires `projects.auto_run_consent_at IS NOT NULL` (granted via `POST /api/projects/{id}/grant-consent` — typed-acknowledgment Pydantic schema). Mode B / Step 2 architecture. |
| **Source-text-locked detail strings** | `_DETAIL_*_TEMPLATE` constants on routers | Wire-error strings pinned via constants + byte-equality tests so `git grep` finds every consumer. Pattern from #122. |

---

## LLM provider reference (Kanban #1086)

Set `LANGGRAPH_LLM_PROVIDER` in `.env` to switch the engine's inference backend. All providers share the same `make_chat_model()` factory in `langgraph/llm.py`; no other code changes are needed.

| Provider | Env value | Key var | Model var | Default model | Cost tier | Quality / use-case |
|---|---|---|---|---|---|---|
| **Anthropic** | `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | $$$ | Best reasoning + instruction-following; recommended for production orchestration. |
| **OpenAI** | `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` | `gpt-4o` | $$$ | Strong all-rounder; good tool-use support; useful when Anthropic is unavailable. |
| **DeepSeek** | `deepseek` | `DEEPSEEK_API_KEY` | `LANGGRAPH_DEEPSEEK_MODEL` | `deepseek-chat` (V3) | $ | Very low cost per token; competitive quality. `deepseek-reasoner` (R1) adds chain-of-thought for harder tasks at moderate cost. Best for cost-sensitive or high-volume workloads. |
| **Ollama** | `ollama` | _(none — local)_ | `OLLAMA_MODEL` | `llama3.2` | free | Fully offline; no API key. Quality depends on the pulled model (`qwen2.5:7b` recommended for agent tasks). Requires a local Ollama server reachable from Docker. |

**Switching example (DeepSeek):**

```
LANGGRAPH_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
# Optional — defaults to deepseek-chat (V3). Use deepseek-reasoner for R1 chain-of-thought.
LANGGRAPH_DEEPSEEK_MODEL=deepseek-chat
LANGGRAPH_DEEPSEEK_BASE_URL=https://api.deepseek.com
```

Then restart: `docker compose -p agent-teams restart langgraph`.

---

## Day-to-day usage

### Through the Kanban UI

1. Open http://localhost:5431.
2. **Create a project** → fill in name, paths (web/api/db), stack, standards.
3. **Create a task** → role, description, priority.
4. **Trigger Lead** → click "Start" on a task → Lead picks it up, spawns the right subagent, updates status.

### Natural language through Claude Code

```
add a login feature with API
```

Lead will:
1. ask which project this session is for, then resolve via `curl http://localhost:8456/api/projects/by-name/<name>`,
2. (optional) create a parent task with `POST /api/tasks` for Kanban tracking,
3. read `context/projects/<active>/shared/*` (decisions, api-contracts, db-schema),
4. choose which standards to inject per the lane mapping,
5. spawn `dev-backend` → apply api-contracts → spawn `dev-frontend` → spawn `dev-tester` → spawn `dev-reviewer`,
6. update task status in the DB as it goes,
7. report a summary back to you.

### Naming roles directly

```
have dev-frontend and dev-backend work on feature X in parallel
```

### Switching project

```
switch to project myapp: add the /users endpoint
```

Lead resolves it via `GET /api/projects/by-name/myapp` and uses `projects/myapp/` as context instead.

### Common command shapes

| You say | Lead does (under `team='dev'`) |
|---|---|
| "add endpoint X" | spawn dev-backend → apply shared updates |
| "user dashboard page" | spawn dev-frontend (reading existing api-contracts) |
| "compose file for dev" | spawn dev-devops |
| "e2e tests for the login flow" | spawn dev-tester |
| "review the current PR" | spawn dev-reviewer |
| "feature complete: post comments" | dev-backend → dev-frontend → dev-tester → dev-reviewer |

---

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

---

## Bootstrap fallback

If Lead can't reach the API:
1. Lead tries the seed: `docker compose exec api python -m scripts.seed`.
2. If the seed fails (DB down, script error), Lead reports the error and asks you to:
   - `docker compose ps` (PG running?)
   - `docker compose logs api` (FastAPI started?)
3. After you fix it, tell Lead to retry.

---

## Context persistence

```
context/
├── standards/                            ← Standards zone — universal, humans only
│   ├── README.md
│   ├── general.md                        ← rules + Kanban schema codes (status/priority/role)
│   ├── nextjs/  react/  typescript/  tailwind/
│   ├── fastapi/  python/  pydantic/  sqlalchemy/
│   └── postgresql/  docker/
│
├── teams/                                ← Team-methodology zone — Lead writes
│   └── <team>/                             (e.g. dev/, novel/, ...)
│       ├── decisions.md                  ← system / methodology decisions log
│       └── *-methodology.md              ← cross-project flow rules per team
│
└── projects/                             ← Project zones (shared + role state)
    └── <project>/
        ├── shared/                       ← Project-shared zone — Lead writes only (committed)
        │   ├── decisions.md
        │   ├── api-contracts.md
        │   └── db-schema.md
        └── <role>/                       ← Role-state zone — role-owned (gitignored except .gitkeep)
            ├── current-state.md
            └── session-<date>-<slug>.md
```

(The fifth zone, **DB**, lives in PostgreSQL — see `api/`, not the filesystem. See [CLAUDE.md](CLAUDE.md) for the full Storage architecture table + Q0–Q2 placement framework.)

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

---

## Standards lane mapping

Standards are injected by the **active team's playbook** — each team defines its own role-to-lane mapping. For `team='dev'` (see [.claude/teams/dev.md](.claude/teams/dev.md)):

| Role | Lanes injected |
|---|---|
| dev-frontend | `standards.web` |
| dev-backend | `standards.api` + `standards.db` |
| dev-devops | every lane |
| dev-tester | every lane |
| dev-reviewer | every lane |

Other teams define their own lanes (e.g., `team='novel'` uses `voice` / `structure` / `research` / `markup`). `context/standards/general.md` is injected into every role regardless of lane and team — it carries the universal Kanban schema codes used when updating task status.

---

## File structure

```
agent-teams/
├── CLAUDE.md                       # Lead's playbook (auto-loaded)
├── README.md                       # user-facing install + FAQ
├── readme_dev.md                   # this file (developer guide)
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
├── web/                            # Next.js 14 App Router — Linear-style Kanban board (V2 read-only; V3 switcher pending)
├── context/
│   ├── standards/                  # Standards zone (committed)
│   ├── teams/                      # Team-methodology zone (committed)
│   └── projects/
│       └── agent-teams/            # Project zones (shared committed, role gitignored)
└── .claude/
    ├── agents/                     # 5 role definitions
    ├── docs/                       # Lead's reference docs (loaded on demand)
    └── settings.json               # permission policy
```

---

## Customizing agents

Each role lives in `.claude/agents/<role>.md` — edit it directly to:
- expand or shrink the stack the role knows,
- adjust the report structure,
- add role-specific constraints.

Framework-specific conventions belong in `context/standards/<framework>/<topic>.md` — they apply to every project that picks that framework.

---

## Workflow examples

### Example 1: single-agent task

```
You: add a <UserAvatar> component in web

Lead:
  → curl http://localhost:8456/api/projects/by-name/agent-teams → {name: "agent-teams", team: "dev", paths: {...}, standards: {...}}
  → Read .claude/teams/dev.md  (load active team's playbook)
  → Read context/projects/agent-teams/shared/decisions.md
  → Read context/projects/agent-teams/dev-frontend/current-state.md
  → Read context/standards/{general,nextjs,react,typescript,tailwind}/*.md
  → Spawn Agent({subagent_type: "dev-frontend", prompt: "...add UserAvatar..." + context})

Subagent (dev-frontend):
  → Read package.json, existing components
  → Write src/components/user-avatar.tsx [user approves]
  → Update context/projects/agent-teams/dev-frontend/current-state.md [user approves]
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
  → Plan: dev-backend → apply contract → dev-frontend → dev-tester → dev-reviewer
  → curl PATCH /api/tasks/<id> {process_status: 2, started_at: now}  # in_progress
  → Spawn dev-backend("create POST /auth/login + User model + migration")

dev-backend subagent:
  → Generate Alembic migration
  → Write Pydantic models, endpoint, password hashing
  → Update context/projects/agent-teams/dev-backend/current-state.md
  → Return: {summary, proposed api-contracts.md update, proposed db-schema.md update,
             handoff: dev-devops-apply-migration, dev-frontend-consume-contract}

Lead:
  → Apply proposed shared updates [user approves]
  → Spawn dev-devops → apply migration
  → Spawn dev-frontend → consume contract
  → Spawn dev-tester + dev-reviewer in parallel
  → curl PATCH /api/tasks/<id> {process_status: 5, completed_at: now}  # done
  → Report to user
```

### Example 3: read-only review

```
You: review branch feature/payments

Lead:
  → Spawn dev-reviewer with the full standards inject

dev-reviewer subagent:
  → git diff main...feature/payments [user approves]
  → Read changed files
  → Write context/projects/agent-teams/dev-reviewer/review-2026-05-04-payments.md
  → Return: {summary, blockers: 1, major: 3, minor: 5}

Lead:
  → Report blockers + path to the review file
```

---

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
1. `docker compose exec api alembic current` — what revision are we on? (read-only; safe to run on any DB)
2. (DEV ONLY — wipes data) reset against the test DB:
   ```bash
   docker compose exec api alembic downgrade base
   docker compose exec api alembic upgrade head
   ```
   These commands target whatever `DATABASE_URL` resolves to. The MIGRATION_TARGET gate (see [Live migration procedure](#live-migration-procedure-production--dev-db) below) will refuse if you accidentally point at a non-`_test` DB.
3. PL/pgSQL trigger errors → `docker compose logs db` for migration syntax issues.

### Fresh machine / after-pull bring-up

After cloning on a secondary machine, or after a `git pull` that brought in new migrations, run one command to bring everything up to date:

**Windows:**
```
bin\bring-up.cmd
```
(Batch files bypass PowerShell's ExecutionPolicy — no policy changes required. See the [ExecutionPolicy note](#executionpolicy-note-windows) below for details.)

**Mac / Linux / WSL / Git-Bash:**
```bash
./bin/bring-up.sh
```

The command runs four steps in order:

1. **`git pull --ff-only`** — fast-forward only; aborts cleanly if history has diverged so you can resolve the conflict manually. Also aborts if the working tree is dirty (uncommitted/untracked changes). Pass `--force` / `-Force` to skip the dirty check.
2. **`docker compose up -d --build`** — builds images (cached after first run) and starts all services.
3. **`alembic upgrade head` with `MIGRATION_TARGET=live`** — applies any new migrations to the live `agent_teams` DB. The `MIGRATION_TARGET=live` env var is required by the L10 guard in `api/alembic/env.py` (Kanban #1117), which refuses to migrate a non-`_test` DB without it. Without this guard a migration misfired against the wrong DB in the 2026-05-17 incident.
4. **`scripts/seed` with `SEED_TARGET=production`** — seeds default projects/tasks. The `SEED_TARGET=production` env var is required by the L11 guard in `scripts/seed.py`, which refuses to seed a non-`_test` DB without it (prevents the dev-DB-wipe pattern from the 2026-05-17 incident, where seed silently erased a populated DB).

> **The API does NOT auto-migrate on startup.** The Dockerfile CMD is `uvicorn --reload` only — it does not call `alembic upgrade head`. After any `git pull` that includes new migration files, you must run the migrate step explicitly. `bring-up.*` does this for you.

`bring-up.*` delegates entirely to the existing `bin/install.*` scripts (idempotent, safe to re-run, does **not** wipe data). Alembic reports "no new revisions" and seed reports "already seeded" on a current DB — both are no-ops.

To wipe the DB and rebuild from scratch, use `bin/reset.*` instead (destructive — see [Reset everything](#reset-everything-dev-only) below).

**Post-pull `.next` desync sub-case:** if the web container shows a 500 / white page immediately after bring-up, the `.next/` cache may be stale from the bind-mount. See [Web shows a 500 / white page after rapid FE edits](#web-shows-a-500--white-page-after-rapid-fe-edits) (Kanban #1625) for the `web-heal.*` one-liner fix.

### Live migration procedure (production / dev DB)

> **First-time install vs live migration.** If you are installing agent-teams for the first time on a freshly checked-out repo + freshly-created docker compose db volume, use `./bin/install.sh` (Linux/Mac) or `.\bin\install.ps1` (Windows). The installer transparently bypasses the L10/L11 safety guards with `MIGRATION_TARGET=live` + `SEED_TARGET=production` env vars — this is safe because a fresh DB has nothing to lose. The procedure below ("Live migration procedure") applies ONLY to a DIFFERENT scenario: applying a new migration against a populated production DB where the L10 guard exists to prevent accidental destructive ops. If you're a non-tech installer, you don't need this section.

`api/alembic/env.py` refuses to apply migrations to any DB whose name does NOT end with `_test` unless the env var `MIGRATION_TARGET=live` is set. This is the L10 prevention layer from the 2026-05-17 incident response — defense against silent migration on the wrong DB (see `context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md`).

To apply a migration to the live `agent_teams` DB:

1. **Backup first.** Force an off-site backup before any DDL hits live:
   ```bash
   curl -X POST http://localhost:8456/api/admin/backup/run-now -H "X-Project-Id: 1"
   ```
2. **Run alembic with `MIGRATION_TARGET=live`** explicitly set:
   ```bash
   docker compose exec -e MIGRATION_TARGET=live api alembic upgrade head
   ```
   Without `MIGRATION_TARGET=live`, env.py raises `RuntimeError: alembic: refusing to migrate against 'agent_teams' (non-_test DB). ...` and no DDL runs.
3. **Verify the migration applied:**
   ```bash
   docker compose exec -e MIGRATION_TARGET=live api alembic current
   ```
   (read-only — the gate still requires the env var, but no DDL runs).

The conftest is unaffected: every pytest invocation builds `agent_teams_test` which satisfies the `_test` suffix and skips the gate transparently.

### Reset everything (DEV ONLY)
Use the hardened wrapper script (L13 / Kanban #1127):
```powershell
.\bin\reset.ps1            # interactive: prompts you to type 'WIPE' to confirm
.\bin\reset.ps1 -Yes       # CI / scripted bypass
```
```bash
./bin/reset.sh             # interactive: prompts you to type 'WIPE'
./bin/reset.sh --yes       # CI / scripted bypass
```
Both scripts pin `-p agent-teams` on the `docker compose down -v` call (defense vs cwd / worktree / multi-installation drift) and refuse to run from `.claude/worktrees/` or from a directory without `docker-compose.yml`. `-v` removes the named volume `agent-teams-pgdata` — Postgres re-initializes on the next `up`.

If you must call docker directly, the equivalent is `docker compose -p agent-teams down -v` — the `-p agent-teams` flag is critical when multiple compose projects exist on the same host.

### Web shows a 500 / white page after rapid FE edits
**Cause:** `next dev` Fast-Refresh performs incremental recompiles on every file-change event. When a dev agent edits several files in rapid succession over a Windows Docker-Desktop bind mount, filesystem change events can arrive coalesced or out-of-order, leaving `.next/server/` in an inconsistent state where the webpack runtime chunk references module IDs that no longer exist in the current chunk manifest. The process stays alive serving 500s (`TypeError: e[o] is not a function` at `.next/server/webpack-runtime.js`) — it does not crash — so neither the healthcheck nor a restart policy recovers it automatically.
**Fix:**
```powershell
# Windows
.\bin\web-heal.ps1
# Mac / Linux / WSL
./bin/web-heal.sh
```
Add `-Clean` / `--clean` for the stubborn case (stops the container, removes `web/.next/` from the host, then brings it back up so Next.js rebuilds from scratch). Manual equivalent: `docker compose -p agent-teams restart web`.

If the 500 keeps recurring after multiple restarts, set `WATCHPACK_POLLING=true` in the `web` service env in `docker-compose.yml` — this forces reliable polling-based file watching inside Docker instead of relying on inotify events over the bind mount.

#### ExecutionPolicy note (Windows)

On a fresh Windows machine the default PowerShell ExecutionPolicy is **Restricted**, which blocks `.ps1` scripts with a `PSSecurityException` ("running scripts is disabled on this system"). Three remedies in order of preference:

1. **Single-shot, no system change (recommended for first use):**
   ```
   powershell -ExecutionPolicy Bypass -File .\bin\web-heal.ps1 -Clean
   ```
   Policy is bypassed only for this invocation — nothing is changed globally.

2. **Persistent dev-machine setting (if you run `.ps1` scripts regularly):**
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```
   Applies only to your user; does not affect other users or the system policy.

3. **Git Bash / WSL alternative (no policy involved):**
   ```bash
   ./bin/web-heal.sh --clean
   ```
   Bash scripts are unaffected by PowerShell ExecutionPolicy.

For the **bring-up flow** specifically, `bin\bring-up.cmd` already sidesteps this: batch files run unconditionally and launch `bring-up.ps1` with `-ExecutionPolicy Bypass` internally, so no policy change is ever needed for that path.

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

---

## Further reading

- [CLAUDE.md](CLAUDE.md) — Meta-Lead playbook (universal rules, bootstrap, team dispatch)
- [.claude/teams/](.claude/teams/) — per-domain team playbooks (`dev.md`, `novel.md`, ...)
- [.claude/agents/](.claude/agents/) — per-role subagent definitions (`dev-*.md`, `novel-*.md`, ...)
- [.claude/docs/](.claude/docs/) — Lead's reference docs (spawn template, context layout, new project flow, lessons)
- [context/standards/README.md](context/standards/README.md) — the standards system
- [context/standards/general.md](context/standards/general.md) — universal Kanban schema codes
- [context/projects/agent-teams/shared/](context/projects/agent-teams/shared/) — starter templates
