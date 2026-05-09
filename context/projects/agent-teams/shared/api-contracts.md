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

## Headers

**`X-Project-Id`** (int, **required on every `/api/tasks*` endpoint**) — locks each request to the session-bound project. The API verifies that the resource's `project_id` matches the header value; missing / non-int / mismatch → 400 with stable detail. Project endpoints (`/api/projects/*`, `/api/projects/{id}/grant-consent`) do NOT need the header — the project IS the resource.

400 detail strings (source-text-locked in `services/session_project.py` per the #122 / #690 pattern):

- `{"detail":"X-Project-Id header is required for task endpoints"}` — header missing.
- `{"detail":"task <n> does not belong to project_id <h>"}` — fetched task's `project_id` differs from header value `<h>` on GET-by-id / PATCH / DELETE. Fires AFTER `get_or_404`, so a missing id still surfaces 404 first.
- `{"detail":"X-Project-Id header <h> does not match request body project_id <b>"}` — POST body's `project_id` differs from header value `<h>`. Header wins on conflict; body's `project_id` is defense-in-depth (cross-validated, not authoritative). Header value appears FIRST in the message, body value SECOND.

422 (NOT 400) on a non-int header — Pydantic `Header(int | None)` coercion. Reasoning + Phase rollout: `context/teams/dev/decisions.md` 2026-05-09 'Session-scoped active project'. (Kanban #695, Phase 3 of the session-scoped active project shift.)

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

### POST /api/projects/{id}/grant-consent
**Purpose:** Grant per-project consent for Mode B (`run_mode='auto_headless'`) tasks (Kanban #481/#483). Typed-acknowledgment UX — `confirm_name` must match `project.name` byte-for-byte (case-sensitive). **Idempotent on re-grant:** calling again on an already-consented project returns 200 + the existing row WITHOUT re-stamping `auto_run_consent_at` OR bumping `updated_at`. The first consent is the legally / auditably significant timestamp; re-action is a no-op confirmation.
**Auth:** none

**Request:**
```json
{ "confirm_name": "agent-teams" }
```

Body uses `extra="forbid"` (NOT the default `extra="ignore"`) — sending any other field returns 422. Deliberate-action UX must fail loud on smuggled fields.

**Response 200:** `ProjectRead` (with `auto_run_consent_at` set on first grant; unchanged on re-grant)

**Errors:**
- `400` — `{"detail":"confirm_name must match project name exactly"}` when `body.confirm_name != project.name` (case-sensitive). Source-text-locked in `routers/projects.py` per the #122 detail-string lock pattern.
- `404` — `{"detail":"Project id=<n> not found"}` when id is missing OR soft-deleted (`status=0`).
- `422` — Pydantic validation error: `confirm_name` missing/empty/too long, or any extra field present.

A future `POST /api/projects/{id}/revoke-consent` will set `auto_run_consent_at` back to NULL — out of scope for #481, follow-up.

### GET /api/tasks
**Purpose:** List tasks for the session-bound project (paginated, filterable).
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. List scope is taken from the header — the legacy `?project_id=<int>` query param was REMOVED by Kanban #695. (See top-level Headers section.)
**Query:** `process_status` (1..5, optional), `assigned_role` (optional), `parent_task_id` (optional, ge=1 — return only direct children of N), `top_level_only` (bool, default false — when true, return only `parent_task_id IS NULL` rows), `limit`, `offset`. Precedence when both `parent_task_id` and `top_level_only` are provided: `top_level_only` takes precedence and `parent_task_id` is silently ignored (`routers/tasks.py` `list_tasks` `if top_level_only: ... elif parent_task_id is not None: ...`). Subtask hierarchy added 2026-05-08 by Kanban #238.
**Response 200:** `[TaskRead, ...]`
**Errors:**
- `400` — `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing (Kanban #695)

### GET /api/tasks/{id}
**Purpose:** Fetch a single task.
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The fetched row's `project_id` must match the header value; mismatch → 400. The header check fires AFTER `get_or_404`, so a missing id still surfaces 404. (Kanban #695)
**Response 200:** `TaskRead`
**Errors:**
- `404` — task id not found
- `400` — `{"detail":"task <n> does not belong to project_id <h>"}` when fetched row's `project_id` ≠ header (Kanban #695)
- `400` — `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing (Kanban #695)

### POST /api/tasks
**Purpose:** Create a task.
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. Header value MUST equal `body.project_id`; mismatch → 400. The header is canonical; body's `project_id` is defense-in-depth (cross-validated, not authoritative). (Kanban #695)

**Request:**
```json
{
  "project_id": 1,
  "title": "Phase 3 — kanban UI scaffold",
  "description": "...",
  "process_status": 1,
  "priority": 2,
  "assigned_role": 1,
  "parent_task_id": null,
  "run_mode": "manual"
}
```

`parent_task_id` (int, optional, ge=1, default null) — set this to the id of an existing active task in the same project to create a subtask. Omit (or pass null) for a top-level task. Added 2026-05-08 by Kanban #238.

`run_mode` (`"manual"` | `"auto_pickup"` | `"auto_headless"`, optional, default `"manual"`) — Step 2 execution mode (Kanban #483). `auto_headless` requires `project.auto_run_consent_at IS NOT NULL` — see 400 below.

**Response 201:** `TaskRead`

**Errors:**
- `400` — header gate violation (Kanban #695):
  - `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing.
  - `{"detail":"X-Project-Id header <h> does not match request body project_id <b>"}` when body's `project_id` diverges from header. Header wins on conflict.
- `400` — FK or CHECK violation. Detail strings (stable wire contract; mirror M5 PATCH pattern — CHECK branches gated by Pydantic 422 first, reachable today only via raw-SQL bypass or future schema drift):
  - `{"detail":"project_id <n> does not exist"}` when `project_id` references a non-existent or soft-deleted project. **Run-mode-agnostic** — wire-byte-identical across all `run_mode` values. Surfaces from two paths: (a) `IntegrityError` translation in `routers/tasks.py` for `manual` / `auto_pickup` (FK violation), (b) the cross-table validator's "no active row" branch in `services/run_mode.py` for `auto_headless`. Source-text-locked in both files. (Kanban #483, refined by #690)
  - `{"detail":"parent_task_id <n> does not exist or is deleted"}` when `parent_task_id` references a missing or soft-deleted parent (Kanban #238)
  - `{"detail":"parent_task_id <n> belongs to a different project"}` when parent's `project_id` differs from payload (cross-project parent rejection — app-layer enforced; Kanban #238)
  - `{"detail":"project <n> has not granted auto-headless consent"}` when `run_mode='auto_headless'` and the parent project EXISTS+ACTIVE but has `auto_run_consent_at IS NULL`. Cross-table validator at `services/run_mode.py` — does not fire for `manual` (default) or `auto_pickup` (Mode A2 doesn't need consent). Source-text-locked. (Kanban #483, refined by #690)
  - `{"detail":"process_status violates ck_tasks_process_status_valid"}`
  - `{"detail":"priority violates ck_tasks_priority_valid"}`
  - `{"detail":"run_mode violates ck_tasks_run_mode_valid"}` (defensive — Pydantic Literal gates this first)
  - `{"detail":"status violates ck_tasks_status_valid"}` (defensive — `status` is not a public POST field)
  - `{"detail":"Task creation violates a database constraint"}` (fallback for unknown constraints)
- `422` — Pydantic validation error (includes `run_mode` outside the literal set)

### PATCH /api/tasks/{id}
**Purpose:** Partial update. Transitioning to `process_status=2` (in_progress) sets `started_at=now()` if NULL; transitioning to `process_status=5` (done) sets `completed_at=now()`. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (N7 no-op-skip — `routers/tasks.py:121-130`; parity with PATCH `/api/projects/{id}`).
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The fetched row's `project_id` must match the header value; mismatch → 400. (Kanban #695)

**Request:** any subset of `{title, description, process_status, priority, assigned_role, started_at, completed_at, run_mode}`. The soft-delete `status` flag is intentionally absent — sending `{"status": 0}` is silently ignored (use `DELETE` to soft-delete). `parent_task_id` is REJECTED (V1 forbids re-parenting per Kanban #238) — see 422 below.

**Response 200:** `TaskRead`

**Errors:**
- `404` — task id not found
- `422` — Pydantic validation error if `parent_task_id` key is **present in the body** (whether int OR null). V1 forbids re-parenting; clients must omit the key. The router uses a `model_validator` that checks `model_fields_set` membership, so explicit-null is treated identically to a non-null value. Error message substring: `parent_task_id`. (Kanban #238)
- `400` — header gate violation (Kanban #695):
  - `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing.
  - `{"detail":"task <n> does not belong to project_id <h>"}` when fetched row's `project_id` ≠ header.
- `400` — Cross-table or CHECK violation. Detail strings (stable wire contract):
  - `{"detail":"project <n> has not granted auto-headless consent"}` when the **resolved final** `run_mode` (PATCH-supplied OR existing if not in body) is `auto_headless` AND the project lacks consent. Downgrading from `auto_headless` to `manual` always succeeds (resolved=manual → validator does not fire). Source-text-locked. (Kanban #483)
  - `{"detail":"process_status violates ck_tasks_process_status_valid"}`
  - `{"detail":"priority violates ck_tasks_priority_valid"}`
  - `{"detail":"run_mode violates ck_tasks_run_mode_valid"}` (defensive — Pydantic Literal gates first)
  - `{"detail":"status violates ck_tasks_status_valid"}` (defensive — `status` is not a public PATCH field)
  - `{"detail":"Task update violates a database constraint"}` (fallback for unknown CHECK constraints)

### DELETE /api/tasks/{id}
**Purpose:** Soft-delete a task — flips `status=0`. First DELETE advances `updated_at`; subsequent DELETEs on an already-deleted row are idempotent no-ops (return 204 without further `updated_at` bump — parity with DELETE `/api/projects/{id}`). The audit trigger snapshots the flip as `'U'` in `tasks_history`. Blocked when active subtasks reference the row (Kanban #238).
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The fetched row's `project_id` must match the header value; mismatch → 400. (Kanban #695)
**Response 204:** No content
**Errors:**
- `404` — `{"detail":"Task id=<n> not found"}` when id does not exist
- `400` — `{"detail":"task <n> does not belong to project_id <h>"}` when fetched row's `project_id` ≠ header (Kanban #695)
- `400` — `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing (Kanban #695)
- `409` — `{"detail":"Cannot delete task — <n> active subtask(s) reference this task"}` when at least one row has `parent_task_id=<id> AND status=1`. Soft-delete the children first, then retry the parent. Idempotent re-DELETE on an already soft-deleted parent still returns 204 (the active-children check runs only on the first delete; per `routers/tasks.py` `delete_task`). (Kanban #238)

## Schemas

**`ProjectRead`** — `{id:int, name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config:object, is_active:bool, team:"dev"|"novel", created_at, updated_at, auto_run_consent_at:datetime|null}`

`auto_run_consent_at` (datetime ISO-8601 with timezone, or null) added 2026-05-09 by Kanban #483 — per-project consent gate for `tasks.run_mode='auto_headless'` (Mode B / Step 2 architecture). Default null = not consented; non-null = user consented at this timestamp via `POST /api/projects/{id}/grant-consent`.

**`TaskRead`** — `{id:int, project_id:int, parent_task_id:int|null, title, description, process_status:int, priority:int, assigned_role:int|null, run_mode:"manual"|"auto_pickup"|"auto_headless", created_at, updated_at, started_at:datetime|null, completed_at:datetime|null}`

`run_mode` added 2026-05-09 by Kanban #483 — Step 2 execution mode. Default `"manual"` (existing rows backfilled by migration `0005_run_mode_and_consent`).

Integer code fields (`process_status`, `priority`, `assigned_role`) follow `context/standards/general.md` §"Kanban schema codes". Note that the `tasks` lifecycle code is named `process_status` everywhere on the wire (renamed from `status` by the 2026-05-08 migration); `status` on the wire is reserved as the internal soft-delete flag and is not exposed.

<!-- No endpoints documented yet. First endpoint goes above this line. -->
