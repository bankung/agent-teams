# API contracts (FastAPI ↔ Next.js)

> **Lead is the only writer of this file.** Backend proposes new/changed contracts; frontend consumes them. Lead reviews and writes.
>
> This is the source of truth for HTTP contracts shared between the Next.js client and the FastAPI server. If code disagrees with this file, fix the code (or fix this file via a proposal — not both at once).

## Conventions

- **Base URL:** `http://localhost:8456` (development; override via `.env`)
- **Auth:** `none` for v1 (single-user dogfood)
- **Error envelope:** FastAPI default — `{"detail": "<message>"}` with appropriate HTTP status
- **Pagination:** `offset` / `limit` query params, defaults `offset=0` `limit=50`, `limit` max 500
- **Datetime:** ISO 8601 with timezone (`2026-05-04T12:34:56+07:00`)
- **IDs:** `BigInteger` (positive integer; serialized as JSON number) — see [decisions.md](decisions.md) entry on BigInt vs UUID

## Endpoints

<!--
Template for a new endpoint:

### <METHOD> /path/{param}
**Purpose:** <one line>
**Auth:** <required role / public>

**Request:**
```json
{ "field": "type" }
```

**Response 200:**
```json
{ "field": "type" }
```

**Errors:**
- `400` — `{ "detail": "<message>" }` when <condition>
- `401` — when <condition>
- `404` — when <condition>
-->

### GET /api/projects
**Purpose:** List projects (paginated).
**Auth:** none
**Query:** `limit` (1..500, default 50), `offset` (>=0, default 0)
**Response 200:** `[ProjectRead, ...]`

### GET /api/projects/active
**Purpose:** Get the single active project.
**Auth:** none
**Response 200:** `ProjectRead`
**Errors:**
- `404` — `{"detail":"No active project"}` when no row has `is_active=true`

### GET /api/projects/by-name/{name}
**Purpose:** Look up a project by its unique name.
**Auth:** none
**Response 200:** `ProjectRead`
**Errors:**
- `404` — `{"detail":"Project '<name>' not found"}` when name does not exist

### POST /api/projects
**Purpose:** Create a project + auto-scaffold its `context/projects/<name>/` folder.
**Auth:** none

**Request:**
```json
{
  "name": "agent-teams",
  "description": "Self-hosted Kanban for managing dev team tasks (dogfood)",
  "paths": { "web": "...", "api": "...", "db": "..." },
  "stack": { "web": "Next.js 14 + ...", "api": "FastAPI + ...", "db": "PostgreSQL 16" },
  "config": {},
  "standards": {
    "web": ["nextjs","react","typescript","tailwind"],
    "api": ["fastapi","python","pydantic","sqlalchemy"],
    "db": ["postgresql"]
  },
  "is_active": true
}
```

**Response 201:** `ProjectRead`

**Errors:**
- `409` — `{"detail":"Project '<name>' already exists"}` on unique-name violation
- `422` — Pydantic validation error on missing/invalid fields

### PATCH /api/projects/{id}
**Purpose:** Partial update. Setting `is_active=true` atomically clears every other row's `is_active`.
**Auth:** none

**Request:** any subset of `{name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config, is_active}`

**Response 200:** `ProjectRead`

**Errors:**
- `404` — project id not found
- `409` — name conflict on rename

### GET /api/tasks
**Purpose:** List tasks for a project (paginated, filterable).
**Auth:** none
**Query:** `project_id` (required), `status` (1..5), `assigned_role` (1..5), `limit`, `offset`
**Response 200:** `[TaskRead, ...]`

### GET /api/tasks/{id}
**Purpose:** Fetch a single task.
**Auth:** none
**Response 200:** `TaskRead`
**Errors:**
- `404` — task id not found

### POST /api/tasks
**Purpose:** Create a task.
**Auth:** none

**Request:**
```json
{
  "project_id": 1,
  "title": "Phase 3 — kanban UI scaffold",
  "description": "...",
  "status": 1,
  "priority": 2,
  "assigned_role": 1
}
```

**Response 201:** `TaskRead`

**Errors:**
- `400` — FK violation (`project_id` does not exist) or CHECK violation (out-of-range code)
- `422` — Pydantic validation error

### PATCH /api/tasks/{id}
**Purpose:** Partial update. Transitioning to `status=2` (in_progress) sets `started_at=now()` if NULL; transitioning to `status=5` (done) sets `completed_at=now()`. Server also bumps `updated_at` on every PATCH.
**Auth:** none

**Request:** any subset of `{title, description, status, priority, assigned_role, started_at, completed_at}`

**Response 200:** `TaskRead`

**Errors:**
- `404` — task id not found
- `400` — CHECK violation

## Schemas

**`ProjectRead`** — `{id:int, name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config:object, is_active:bool, created_at, updated_at}`

**`TaskRead`** — `{id:int, project_id:int, title, description, status:int, priority:int, assigned_role:int|null, created_at, updated_at, started_at:datetime|null, completed_at:datetime|null}`

Integer code fields (`status`, `priority`, `assigned_role`) follow `context/standards/general.md` §"Kanban schema codes".

<!-- No endpoints documented yet. First endpoint goes above this line. -->
