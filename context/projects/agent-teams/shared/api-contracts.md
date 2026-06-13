# API contracts — FULL reference (on-demand)

> Split 2026-06-02 (#1798). **Hot endpoints — projects stats/by-name/{id} + tasks GET-list/GET-id/POST/PATCH/DELETE — live in [api-contracts-core.md](api-contracts-core.md)** and are read at bootstrap. This file holds every OTHER endpoint; grep/read on demand. Mirror source: api/src/schemas/*.py.

## Conventions

- **Base URL:** `http://localhost:8456` (development; override via `.env`)
- **Frontend base URL convention:** the `web/` Next.js client reads its API base URL from `process.env.NEXT_PUBLIC_API_URL`. `NEXT_PUBLIC_*` vars are inlined into the client bundle at build time by Next.js — visible in browser-shipped JS, so MUST NOT contain secrets (the API itself enforces auth — Phase 4 deferred). The default value in both `docker-compose.yml` and `.env.example` is `http://localhost:8456` because the browser runs on the host (not inside the compose network) and cannot DNS-resolve the `api` service hostname. Server-side rendering / Route Handler calls (V2+) running INSIDE the web container would use `http://api:8456` and require an explicit override. **V2 implementation (Kanban #406, 2026-05-10):** `web/lib/api.ts` reads `INTERNAL_API_URL` (default falls back to `NEXT_PUBLIC_API_URL`) for SSR fetches and selects browser-vs-server at runtime via `typeof window === 'undefined'`. dev-devops sets `INTERNAL_API_URL=http://api:8456` on the `web` service so SSR stays on the compose network — without it, SSR happens to work on Windows (Docker Desktop routes `localhost:8456` from container to host) but WILL break on Linux compose.
- **Auth:** `none` for v1 (single-user dogfood)
- **Error envelope:** FastAPI default — `{"detail": "<message>"}` with appropriate HTTP status
- **Pagination:** `offset` / `limit` query params, defaults `offset=0` `limit=50`, `limit` max 500
- **Datetime:** ISO 8601 with timezone (`2026-05-04T12:34:56+07:00`)
- **IDs:** `BigInteger` (positive integer; serialized as JSON number) — see [decisions.md](decisions.md) entry on BigInt vs UUID
- **Soft delete:** business resources (`projects`, `tasks`) carry an internal `status` flag (1=active, 0=deleted) that is **NOT** exposed on Read schemas or accepted on Create/Update bodies. List endpoints default-filter to active rows. Clients soft-delete via `DELETE /api/<resource>/{id}` (204 No Content). Detail endpoints (`GET /{id}`) return rows regardless of soft-delete status. The `?include_deleted=true` query param on list endpoints is debug-only and intentionally omitted from this contract. PATCH ignores `{"status": 0}` silently (Pydantic Update schemas do not declare the field).
- **`BACKEND_FAILURE_INJECT` (web env-var, dev-only).** Test-only knob added 2026-05-11 by Kanban #761. When set to `"true"` AND `NODE_ENV != "production"`, `web/lib/api.ts` `jsonFetch` throws a synthetic `HttpError(500, ...)` BEFORE hitting the backend. Detail / message source-text-locked: `"BACKEND_FAILURE_INJECT=true (synthetic 500 from web/lib/api.ts)"`. Used by dev-tester to verify the WARN-1 fix from #760 (Server Component catch routes non-404 errors to `app/error.tsx`). Non-`NEXT_PUBLIC_*` prefix → SSR-only (client bundle inlines `undefined`). Boolean only — no per-path scoping in V1. **NEVER set in production.** Enabling requires a `docker-compose.yml` edit + `docker compose up -d web` (Next.js reads env at process startup); the dev-tester methodology probe C1-live (`context/teams/dev/smoke-methodology.md`) wraps the full enable / probe / restore cycle.

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
**Query:** `limit` (1..500, default 50), `offset` (>=0, default 0). Note: the list already default-filters to `status=1` (active) via the project-level soft-delete contract; `?status=<int>` is currently silently accepted-and-ignored by FastAPI (no `status` param on the Query signature). FE V3 sends `?status=1` per the `/active` deprecation migration message; the wire behavior is correct (active-only) but the filter is not actually applied. Clients MUST NOT depend on `?status=<int>` until backend adds explicit `status: int | None = Query(None)` plumbing — tracked as a follow-up (Kanban #407 reviewer YELLOW, 2026-05-11).
**Response 200:** `[ProjectRead, ...]`

### GET /api/projects/active
**Purpose:** ~~Get the single active project.~~ **DEPRECATED 2026-05-10 (Kanban #694 Phase 2 — session-scoped active project shift).** The "single active project" invariant is gone — multiple rows may legitimately carry `is_active=true` because each Claude Code session binds to a project by name independently. Callers MUST migrate to `/api/projects/by-name/{name}` or `/api/projects?status=1`.
**Auth:** none
**Errors:**
- `410` — `{"detail":"Endpoint deprecated. Use /api/projects/by-name/{name} or /api/projects?status=1 instead."}` — always returned. Source-text-locked in `routers/projects.py` per the #122 pattern. Documented in `/openapi.json` via `responses={410: {...}}` on the route decorator (FastAPI does NOT auto-document runtime `raise HTTPException(...)` codes).

### GET /api/projects/{id}/progress-stats (Kanban #1292, 2026-06-02)
**Purpose:** Read-only burndown + velocity series for one project, from the `tasks` table. Powers the per-project PROGRESS mini-charts in the Board header.
**Auth:** `X-Project-Id` header REQUIRED and MUST equal the path `{id}` (mirrors `GET /api/projects/{id}/pl`).
**Query:** `bucket` = `day` | `week` (default `week`, ISO week / Monday start); `days` = lookback int 1..365 (default 90).
**Response 200:** `{project_id, bucket, window_days, burndown:[{t, remaining}], velocity:[{t, completed}], generated_at}`. Both series ascend by `t` (a `YYYY-MM-DD` bucket-start date) and are zero-filled (one entry per bucket, never skipped, equal length). Counts are plain integers (NOT the Decimal-as-string money convention). `generated_at` is ISO-8601 UTC with a `Z` suffix.
- `burndown[i].remaining` = open as of bucket-end: `created_at <= bucket_end AND status=1 AND process_status != 6 AND (completed_at IS NULL OR completed_at > bucket_end)` — all-open backlog, no `created_at` lower bound (classic remaining-work; an ongoing project's line rises).
- `velocity[i].completed` = `process_status=5 AND status=1 AND completed_at IN [bucket_start, bucket_end)`.
**Errors:** `404` — project not found / soft-deleted (active-only; same detail format as `/{id}`); `400` — missing `X-Project-Id`; `422` — bad `bucket` or `days` outside 1..365.
**Notes:** v1 reads `tasks` only (`completed_at`-based velocity; `tasks_history` exact-transition counting deferred). Single SELECT + Python bucketing (no N+1). FE helper: `getProjectProgressStats` in `web/lib/api.ts`. Velocity verified exact vs `date_trunc('week')` DONE counts.

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

**Added by Kanban #777 (2026-05-12) — all optional:**
- `working_path` (string | null, `min_length=1` when set) — single project-root path on the host. Advisory metadata only; not validated for existence. Orthogonal to `paths_web/api/db` (which are lane-specific sub-paths).
- `working_repo` (string | null, `min_length=1` when set) — free-form repo identifier (URL or path; no regex).
- `agent_overrides` (`object<string, "haiku"|"sonnet"|"opus">` | null, default `{}`) — per-project subagent model routing. Keys MUST match `^[a-zA-Z0-9_-]{1,64}$` (same shape as `name`). Values constrained to the 3 Claude tiers.

**Added by Kanban #778 (2026-05-13) — optional:**
- `sources` (`list<SourceEntry>` | null, default `[]`, `max_length=20`) — per-project curated reference list (URLs, refs, doc anchors, repos). Each `SourceEntry`: `{url: str (1..2000), label?: str (1..200), kind?: "doc"|"spec"|"repo"|"dashboard"|"other"}` with `extra="forbid"` (typo'd keys reject 422). Optional fields stored with `exclude_none` — omitted from JSONB / response, NOT serialized as null.
  - **`url` scheme allowlist (BLOCKER-1, fixed 2026-05-13):** accepts a scheme-prefixed URL where the scheme (case-insensitive) is one of **`http`, `https`, `ref`, `file`**, OR a Unix absolute path (`/...`), OR a Windows absolute path (`X:\...` or `X:/...`). Any other shape (including code-execution schemes `javascript:`, `data:`, `vbscript:`, non-allowlisted `gopher:`/`ftp:`, or the bare `://` separator) is rejected 422. The allowlist is the XSS-bypass gate — a permissive `"://" in s` substring check admitted `javascript://%0aalert(1)//` (canonical AngularJS-sanitizer-bypass payload) and `rel="noopener noreferrer"` does NOT block scheme execution. FE renderers MUST mirror the allowlist before producing a click-navigable `<a href>` (see `web/components/SourcesBadge.tsx::isClickable`; FE intentionally narrower — clickable set is `{http, https}` only (Kanban #868, 2026-05-14 tightened from prior `{http, https, ref}`). `ref://` is an agent-internal reference scheme (consumed by research tooling, not the browser); `file://` is cross-origin-blocked from remote-served pages. Both render as non-clickable `<span data-non-clickable-source>` with monospace + muted styling, still selectable / copyable).

**Response 201:** `ProjectRead`

**Errors:**
- `409` — `{"detail":"Project '<name>' already exists"}` on unique-name violation
- `422` — Pydantic validation error on missing/invalid fields, including missing `team` or `team` not in `{"dev","novel"}`. `name` must match `^[a-zA-Z0-9_-]{1,64}$` (path-traversal hardening per Kanban #121); rejection shape: `{"detail":[{"type":"string_pattern_mismatch","loc":["body","name"],...}]}`. Same regex applies to PATCH `/api/projects/{id}` `name` updates.
- `422` — `working_path` or `working_repo` empty string. Rejection shape: `{"detail":[{"type":"string_too_short","loc":["body","working_path"|"working_repo"],...}]}` (Kanban #777).
- `422` — `agent_overrides` value not in `{"haiku","sonnet","opus"}`. Rejection shape: `{"detail":[{"type":"literal_error","loc":["body","agent_overrides","<key>"],...}]}` (Kanban #777).
- `422` — `agent_overrides` key fails `^[a-zA-Z0-9_-]{1,64}$`. Rejection shape: `{"detail":[{"type":"value_error","loc":["body","agent_overrides"],"msg":"... must match ^[a-zA-Z0-9_-]{1,64}$"}]}` (Kanban #777 WARN-4).
- `422` — `sources` length > 20. Rejection shape: `{"detail":[{"type":"too_long","loc":["body","sources"],...,"ctx":{"max_length":20,"actual_length":<n>}}]}` (Kanban #778).
- `422` — `sources[i].url` scheme not in allowlist OR not an absolute path. Rejection shape: `{"detail":[{"type":"value_error","loc":["body","sources",<i>,"url"],"msg":"Value error, url must be http/https/ref/file scheme, or an absolute path",...}]}` (Kanban #778 BLOCKER-1).
- `422` — `sources[i].kind` not in `{"doc","spec","repo","dashboard","other"}`. Rejection shape: `{"detail":[{"type":"literal_error","loc":["body","sources",<i>,"kind"],...}]}` (Kanban #778).
- `422` — `sources[i]` contains an unknown key (`SourceEntry.extra="forbid"`). Rejection shape: `{"detail":[{"type":"extra_forbidden","loc":["body","sources",<i>,"<key>"],...}]}` (Kanban #778).

### PATCH /api/projects/{id}
**Purpose:** Partial update. Setting `is_active=true` ~~atomically clears every other row's `is_active`~~ **2026-05-10 (Kanban #694 Phase 2):** no longer touches other rows — multiple projects may carry `is_active=true` simultaneously under session-scoped binding. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (no `updated_at` advance, no audit-row noise) — N7 no-op-skip parity with PATCH `/api/tasks/{id}`.
**Auth:** none

**Request:** any subset of `{name, description, paths_web, paths_api, paths_db, stack_web, stack_api, stack_db, config, is_active, team, working_path, working_repo, agent_overrides, sources}` (`working_path`/`working_repo`/`agent_overrides` added by Kanban #777, 2026-05-12; `sources` added by Kanban #778, 2026-05-13)

**Null semantics (Kanban #777 / #778):**
- `working_path: null` / `working_repo: null` → clears the field to SQL NULL (parity with `description`, `stack_*`).
- `agent_overrides: null` → router normalizes to `{}` BEFORE the UPDATE (WARN-1 Option A). Response and subsequent GET both return `{}`, never `null`. The `server_default '{}'::jsonb` fires only on INSERT; this transform keeps the wire contract "always a dict at the response boundary" intact across PATCH too.
- `sources: null` → router normalizes to `[]` BEFORE the UPDATE (parity with `agent_overrides` Option A). Response and subsequent GET both return `[]`, never `null`. The DB column IS nullable but the app layer treats NULL identically to `[]`, so the response boundary contract is "always a list, never null". Kanban #778.
- Key-absent → leave existing value unchanged (parity with every other optional field via `exclude_unset=True`).

**Replace semantics for JSONB collection fields:**
- `agent_overrides`: the value sent is the NEW value, full-stop — NOT deep-merged with existing keys. Locked by `test_patch_project_agent_overrides_replace_semantics`.
- `sources`: same — the array sent fully replaces the previous list, NOT element-merged. Locked by `test_sources_happy_crud_round_trip` (Kanban #778).

**Response 200:** `ProjectRead`

**Errors:**
- `404` — project id not found
- `409` — name conflict on rename. Detail strings (stable wire contract):
  - `{"detail":"Project name '<name>' already exists"}` when `ux_projects_name_active` is violated
  - `{"detail":"Project update conflicts with an existing row"}` (fallback for unknown integrity errors)
  Note: POST `/api/projects` 409 uses `"Project '<name>' already exists"` (no "name " word) — the two strings will be consolidated in a future contract revision.
- `422` — `team` outside `{"dev","novel","general"}` (`'general'` added by Kanban #844, 2026-05-13)
- `422` — `working_path`/`working_repo` empty string, or `agent_overrides` value not in `{haiku|sonnet|opus}`, or `agent_overrides` key fails `^[a-zA-Z0-9_-]{1,64}$` (Kanban #777). Identical wire shapes to POST `/api/projects`.
- `422` — `sources` length > 20, `sources[i].url` outside the allowlist, `sources[i].kind` enum miss, or unknown key per `extra="forbid"` (Kanban #778). Identical wire shapes to POST `/api/projects`.
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

### GET /api/tasks/{id}/blocks (Kanban #771, 2026-05-12)
**Purpose:** Reverse-lookup for `blocked_by`. Returns the list of active tasks that have `blocked_by == {id}` — i.e., the dependents this task is currently blocking. Used by the FE TaskDetail panel to render an "Also blocks" list.
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The looked-up `{id}` row's `project_id` must match the header value (Kanban #695 convention). Returns `[]` when no dependents reference it.
**Response 200:** `list[TaskRead]` ordered by `id` ASC. Soft-deleted dependents excluded.
**Errors:**
- `404` — `{"detail":"Task id=<n> not found"}` when `{id}` does not exist.
- `400` — header gate violations as per `GET /api/tasks/{id}` (Kanban #695).

### POST /api/tasks/{id}/reorder (Kanban #772, 2026-05-12)
**Purpose:** Anchor-based within-lane reorder. Computes a new `sort_order` for the moved task server-side and writes it atomically with the cross-row blocker-order check. User-facing API for dnd-kit drag-drop in the "New tasks" lane.
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED.

**Body schema (`TaskReorder`, `extra='forbid'`):**
- `before_id: int | null` (optional, ge=1) — the task that should appear immediately AFTER the moved task post-reorder.
- `after_id:  int | null` (optional, ge=1) — the task that should appear immediately BEFORE the moved task post-reorder.
- At least one of `before_id` / `after_id` required (Pydantic 422 with `"reorder requires at least one of before_id or after_id"`).
- `before_id == after_id` rejected (Pydantic 422 with `"before_id and after_id cannot reference the same task"`).

**Same-lane invariant:** the moved task and both anchors MUST share the same `process_status`. Cross-lane reorder is out of scope.

**Sort-order computation:**
- Both anchors → `new = (after_anchor.sort_order + before_anchor.sort_order) / 2`.
- `before_id` only → average between `before_id.sort_order` and the largest sort_order strictly less than it in same lane (excluding the moved task); if none, `before_id.sort_order - 1.0`.
- `after_id` only → mirror: average between `after_id.sort_order` and the smallest sort_order strictly greater; if none, `after_id.sort_order + 1.0`.
- Any NULL anchor sort_order triggers lane materialization first (floor floats 1.0, 2.0, ... assigned in `NULLS LAST, created_at ASC` order; moved task excluded; same transaction; rolled back if validator subsequently fails).

**Cross-row blocker-order constraint:** server walks the transitive blocker chain (depth ≤ 10, `_REORDER_BLOCKER_CHAIN_DEPTH`). For each blocker B in same lane (`process_status=TODO`) with non-null sort_order, enforces `target.sort_order >= B.sort_order`. Violation → 422.

**Response 200:** `TaskRead` (the moved task with updated sort_order + updated_at).

**Errors (all 422, byte-locked in `routers/tasks.py`):**
- `{"detail":"reorder anchor #<n> not found in project"}` — anchor missing or cross-project.
- `{"detail":"reorder anchor #<n> is deleted"}` — anchor soft-deleted.
- `{"detail":"reorder requires moved task #<n> and anchor(s) to share the same process_status; moved=<n> before_id_status=<n|null> after_id_status=<n|null>"}` — same-lane violation. Missing-anchor renders as `null` (JSON-conformant; `_opt_int_str` helper). Locked 2026-05-12.
- `{"detail":"task #<T> cannot be ordered before its blocker #<B>"}` — blocker-order constraint violation (specific T, B pair).
- `{"detail":"reorder blocker chain exceeds maximum depth of 10"}` — defensive walker exhaust.
- Pydantic `422`: `"reorder requires at least one of before_id or after_id"` / `"before_id and after_id cannot reference the same task"`.
- `404`: `{"detail":"Task id=<n> not found"}` — moved task missing or soft-deleted.

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

**Response 201:** `SessionRead` (with `session_root_path` set, server-computed).

**Errors:**
- `400` — `{"detail":"project_id <n> does not exist"}` when `project_id` references a missing or soft-deleted project. Source-text-locked. (Kanban #716)
- `422` — Pydantic validation (e.g., `project_id<1`); also fires on extra fields in the body (`extra='forbid'` since Kanban #721, 2026-05-11) — smuggled `status` / `closed_at` / unknown keys return `detail[0].loc=["body", <field>]` + `type="extra_forbidden"`. Mirrors the `ConsentGrant` typed-acknowledgment pattern (#483).

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

**`TaskRead`** — `{id:int, project_id:int, parent_task_id:int|null, title, description, process_status:int (1..6; 6=CANCELLED added by #854), priority:int, assigned_role:int|null, run_mode:"manual"|"auto_pickup"|"auto_headless", task_kind:"ai"|"human" (default "ai" since #858), task_type:str, is_template:bool, recurrence_rule:str|null, recurrence_timezone:str, next_fire_at:datetime|null, spawned_from_task_id:int|null, scheduled_at:datetime|null, blocked_by:int|null, sort_order:float|null, acceptance_criteria:AcceptanceCriterion[]|null, subagent_models:SubagentModelEntry[] (NOT NULL, default [], Kanban #887), interaction_kind:"work"|"question"|"decision", question_payload:QuestionPayload|null, resume_context:object|null, halt_reason:str|null, status_change_reason:str|null (Kanban #854), is_pending:bool, created_at, updated_at, started_at:datetime|null, completed_at:datetime|null}` — `status_change_reason` is free-form rationale for a `process_status` flip; min_length=1 on POST/PATCH (empty `""` → 422); audit-trigger snapshots into `tasks_history`; most common pair `{process_status:6, status_change_reason:<text>}` on cancellation. `subagent_models` is always an array on the wire (never null — column is NOT NULL DEFAULT '[]'); each element shape: `{agent:str, model:"opus"|"sonnet"|"haiku", at:datetime}` validated by `SubagentModelEntry` at the API boundary.

**`SubagentModelEntry`** (Kanban #887, 2026-05-13) — `{agent:str (min_length=1), model:"opus"|"sonnet"|"haiku", at:datetime (ISO-8601 UTC)}`. JSONB element shape; `extra="forbid"` rejects unknown keys at 422. Lead populates `at` with UTC spawn timestamp; `agent` is the agent name from frontmatter. Full-replace PATCH semantics — Lead accumulates and sends the whole list.

**`AcceptanceCriterion`** (Kanban #797, shape mirrored at FE in `web/lib/api.ts` since #827) — `{text:str, status:"pending"|"passed"|"failed"|"na", verified_by:str|null, verified_at:datetime|null, notes:str|null}`. JSONB on disk; Pydantic enforces shape at API boundary. NULL on TaskRead.acceptance_criteria = field unset; empty array = explicitly cleared. Hard process_status=5 flip is NOT gated by the API — soft enforcement lives in Lead/agent prompts per CLAUDE.md "Acceptance criteria discipline".

`is_pending` (bool, default false) added 2026-05-11 by Kanban #750 (migration 0011). Means "in-flight work that hit a problem and is stuck" — orthogonal to `process_status`. Cross-state invariant: `is_pending=true` requires `process_status=2 (in_progress)`. Enforced **app-layer** by `services/is_pending.py` (4th cross-state validator in lockstep with `task_kind`/`run_mode`, `run_mode`/consent, `scheduled_at`/`is_template`). Fires on POST + PATCH against the **resolved-final** `(is_pending, process_status)` pair (PATCH-supplied if in `model_fields_set`, else existing row value). Mismatch → 400 with detail `"is_pending=true requires process_status=2 (in_progress)"` — source-text-locked in the validator + verified byte-equal by Tier-1 smoke (Kanban #756). No DB CHECK; abuse evidence may add one later. FE predicate: yellow card bg + `<PendingBadge>` + `data-card-pending="true"` render IFF `task.is_pending && task.process_status === IN_PROGRESS`.

`scheduled_at` (datetime ISO-8601 with TZ, default null) added 2026-05-10 by Kanban #723 (V3+ T1 audit follow-up; migration 0010). One-shot fire path. Mutually exclusive with `is_template=true` — DB CHECK `ck_tasks_scheduled_xor_template` is the backstop, but the wire-layer XOR (Pydantic + router resolved-final) catches first. Stored as TIMESTAMPTZ; serializes as trailing `Z` form on output.

`run_mode` added 2026-05-09 by Kanban #483 — Step 2 execution mode. Default `"manual"` (existing rows backfilled by migration `0005_run_mode_and_consent`).

`task_kind` + recurrence template fields (`is_template`, `recurrence_rule`, `recurrence_timezone`, `next_fire_at`, `spawned_from_task_id`) added 2026-05-10 by Kanban #706 (V3+ T1 / scope-lock). Defaults backfilled by migration `0007_task_kind_and_recurrence`: `task_kind='human'`, `is_template=false`, `recurrence_timezone='UTC'`, NULLs on the remaining nullable fields. `recurrence_rule` is a cron expression validated by `croniter.is_valid()`; `recurrence_timezone` is an IANA TZ name validated by `zoneinfo.available_timezones()`. `spawned_from_task_id` is system-managed lineage — settable on POST by the T2 scheduler when it spawns a child from a template; NEVER editable on PATCH. Datetime fields with UTC offset serialize as trailing `Z` form on output (Pydantic v2 default) regardless of input form — FE round-trip comparisons must use `Date.parse()`/`new Date(s)`, not string `===`.

Integer code fields (`process_status`, `priority`, `assigned_role`) follow `context/standards/general.md` §"Kanban schema codes". Note that the `tasks` lifecycle code is named `process_status` everywhere on the wire (renamed from `status` by the 2026-05-08 migration); `status` on the wire is reserved as the internal soft-delete flag and is not exposed.

**`SessionRead`** — `{id:int, project_id:int, process_label:str|null, status:"active"|"compacting"|"closed", token_budget_per_run:int|null, compacted_history_ceiling_tokens:int, recent_activity_ceiling_tokens:int, card_detail_ceiling_tokens:int, output_budget_tokens:int, session_root_path:str, started_at, closed_at:datetime|null, created_at, updated_at, runs_count:int, compacts_count:int}` — added 2026-05-10 by Kanban #716 (CTX-1); `card_detail_ceiling_tokens` + `output_budget_tokens` added 2026-05-10 by Kanban #722 (migration 0009, audit follow-up). `runs_count` / `compacts_count` are 0 on list responses; populated on detail GET only (avoids N+1).

**`SessionRunRead`** — `{id:int, session_id:int, task_id:int|null, status:"running"|"done"|"error"|"timeout", started_at, finished_at:datetime|null, total_input_tokens:int, total_output_tokens:int, total_context_chars:int, total_cost_usd:Decimal, budget_warning:bool, card_log_path:str|null, created_at, updated_at}` — added 2026-05-10 by Kanban #716. Server-stamps `finished_at` when transitioning to terminal status (`done`/`error`/`timeout`). `total_cost_usd` became server-authoritative 2026-05-10 by Kanban #718 (CTX-3) — see `PATCH /api/session_runs/{id}` Cost computation block. `provider` + `model` are PATCH inputs only; not persisted on the row.

**`SessionCompactRead`** — `{id:int, session_id:int, trigger_kind:"size"|"manual"|"run_count", archive_path:str, before_tokens:int, after_tokens:int, compact_model:str, compact_cost_usd:Decimal, compacted_at:datetime}` — added 2026-05-10 by Kanban #716. Read-only in CTX-1; POST/compact action ships in CTX-4 (#719).

**`SessionActivityCreate`** — `{task_id?:int>=1, summary:str(1..4000), role?:str(<=64), kind?:str(<=64)}` — added 2026-05-10 by Kanban #717 (CTX-2). Tightened to `extra="forbid"` by Kanban #721 (2026-05-11) for parity with `ConsentGrant`; smuggled keys → 422 `type="extra_forbidden"`.

**`SessionActivityRead`** — `{appended_block:str, section_preview:str, section_chars:int, compact_recommended:bool|null, current_recent_tokens:int|null, recent_ceiling_tokens:int|null}` — `appended_block`/`section_preview`/`section_chars` added 2026-05-10 by Kanban #717 (CTX-2); 3 advisory fields added 2026-05-10 by Kanban #718 (CTX-3). Advisory fields are `Optional` on the schema (forward-compat) but V1 router always sets them; tighten to required in a future hardening task if FE strict-typing demands.

**`SessionPromptRead`** — `{markdown:str, char_count:int}` — added 2026-05-10 by Kanban #717. `char_count` is `len(markdown)` (code points). Token count deferred to CTX-3 #718.

**`SessionRunHeartbeat`** — `{content:str(1..20000), mode:"append"|"replace"}` — added 2026-05-10 by Kanban #717. Tightened to `extra="forbid"` by Kanban #721 (2026-05-11) for parity with `ConsentGrant`; smuggled keys → 422 `type="extra_forbidden"`.

**`SessionRunHeartbeatRead`** — `{card_log_path:str, total_bytes:int}` — added 2026-05-10 by Kanban #717. `total_bytes` is the total card file size after this write (renamed from `bytes_written` per CTX-2 reviewer M1).

---

## Headless Auto-Run endpoints (Kanban #832 / #833, 2026-05-12)

### PATCH /api/tasks/{id} — action fields (Kanban #832)

In addition to the standard field-update semantics, the following **action-only fields** may be sent. They are NOT stored in DB columns — they trigger logic in the router and are popped before the ORM write.

| Field | Type | Description |
|---|---|---|
| `new_answer` | `string \| null` | Append an answer to `question_payload.answer_history`. Only valid when resolved `interaction_kind` is `'question'` or `'decision'`. |
| `new_answer_by` | `string \| null` | Who is submitting the answer. Used with `new_answer`. Default: `'user'`. |
| `invalidate_last_answer` | `boolean \| null` | When `true`, finds the last `is_valid=true` entry in `answer_history` and flips it to `false`. Requires `invalidated_reason`. |
| `invalidated_reason` | `string \| null` | Required when `invalidate_last_answer=true`. |

**Error details (422):**
- `"new_answer is only valid for interaction_kind 'question' or 'decision'"` — sent `new_answer` on a `work` task
- `"invalidated_reason is required when invalidate_last_answer=True"` — schema-layer rejection
- `"no valid answer to invalidate"` — `answer_history` has no `is_valid=true` entry
- `"no question_payload on this task — cannot invalidate"` — task has no payload

**Auto-unblock side effect:** when a `question` or `decision` task transitions to `process_status=5` (DONE), any tasks with `blocked_by=<this_task_id>` AND `status=1` (active) will have `blocked_by` cleared to `null`; their `halt_reason` is also cleared if it starts with `'Question:'`.

### GET /api/tasks/next-autorun (Kanban #833)

**Purpose:** read-only snapshot for the headless auto-run loop — what to do next.

**Header:** `X-Project-Id: <project_id>` (required)

**Response 200:** `NextAutorunResponse`
```json
{
  "next_task": TaskRead | null,
  "resume_tasks": [TaskRead],
  "pending_questions": [TaskRead],
  "blocked_count": int
}
```

- `next_task` — top-priority TODO task with `run_mode IN ('auto_pickup','auto_headless')`, `halt_reason IS NULL`, and blocker DONE or absent. Ordered: `priority DESC, sort_order ASC NULLS LAST, created_at ASC`.
- `resume_tasks` — tasks with `halt_reason IS NOT NULL` whose blocker is DONE (ready to re-run with resume_context).
- `pending_questions` — active `interaction_kind IN ('question','decision')` tasks not yet DONE.
- `blocked_count` — count of TODO/IN_PROGRESS tasks whose blocker is still active (not DONE).

No side effects.

### POST /api/tools/email/{gmail,outlook}/trash — `X-Agent-Role` tool-grant gate (Kanban #1799, 2026-06-02)

**New optional header:** `X-Agent-Role: <agent-type-name>` (e.g. `secretary`, `dev-backend`). Advisory + **spoofable** (Mode-A: stops agent drift, not malice).

**Layer-0 authorization gate** (fires before auth/quota), driven by `projects.config.tool_grants` `{ "<role>": ["<tool_name>", ...] }` (tool names `gmail.trash` / `outlook.trash`, validated against `services/tool_registry`):
- **403** `detail: "tool_grant_denied: role '<r>' is not granted tool '<t>' ..."` when the role **IS a key** in `tool_grants` and the tool is **NOT** in its list (empty list = denied every tool).
- **Allow (unrestricted)** when: `tool_grants` absent, role not a key, or header omitted (opt-in regime — you only lock down roles you list).

Complementary to (not replacing) `langgraph/tools/permission_gate` (tier-based, Mode-B) and the `gate.py` daily-units cap (both untouched). Discovery is Lead-mediated (Lead injects allowed tools into spawn briefs). Design: `shared/design/tool-registry-governance.md`.

### GET /api/user/pending (Kanban #1457 phase 2, 2026-06-02)

**Purpose:** cross-project HITL pending aggregate for the operator inbox badge.

**Header:** none — operator-scoped/cross-project, does **NOT** take `X-Project-Id`.

**Response 200:** `UserPendingResponse`
```json
{ "count": int, "oldest_age_hours": float | null, "by_project": [ {"project_id": int, "project_name": str, "count": int} ] }
```
- Predicate (mirrors phase-1 `InboxBadge.tsx`): `interaction_kind IN ('question','decision') AND process_status NOT IN (5,6) AND tasks.status=1 AND projects.status=1`.
- `oldest_age_hours` = age of the oldest pending task's `created_at`; `null` when `count=0`. `by_project` sorted by `project_name`. Single GROUP BY query (no N+1). No side effects.

### GET /api/tasks/{task_id}/outputs + /outputs/{filename} (Kanban #1305, 2026-06-12)

**Purpose:** task-output viewer — list + serve files agents wrote to the task's output folder. Consumed by `web/components/TaskOutputs.tsx` (drawer Outputs section).

**Headers:** `X-Project-Id` required on both. Gate order (mirrors tool-calls): 400 missing header → 404 unknown task → 400 cross-project → 410 soft-deleted parent.

**Listing 200:** `[ {"filename": str, "mime": str, "size": int, "kind": "chart"|"doc"|"export"|"text"} ]` — flat, sorted by filename, capped at `MAX_OUTPUT_FILES=50` (warning logged on truncation). Empty / no folder → `[]` (NOT an error). kind by ext: png/svg/html→chart; md→doc; csv/json→export; else→text.

**File serve:** filename validated (reject `/ \ .. NUL " CR LF` → 404, no echo; same rejection applied at listing-scan time); must be in the listing (client input never joined onto a root); containment via `Path.resolve()`+`is_relative_to`. Default `Content-Disposition: inline`; `?download=1` → `attachment`; **active content (.html/.htm/.svg/.xml) always forced `attachment`** (stored-XSS guard — FE previews via fetch+sandboxed iframe, never this inline path). Always `X-Content-Type-Options: nosniff`. Served as in-memory `Response` (threadpool `read_bytes`, 50 MB cap) — **FileResponse is BANNED on this app**: it deadlocks the whole server under the BaseHTTPMiddleware stack (live-reproduced 2026-06-12; TestClient cannot see it — real-socket probes required).

**Output-folder convention:** `working_path`+team=data-analytics → `<wp>/analysis/outputs/<task_id>/`; `working_path` set → `<wp>/outputs/<task_id>/`; `working_path` null → role-folder scan `<repo_root>/context/projects/<name>/<role>/` for `task-<id>-*` files + `<id>/` subdir (DIRECT files only; name-filter runs BEFORE stat — 9P bind-mount RPCs are ~47ms/file, an unfiltered scan of a 1356-file dir took 40-78s).

### GET /api/agents/validate (Kanban #1016, 2026-06-12)

**Purpose:** scan-all validator for `.claude/agents/*.md` frontmatter — file:line diagnostics for what Claude Code's session-start parse only reports as "agent doesn't exist". Same service backs the CLI: `docker exec agent-teams-api python -m scripts.validate_agents` (exit 1 on any error).

**Headers:** none — platform-level resource (no `X-Project-Id`). GET only; POST → 405. NO parameters by design (the spec's `POST body={path}` variant was dropped — it would be an arbitrary-path read primitive); scan dir is fixed server-side (`<repo_root>/.claude/agents`).

**Response 200:** `{"files_scanned": int, "diagnostics": [{"file": basename, "line": int, "field": str, "message": str, "severity": "error"|"warning"}], "error_count": int, "warning_count": int}` — basenames only on the wire, OSError messages path-stripped.

**Severity model:** errors = missing/duplicate/regex-violating `name` (fullmatch `^[a-z0-9]+(-[a-z0-9]+)*$`), missing `description`, bad `model` enum (opus/sonnet/haiku), malformed YAML (mark line), missing/empty frontmatter. Warnings = unknown frontmatter keys (real files carry e.g. `email_actions`) + unknown tool names. `tools` accepts YAML list | `"All tools"` | absent. Calibration lock: the real 38-file agents dir = 0 errors / 2 known warnings. Parser is line-oriented with `yaml.safe_load` fallback (strict whole-block YAML false-errors on mid-sentence colons in descriptions); BOM-tolerant (`utf-8-sig`).

### GET /api/agents + /api/agents/{name} (Kanban #1017, 2026-06-12)

**Purpose:** agent gallery — browse every installed `.claude/agents/*.md` with metadata; consumed by `web/app/agents` (grid + filters) and `web/app/agents/[name]` (detail). Built on the #1016 validation service (single parser).

**Headers:** none (platform-level, like /api/agents/validate). GET only.

**Listing 200:** flat array sorted by name: `{name, description, model: opus|sonnet|haiku|null, tools_summary ("All tools"|"N tools"), tool_count: int|null, hook_count: int, source_file: basename, domain, valid: bool, validation_errors: [diagnostics]}`. `domain` = name-prefix heuristic (dev/novel/content/secretary/sem/seo/data/general/other — agents carry no domain field). Invalid files still listed (`valid:false`; warnings don't invalidate).

**Detail 200:** all of the above + `{raw_frontmatter: str (verbatim), full_description: str, spawns: [{task_id, project_id, project_name, model: str|null, at: str|null}]}` — spawns = cross-project scan of `tasks.subagent_models` JSONB (`@>` pre-filter + `jsonb_array_elements` LATERAL, parametrized `text()`, soft-deleted excluded, newest-first w/ `at`→updated_at COALESCE fallback, cap 20). `at` can be null on legacy rows — FE must guard. 404: unknown name, regex-violating name (`AGENT_NAME_RE.fullmatch` gate before any FS/DB access), and RESERVED names.

**Route-order invariant:** FastAPI matches in REGISTRATION ORDER — the validation router's static `/agents/validate` registers before this router's `/{name}` in `main.py` (load-bearing); `RESERVED_AGENT_NAMES = {"validate"}` backstops it (reserved name → validator ERROR diagnostic + gallery-detail 404).

<!-- No endpoints documented yet. First endpoint goes above this line. -->
