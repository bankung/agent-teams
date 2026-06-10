# Code map — api/ (2026-06-10)

## Totals

| metric | count |
|---|---|
| source files (`api/src/**/*.py`) | 145 |
| total source LOC | 40,413 |
| routers registered in `main.py` | 29 (28 router objects + 1 pnl_router split from pl.py) |
| endpoints (`@router.*` decorators, all router files) | ~130 (118 `@router.*` hits + ~12 via `router_project`/`router_resource`/`pnl_router`/`runs_router` alternate names) |
| ORM models (`__tablename__`) | 18 tables across 16 model files |
| service files (`services/*.py`) | 63 |
| total service LOC | 13,535 |
| alembic migrations | 63 |
| test files | 136 |
| test LOC | 58,527 (including `conftest.py` at 613 LOC) |

Test LOC exceeds source LOC by 45%. The test suite is the largest single body of code in the repo.

---

## Modules

### `api/src/` — top-level

| module | LOC | purpose | key deps | public surface | tests | status | evidence |
|---|---|---|---|---|---|---|---|
| `main.py` | 444 | App factory: CORS, middlewares, router mount, APScheduler lifespan (recurrence + backup + health + HITL nudge + audit-archive) | FastAPI, APScheduler, all routers | `GET /health` | `test_lifespan_db_validation.py` | live | called at container start |
| `db.py` | 122 | Async engine + `SessionLocal` factory; SQLAlchemy pool (3/2 per #2110) | sqlalchemy asyncpg | `SessionLocal`, `engine` | `test_db_build_engine_canary.py` | live | imported by every router |
| `settings.py` | 132 | Pydantic-settings `Settings` class; intentionally NOT `@lru_cache` (incident fix) | pydantic-settings | `get_settings()` | `test_lifespan_db_validation.py` | live | called at every request |
| `constants.py` | 420 | Enum-like constants (task status, roles, task types, etc.) | stdlib | enums | — | live | imported widely |
| `pricing.py` | 142 | Provider model → token cost lookup table | stdlib | `PRICING_TABLE` | `test_pricing.py` | live | used by `cost_tracker.py` |

### `api/src/routers/`  (total 13,611 LOC)

| module | LOC | purpose | key deps (services) | public surface | tests | status | evidence |
|---|---|---|---|---|---|---|---|
| `tasks.py` | 3,030 | Core Kanban task CRUD + lifecycle transitions (create, update, reorder, DnD, DONE-flip, HITL decide, snooze, delete, next-autorun, ai-parse, resolve-flag, comments) | `ai_task_parser`, `content_moderation`, `recurrence`, `budget_enforcer`, `budget_gate`, `run_mode`, `task_cost_estimator`, `task_interaction`, `task_kind`, `session_project`, `operator_auth`, `action_templates`, `handoff_spawn`, `task_comment`, `notify_ntfy`, `audit_flag`, `notification_router`, `pause_switch` | 16 endpoints (`GET/POST/PATCH/DELETE /api/tasks`, `/api/tasks/{id}`, sub-routes) | `test_routes_smoke.py`, `test_tasks_*.py` (14 files) | live | `web/lib/api.ts:690`, `langgraph/worker.py:263,941` |
| `projects.py` | 1,173 | Project CRUD + kill/revive/pause/unpause/grant-consent/reconcile-budget/stats/progress-stats | `kill_switch`, `pause_switch`, `budget_gate`, `zero_config_scaffold` | 15 endpoints | `test_projects_stats.py`, `test_kill_switch_integration.py` | live | `web/lib/api.ts:346,365,458,558` |
| `tools_email.py` | 1,817 | Gmail + Outlook actions (trash/mark/archive/draft/search/get/thread/labels/attachment) + OAuth start/callback/status | `tool_grants`, `credentials_crypto`, `tools/email/gmail_client`, `tools/email/outlook_client` | 22 endpoints under `/api/tools/email` | `test_tools_email.py`, `test_email_tier1_actions.py`, `test_email_tier_gate.py` | live | `.claude/agents/secretary.md:66`, `.claude/hooks/secretary-email-action-gate.ps1` |
| `sessions.py` | 751 | Session + session_run CRUD; compact; heartbeat; prompt read; activity append | `compact_runner`, `session_store` | 10 endpoints + `runs_router` (separate APIRouter) | `test_sessions.py` | live | `langgraph/worker.py:815,878` |
| `ingest.py` | 715 | Email-to-task (Mailgun shape) + generic JSON webhook ingest | `email_ingest` service | 2 endpoints (`POST /api/ingest/email`, `POST /api/ingest/webhook/{project_id}/{tag}`) | `test_email_ingest.py`, `test_webhook_ingest_router.py` | dormant | no FE grep hit; called by external Mailgun webhook only |
| `resources.py` | 555 | Project file/link resources (two router objects: `router_project`, `router_resource`) | `resource_storage`, `resource_verify` | 6 endpoints (`POST/GET /api/projects/{id}/resources`, `GET/DELETE /api/resources/{id}`, preview) | `test_resources_integration.py`, `test_resources_link_smoke.py` | live | `web/lib/api.ts:2188,2206,2241,2257` |
| `webhooks.py` | 554 | Stripe + PayPal payment webhook ingest → `transactions` table | `webhook_verifiers`, `webhook_rate_limit` | 2 endpoints (`POST /api/webhooks/stripe/{project_id}`, `.../paypal/{project_id}`) | `test_webhooks_router.py` | dormant | no FE or worker grep hit; external payment providers only |
| `tools_calendar.py` | 477 | Google + Outlook calendar: list-events, freebusy, create-event, respond | `tools/email/calendar_client`, `tools/email/outlook_calendar_client` | 4 endpoints under `/api/tools/calendar` | `test_calendar_tools.py` | live | `.claude/agents/secretary.md` via tn-email skill |
| `credentials.py` | 458 | Per-project credential vault CRUD (Fernet-encrypted) | `credentials_crypto` | 5 endpoints | `test_credentials_router.py` | live | `web/lib/api.ts` (no direct grep hit — accessed via settings/integrations UI) |
| `user_actions.py` | 385 | Cross-project next-action recommender + `GET /api/user/pending` | `budget_enforcer`, `next_action_ranker` | 2 endpoints | `test_user_next_action.py`, `test_user_pending.py` | live | `web/lib/api.ts:1784` |
| `pl.py` | 375 | Per-project P&L + cross-project rollup (`pnl_router`) | `pl_calculator` | 3 endpoints (`GET /api/projects/{id}/pl`, `/api/projects/{id}/export`, `GET /api/pnl`) | `test_pl_endpoint.py`, `test_pl_cross_project.py` | live | `web/lib/api.ts:1454,1535` |
| `milestones.py` | 340 | Per-project milestone CRUD + rollup | `milestone` model | ~8 endpoints | `test_milestones_smoke.py` | live | `web/lib/api.ts:1921,1944,1961` |
| `task_templates.py` | 307 | Global task-template CRUD (operator-gated write) | `template_render`, `action_templates` | ~6 endpoints | `test_task_templates_router.py` | live | `web/lib/api.ts:1906` |
| `push.py` | 300 | Web Push (VAPID) subscription CRUD | `notify_web_push` | ~5 endpoints | `test_push_subscriptions_smoke.py` | live | `web/lib/api.ts:1257,1267,1292,1311` |
| `handoff_templates.py` | 259 | Handoff-template CRUD (auto-spawn on DONE-flip) | `handoff_spawn` | ~4 endpoints | `test_handoff_templates_smoke.py` | live | `web/lib/api.ts:1689` |
| `digest.py` | 242 | Daily-digest fire (Gmail SMTP) + opt-out token check; calls `skill_stub_detector` + `stale_doc_curator` as side-effects | `skill_stub_detector`, `stale_doc_curator`, `digest_template`, `notify_email` | 2 endpoints (`POST /api/digest/fire`, `GET /api/notifications/digest-optout`) | `test_digest_router.py`, `test_digest_integration.py` | live | called by operator manually / scheduler |
| `transactions.py` | 217 | Transaction ledger CRUD (income/expense entries) | `pl_calculator` | ~5 endpoints | `test_transactions.py` | live | `web/lib/api.ts` (P&L flow) |
| `tools_directory.py` | 209 | Agent-runtime tool directory + missing-tool suggestion | `tool_registry` | ~4 endpoints | `test_tool_directory.py` | live | `.claude/agents` tool-check calls |
| `audit.py` | 202 | Cross-project daily audit rollup aggregation | `audit_flag`, `audit_archive` | ~4 endpoints | `test_audit_rollup.py` | live | `web/lib/api.ts:438` |
| `notifications.py` | 201 | Notification delivery endpoint + opt-out | `notification_router` | ~4 endpoints | `test_notification_router.py`, `test_notifications_optout.py` | live | `.claude/hooks/notify-session-waiting.ps1:159` |
| `scaffold.py` | 158 | Project file scaffold (team-specific context file templates) | `zero_config_scaffold`, `project_scaffold` | ~4 endpoints | `test_routes_scaffold.py` | live | `bin/agent-teams-init.ps1:134` |
| `decisions.py` | 142 | Retro decisions feed (read-only JSONB decisions.md snapshot) | `task` model | ~3 endpoints | — | live | `web/lib/api.ts` (referenced in comments) |
| `tool_calls.py` | 141 | Specialist tool-call audit timeline (sub-resource of tasks) | `tool_call_writer` | ~3 endpoints | `test_tool_calls.py` | live | `web/lib/api.ts:1111` |
| `usage.py` | 136 | Cross-project LLM provider cost rollup | `cost_tracker`, `token_counter` | ~2 endpoints (`GET /api/usage/daily`) | `test_usage_daily.py` | live | `web/lib/api.ts:2301` |
| `dashboard.py` | 108 | Cross-project active-task list (operator-level) | `project`, `task` models | ~2 endpoints | `test_dashboard_active_tasks.py` | live | `web/lib/api.ts:1572` |
| `push_ntfy.py` | 98 | ntfy push-notification fire (POST /api/push/fire) | `notify_ntfy` | 1 endpoint | `test_push_ntfy_router.py` | live | called manually / from `tasks.py` HITL trigger |
| `events.py` | 96 | SSE stream for row-changed events (`/api/events/stream`) | `row_changed_listener` | 1 endpoint | `test_sse.py` | live | `web/lib/WildcardSSEContext.tsx:79`, `useRowChangedEvents.ts:73` |
| `settings.py` | 83 | Integrations settings popup (toggle enable/disable) | `integrations_registry` | ~2 endpoints | `test_settings_router.py` | live | `web/lib/api.ts:1759` |
| `teams.py` | 44 | Global team registry (GET /api/teams) | `constants` | 1 endpoint | — | live | `web/lib/api.ts:1637` |
| `templates.py` | 37 | Action template library (GET /api/templates/actions) | `action_templates` | 1 endpoint | — | live | `web/lib/api.ts:1647`, `web/components/ActionTemplatePicker.tsx:6` |

### `api/src/models/` (total 3,093 LOC)

| module | LOC | table(s) | status |
|---|---|---|---|
| `task.py` | 732 | `tasks`, `tasks_history` | live |
| `project.py` | 451 | `projects` | live |
| `session.py` | 326 | `sessions`, `session_runs`, `session_compacts` | live |
| `credential.py` | 199 | `project_credentials`, `credential_access_log` | live |
| `project_resource.py` | 176 | `project_resources` | live |
| `transaction.py` | 166 | `transactions` | live |
| `task_template.py` | 156 | `task_templates` | live |
| `handoff_template.py` | 155 | `handoff_templates` | live |
| `milestone.py` | 138 | `milestones` | live |
| `push_subscription.py` | 125 | `push_subscriptions` | live |
| `projects_audit.py` | 124 | `projects_audit` | live |
| `tool_call.py` | 109 | `tool_calls` | live |
| `task_comment.py` | 101 | `task_comments` | live |
| `email_oauth_token.py` | 85 | `email_oauth_tokens` | live |

### `api/src/schemas/` (total 6,311 LOC)

| module | LOC | purpose |
|---|---|---|
| `task.py` | 1,463 | TaskCreate/Read/Update + sub-schemas (acceptance_criteria, question_payload, etc.) |
| `project.py` | 1,225 | ProjectCreate/Read/Update + approval_policies, tools_config |
| `tools_email.py` | 713 | Gmail + Outlook request/response bodies |
| `tools_calendar.py` | 352 | Calendar list/create/respond request/response bodies |
| `session.py` | 343 | Session/SessionRun/SessionCompact read/write |
| `project_resource.py` | 198 | Resource upload/link/read shapes |
| `pl.py` | 158 | PLSummary, PLCrossProject response shapes |
| `handoff_template.py` | 166 | HandoffTemplate CRUD shapes |
| `task_template.py` | 158 | TaskTemplate CRUD shapes |
| `milestone.py` | 153 | Milestone CRUD + rollup shapes |
| others (15 files) | ~1,182 | credential, push, ai_task, dashboard, webhook, audit, notification, tool_call, transaction, user_actions, usage, email_ingest, integration, action_template, task_comment |

### `api/src/services/` (total 13,535 LOC, 63 files)

Grouped by function:

**Scheduling / control-plane** (called from `main.py` lifespan):
| module | LOC | purpose | status |
|---|---|---|---|
| `health_monitor.py` | 687 | Periodic API self-health sweep (pings /health, project checks) | live |
| `backup.py` | 626 | S3 encrypted backup runner (boto3, deferred import) | live (dormant if BACKUP_S3_BUCKET unset) |
| `recurrence.py` | 330 | Template-spawn + one-shot transitions tick | live |
| `hitl_nudge.py` | 207 | HITL aging nudge APScheduler job | live |
| `audit_archive.py` | 312 | Daily audit-task archival sweep | live |

**Task lifecycle** (called from `routers/tasks.py`):
| module | LOC | purpose | status |
|---|---|---|---|
| `ai_task_parser.py` | 353 | LLM-based task text → structured fields | live |
| `content_moderation.py` | 231 | Destructive-intent pattern scan on task payload | live |
| `budget_enforcer.py` | 391 | Per-task cost budget check + spend compute | live |
| `budget_gate.py` | 365 | Spawn-budget gate (pre-create check) | live |
| `approval_evaluator.py` | 275 | Project `approval_policies` evaluation | live |
| `task_cost_estimator.py` | 207 | Token-cost estimation before task start | live |
| `task_interaction.py` | 170 | Task question/answer interaction helpers | live |
| `task_kind.py` | 86 | task_kind coercion + defaults | live |
| `is_pending.py` | 46 | Assert task is in pending state | live |
| `run_mode.py` | 60 | Consent gate for run_mode changes | live |
| `handoff_spawn.py` | 175 | Spawn child task from handoff template on DONE-flip | live |
| `notification_router.py` | 422 | DeliveryTarget DSL — fan-out push to web/ntfy/telegram/email | live |
| `audit_flag.py` | 325 | Audit-report → flag creation + routing | live |
| `action_templates.py` | 149 | Action template library lookup | live |
| `task_comment.py` | 56 | Task comment post helper | live |

**Session management**:
| module | LOC | purpose | status |
|---|---|---|---|
| `session_store.py` | 388 | Session + run read/write/compact helpers | live |
| `compact_runner.py` | 430 | Session compaction runner | live |
| `session_project.py` | 93 | X-Project-Id session scope resolver | live |

**Project management**:
| module | LOC | purpose | status |
|---|---|---|---|
| `kill_switch.py` | 360 | Project kill/revive state machine | live |
| `pause_switch.py` | 653 | Project pause/unpause + flag-resolve logic | live |
| `project_scaffold.py` | 128 | Context-dir scaffold for new projects | live |
| `zero_config_scaffold.py` | 298 | Zero-config scaffold (team-specific file templates) | live |

**Resources**:
| module | LOC | purpose | status |
|---|---|---|---|
| `resource_storage.py` | 270 | File upload + storage path management | live |
| `resource_verify.py` | 446 | Link/file resource verification pipeline | live |

**External integrations** (email/calendar/webhooks):
| module | LOC | purpose | status |
|---|---|---|---|
| `email_ingest.py` | 266 | Email ingest → task creation | live (dormant if Mailgun not configured) |
| `webhook_verifiers.py` | 193 | Stripe/PayPal signature verification | live (dormant if not configured) |
| `webhook_rate_limit.py` | 127 | Per-project webhook rate limiter | live |
| `webhook_templates.py` | 222 | Webhook payload → task body templates | live |
| `credentials_crypto.py` | 88 | Fernet encrypt/decrypt for credential vault | live |
| `integrations_registry.py` | 355 | Platform integration toggle registry | live |
| `tool_grants.py` | 170 | Tool-grant permission check (Layer-0 gate) | live |
| `tool_registry.py` | 77 | Agent-runtime tool directory (static registry) | live |

**Notifications / push**:
| module | LOC | purpose | status |
|---|---|---|---|
| `notify_ntfy.py` | 160 | ntfy.sh push adapter | live |
| `notify_telegram.py` | 162 | Telegram bot adapter | live (dormant if TELEGRAM_BOT_TOKEN unset) |
| `notify_email.py` | 158 | Gmail SMTP adapter for digest | live |
| `notify_web_push.py` | 309 | VAPID web push adapter | live |

**Cost tracking**:
| module | LOC | purpose | status |
|---|---|---|---|
| `cost_tracker.py` | 168 | Session-run cost persistence | live |
| `token_counter.py` | 160 | Token counting utilities | live |
| `pl_calculator.py` | 221 | P&L rollup calculation | live |

**Utility / agent-side**:
| module | LOC | purpose | status |
|---|---|---|---|
| `agent_context_sanitizer.py` | 107 | Sanitize agent context for session handoff | live |
| `operator_auth.py` | 184 | Operator-proof key gate | live |
| `next_action_ranker.py` | 238 | Cross-project next-action scoring | live |
| `digest_template.py` | 382 | Daily digest email HTML/text template builder | live |
| `template_render.py` | 104 | Task template variable interpolation | live |
| `skill_stub_detector.py` | 398 | Detects skill stubs in codebase (called on digest fire) | live |
| `stale_doc_curator.py` | 428 | Detects stale shared docs (called on digest fire) | live |
| `row_changed_listener.py` | 210 | PostgreSQL `LISTEN`/`NOTIFY` → SSE broker | live |
| `hitl_nudge.py` | 207 | HITL aging nudge (aging tasks push reminder) | live |

### `api/src/tools/email/` (total 2,457 LOC)

| module | LOC | purpose | status | evidence |
|---|---|---|---|---|
| `gmail_client.py` | 727 | Gmail API client (trash/mark/archive/draft/search/get/thread/labels/attachment) | live | called by `routers/tools_email.py` |
| `outlook_client.py` | 706 | Outlook/Graph API client (same action set) | live | called by `routers/tools_email.py` |
| `calendar_client.py` | 393 | Google Calendar client (list/freebusy/create/respond) | live | called by `routers/tools_calendar.py` |
| `outlook_calendar_client.py` | 347 | Outlook Calendar client (same action set) | live | called by `routers/tools_calendar.py` |
| `token_store.py` | 169 | OAuth token persistence (DB-backed) | live | called by both email + calendar clients |
| `gate.py` | 115 | Layer-0 operator-proof gate enforcement | live | called by `tools_email.py` router |

### `api/src/middleware/`

| module | LOC | purpose | status |
|---|---|---|---|
| `rate_limit.py` | ~30 | slowapi limiter instance + per-IP rate limit | live |
| `request_size.py` | ~50 | 2MB payload cap, returns 413 | live |

---

## Oversized files (>500 LOC)

### `api/src/routers/tasks.py` (3,030 LOC)

| line range | feature area |
|---|---|
| 1–337 | Imports, helpers, constants, HITL push fire helper |
| 338–513 | `GET /api/tasks` list (filter, keyset pagination, sort) |
| 514–699 | `GET /api/tasks/next-autorun` (worker poll) |
| 700–741 | `POST /api/tasks/ai-parse` |
| 742–867 | `GET /api/tasks/{id}`, `GET /api/tasks/{id}/blocks`, `POST /api/tasks/{id}/comments`, `GET /api/tasks/{id}/comments` |
| 868–1169 | DnD reorder helpers (`_enforce_blocker_order`, `_materialize_null_sort_orders`, `_redensify_lane`, `reorder_task`) |
| 1170–1636 | `POST /api/tasks` create (action templates, recurrence, auto-audit) |
| 1637–2510 | `PATCH /api/tasks/{id}` update (status transitions, DONE-flip, handoff, budget, audit, HITL halt) |
| 2511–2568 | `POST /api/tasks/{flag_id}/resolve-flag` |
| 2569–2674 | `POST /api/tasks/{id}/confirm-template-auto-run`, `POST /api/tasks/{id}/fire-now` |
| 2675–2958 | `POST /api/tasks/{id}/decide` (HITL decide — dual-contract: structured ballot + legacy) |
| 2959–3030 | `POST /api/tasks/{id}/snooze`, `DELETE /api/tasks/{id}` |

### `api/src/routers/tools_email.py` (1,817 LOC)

| line range | feature area |
|---|---|
| 1–386 | Imports, shared helpers (enforce grant, write audit, enforce operator tier, escalate external send) |
| 387–495 | Gmail OAuth (auth-start, auth-callback, auth-status) |
| 496–1130 | Gmail actions: trash, usage, mark, archive, draft, search, get, thread, labels, attachment |
| 1130–1216 | Gmail attachment download |
| 1217–1330 | Outlook OAuth (auth-start, auth-callback, auth-status) |
| 1331–1816 | Outlook actions: trash, mark, archive, draft, search, get |

### `api/src/schemas/task.py` (1,463 LOC)

Comprehensive Pydantic v2 schemas for every task field variant; includes nested JSONB field schemas (acceptance_criteria, question_payload, approved_by_spec, subagent_models). No oversized single section — density from field count.

### `api/src/schemas/project.py` (1,225 LOC)

Project schemas including approval_policies DSL, tools_config, enabled_roles, required_binaries, recurrence_rule. Mirrors the wide `projects` table column set.

### `api/src/services/health_monitor.py` (687 LOC)

Self-contained health sweep: checks API liveness, DB row counts, session state, project health scores. No external callers beyond APScheduler lifespan job.

### `api/src/services/pause_switch.py` (653 LOC)

Project pause/unpause + audit-flag resolve logic. Large because it handles three distinct state machines: pause/unpause, flag raise, flag resolve.

### `api/src/services/backup.py` (626 LOC)

S3 encrypted backup: `BackupConfig.from_env()`, `BackupRunner.run_once()`, catchup logic, age-encryption via `age` CLI. boto3 import is deferred behind `BACKUP_S3_BUCKET` env check (`main.py:205`).

### `api/src/models/task.py` (732 LOC)

Two ORM classes: `Task` (60–680, ~120 columns including JSONB fields) and `TaskHistory` (681–732, audit shadow table written by PG trigger).

### `api/src/models/project.py` (451 LOC)

Single `Project` ORM class; wide column set (JSONB: approval_policies, tools_config, enabled_roles, etc.).

---

## Cross-cutting observations (facts only)

### Config: mixed Settings + os.environ

`api/src/settings.py` defines 9 fields via `pydantic-settings`. There are 88 additional `os.getenv`/`os.environ` calls across `src/` (46 in services alone). Many service-level env vars (TELEGRAM_BOT_TOKEN, GMAIL_*, NTFY_*, BACKUP_*, HEALTH_MONITOR_*, OPERATOR_ACTION_KEY) are read raw, not declared in `Settings`. Partial list of env-gated features:

| env var | gates |
|---|---|
| `BACKUP_S3_BUCKET` | nightly S3 backup (boto3 deferred import) |
| `APP_SCHEDULER_DISABLE=true` | disables APScheduler entirely (used by tests) |
| `APP_SCHEDULER_TICK_SECONDS` | recurrence tick interval (default 60s) |
| `HEALTH_MONITOR_DISABLED=1` | disables health monitor sweep |
| `OPERATOR_ACTION_KEY` | operator-proof gate (fail-open if unset; logged as warning) |
| `BACKUP_CATCHUP_MAX_AGE_HOURS` | backup catchup threshold (default 24h) |
| `AUDIT_ARCHIVE_DAYS` | audit task archival TTL (default 30d) |
| `HITL_NUDGE_INTERVAL_MINUTES` | HITL nudge cadence (default 30m) |
| `DB_NAME_ALLOWLIST` | DB name allowlist at lifespan (default: `agent_teams,agent_teams_test`) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | web push (fail-open if unset) |

### Dual-router pattern in pl.py and resources.py

`pl.py` defines two `APIRouter` objects exported separately (`router` and `pnl_router`) and mounted independently in `main.py`. `resources.py` similarly defines `router_project` and `router_resource`. Both are registered correctly; the pattern is consistent but non-obvious to newcomers.

### Notification fan-out has 4 adapters

`services/notification_router.py` dispatches to: `notify_web_push.py` (VAPID), `notify_ntfy.py` (ntfy.sh), `notify_telegram.py` (Telegram bot), `notify_email.py` (Gmail SMTP). Each adapter is independently gated by env vars and silently skips if unconfigured. The `OPERATOR_ACTION_KEY` env gates a second layer for email/calendar mutations.

### TODO count in tasks.py

9 lines containing TODO/FIXME in `tasks.py` — all are domain-logic comments (e.g. `# TODO: soft-delete`) rather than unfinished code paths.

### Test suite size vs source

Test LOC (58,527) is 1.45× source LOC (40,413). `test_routes_smoke.py` alone is 4,565 LOC. The test suite covers all 29 router files and most service files.

### `tools/email/` is a full external-API client layer

`api/src/tools/email/` (2,457 LOC) is a dedicated Gmail + Outlook + Google Calendar + Outlook Calendar HTTP client layer, not reusing any off-the-shelf SDK abstractions. It is entirely internal; the only callers are `routers/tools_email.py` and `routers/tools_calendar.py`.

### `ingest.py` and `webhooks.py` are integration-only

Both have no FE callers and no worker callers. They are external webhook receivers (Mailgun, Stripe, PayPal). Status classified as dormant in the context of normal dev workflow; they are live in production if those providers are configured.

---

## Open questions

- `api/src/routers/events.py` (96 LOC): the `row_changed_listener.py` uses PostgreSQL `LISTEN/NOTIFY` — the exact channel names and which tables emit `NOTIFY` were not traced (would require reading the PG trigger definitions in migrations).
- `api/src/models/projects_audit.py` (124 LOC, `projects_audit` table): no router directly serves this table — it appears written by a PG trigger. The read path was not found in routers; may be exposed via `audit.py` rollup or direct SQL inside a service.
- `api/src/schemas/ai_task.py` (113 LOC): referenced in schemas but the corresponding model file was not found under `models/` — may be a schema-only response type (no DB-backed model).
- `api/src/services/next_action_ranker.py` (238 LOC) and `user_actions.py` router: the ranking algorithm was not read — scoring logic is opaque from file names alone.
- `api/src/services/compact_runner.py` (430 LOC): compaction trigger conditions (word-count threshold? turn count? explicit operator call only?) were not read.

## Followups

- The `api/src/tools/email/` client layer (2,457 LOC) and `api/src/routers/tools_email.py` (1,817 LOC) together total 4,274 LOC for the email/calendar integration surface alone — a separate focused map of just that subsystem may be warranted for the over-engineering review.
- 88 raw `os.environ` calls vs 9 declared `Settings` fields: a consolidation inventory could help but is out of scope here.
- `api/src/models/projects_audit.py` write path (PG trigger vs service) should be confirmed from migration files.

## Standards insights (proposal only — Lead applies)

- The `@router.*(...)` pattern is inconsistent for routers using alternate `APIRouter` instance names (`router_project`, `router_resource`, `pnl_router`, `runs_router`) — a naming convention (`router_<scope>`) would make grep-based endpoint counting reliable.
- Service env vars are largely undocumented outside their own source files. A single `.env.example` with inline comments grouping vars by feature gate (backup, health, HITL, operator-proof, push) would serve onboarding.
