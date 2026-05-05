# Dev Team Orchestrator

Multi-agent dev team + **self-hosted Kanban** สำหรับ stack **Next.js + FastAPI + PostgreSQL** — **Lead** หนึ่งตัว spawn **specialist subagents** ตามต้องการ ผ่าน Claude Code subagent system (ไม่มี tmux)

แทนที่จะสั่ง AI ทำทีละขั้น — คุณสร้าง task ใน Kanban UI หรือพิมพ์งานให้ Lead Lead วิเคราะห์งาน → spawn agent เฉพาะ role ที่จำเป็น → รวมผลลัพธ์ → รายงานกลับ Agent ทำงานแบบ ephemeral (spawn-per-task) แล้ว terminate เมื่องานเสร็จ — state ที่สำคัญถูกเก็บ persistent

รองรับ **multi-project** — Kanban UI เป็นที่จัดการ project ทั้งหมด (paths, stack, standards mapping) Lead จัดการ knowledge แยกตาม project พร้อมแชร์ **cross-project standards** ให้ทุก project ใช้ร่วมกัน

## Storage architecture (Three buckets)

| Bucket | Storage | ตัวอย่างข้อมูล | Writer |
|---|---|---|---|
| **1. Project config + Tasks** | PostgreSQL DB | name, paths, stack, standards mapping; Kanban tasks (status/priority/role) | UI ผ่าน Kanban + Lead via API |
| **2. Cross-project standards** | MD files (`context/standards/<framework>/`) | coding conventions, Kanban schema codes | มนุษย์ MA โดยตรง |
| **3. Per-project knowledge** | MD files (`context/projects/<p>/`) | decisions, api-contracts, db-schema, role state | Lead writes shared/, role writes own folder |

## Architecture

```
                    ┌─────────────┐
                    │    User     │
                    └──────┬──────┘
                           │ (1) สั่ง Lead / (2) สร้าง task ใน Kanban UI
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

- User สั่งงาน Lead ผ่าน Claude Code (CLI / IDE / Web) **หรือ** สร้าง task ใน Kanban UI
- Lead resolve active project จาก **API** (`GET /api/projects/active`) — ไม่มี `projects.json` แล้ว
- Lead **ไม่แก้โค้ดเอง** — delegate ไปยัง subagent ผ่าน `Agent` tool
- Subagent ทำงาน → เขียน state กลับมาที่ `context/projects/<active>/<role>/` ของตัวเอง → return summary → terminate
- Decisions / API contracts / DB schema ที่ข้าม role อยู่ใน `context/projects/<active>/shared/` (Lead writes only) — **per-project**
- Coding conventions ที่ข้าม project + Kanban schema codes อยู่ใน `context/standards/<framework>/` — **มนุษย์เป็นคน MA เท่านั้น**

## Why no tmux?

ของเดิมใช้ tmux pane เพื่อให้ agent หลายตัว run พร้อมกัน — แต่ Claude Code มี subagent system ในตัว (`Agent` tool + `subagent_type`) ที่ spawn parallel ได้เหมือนกัน ไม่ต้องดู screen แยก ไม่มีปัญหา paste-buffer ค้าง ไม่ต้อง install tmux/jq และใช้บน Windows ได้ตรง ๆ

Trade-off: subagent เป็น ephemeral (จบงานก็หาย) — เลยต้องมี persistent context (DB + MD files) เพื่อให้รอบหน้าทำงานต่อจากเดิมได้

## Team roster

| Role | Stack / scope | Owns (writes only here) |
|---|---|---|
| **frontend** | Next.js (App Router), React, TypeScript | `context/projects/<active>/frontend/` |
| **backend**  | FastAPI, Pydantic, SQLAlchemy/Alembic | `context/projects/<active>/backend/` |
| **devops**   | Docker, CI/CD, env, deploy, apply migrations | `context/projects/<active>/devops/` |
| **qa**       | Vitest/Jest/Playwright, pytest, edge cases | `context/projects/<active>/qa/` |
| **reviewer** | Code review (read-only — quality, security, perf) | `context/projects/<active>/reviewer/` |

Definition แต่ละ role: [.claude/agents/](.claude/agents/)

## Prerequisites

| Requirement | Install |
|---|---|
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | `npm i -g @anthropic-ai/claude-code` แล้ว `claude login` |
| Docker Desktop | สำหรับรัน PostgreSQL + FastAPI ใน container |
| Node + Python toolchain ของ project ปลายทาง | ตามที่แต่ละ project ต้องใช้ |

## Quick start

```bash
# 1. Clone
git clone <this-repo> agent-teams
cd agent-teams

# 2. Copy env template (แก้ค่าใน .env ถ้าจำเป็น — defaults ใช้ได้ทันที)
cp .env.example .env

# 3. Start PostgreSQL + FastAPI backend
docker compose up --build
# - PG ที่ port ${POSTGRES_PORT:-5432}
# - FastAPI ที่ port ${API_PORT:-8456}
# (ใส่ -d ถ้าอยาก detach; ครั้งแรกแนะนำ foreground เพื่อดู build log)

# 4. ในอีก shell หลัง api log ขึ้น "Application startup complete":
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed
# seed สร้าง agent-teams project default + sample tasks

# 5. ทดสอบ
curl http://localhost:8456/api/projects/active

# 6. (optional) เปิด Kanban UI — Phase 3
# cd web && pnpm dev
# เปิด http://localhost:3000

# 7. เปิด Claude Code ที่ root ของ agent-teams
claude
# Lead จะ resolve active project ผ่าน curl ไปยัง localhost:8456
```

CLAUDE.md จะถูกโหลดอัตโนมัติ — Claude พร้อมรับงานใน role Lead

> **ครั้งแรกที่ Lead curl** Claude Code จะ prompt ให้ allow — เลือก "Yes and don't ask again for this command" เพื่อ allowlist

### Run with Docker — รายละเอียด

| Service | Container | Port | หมายเหตุ |
|---|---|---|---|
| `db` | `agent-teams-db` | `${POSTGRES_PORT:-5432}` | Postgres 16, named volume `agent-teams-pgdata` |
| `api` | `agent-teams-api` | `${API_PORT:-8456}` | bind-mount repo ที่ `/repo` (auto-scaffold ของ project ใหม่ writable) |
| `web` | (Phase 3) | `3000` | placeholder ใน `docker-compose.yml` |

`docker-compose.yml` ตั้ง `DATABASE_URL` ของ api ให้ชี้ host `db` (service name) อัตโนมัติ — `.env` ของ host ใช้ตอน run `uvicorn` นอก compose เท่านั้น

## วิธีใช้งานจริง

### ใช้ผ่าน Kanban UI

1. เข้า http://localhost:3000
2. **สร้าง project ใหม่** → กรอก name, paths (web/api/db), stack, standards
3. **สร้าง task** → ระบุ role ที่จะทำ + description + priority
4. **Trigger Lead** → กด "Start" ที่ task → Lead รับงาน → spawn subagent → กลับมา update status

### สั่งงานแบบ Natural language ผ่าน Claude Code

```
เพิ่ม feature login พร้อม API
```

Lead จะ:
1. resolve active project ผ่าน `curl http://localhost:8456/api/projects/active`
2. (optional) create task ใน DB ผ่าน `POST /api/tasks` เพื่อ track ใน Kanban
3. อ่าน `context/projects/<active>/shared/*` (decisions, api-contracts, db-schema)
4. เลือก standards ที่จะ inject ตาม lane mapping
5. spawn `backend` ก่อน → apply api-contracts → spawn `frontend` → spawn `qa` → spawn `reviewer`
6. update task status ใน DB ตาม progress
7. รายงานสรุปผลให้คุณ

### สั่งระบุ role ตรง ๆ

```
ให้ frontend และ backend ทำ feature X พร้อมกัน
```

### สั่งสลับ project

```
ย้ายไปทำ project myapp: เพิ่ม endpoint /users
```

Lead จะ resolve ผ่าน `GET /api/projects/by-name/myapp` แล้วใช้ context ของ `projects/myapp/` แทน

### รูปแบบคำสั่งที่ใช้บ่อย

| สั่ง | Lead ทำอะไร |
|---|---|
| "เพิ่ม endpoint X" | spawn backend → apply shared updates |
| "หน้า dashboard ของผู้ใช้" | spawn frontend (อ่าน api-contracts ที่มีอยู่) |
| "สร้าง docker-compose สำหรับ dev" | spawn devops |
| "เขียน e2e test ของ login flow" | spawn qa |
| "review PR ปัจจุบัน" | spawn reviewer |
| "feature complete: comment ใน post" | spawn backend → frontend → qa → reviewer |

## Permission model

ไฟล์ [.claude/settings.json](.claude/settings.json) ตั้งให้:

| Tool | Behavior |
|---|---|
| `Read`, `Glob`, `Grep` | auto-allow |
| `Write`, `Edit`, `Bash` | **ask ทุกครั้ง** |

ทุก subagent ที่ Lead spawn inherit policy เดียวกัน ไม่ใช้ `--dangerously-skip-permissions`

**คำสั่งที่ Lead จะใช้บ่อย** — แนะนำ allowlist ตอน prompt ครั้งแรก:
- `curl http://localhost:8456/api/*` (resolve project, update task status)
- `git status`, `git diff` (verify subagent work)

## Bootstrap fallback

ถ้า Lead `curl` ไม่ได้:
1. Lead จะลอง run seed: `docker compose exec api python -m scripts.seed`
2. ถ้า seed fail (DB down, script error) → Lead แจ้ง error + ขอให้ user แก้:
   - `docker compose ps` (PG container running?)
   - `docker compose logs api` (FastAPI ขึ้นไหม?)
3. หลัง user แก้ → บอก Lead retry

## Context persistence

```
context/
├── standards/                            ← Bucket 2: cross-project, มนุษย์ MA
│   ├── README.md
│   ├── general.md                        ← rule + Kanban schema codes (status/priority/role)
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
        └── <role>/                       ← role-owned (gitignored ยกเว้น .gitkeep)
            ├── current-state.md
            └── session-<date>-<slug>.md
```

(Bucket 1 = DB ใน PG ดูใน `api/` source ไม่ใช่ filesystem)

**Rules:**
- Subagent **อ่าน** `context/projects/<p>/shared/*` ได้ แต่ **ห้ามเขียน** ส่ง proposal กลับให้ Lead
- Subagent **เขียน** `context/projects/<p>/<role>/` ของตัวเองได้อิสระ
- Subagent **อ่าน** `context/standards/*` ได้ แต่ **ห้ามเขียน** ทุกกรณี ถ้ามี insight propose ใน "Standards insights" ส่วนของ final report
- ทุกครั้งก่อน return subagent ถูก mandate ให้ update `current-state.md`
- DB writes ผ่าน FastAPI endpoint เท่านั้น — Lead/subagent ห้าม direct SQL

**ทำไม standards/ และ shared/ commit แต่ role/ gitignore?**

| Path | Commit? | เหตุผล |
|---|---|---|
| `context/standards/` | ✅ | Cross-project knowledge ทีมต้องเห็นเหมือนกัน |
| `context/projects/<p>/shared/` | ✅ | Per-project contract ทีมต้องเห็นเหมือนกัน |
| `context/projects/<p>/<role>/` | ❌ | per-machine state — ความจำส่วนตัวต่อเครื่อง |

## Standards lane mapping

ตอน spawn subagent role X Lead resolve standards จาก `projects.config.standards` (ที่ได้จาก API):

| Role | Lanes ที่ inject |
|---|---|
| frontend | `standards.web` |
| backend | `standards.api` + `standards.db` |
| devops | ทุก lane |
| qa | ทุก lane |
| reviewer | ทุก lane |

`context/standards/general.md` inject เข้าทุก role เสมอ ไม่ขึ้นกับ lane (รวม Kanban schema codes ที่ใช้ตอน update task status)

## File structure

```
agent-teams/
├── CLAUDE.md                       # playbook ของ Lead (โหลดอัตโนมัติ)
├── README.md                       # ไฟล์นี้
├── docker-compose.yml              # PG + FastAPI services
├── .env.example                    # template env vars
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
    └── settings.json               # permission policy
```

## Customizing agents

แต่ละ role อยู่ใน `.claude/agents/<role>.md` — แก้ได้โดยตรงเพื่อ:
- เพิ่ม / ลด stack ที่ role นั้นรู้
- ปรับ report structure
- เพิ่ม constraint เฉพาะ

ถ้าจะใส่ convention เฉพาะ framework — เขียนใน `context/standards/<framework>/<topic>.md` (apply กับทุก project ที่เลือก framework นั้น)

## Workflow examples

### Example 1: Single agent task

```
You: เพิ่ม component <UserAvatar> ใน web

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
  → Verify file exists
  → Report to user
```

### Example 2: Multi-role feature with Kanban tracking

```
You: feature login (email + password) เต็ม flow

Lead:
  → curl POST http://localhost:8456/api/tasks (สร้าง parent task)
  → Plan: backend ก่อน → apply contract → frontend → qa → reviewer
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
  → Spawn qa + reviewer parallel
  → curl PATCH /api/tasks/<id> {status: 5, completed_at: now}  # done
  → Report to user
```

### Example 3: Read-only review

```
You: review branch feature/payments

Lead:
  → Spawn reviewer with full standards inject

Reviewer subagent:
  → git diff main...feature/payments [user approves]
  → Read changed files
  → Write context/projects/agent-teams/reviewer/review-2026-05-04-payments.md
  → Return: {summary, blockers: 1, major: 3, minor: 5}

Lead:
  → Report blockers + path to review file
```

## Troubleshooting

### Subagent หยุดเพราะ user deny permission
**สาเหตุ:** ผู้ใช้กด deny ตอน Claude Code prompt
**แก้:** Lead จะรายงานว่า block ที่ขั้นไหน — บอก Lead skip step นั้น หรือ allow ก่อนแล้วสั่ง retry

### Lead curl ไม่ผ่าน
**สาเหตุ:** FastAPI server ไม่ขึ้น / PG container ไม่ขึ้น / port ผิด
**แก้:**
1. `docker compose ps` — เช็ค container running
2. `docker compose logs api` — เช็ค FastAPI startup error
3. ถ้า DB ว่างเปล่า — `docker compose exec api python -m scripts.seed`

### API can't reach DB (`api` ขึ้นแต่ connect db ไม่ได้)
**สาเหตุที่พบบ่อย:** db container ยังไม่ healthy / password mismatch / `DATABASE_URL` ใน api ชี้ผิด host
**แก้:**
1. `docker compose ps` — `db` ต้องเป็น `healthy`
2. `docker compose logs db` — มอง error ตอน startup
3. ตรวจว่า api ใช้ `host=db` (compose ตั้งให้) ไม่ใช่ `localhost`

### Migration fails
**แก้:**
1. `docker compose exec api alembic current` — ดู revision ปัจจุบัน
2. (DEV ONLY — wipes data) reset โดย:
   ```bash
   docker compose exec api alembic downgrade base
   docker compose exec api alembic upgrade head
   ```
3. ถ้า PL/pgSQL trigger error → ดู `docker compose logs db` หา syntax error ใน migration

### Reset everything (DEV ONLY)
ลบ container + volume + DB content ทั้งหมด:
```bash
docker compose down -v
```
`-v` ลบ named volume `agent-teams-pgdata` — Postgres จะ init ใหม่หมดในครั้ง `up` ถัดไป

### Subagent อ้างว่าแก้ shared/ หรือ standards/ แล้ว
**ตรวจ:** `git status` / `git diff` ของ `context/projects/*/shared/` และ `context/standards/`
**แก้:** ถ้ามี diff ที่ Lead ไม่ได้เขียนเอง → revert แล้วบอก Lead rewrite ตาม proposal

### Context file ใหญ่เกินไป
**แก้:**
- บอก Lead paste เฉพาะ section ที่ relevant
- ลบ session note เก่าที่ consolidate เข้า current-state.md แล้ว
- แยก api-contracts.md เป็นหลายไฟล์ตาม domain
- standards ต่อ framework แตก section ลงหลายไฟล์

### Project switch แล้ว context ปนกัน
**แก้:** สั่ง Lead "resolve active project ใหม่ + อ่าน context/projects/<new>/shared ใหม่ทั้งหมด"

## อ่านต่อ

- [CLAUDE.md](CLAUDE.md) — playbook เต็มของ Lead (lifecycle, spawn template, role boundaries, bootstrap)
- [.claude/agents/](.claude/agents/) — definition + report structure ของแต่ละ role
- [context/standards/README.md](context/standards/README.md) — ระบบ standards
- [context/standards/general.md](context/standards/general.md) — Kanban schema codes
- [context/projects/agent-teams/shared/](context/projects/agent-teams/shared/) — starter templates
