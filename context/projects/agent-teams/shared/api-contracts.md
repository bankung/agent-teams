# API contracts (FastAPI ↔ Next.js)

> **Lead is the only writer of this file.** Backend proposes new/changed contracts; frontend consumes them. Lead reviews and writes.
>
> This is the source of truth for HTTP contracts shared between the Next.js client and the FastAPI server. If code disagrees with this file, fix the code (or fix this file via a proposal — not both at once).

## Conventions

- **Base URL:** `http://localhost:8456` (development; override via `.env`)
- **Frontend base URL convention:** the `web/` Next.js client reads its API base URL from `process.env.NEXT_PUBLIC_API_URL`. `NEXT_PUBLIC_*` vars are inlined into the client bundle at build time by Next.js — visible in browser-shipped JS, so MUST NOT contain secrets (the API itself enforces auth — Phase 4 deferred). The default value in both `docker-compose.yml` and `.env.example` is `http://localhost:8456` because the browser runs on the host (not inside the compose network) and cannot DNS-resolve the `api` service hostname. Server-side rendering / Route Handler calls (V2+) running INSIDE the web container would use `http://api:8456` and require an explicit override. **V2 implementation (Kanban #406, 2026-05-10):** `web/lib/api.ts` reads `INTERNAL_API_URL` (default falls back to `NEXT_PUBLIC_API_URL`) for SSR fetches and selects browser-vs-server at runtime via `typeof window === 'undefined'`. dev-devops sets `INTERNAL_API_URL=http://api:8456` on the `web` service so SSR stays on the compose network — without it, SSR happens to work on Windows (Docker Desktop routes `localhost:8456` from container to host) but WILL break on Linux compose.
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
**Purpose:** ~~Get the single active project.~~ **DEPRECATED 2026-05-10 (Kanban #694 Phase 2 — session-scoped active project shift).** The "single active project" invariant is gone — multiple rows may legitimately carry `is_active=true` because each Claude Code session binds to a project by name independently. Callers MUST migrate to `/api/projects/by-name/{name}` or `/api/projects?status=1`.
**Auth:** none
**Errors:**
- `410` — `{"detail":"Endpoint deprecated. Use /api/projects/by-name/{name} or /api/projects?status=1 instead."}` — always returned. Source-text-locked in `routers/projects.py` per the #122 pattern. Documented in `/openapi.json` via `responses={410: {...}}` on the route decorator (FastAPI does NOT auto-document runtime `raise HTTPException(...)` codes).

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
**Purpose:** Partial update. Setting `is_active=true` ~~atomically clears every other row's `is_active`~~ **2026-05-10 (Kanban #694 Phase 2):** no longer touches other rows — multiple projects may carry `is_active=true` simultaneously under session-scoped binding. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (no `updated_at` advance, no audit-row noise) — N7 no-op-skip parity with PATCH `/api/tasks/{id}`.
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
**Purpose:** Soft-delete a project — flips `status=0`. If the project was active (`is_active=true`), the same transaction also clears `is_active` — defensive cleanup so a soft-deleted row does not advertise itself as active in any list / by-name query (post-#694 Phase 2: no longer about a unique-index slot, since the index is gone; about read-side consistency). First DELETE advances `updated_at`; subsequent DELETEs on an already-deleted row are idempotent no-ops (return 204 without further `updated_at` bump — this is the M9 observable signal). Folder under `context/projects/<name>/` is **not** removed (handled out-of-band).
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
**Query:** `process_status` (1..5, optional), `pending` (bool, default false — when true, return only rows with `process_status != 5`, i.e., todo + in_progress + review + blocked. Convenience shortcut for the Lead-bootstrap "list pending tasks" query. When both `pending=true` and `process_status=N` are provided, `process_status` wins (more specific) and `pending` is silently ignored — enforced by `elif pending:` in `routers/tasks.py` `list_tasks`. Added 2026-05-10 by Kanban #697.), `assigned_role` (optional), `parent_task_id` (optional, ge=1 — return only direct children of N), `top_level_only` (bool, default false — when true, return only `parent_task_id IS NULL` rows), `limit`, `offset`. Precedence when both `parent_task_id` and `top_level_only` are provided: `top_level_only` takes precedence and `parent_task_id` is silently ignored (`routers/tasks.py` `list_tasks` `if top_level_only: ... elif parent_task_id is not None: ...`). Subtask hierarchy added 2026-05-08 by Kanban #238.
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

`task_kind` (`"ai"` | `"human"`, optional, default `"human"`) — V3+ scope-lock 2026-05-10 (Kanban #706). Discriminates AI-runner-driven work from human work. Cross-table rule: `task_kind='human'` MUST pair with `run_mode='manual'`; mismatch → 400 (see Errors below). Validator at `services/task_kind.py` fires BEFORE the consent gate (cheaper pure-function check first).

`is_template` (bool, optional, default false) — recurrence template flag. When true, `recurrence_rule` AND `next_fire_at` are required (Pydantic 422; DB CHECK 400 fallback).

`recurrence_rule` (str, optional, max 255, default null) — cron expression. Pydantic field validator runs `croniter.is_valid()`; invalid → 422 with `recurrence_rule` in the error loc.

`recurrence_timezone` (str, optional, max 64, default `"UTC"`) — IANA TZ name. Pydantic field validator checks `zoneinfo.available_timezones()`; unknown → 422 with `recurrence_timezone` in the error loc.

`next_fire_at` (datetime ISO-8601 with timezone, optional, default null) — scheduler hot-path target. Datetimes serialize as trailing `Z` form on output (Pydantic v2 default).

`spawned_from_task_id` (int, optional ge=1, default null) — system-managed lineage pointer. Set by the T2 scheduler on spawn from a template; user-driven POSTs default to null. NEVER editable on PATCH (V1 forbids re-parenting lineage; same model_fields_set membership pattern as `parent_task_id`).

`scheduled_at` (datetime ISO-8601 with timezone, optional, default null) — added 2026-05-10 by Kanban #723 (V3+ T1 audit follow-up). One-shot fire time for the T2 scheduler; non-recurring. **Mutually exclusive with `is_template=true`** — sending both → 422 (Pydantic XOR with detail substring containing both `scheduled_at` AND `is_template`). DB CHECK `ck_tasks_scheduled_xor_template` is the raw-SQL-bypass backstop. Stored in TIMESTAMPTZ — clients may send any TZ offset (e.g. `+07:00`); response always serializes to UTC `Z` form (verified live with `+07:00` → `Z` round-trip). Templates use `recurrence_rule` + `next_fire_at` (T1 path); regular one-shot tasks use `scheduled_at` (this path). T2 scheduler scans both fire paths.

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
  - `{"detail":"task_kind 'human' is incompatible with run_mode '<run_mode>'"}` when `task_kind='human'` AND `run_mode != 'manual'`. Cross-table validator at `services/task_kind.py`. Source-text-locked. Fires BEFORE the consent gate (cheaper pure-function check). (Kanban #706)
  - `{"detail":"task_kind violates ck_tasks_task_kind_valid"}` (defensive — Pydantic Literal gates first)
  - `{"detail":"template fields incomplete violates ck_tasks_template_recurrence_complete"}` (defensive — Pydantic model_validator catches at 422 first)
  - `{"detail":"scheduled_at is incompatible with is_template=true (use recurrence_rule for templates)"}` — DB CHECK `ck_tasks_scheduled_xor_template` fallback. Source-text-locked. Reachable today only via raw-SQL bypass — Pydantic XOR validator catches at 422 first. (Kanban #723)
  - `{"detail":"Task creation violates a database constraint"}` (fallback for unknown constraints)
- `422` — Pydantic validation error. Includes `run_mode` outside the literal set; `task_kind` outside `{"ai","human"}` (Kanban #706); invalid cron in `recurrence_rule`; unknown IANA TZ in `recurrence_timezone`; `is_template=true` without both `recurrence_rule` AND `next_fire_at` (Kanban #706); `is_template=true` AND `scheduled_at IS NOT NULL` in the same body — XOR rejection, detail substring contains both `scheduled_at` AND `is_template` (Kanban #723).

### PATCH /api/tasks/{id}
**Purpose:** Partial update. Transitioning to `process_status=2` (in_progress) sets `started_at=now()` if NULL; transitioning to `process_status=5` (done) sets `completed_at=now()`. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (N7 no-op-skip — `routers/tasks.py:121-130`; parity with PATCH `/api/projects/{id}`).
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The fetched row's `project_id` must match the header value; mismatch → 400. (Kanban #695)

**Request:** any subset of `{title, description, process_status, priority, assigned_role, started_at, completed_at, run_mode, task_kind, is_template, recurrence_rule, recurrence_timezone, next_fire_at, scheduled_at}`. The soft-delete `status` flag is intentionally absent — sending `{"status": 0}` is silently ignored (use `DELETE` to soft-delete). `parent_task_id` AND `spawned_from_task_id` are BOTH REJECTED (V1 forbids re-parenting subtask hierarchy per Kanban #238 AND recurrence lineage per Kanban #706) — see 422 below. `scheduled_at` accepts any TZ offset on input; storage + GET response always normalize to UTC `Z` form. Set `{"scheduled_at": null}` to un-schedule a one-shot task (Kanban #723).

**Response 200:** `TaskRead`

**Errors:**
- `404` — task id not found
- `422` — Pydantic validation error if `parent_task_id` OR `spawned_from_task_id` is **present in the body** (whether int OR null). V1 forbids re-parenting both subtask hierarchy (#238) AND recurrence lineage (#706); clients must omit both keys. The router's `model_validator` checks `model_fields_set` membership, so explicit-null is treated identically to a non-null value. Error message substring: the field name. (Kanban #238 + #706)
- `400` — header gate violation (Kanban #695):
  - `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing.
  - `{"detail":"task <n> does not belong to project_id <h>"}` when fetched row's `project_id` ≠ header.
- `400` — Cross-table or CHECK violation. Detail strings (stable wire contract):
  - `{"detail":"task_kind 'human' is incompatible with run_mode '<run_mode>'"}` when the **resolved final** task_kind/run_mode pair (PATCH-supplied OR existing if not in body) is `'human' + non-manual`. Asymmetric drift fails (PATCH only `task_kind='human'` on existing `auto_pickup` row → 400); bundled downgrade `{task_kind:'human', run_mode:'manual'}` succeeds. Resolved-final pattern mirrors the consent validator. Validator at `services/task_kind.py` fires BEFORE consent gate. Source-text-locked. (Kanban #706)
  - `{"detail":"project <n> has not granted auto-headless consent"}` when the **resolved final** `run_mode` (PATCH-supplied OR existing if not in body) is `auto_headless` AND the project lacks consent. Downgrading from `auto_headless` to `manual` always succeeds (resolved=manual → validator does not fire). Source-text-locked. (Kanban #483)
  - `{"detail":"process_status violates ck_tasks_process_status_valid"}`
  - `{"detail":"priority violates ck_tasks_priority_valid"}`
  - `{"detail":"run_mode violates ck_tasks_run_mode_valid"}` (defensive — Pydantic Literal gates first)
  - `{"detail":"task_kind violates ck_tasks_task_kind_valid"}` (defensive — Pydantic Literal gates first; Kanban #706)
  - `{"detail":"template fields incomplete violates ck_tasks_template_recurrence_complete"}` (defensive — Pydantic model_validator catches at 422 first; Kanban #706)
  - `{"detail":"scheduled_at is incompatible with is_template=true (use recurrence_rule for templates)"}` — fires when the **resolved final** state (PATCH-supplied OR existing if not in body) has `is_template=true AND scheduled_at IS NOT NULL`. Caught at the **router** (HTTP 422 — application-layer pre-check, not DB CHECK fallback) so direction-A (existing template + PATCH adds scheduled_at) AND direction-B (existing scheduled_at + PATCH flips is_template=true) BOTH 422 with this detail. Resolved-final pattern mirrors `task_kind`/consent. Same source-text-locked detail string also surfaces as 400 if a raw-SQL bypass triggers DB CHECK `ck_tasks_scheduled_xor_template`. (Kanban #723)
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

### POST /api/tasks/{id}/fire-now (Kanban #707, T2, 2026-05-10)
**Purpose:** Manual trigger for a recurrence template. Bypasses the `next_fire_at <= now()` check. Spawns a child row + advances the template's `next_fire_at` to the next future cron slot. Useful for "test fire" / "run now" UX without waiting for the scheduler tick.
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The row's `project_id` must match (Kanban #695).
**Request:** body NONE (path parameter only).
**Response 200:** `TaskRead` of the newly-spawned child row. Side effect: template's `next_fire_at` advances; visible on a follow-up GET.
**Errors:**
- `404` — `{"detail":"Task id=<n> not found"}` when id does not exist OR is soft-deleted (status=0).
- `400` — `{"detail":"Task id=<n> is not a template; fire-now only applies to is_template=true"}` when row exists + active but `is_template=false`. Source-text-locked per #122 pattern.
- `400` — `{"detail":"X-Project-Id header is required for task endpoints"}` when header missing (Kanban #695).
- `400` — `{"detail":"task <n> does not belong to project_id <h>"}` when row's `project_id` ≠ header (Kanban #695).

### Recurrence scheduler runtime (Kanban #707, T2, 2026-05-10)

The FastAPI `lifespan` boots `AsyncIOScheduler` (apscheduler 3.x) with one job `recurrence_tick` firing every `APP_SCHEDULER_TICK_SECONDS` (default 60s). `max_instances=1, coalesce=True` defends against tick overlap. Each tick runs both fire paths in two independent sessions:

**Path A — Templates (#706 T1).** SELECT `is_template=true AND next_fire_at IS NOT NULL AND next_fire_at <= now() AND status=1` ORDER BY `next_fire_at` LIMIT 50. For each: spawn child row (copy `title`, `description`, `priority`, `assigned_role`, `run_mode`, `task_kind`, `parent_task_id`; set `is_template=false`, `spawned_from_task_id=<template>`, `process_status=1`); advance template's `next_fire_at = croniter(rule, now(tz=recurrence_timezone)).get_next(datetime)` (anchored at NOW, not stale `next_fire_at`).

**Path B — One-shot scheduled tasks (#723).** SELECT `scheduled_at IS NOT NULL AND scheduled_at <= now() AND process_status=1 AND status=1 AND is_template=false` ORDER BY `scheduled_at` LIMIT 50. For each: transition the existing row in place (NOT spawn a child) — `process_status` 1→2, stamp `started_at=now()` if NULL, **clear `scheduled_at` to NULL** (prevents re-fire on a manual ps→1 flip later).

**Catch-up policy: single-fire on resume.** A template with `next_fire_at` 3 days ago + daily cron spawns ONE child and advances `next_fire_at` straight to the next future slot — NOT 3 children. Tier-1 live-verified.

**Audit trail.** Both paths write via SQLAlchemy ORM commits; the existing `tasks_audit_trg AFTER UPDATE OR DELETE` captures Path A's template advance + Path B's row transition. Newly-INSERTed children in Path A do NOT generate `tasks_history` rows until first mutation (matches project-wide audit policy — UPDATE/DELETE only).

**Concurrency scope.** Single-process V1 (no Redis lock, no `FOR UPDATE SKIP LOCKED`). Multi-replica deploys would need a distributed lock — out of scope per #707.

**Env knobs:**
- `APP_SCHEDULER_TICK_SECONDS` — interval seconds (default 60).
- `APP_SCHEDULER_DISABLE=true` — skip the scheduler entirely (used by pytest via `conftest.py`).

**Visibility gap (known, follow-up filed):** uvicorn's default logging config does NOT propagate non-uvicorn INFO loggers to stdout. The `"recurrence scheduler started"` log line + future `logger.exception(...)` from tick errors are silently swallowed. Liveness is provable via tick-cadence DB queries (extra `SELECT tasks` pair every `APP_SCHEDULER_TICK_SECONDS`). Fix planned: `--log-config` for uvicorn OR `logging.basicConfig(level=INFO)` at `src/main.py` import.

### Sessions (CTX-1, Kanban #716, 2026-05-10)

Session-based context store. Hybrid storage: DB rows for metadata + queryability; markdown content lives at `<repo_root>/_sessions/<id>/` (gitignored). Sessions are scoped per-project × per-Claude-Code-instance — multiple `status='active'` rows per project are allowed (multi-instance support; partial index `ix_sessions_project_id_active` is an accelerator NOT a uniqueness gate). NO audit trigger on `sessions` / `session_runs` / `session_compacts` tables — sessions self-audit via `session_compacts` archive history. **All `/api/sessions/*` and `/api/session_runs/*` endpoints follow the project-endpoint convention: NO `X-Project-Id` header required.**

#### POST /api/sessions
**Purpose:** Create a session row + filesystem skeleton (`_sessions/<id>/{session.md, archive/, cards/}`). Server-computed `session_root_path = "_sessions/<id>/"` post-INSERT (single COMMIT via `flush()` + mutate + `commit()`).
**Auth:** none

**Request:**
```json
{ "project_id": 1, "process_label": "term-1", "token_budget_per_run": null }
```

`process_label` (str, optional, max 64) — human hint (terminal id, branch name). `token_budget_per_run` (int, optional, ge=1, default null) — soft budget; null = no budget.

**Ceilings (all 4 optional on POST, default to server values when omitted; bounds `ge=1, le=1_000_000` on each):**
- `compacted_history_ceiling_tokens` (default 13000)
- `recent_activity_ceiling_tokens` (default 15000)
- `card_detail_ceiling_tokens` (default 6000) — added 2026-05-10 by Kanban #722, migration 0009
- `output_budget_tokens` (default 4000) — added 2026-05-10 by Kanban #722, migration 0009

**4-bucket token model** (per Agent Orchestration doc §1.3): `system prompt ~2k (fixed) + session.md ~28k (compacted_history 13k + recent_activity 15k) + card_detail ~6k + output_budget ~4k = ~40k total per run`. Schema-level since migration 0009; CTX-3 (#718) wires the runtime token counter and reads the 4 ceiling columns. `le=1_000_000` cap (Kanban #722 M2) guards against operator typos with soft-warn semantics.

**Pydantic extra-policy note:** `SessionCreate` currently uses default `extra="ignore"` — smuggled fields (e.g., `{"status":"weird"}`) are silently dropped; server applies its defaults. Filed as #721 follow-up to tighten to `extra="forbid"` for parity with `ConsentGrant` (deliberate-action UX must fail loud). Until #721 lands, FE must NOT rely on 422 for unknown fields on Session POST.

**Response 201:** `SessionRead` (with `session_root_path` set, server-computed).

**Errors:**
- `400` — `{"detail":"project_id <n> does not exist"}` when `project_id` references a missing or soft-deleted project. Source-text-locked. (Kanban #716)
- `422` — Pydantic validation (e.g., `project_id<1`).

#### GET /api/sessions
**Purpose:** List sessions with optional filters.
**Auth:** none
**Query:** `project_id` (int ge=1, optional), `status` (`active`|`compacting`|`closed`, optional), `limit` (1..500, default 50), `offset` (≥0, default 0).
**Response 200:** `[SessionRead, ...]` — `runs_count` and `compacts_count` are 0 in list responses (avoids N+1; detail GET fills real counts).

#### GET /api/sessions/{id}
**Purpose:** Detail with computed `runs_count` + `compacts_count`.
**Auth:** none
**Response 200:** `SessionRead`
**Errors:**
- `404` — `{"detail":"Session id=<n> not found"}` (source-text-locked).

#### PATCH /api/sessions/{id}
**Purpose:** Partial update — narrow surface (`process_label` / `token_budget_per_run` / `status` / 4 ceilings). Setting `status='closed'` server-stamps `closed_at=now()`. **`status='closed'` is terminal** — any subsequent PATCH on a closed row → 400. All 4 ceilings are mutable mid-session (operator may bump on a misbehaving long-context run; soft-warn only). Bounds `ge=1, le=1_000_000` enforced.
**Auth:** none
**Request:** any subset of `{process_label, token_budget_per_run, status, compacted_history_ceiling_tokens, recent_activity_ceiling_tokens, card_detail_ceiling_tokens, output_budget_tokens}`.
**Response 200:** `SessionRead`.
**Errors:**
- `400` — `{"detail":"Session id=<n> already closed"}` when attempting to mutate a closed session. Source-text-locked per #122 pattern. (Kanban #716)
- `404` — session id not found.
- `422` — bad status literal.

#### POST /api/sessions/{id}/runs
**Purpose:** Register a run within a session. When `task_id` is given, the server writes a `_sessions/<sid>/cards/<task_id>.md` skeleton on disk after commit (FS write follows audit-row durability rule).
**Auth:** none
**Request:** `{ "task_id": int|null = null, "status": "running"|"done"|"error"|"timeout" = "running" }`. `session_id` is NOT in the body — taken from URL.
**Response 201:** `SessionRunRead` (with `card_log_path` set when `task_id` is given).
**Errors:**
- `400` — `{"detail":"Session id=<n> is closed; cannot create runs"}`. Source-text-locked.
- `400` — `{"detail":"task_id <n> does not exist or is deleted"}`.
- `400` — `{"detail":"task <t> belongs to project <p>, session belongs to project <q>"}` when `task.project_id != session.project_id` (cross-project rejection — mirror of `parent_task_id belongs to a different project` from #238). Source-text-locked. (Kanban #716)
- `404` — session id not found.

#### PATCH /api/session_runs/{id}
**Purpose:** Update a run's status / totals / cost. Transitioning `status` to a terminal state (`done`/`error`/`timeout`) auto-stamps `finished_at=now()` if NULL. **`total_cost_usd` is server-authoritative since CTX-3 (#718, 2026-05-10)** — see "Cost computation" below.
**Auth:** none
**Request:** any subset of `{status, finished_at, total_input_tokens, total_output_tokens, total_context_chars, total_cost_usd, budget_warning, provider, model}`.
**Response 200:** `SessionRunRead`.
**Errors:**
- `404` — `{"detail":"Session run id=<n> not found"}`.
- `422` — bad status literal / negative token total / `provider` or `model` over 64 chars.

**Cost computation (CTX-3 #718):** When all 4 fields (`total_input_tokens`, `total_output_tokens`, `provider`, `model`) are present in the body, server computes `session_runs.total_cost_usd` from the locked PRICING table and stamps the column. Client-supplied `total_cost_usd` is **silently ignored** (not 422 — `extra="ignore"` retained per #721 deferral). `provider` + `model` are pricing-table inputs only — NOT persisted on the run row (per-run provenance deferred to a future task).

**Pricing table (USD per 1M tokens):**

| provider | model | input | output |
|---|---|---|---|
| `anthropic` | `claude-opus-4-7` | 15.0 | 75.0 |
| `anthropic` | `claude-sonnet-4-6` | 3.0 | 15.0 |
| `anthropic` | `claude-haiku-4-5-20251001` | 0.8 | 4.0 |

Unknown `(provider, model)` pair → cost compute SKIPPED, WARNING logged (`session_runs cost lookup failed: run_id=<n> provider='<p>' model='<m>' err=...`), `total_cost_usd` column unchanged, PATCH still 200. Tester live-verified with `(openai, gpt-4o)` — log captured verbatim.

**Soft-warn budget (CTX-3 #718):** When `total_input_tokens` is present in the body AND `sessions.token_budget_per_run IS NOT NULL` AND `total_input_tokens > token_budget_per_run`, server sets `session_runs.budget_warning=true` AND emits `WARNING` log: `"session_runs.budget_warning fired: session_id=<n> run_id=<n> current=<n> budget=<n> over_by=<n>"`. Never blocks (soft enforcement contract). Status-only PATCHes do NOT re-fire the warning.

#### GET /api/sessions/{id}/runs
**Purpose:** List runs in a session.
**Auth:** none
**Query:** `status` (literal, optional), `limit`, `offset`.
**Response 200:** `[SessionRunRead, ...]`.
**Errors:** `404` — session id not found.

#### GET /api/sessions/{id}/compacts
**Purpose:** List compact events for a session. CTX-4 (#719) owns the POST/compact action.
**Auth:** none
**Query:** `limit`, `offset`.
**Response 200:** `[SessionCompactRead, ...]`.
**Errors:** `404` — session id not found.

#### POST /api/sessions/{id}/compact (CTX-4, Kanban #719, 2026-05-10)
**Purpose:** Run the LLM compact pipeline. Reads `## Recent Activity` + existing `## Compacted History` from session.md; calls Anthropic Haiku 4.5 to summarize; writes `_sessions/<sid>/archive/compact_NNN.md` (full forensic record — prior Compacted History + original Recent Activity + LLM summary, in that order); REPLACES `## Compacted History` with the LLM summary; CLEARS `## Recent Activity`; INSERTs a `session_compacts` audit row; returns 201.
**Auth:** none. **Header:** NO `X-Project-Id` (sessions endpoint convention).
**Request:** `{"trigger_kind": "size"|"manual"|"run_count"}` — default `"manual"` if body empty/omitted.
**Response 201:** `SessionCompactRead`.

**Errors (source-text-locked per #122):**
- `404` — `{"detail":"Session id=<n> not found"}` — missing or soft-deleted session.
- `400` — `{"detail":"Session id=<n> is closed; cannot compact"}` — closed-session lock; mirrors CTX-2 closed-session pattern.
- `409` — `{"detail":"Session id=<n> is already compacting"}` — atomic status lock prevents concurrent compacts. Set via `UPDATE sessions SET status='compacting' WHERE id=:sid AND status='active' RETURNING id` (single-statement atomicity).
- `503` — `{"detail":"compact runner unavailable: ANTHROPIC_API_KEY not configured"}` — server missing the env var. **Realistic live state today** (key not provisioned). Status lock acquires THEN releases cleanly via `try/finally`; no archive file or audit row written. Tier-1 smoke verified the rollback at SQL layer (`UPDATE sessions SET status='compacting'` followed immediately by reverse `UPDATE sessions SET status='active'`).
- `502` — `{"detail":"compact runner: Anthropic API call failed"}` — provider/network failure. Underlying exception logged server-side (visibility gap per #739); details NOT leaked to client.
- `422` — Pydantic guard on bad `trigger_kind` (outside Literal); error loc `["body", "trigger_kind"]`.

**Side effects on success:**
- `_sessions/<sid>/archive/compact_NNN.md` written (NNN = next ordinal, zero-padded 3 digits, scanned via `max(existing)+1` to handle gaps). Format: header line (`# Compact NNN — <ts> — trigger=<kind>`) + `## Prior Compacted History (verbatim — input context to this compact)` + `## Original Recent Activity (verbatim)` + `## LLM Summary`.
- `session.md` `## Compacted History` body REPLACED by LLM summary (NOT concatenated — LLM saw prior context as input). Prior history is preserved ONLY in the archive file.
- `session.md` `## Recent Activity` body CLEARED to single blank line.
- `session_compacts` row INSERTed: `{trigger_kind, archive_path, before_tokens, after_tokens, compact_model='claude-haiku-4-5-20251001', compact_cost_usd}`.
- `sessions.status` flips `'active'` → `'compacting'` for the duration; releases to `'active'` on completion (success OR failure).

**Cost computation:** uses `usage.input_tokens` + `usage.output_tokens` from the Anthropic SDK response (more accurate than chars/4) × `cost_tracker.PRICING['anthropic', 'claude-haiku-4-5-20251001']` (input $0.8/M, output $4/M); quantized to `numeric(10,4)`.

**Concurrency:** atomic status lock via single UPDATE. Concurrent compacts on the same session: one wins (200 + audit), one loses (409). Tested via `asyncio.gather` + slow-stub respx fixture in pytest. Live testing of the 409 path requires an API key — covered in pytest only.

### Sessions — CTX-2 (Kanban #717, 2026-05-10)

Filesystem service layer. Writes / reads `_sessions/<id>/session.md` (Recent Activity section) + `_sessions/<id>/cards/<task_id>.md` (per-task heartbeat log). Pure-Python helpers in `services/session_store.py`; per-session advisory file lock at `_sessions/<id>/.lock`. Single-process FastAPI is V1; multi-process (gunicorn workers) deferred.

#### POST /api/sessions/{id}/activity
**Purpose:** Append a structured entry to the session's `## Recent Activity` section. Atomic under `filelock`.
**Auth:** none
**Request:** `{task_id?: int>=1, summary: str(1..4000), role?: str(<=64), kind?: str(<=64)}`. `task_id` (when given) must reference an active task in the **same project** as the session.
**Response 201:** `{appended_block: str, section_preview: str, section_chars: int, compact_recommended: bool|null, current_recent_tokens: int|null, recent_ceiling_tokens: int|null}`. `section_chars` is the post-append total length of the Recent Activity section, NOT the new block size.

**Advisory fields (CTX-3 #718, additive):** `compact_recommended` is `true` when `current_recent_tokens > recent_ceiling_tokens`. The 3 advisory fields are typed `Optional` for forward-compat (preserves the #717 contract for callers that don't care) but the V1 router ALWAYS sets them. `current_recent_tokens` uses the chars/4 heuristic (locked direction; ~10-20% inaccuracy English; worse on code/CJK). Caller (Lead/master agent) reads `compact_recommended` and may trigger CTX-4 compact (#719). Status remains 201 either way — advisory only, never blocks.
**Errors:**
- `400` — `{"detail":"Session id=<n> is closed; cannot append activity"}` — closed-session lock. Source-text-locked. (Kanban #717)
- `400` — `{"detail":"task_id <n> does not exist or is deleted"}` — task lookup miss (active rows only). Source-text-locked.
- `400` — `{"detail":"task <t> belongs to project <p>, session belongs to project <q>"}` — cross-project rejection. Mirrors the run cross-project detail VERBATIM (consolidated to a single `_DETAIL_CROSS_PROJECT_TEMPLATE` constant per N1 follow-up).
- `404` — `{"detail":"Session id=<n> not found"}`.
- `422` — Pydantic validation (missing `summary`, `summary` length out of 1..4000, `role`/`kind` over 64 chars).

#### GET /api/sessions/{id}/prompt
**Purpose:** Return prompt-ready markdown for LLM injection. Concatenates `## Compacted History` + `## Recent Activity` from session.md, optionally appending `## Current card detail (task #<id>)` from `cards/<id>.md`.
**Auth:** none
**Query:** `include_card_id` (int>=1, optional). Missing card file → silently omitted (NOT 404). 404 only fires if the session itself is missing.
**Response 200:** `{markdown: str, char_count: int}`. `char_count = len(markdown)` — code-point count, not byte / token count. CTX-3 (#718) wires the real token counter.
**Errors:**
- `404` — `{"detail":"Session id=<n> not found"}`.

Reader takes the per-session lock (V1 — serializes reads behind writes; avoids torn observations from concurrent appenders).

#### POST /api/session_runs/{run_id}/heartbeat
**Purpose:** Write to a run's per-task card log (`_sessions/<sid>/cards/<task_id>.md`). Append-mode for periodic heartbeats from a long-running run; replace-mode for snapshot rewrites.
**Auth:** none
**Request:** `{content: str(1..20000), mode: "append"|"replace"}`. Append writes `content + "\n"`; replace writes `content` verbatim with no trailing newline (so a same-content replace gets `total_bytes = len(content)`, not `len(content)+1`).
**Response 201:** `{card_log_path: str, total_bytes: int}`. **`total_bytes` is the total card file size after this write** (`card_path.stat().st_size`) — NOT bytes appended in this single call. (Renamed from `bytes_written` per #717 reviewer M1 — the old name was misleading on append.)
**Errors:**
- `400` — `{"detail":"Session id=<n> is closed; cannot write heartbeat"}` — closed-session lock. Source-text-locked. (Kanban #717)
- `400` — `{"detail":"Session run id=<n> has no task_id; heartbeat requires a card log"}` — runless run rejection. Heartbeats need a card log path; runs created without `task_id` (e.g. master-agent bookkeeping runs) cannot heartbeat. Source-text-locked.
- `404` — `{"detail":"Session run id=<n> not found"}`.
- `422` — Pydantic validation (missing `content`, length out of 1..20000, `mode` outside Literal).

## Schemas

**`ProjectRead`** — `{id:int, name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config:object, is_active:bool, team:"dev"|"novel", created_at, updated_at, auto_run_consent_at:datetime|null}`

`auto_run_consent_at` (datetime ISO-8601 with timezone, or null) added 2026-05-09 by Kanban #483 — per-project consent gate for `tasks.run_mode='auto_headless'` (Mode B / Step 2 architecture). Default null = not consented; non-null = user consented at this timestamp via `POST /api/projects/{id}/grant-consent`.

**`TaskRead`** — `{id:int, project_id:int, parent_task_id:int|null, title, description, process_status:int, priority:int, assigned_role:int|null, run_mode:"manual"|"auto_pickup"|"auto_headless", task_kind:"ai"|"human", is_template:bool, recurrence_rule:str|null, recurrence_timezone:str, next_fire_at:datetime|null, spawned_from_task_id:int|null, scheduled_at:datetime|null, created_at, updated_at, started_at:datetime|null, completed_at:datetime|null}`

`scheduled_at` (datetime ISO-8601 with TZ, default null) added 2026-05-10 by Kanban #723 (V3+ T1 audit follow-up; migration 0010). One-shot fire path. Mutually exclusive with `is_template=true` — DB CHECK `ck_tasks_scheduled_xor_template` is the backstop, but the wire-layer XOR (Pydantic + router resolved-final) catches first. Stored as TIMESTAMPTZ; serializes as trailing `Z` form on output.

`run_mode` added 2026-05-09 by Kanban #483 — Step 2 execution mode. Default `"manual"` (existing rows backfilled by migration `0005_run_mode_and_consent`).

`task_kind` + recurrence template fields (`is_template`, `recurrence_rule`, `recurrence_timezone`, `next_fire_at`, `spawned_from_task_id`) added 2026-05-10 by Kanban #706 (V3+ T1 / scope-lock). Defaults backfilled by migration `0007_task_kind_and_recurrence`: `task_kind='human'`, `is_template=false`, `recurrence_timezone='UTC'`, NULLs on the remaining nullable fields. `recurrence_rule` is a cron expression validated by `croniter.is_valid()`; `recurrence_timezone` is an IANA TZ name validated by `zoneinfo.available_timezones()`. `spawned_from_task_id` is system-managed lineage — settable on POST by the T2 scheduler when it spawns a child from a template; NEVER editable on PATCH. Datetime fields with UTC offset serialize as trailing `Z` form on output (Pydantic v2 default) regardless of input form — FE round-trip comparisons must use `Date.parse()`/`new Date(s)`, not string `===`.

Integer code fields (`process_status`, `priority`, `assigned_role`) follow `context/standards/general.md` §"Kanban schema codes". Note that the `tasks` lifecycle code is named `process_status` everywhere on the wire (renamed from `status` by the 2026-05-08 migration); `status` on the wire is reserved as the internal soft-delete flag and is not exposed.

**`SessionRead`** — `{id:int, project_id:int, process_label:str|null, status:"active"|"compacting"|"closed", token_budget_per_run:int|null, compacted_history_ceiling_tokens:int, recent_activity_ceiling_tokens:int, card_detail_ceiling_tokens:int, output_budget_tokens:int, session_root_path:str, started_at, closed_at:datetime|null, created_at, updated_at, runs_count:int, compacts_count:int}` — added 2026-05-10 by Kanban #716 (CTX-1); `card_detail_ceiling_tokens` + `output_budget_tokens` added 2026-05-10 by Kanban #722 (migration 0009, audit follow-up). `runs_count` / `compacts_count` are 0 on list responses; populated on detail GET only (avoids N+1).

**`SessionRunRead`** — `{id:int, session_id:int, task_id:int|null, status:"running"|"done"|"error"|"timeout", started_at, finished_at:datetime|null, total_input_tokens:int, total_output_tokens:int, total_context_chars:int, total_cost_usd:Decimal, budget_warning:bool, card_log_path:str|null, created_at, updated_at}` — added 2026-05-10 by Kanban #716. Server-stamps `finished_at` when transitioning to terminal status (`done`/`error`/`timeout`). `total_cost_usd` became server-authoritative 2026-05-10 by Kanban #718 (CTX-3) — see `PATCH /api/session_runs/{id}` Cost computation block. `provider` + `model` are PATCH inputs only; not persisted on the row.

**`SessionCompactRead`** — `{id:int, session_id:int, trigger_kind:"size"|"manual"|"run_count", archive_path:str, before_tokens:int, after_tokens:int, compact_model:str, compact_cost_usd:Decimal, compacted_at:datetime}` — added 2026-05-10 by Kanban #716. Read-only in CTX-1; POST/compact action ships in CTX-4 (#719).

**`SessionActivityCreate`** — `{task_id?:int>=1, summary:str(1..4000), role?:str(<=64), kind?:str(<=64)}` — added 2026-05-10 by Kanban #717 (CTX-2). `extra="ignore"` in CTX-2; #721 will tighten to `extra="forbid"` for parity with `ConsentGrant`.

**`SessionActivityRead`** — `{appended_block:str, section_preview:str, section_chars:int, compact_recommended:bool|null, current_recent_tokens:int|null, recent_ceiling_tokens:int|null}` — `appended_block`/`section_preview`/`section_chars` added 2026-05-10 by Kanban #717 (CTX-2); 3 advisory fields added 2026-05-10 by Kanban #718 (CTX-3). Advisory fields are `Optional` on the schema (forward-compat) but V1 router always sets them; tighten to required in a future hardening task if FE strict-typing demands.

**`SessionPromptRead`** — `{markdown:str, char_count:int}` — added 2026-05-10 by Kanban #717. `char_count` is `len(markdown)` (code points). Token count deferred to CTX-3 #718.

**`SessionRunHeartbeat`** — `{content:str(1..20000), mode:"append"|"replace"}` — added 2026-05-10 by Kanban #717. `extra="ignore"` in CTX-2; #721 will tighten to `extra="forbid"`.

**`SessionRunHeartbeatRead`** — `{card_log_path:str, total_bytes:int}` — added 2026-05-10 by Kanban #717. `total_bytes` is the total card file size after this write (renamed from `bytes_written` per CTX-2 reviewer M1).

<!-- No endpoints documented yet. First endpoint goes above this line. -->
