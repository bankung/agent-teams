# API contracts (FastAPI ↔ Next.js)

> **Lead is the only writer of this file.** Backend proposes new/changed contracts; frontend consumes them. Lead reviews and writes.
>
> This is the source of truth for HTTP contracts shared between the Next.js client and the FastAPI server. If code disagrees with this file, fix the code (or fix this file via a proposal — not both at once).

## Conventions

- **Base URL:** `http://localhost:8456` (development; override via `.env`)
- **Frontend base URL convention:** the `web/` Next.js client reads its API base URL from `process.env.NEXT_PUBLIC_API_URL`. `NEXT_PUBLIC_*` vars are inlined into the client bundle at build time by Next.js — visible in browser-shipped JS, so MUST NOT contain secrets (the API itself enforces auth — Phase 4 deferred). The default value in both `docker-compose.yml` and `.env.example` is `http://localhost:8456` because the browser runs on the host (not inside the compose network) and cannot DNS-resolve the `api` service hostname. Server-side rendering / Route Handler calls (V2+) running INSIDE the web container would use `http://api:8456` and require an explicit override.
- **Auth:** `none` for v1 (single-user dogfood)
- **Error envelope:** FastAPI default — `{"detail": "<message>"}` with appropriate HTTP status
- **Pagination:** `offset` / `limit` query params, defaults `offset=0` `limit=50`, `limit` max 500
- **Datetime:** ISO 8601 with timezone (`2026-05-04T12:34:56+07:00`)
- **IDs:** `BigInteger` (positive integer; serialized as JSON number) — see [decisions.md](decisions.md) entry on BigInt vs UUID
- **Soft delete:** business resources (`projects`, `tasks`) carry an internal `status` flag (1=active, 0=deleted) that is **NOT** exposed on Read schemas or accepted on Create/Update bodies. List endpoints default-filter to active rows. Clients soft-delete via `DELETE /api/<resource>/{id}` (204 No Content). Detail endpoints (`GET /{id}`) return rows regardless of soft-delete status. The `?include_deleted=true` query param on list endpoints is debug-only and intentionally omitted from this contract. PATCH ignores `{"status": 0}` silently (Pydantic Update schemas do not declare the field).

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
  "is_active": true,
  "team": "dev"
}
```

`team` is required and must be one of `"dev"` | `"novel"` — picks the subagent roster the auto-scaffold uses. (Renamed from `lead` by alembic revision `0004_rename_lead_to_team` — both request key and `ProjectRead` field changed atomically.)

**Response 201:** `ProjectRead`

**Errors:**
- `409` — `{"detail":"Project '<name>' already exists"}` on unique-name violation
- `422` — Pydantic validation error on missing/invalid fields, including missing `team` or `team` not in `{"dev","novel"}`. `name` must match `^[a-zA-Z0-9_-]{1,64}$` (path-traversal hardening per Kanban #121); rejection shape: `{"detail":[{"type":"string_pattern_mismatch","loc":["body","name"],...}]}`. Same regex applies to PATCH `/api/projects/{id}` `name` updates.

### PATCH /api/projects/{id}
**Purpose:** Partial update. Setting `is_active=true` atomically clears every other row's `is_active`. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (no `updated_at` advance, no audit-row noise) — N7 no-op-skip parity with PATCH `/api/tasks/{id}`.
**Auth:** none

**Request:** any subset of `{name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config, is_active, team}`

**Response 200:** `ProjectRead`

**Errors:**
- `404` — project id not found
- `409` — name conflict on rename. Detail strings (stable wire contract):
  - `{"detail":"Project name '<name>' already exists"}` when `ux_projects_name_active` is violated
  - `{"detail":"Project update conflicts with an existing row"}` (fallback for unknown integrity errors)
  Note: POST `/api/projects` 409 uses `"Project '<name>' already exists"` (no "name " word) — the two strings will be consolidated in a future contract revision.
- `422` — `team` outside `{"dev","novel"}`
- `400` — `{"detail":"Cannot activate a soft-deleted project — restore first"}` when PATCH sets `is_active=true` on a row with `status=0`. Restore is a deferred admin path (separate endpoint when UI demands it). Other fields can still be PATCHed on a soft-deleted row.

### DELETE /api/projects/{id}
**Purpose:** Soft-delete a project — flips `status=0`. If the project was active (`is_active=true`), the same transaction also clears `is_active` so a new project can claim the slot. First DELETE advances `updated_at`; subsequent DELETEs on an already-deleted row are idempotent no-ops (return 204 without further `updated_at` bump — this is the M9 observable signal). Folder under `context/projects/<name>/` is **not** removed (handled out-of-band).
**Auth:** none
**Response 204:** No content
**Errors:**
- `404` — `{"detail":"Project id=<n> not found"}` when id does not exist

### GET /api/tasks
**Purpose:** List tasks for a project (paginated, filterable).
**Auth:** none
**Query:** `project_id` (required), `process_status` (1..5, optional), `assigned_role` (optional), `parent_task_id` (optional, ge=1 — return only direct children of N), `top_level_only` (bool, default false — when true, return only `parent_task_id IS NULL` rows), `limit`, `offset`. Precedence when both `parent_task_id` and `top_level_only` are provided: `top_level_only` takes precedence and `parent_task_id` is silently ignored (`routers/tasks.py` `list_tasks` `if top_level_only: ... elif parent_task_id is not None: ...`). Subtask hierarchy added 2026-05-08 by Kanban #238.
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
  "process_status": 1,
  "priority": 2,
  "assigned_role": 1,
  "parent_task_id": null
}
```

`parent_task_id` (int, optional, ge=1, default null) — set this to the id of an existing active task in the same project to create a subtask. Omit (or pass null) for a top-level task. Added 2026-05-08 by Kanban #238.

**Response 201:** `TaskRead`

**Errors:**
- `400` — FK or CHECK violation. Detail strings (stable wire contract; mirror M5 PATCH pattern — CHECK branches gated by Pydantic 422 first, reachable today only via raw-SQL bypass or future schema drift):
  - `{"detail":"project_id <n> does not exist"}` when `tasks_project_id_fkey` is violated (`<n>` substitutes the user-supplied `project_id`)
  - `{"detail":"parent_task_id <n> does not exist or is deleted"}` when `parent_task_id` references a missing or soft-deleted parent (Kanban #238)
  - `{"detail":"parent_task_id <n> belongs to a different project"}` when parent's `project_id` differs from payload (cross-project parent rejection — app-layer enforced; Kanban #238)
  - `{"detail":"process_status violates ck_tasks_process_status_valid"}`
  - `{"detail":"priority violates ck_tasks_priority_valid"}`
  - `{"detail":"status violates ck_tasks_status_valid"}` (defensive — `status` is not a public POST field)
  - `{"detail":"Task creation violates a database constraint"}` (fallback for unknown constraints)
- `422` — Pydantic validation error

### PATCH /api/tasks/{id}
**Purpose:** Partial update. Transitioning to `process_status=2` (in_progress) sets `started_at=now()` if NULL; transitioning to `process_status=5` (done) sets `completed_at=now()`. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (N7 no-op-skip — `routers/tasks.py:121-130`; parity with PATCH `/api/projects/{id}`).
**Auth:** none

**Request:** any subset of `{title, description, process_status, priority, assigned_role, started_at, completed_at}`. The soft-delete `status` flag is intentionally absent — sending `{"status": 0}` is silently ignored (use `DELETE` to soft-delete). `parent_task_id` is REJECTED (V1 forbids re-parenting per Kanban #238) — see 422 below.

**Response 200:** `TaskRead`

**Errors:**
- `404` — task id not found
- `422` — Pydantic validation error if `parent_task_id` key is **present in the body** (whether int OR null). V1 forbids re-parenting; clients must omit the key. The router uses a `model_validator` that checks `model_fields_set` membership, so explicit-null is treated identically to a non-null value. Error message substring: `parent_task_id`. (Kanban #238)
- `400` — CHECK violation. Detail strings (stable wire contract; defense-in-depth — the HTTP path is gated by Pydantic 422 first, so these branches are reachable today only via raw-SQL bypass or future schema drift):
  - `{"detail":"process_status violates ck_tasks_process_status_valid"}`
  - `{"detail":"priority violates ck_tasks_priority_valid"}`
  - `{"detail":"status violates ck_tasks_status_valid"}` (defensive — `status` is not a public PATCH field)
  - `{"detail":"Task update violates a database constraint"}` (fallback for unknown CHECK constraints)

### DELETE /api/tasks/{id}
**Purpose:** Soft-delete a task — flips `status=0`. First DELETE advances `updated_at`; subsequent DELETEs on an already-deleted row are idempotent no-ops (return 204 without further `updated_at` bump — parity with DELETE `/api/projects/{id}`). The audit trigger snapshots the flip as `'U'` in `tasks_history`. Blocked when active subtasks reference the row (Kanban #238).
**Auth:** none
**Response 204:** No content
**Errors:**
- `404` — `{"detail":"Task id=<n> not found"}` when id does not exist
- `409` — `{"detail":"Cannot delete task — <n> active subtask(s) reference this task"}` when at least one row has `parent_task_id=<id> AND status=1`. Soft-delete the children first, then retry the parent. Idempotent re-DELETE on an already soft-deleted parent still returns 204 (the active-children check runs only on the first delete; per `routers/tasks.py` `delete_task`). (Kanban #238)

## Schemas

**`ProjectRead`** — `{id:int, name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config:object, is_active:bool, team:"dev"|"novel", created_at, updated_at}`

**`TaskRead`** — `{id:int, project_id:int, parent_task_id:int|null, title, description, process_status:int, priority:int, assigned_role:int|null, created_at, updated_at, started_at:datetime|null, completed_at:datetime|null}`

Integer code fields (`process_status`, `priority`, `assigned_role`) follow `context/standards/general.md` §"Kanban schema codes". Note that the `tasks` lifecycle code is named `process_status` everywhere on the wire (renamed from `status` by the 2026-05-08 migration); `status` on the wire is reserved as the internal soft-delete flag and is not exposed.

<!-- No endpoints documented yet. First endpoint goes above this line. -->
