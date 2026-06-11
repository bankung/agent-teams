# API contracts — CORE (hot endpoints · bootstrap-read)

> Split 2026-06-02 (#1798) from api-contracts.md. The few endpoints the Lead + agents touch every session (projects read + tasks CRUD). **All other endpoints: see [api-contracts.md](api-contracts.md) (full reference, on-demand).** Mirror source: api/src/schemas/*.py.

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


### GET /api/projects/stats
**Purpose:** Batched cross-project stats — powers the cross-project dashboard (Kanban #769, 2026-05-13; extended by #871 BE 2026-05-13). One entry per active (`status=1`) project in `projects.created_at ASC` order (deterministic; matches `GET /api/projects`). N+1-free: backend computes everything in exactly three SQL round-trips regardless of project count (project list + tasks GROUP BY + session_runs GROUP BY via sessions).
**Auth:** none. **Takes NO `X-Project-Id` header** — cross-project read (parity with `GET /api/projects`, `/api/projects/by-name/{name}`). Project endpoints don't carry the gate.
**Response 200:** `[ProjectStatsEntry, ...]`
**ProjectStatsEntry shape:**
```json
{
  "id": 1,
  "name": "agent-teams",
  "team": "dev",
  "run_mode_breakdown": {"manual": 143, "auto_pickup": 0, "auto_headless": 0},
  "counts": {"1": 24, "2": 2, "3": 0, "4": 0, "5": 117, "6": 0},
  "last_activity_at": "2026-05-13T04:30:25.270599Z",
  "cost_usage": {
    "total_input_tokens": 2102500,
    "total_output_tokens": 1050800,
    "total_context_chars": 0,
    "total_cost_usd": "105.2800",
    "budget_warning_count": 3,
    "session_run_count": 8
  }
}
```
- `counts` keys are string-form ints `"1".."6"` mirroring `tasks.process_status` (TaskStatus codes per general.md; `"6"` CANCELLED added by Kanban #854 2026-05-13). **All six keys always present** even when count is 0 — FE renders the lane grid without `||0` coalescing. The lane-row UI iterates a fixed LANES tuple (1..5) and currently DOES NOT render the cancelled count — that surface ships with #870.
- `run_mode_breakdown` mirrors `tasks.run_mode` ∈ `{manual, auto_pickup, auto_headless}`. **All three keys always present** even when count is 0.
- `last_activity_at` is `MAX(tasks.updated_at)` across the project's active (`status=1`) tasks **EXCLUDING `process_status=6` (CANCELLED)** — parity with the soft-delete-exclusion semantics; cancellation is dead-end work whose `updated_at` bump must not leak as "freshness". `null` when the project has zero non-cancelled active tasks. Locked by `test_stats_cancelled_excluded_from_last_activity_in_counts` (Kanban #854).
- `cost_usage` (Kanban #871 BE, 2026-05-13) — per-project rollup of `session_runs` (joined via `sessions.project_id`; do NOT route via the nullable `session_runs.task_id`). **All six keys always present**, zero-filled when the project has zero session_runs (parity with `counts` / `run_mode_breakdown` no-coalescing invariant). Fields:
  - `total_input_tokens` / `total_output_tokens` / `total_context_chars` (int) — `SUM(...)` across the project's session_runs.
  - `total_cost_usd` (**JSON string**, NOT number) — `SUM(session_runs.total_cost_usd)`. Pydantic v2 serializes `Decimal` as a JSON string (e.g. `"105.2800"` for stored `Numeric(10,4)` totals; `"0"` for the zero-fill default). Mirrors `SessionRunRead.total_cost_usd`. FE must parse via `Number(x)` / `parseFloat(x)` / Decimal.js — never plain `+x` arithmetic.
  - `budget_warning_count` (int) — count of `session_runs` rows where `budget_warning = true` for the project.
  - `session_run_count` (int) — total `session_runs` for the project. Cheapest "no usage yet" empty-state check on FE: `cost_usage.session_run_count === 0`.
  - `session_runs` / `sessions` have no soft-delete column (per db-schema.md "NO audit trigger" on those tables); no equivalent of the tasks-status filter applies on the cost aggregate.
- Soft-deleted projects (`projects.status=0`) excluded from the list entirely.
- Ordering: `projects.created_at ASC` (id ASC tiebreak) — both locked by pytest `test_stats_ordered_by_created_at_asc`.
**Errors:** none expected — the endpoint takes no params, no header, no body. An empty DB yields `[]`.

### GET /api/projects/by-name/{name}
**Purpose:** Look up a project by its unique name.
**Auth:** none
**Response 200:** `ProjectRead`
**Errors:**
- `404` — `{"detail":"Project '<name>' not found"}` when name does not exist

### GET /api/projects/{id}
**Purpose:** Direct id-based lookup. Active-only (parity with `/by-name/{name}`). Added 2026-05-11 by Kanban #691 — prior to this slice the path returned 405 Method Not Allowed (only PATCH + DELETE were registered on `/{project_id}`).
**Auth:** none
**Response 200:** `ProjectRead`
**Errors:**
- `404` — `{"detail":"Project id=<n> not found"}` when id does not exist OR row is soft-deleted (`status=0`). Source-text-locked in `routers/projects.py` + `tests/test_routes_smoke.py` per the #122 pattern. Byte-equal with PATCH `/api/projects/{id}`, DELETE `/api/projects/{id}`, POST `/grant-consent` 404 detail (single shared format).

### GET /api/tasks
**Purpose:** List tasks for the session-bound project (paginated, filterable).
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. List scope is taken from the header — the legacy `?project_id=<int>` query param was REMOVED by Kanban #695. (See top-level Headers section.)
**Query:** `operator_gate` (`"any"` | `"key"` | `"commit"` | `"decision"` | `"hitl"` | `"external"`, optional — Kanban #2127 2026-06-11. Filter to operator-gated ("blocked-on-operator") tasks: a task matches iff its task-level `operator_gate` IS NOT NULL [and equals the value when not `any`] OR ≥1 `acceptance_criteria` item has `gate='operator'` AND `status='pending'` [and `gate_kind=<value>` when not `any`]. Passed/na AC items no longer gate. AC predicate uses `@>` containment backed by GIN `ix_tasks_ac_gin (jsonb_path_ops)`. Bad value → 422. Composes with the other filters below.), `process_status` (1..6, optional — `6=CANCELLED` added by Kanban #854 2026-05-13), `pending` (bool, default false — when true, return only rows with `process_status != 5` AND `process_status != 6`, i.e., todo + in_progress + review + blocked. Convenience shortcut for the Lead-bootstrap "list pending tasks" query. When both `pending=true` and `process_status=N` are provided, `process_status` wins (more specific) and `pending` is silently ignored — enforced by `elif pending:` in `routers/tasks.py` `list_tasks`. Added 2026-05-10 by Kanban #697.), `include_cancelled` (bool, default false — when true, surface `process_status=6` rows in the default list. Precedence: explicit `?process_status=N` wins over `include_cancelled`; same pattern as `pending`. Added 2026-05-13 by Kanban #854 — cancelled is a terminal dead-end-work state, hidden by default to keep boards clean), `assigned_role` (optional), `parent_task_id` (optional, ge=1 — return only direct children of N), `top_level_only` (bool, default false — when true, return only `parent_task_id IS NULL` rows), `limit`, `offset`. Precedence when both `parent_task_id` and `top_level_only` are provided: `top_level_only` takes precedence and `parent_task_id` is silently ignored (`routers/tasks.py` `list_tasks` `if top_level_only: ... elif parent_task_id is not None: ...`). Subtask hierarchy added 2026-05-08 by Kanban #238.
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

`task_kind` (`"ai"` | `"human"`, optional, **default `"ai"`** — flipped from `"human"` by Kanban #858 2026-05-13). V3+ scope-lock 2026-05-10 (Kanban #706). Discriminates AI-runner-driven work from human work. **Cross-table rules at `services/task_kind.py` (BEFORE the consent gate):** (1) `task_kind='human'` MUST pair with `run_mode='manual'`; mismatch → 400. (2) **Kanban #858 interaction-kind coerce:** when resolved `interaction_kind IN ('question','decision')`, the API SILENTLY coerces `task_kind='human'` AND `run_mode='manual'` regardless of caller-supplied values (Option A — atomic; the HUMAN↔MANUAL 400 never fires on question/decision bodies because the coerce runs first). On PATCH: flipping `interaction_kind` to `question`/`decision` also flips `task_kind` + `run_mode`. Reverse (`question/decision → work`) does NOT auto-revert `task_kind`; callers must explicitly PATCH `task_kind="ai"` to revert.

`is_template` (bool, optional, default false) — recurrence template flag. When true, `recurrence_rule` AND `next_fire_at` are required (Pydantic 422; DB CHECK 400 fallback).

`recurrence_rule` (str, optional, max 255, default null) — cron expression. Pydantic field validator runs `croniter.is_valid()`; invalid → 422 with `recurrence_rule` in the error loc.

`recurrence_timezone` (str, optional, max 64, default `"UTC"`) — IANA TZ name. Pydantic field validator checks `zoneinfo.available_timezones()`; unknown → 422 with `recurrence_timezone` in the error loc.

`next_fire_at` (datetime ISO-8601 with timezone, optional, default null) — scheduler hot-path target. Datetimes serialize as trailing `Z` form on output (Pydantic v2 default).

`spawned_from_task_id` (int, optional ge=1, default null) — system-managed lineage pointer. Set by the T2 scheduler on spawn from a template; user-driven POSTs default to null. NEVER editable on PATCH (V1 forbids re-parenting lineage; same model_fields_set membership pattern as `parent_task_id`).

`scheduled_at` (datetime ISO-8601 with timezone, optional, default null) — added 2026-05-10 by Kanban #723 (V3+ T1 audit follow-up). One-shot fire time for the T2 scheduler; non-recurring. **Mutually exclusive with `is_template=true`** — sending both → 422 (Pydantic XOR with detail substring containing both `scheduled_at` AND `is_template`). DB CHECK `ck_tasks_scheduled_xor_template` is the raw-SQL-bypass backstop. Stored in TIMESTAMPTZ — clients may send any TZ offset (e.g. `+07:00`); response always serializes to UTC `Z` form (verified live with `+07:00` → `Z` round-trip). Templates use `recurrence_rule` + `next_fire_at` (T1 path); regular one-shot tasks use `scheduled_at` (this path). T2 scheduler scans both fire paths.

`is_pending` (bool, optional, default false) — added 2026-05-11 by Kanban #750 (migration 0011). Marks an in-progress row as "stuck/blocked". Cross-state validator rejects `is_pending=true` paired with `process_status != 2` (POST path; see 400 below). FE renders yellow card bg + pending badge + `data-card-pending="true"` when both predicates hold.

`halt_reason` (str, optional, min_length=1, default null) — added 2026-05-12 by Kanban #785 (migration `0013_tasks_halt_reason`). Free-form halt signal for full-auto Lead sessions. Non-empty string = task is halted (auto-pickup query in #786 skips these); null/absent = task runs normally. Empty `""` → 422 with `type=string_too_short` at `loc=["body","halt_reason"]`. Orthogonal to `process_status` (same pattern as `is_pending`).

`blocked_by` (int, optional, ge=1, default null) — added 2026-05-12 by Kanban #771 (migration `0017_tasks_blocked_by`). Single-blocker dependency pointer; null = unblocked. **Status-code policy locked 2026-05-12:** cross-row business-rule rejections (FK target deleted/cross-project/self/cycle) return **422** (RFC 4918 Unprocessable Entity — semantically violated, not malformed). Parent_task_id validators still return 400 (legacy lock 2026-05-08); not migrated this slice. POST validates existence + same-project only — self-reference + cycle are structurally impossible on POST (new row has no id yet). Errors below (all 422, byte-locked):
- `{"detail":"blocked_by <n> does not exist or is deleted"}` when the FK target is missing or soft-deleted.
- `{"detail":"blocked_by <n> belongs to a different project"}` when the FK target's `project_id` ≠ payload's `project_id`.

`operator_gate` (`"key"`|`"commit"`|`"decision"`|`"hitl"`|`"external"`, optional, default null) + `operator_gate_note` (str, optional, no length floor) — Kanban #2127 (2026-06-11, migration `2026_06_11_0100_operator_gate`). Task-level "blocked-on-operator" rollup; Lead-set ONLY (no auto-derivation from ACs). PATCH semantics = halt_reason posture: key-absent=unchanged, explicit-null=clear, value=set; note settable independently; clearing the gate does NOT cascade-clear the note. Pydantic Literal gates values (422), no DB CHECK (#1677 posture). AC items additionally accept optional `gate` (`"operator"` only) + `gate_kind` (same 5-enum) — old-shaped AC arrays keep validating; an AC item gates only while `status="pending"`.

`effort_override` (`"off"`|`"low"`|`"medium"`|`"high"`|`"extra"`|`"max"`, optional, default null) — Kanban #2300 (2026-06-11, migration `0065_effort_mode`). Per-task Anthropic effort carrier; null = inherit. Resolution: task carrier > `projects.effort_mode` (`off/low/medium/high/extra/auto`, same migration, PATCH /api/projects) > off. `max` is manual-only — the worker's auto mode clamps at `extra` and writes the resolved level here when the carrier is empty. `session_runs.effort` records the resolved level per run. Pydantic Literal only (422), no DB CHECK. PATCH explicit-null = clear-to-inherit. Design lock: decisions.md 2026-06-11 #2300.

`subagent_models` (list of `SubagentModelEntry`, optional, default `[]`) — added 2026-05-13 by Kanban #887 (migration `0023_tasks_subagent_models`). Append-only audit log of subagent spawns for this task. NOT NULL DEFAULT '[]' at the DB layer — always an array on the wire (the response field is never null). Append logic is on Lead's side; the API accepts the full accumulated list and stores it verbatim (full-replace semantics). Each `SubagentModelEntry`: `{agent: str (min_length=1), model: "opus"|"sonnet"|"haiku", at: datetime (ISO-8601 UTC)}`. `extra="forbid"` on the element type — unknown keys → 422. Bad `model` value → 422. Missing required field → 422.

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
  - `{"detail":"is_pending=true requires process_status=2 (in_progress)"}` — cross-state validator at `services/is_pending.py`. Fires on POST when `is_pending=true` AND `process_status != 2`. Source-text-locked (byte-equal verified by Tier-1 smoke #756). Pure-function check; runs after the task_kind/run_mode pair, before the consent gate. (Kanban #750)
  - `{"detail":"Task creation violates a database constraint"}` (fallback for unknown constraints)
- `422` — Pydantic validation error. Includes `run_mode` outside the literal set; `task_kind` outside `{"ai","human"}` (Kanban #706); invalid cron in `recurrence_rule`; unknown IANA TZ in `recurrence_timezone`; `is_template=true` without both `recurrence_rule` AND `next_fire_at` (Kanban #706); `is_template=true` AND `scheduled_at IS NOT NULL` in the same body — XOR rejection, detail substring contains both `scheduled_at` AND `is_template` (Kanban #723).

### PATCH /api/tasks/{id}
**Purpose:** Partial update. Transitioning to `process_status=2` (in_progress) sets `started_at=now()` if NULL; transitioning to `process_status=5` (done) sets `completed_at=now()`. Server bumps `updated_at` on any real field change; an unchanged-body PATCH is a no-op (N7 no-op-skip — `routers/tasks.py:121-130`; parity with PATCH `/api/projects/{id}`).
**Auth:** none
**Headers:** `X-Project-Id: <int>` REQUIRED. The fetched row's `project_id` must match the header value; mismatch → 400. (Kanban #695)

**Request:** any subset of `{title, description, process_status, priority, assigned_role, started_at, completed_at, run_mode, task_kind, is_template, recurrence_rule, recurrence_timezone, next_fire_at, scheduled_at, is_pending, halt_reason, blocked_by, sort_order, subagent_models}`. The soft-delete `status` flag is intentionally absent — sending `{"status": 0}` is silently ignored (use `DELETE` to soft-delete). `parent_task_id` AND `spawned_from_task_id` are BOTH REJECTED (V1 forbids re-parenting subtask hierarchy per Kanban #238 AND recurrence lineage per Kanban #706) — see 422 below. `scheduled_at` accepts any TZ offset on input; storage + GET response always normalize to UTC `Z` form. Set `{"scheduled_at": null}` to un-schedule a one-shot task (Kanban #723). `halt_reason` PATCH semantics (Kanban #785): key-absent = unchanged; explicit `null` = clear/unhalt; non-empty string = halt; `""` → 422. `blocked_by` PATCH semantics (Kanban #771, 2026-05-12): key-absent = unchanged; explicit `null` = clear/unblock; positive int = set/change blocker (router walks the chain up to depth=10 for cycle detection). Unlike `parent_task_id` / `spawned_from_task_id`, `blocked_by` IS modifiable post-create — re-blocking is supported and expected. `sort_order` PATCH semantics (Kanban #772, 2026-05-12): key-absent = unchanged; explicit `null` = clear (NULL — falls back to created_at ordering); positive float = set directly. After applying, server runs the blocker-order constraint when EITHER `sort_order` or `blocked_by` is in the body (resolved-final); violation → 422 with the same locked detail as the reorder endpoint: `"task #<T> cannot be ordered before its blocker #<B>"`. No-op skip parity: PATCH with `sort_order` equal to existing → no `updated_at` bump. `subagent_models` PATCH semantics (Kanban #887, 2026-05-13): key-absent = unchanged; send the full accumulated list to replace (full-replace, no element-merge; Lead accumulates on its side). The field is NOT NULL in the DB — explicit `null` is semantically invalid (never clears to NULL); omit the key to leave unchanged. Each element: `{agent: str (min_length=1), model: "opus"|"sonnet"|"haiku", at: ISO-8601 UTC datetime}`. Unknown element keys → 422 (`extra="forbid"` on `SubagentModelEntry`). Bad `model` value → 422. Missing `at` → 422.

**Response 200:** `TaskRead`

**Errors:**
- `404` — task id not found
- `422` — Pydantic validation error if `parent_task_id` OR `spawned_from_task_id` is **present in the body** (whether int OR null). V1 forbids re-parenting both subtask hierarchy (#238) AND recurrence lineage (#706); clients must omit both keys. The router's `model_validator` checks `model_fields_set` membership, so explicit-null is treated identically to a non-null value. Error message substring: the field name. (Kanban #238 + #706)
- `422` — `{"detail":[{"msg":"Value error, is_template=true requires recurrence_rule and next_fire_at", ...}]}` when the PATCH payload sets `is_template=true` without **also** supplying both `recurrence_rule` AND `next_fire_at` in the same body. **Option A wire contract:** the validator inspects payload only, never the existing row — clients must self-contain the full template tuple when flipping `is_template=true`, even on a row that already carries `recurrence_rule + next_fire_at` from a prior PATCH. Detail string is byte-for-byte identical to POST `/api/tasks` (single source-text-locked contract for create + patch). DB CHECK `ck_tasks_template_recurrence_complete` is the backstop for raw-SQL bypass paths. (Kanban #714)
- `422` — `{"detail":[{"msg":"Value error, recurrence_timezone cannot be explicitly null — omit the key to leave the existing value, or send a valid IANA TZ string", ...}]}` when the PATCH body contains `{"recurrence_timezone": null}`. Missing-key (key absent from payload) is a no-op — PATCH "missing = don't touch" semantic preserved via `model_fields_set`. Source-text-locked. (Kanban #714)
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
  - `{"detail":"template fields incomplete violates ck_tasks_template_recurrence_complete"}` (defensive — Pydantic model_validator on TaskCreate (Kanban #706) AND TaskUpdate (Kanban #714) catches at 422 first; this 400 only fires on raw-SQL bypass)
  - `{"detail":"scheduled_at is incompatible with is_template=true (use recurrence_rule for templates)"}` — fires when the **resolved final** state (PATCH-supplied OR existing if not in body) has `is_template=true AND scheduled_at IS NOT NULL`. Caught at the **router** (HTTP 422 — application-layer pre-check, not DB CHECK fallback) so direction-A (existing template + PATCH adds scheduled_at) AND direction-B (existing scheduled_at + PATCH flips is_template=true) BOTH 422 with this detail. Resolved-final pattern mirrors `task_kind`/consent. Same source-text-locked detail string also surfaces as 400 if a raw-SQL bypass triggers DB CHECK `ck_tasks_scheduled_xor_template`. (Kanban #723)
  - `{"detail":"is_pending=true requires process_status=2 (in_progress)"}` — fires when the **resolved final** `(is_pending, process_status)` pair (PATCH-supplied OR existing if not in body) is `(true, !=2)`. Both directions caught: direction-A (PATCH is_pending=true on a ps=1 row) AND direction-B (PATCH process_status=1 on a ps=2+is_pending=true row — validator sees `(true, 1)`). Bundled clear `{process_status:1, is_pending:false}` succeeds (resolved pair is `(false, 1)`). Source-text-locked at `services/is_pending.py` (byte-equal verified by Tier-1 smoke #756). Pure-function check; ordered after task_kind/run_mode, before consent gate. (Kanban #750)
  - `{"detail":"status violates ck_tasks_status_valid"}` (defensive — `status` is not a public PATCH field)
  - `{"detail":"Task update violates a database constraint"}` (fallback for unknown CHECK constraints)
- `422` — `blocked_by` validator failures (Kanban #771, 2026-05-12; status-code policy locked 422 same date). Byte-locked source strings in `routers/tasks.py` PATCH handler:
  - `{"detail":"blocked_by cannot reference self"}` when `blocked_by == task_id` (self-reference rejected; the DB CHECK `ck_tasks_blocked_by_not_self` is the raw-SQL bypass backstop).
  - `{"detail":"blocked_by <n> does not exist or is deleted"}` when the FK target is missing or soft-deleted.
  - `{"detail":"blocked_by <n> belongs to a different project"}` when the FK target's `project_id` differs from the row's `project_id`.
  - `{"detail":"blocked_by <n> would create a cycle (depth <N>)"}` when the chain walk from the new blocker hits `task_id` within depth ≤ 10. Depth in the message is 1-indexed.
  - `{"detail":"blocked_by chain exceeds maximum depth of 10"}` defensive — fires when the walker exits the for-range without break. Should not occur in practice; real chains are 1-3 deep.

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

