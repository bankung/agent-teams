# Architectural & process decisions

> **Lead is the only writer of this file.** Subagents propose updates in their final report — Lead reviews, may ask the user, then writes the entry.
>
> Format: append-only log. Newest entry at the top. Each entry has a date, scope, who proposed it, and the reasoning.

<!--
Template for a new entry:

## YYYY-MM-DD — <short title>
**Scope:** frontend | backend | devops | qa | reviewer | shared
**Proposed by:** <role> (or `lead` / `user`)
**Decision:** <what we decided to do>
**Reasoning:** <why — constraints, tradeoffs, alternatives considered>
**Implications:** <what changes downstream>
-->

## 2026-05-08 — Multi-domain `lead` column + soft-delete migration bundled (`0002_soft_delete_and_lead`)
**Scope:** db / backend / shared
**Proposed by:** user (lead column) + dev-backend (migration bundle, scaffold dispatch, `_in_clause_text`)
**Decision:** Single Alembic migration `0002_soft_delete_and_lead` (filename `2026_05_08_0300_soft_delete_and_lead.py`) lands three coupled changes atomically: (a) the soft-delete schema decided 2026-05-05 (rename `tasks.status → tasks.process_status`; add `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0,1))` to `projects` + `tasks`; partial unique on `projects.name` gated on `status=1`; tighten `ux_projects_active_one` to `WHERE is_active IS TRUE AND status=1`), (b) a new `projects.lead TEXT NOT NULL DEFAULT 'dev' CHECK (lead IN ('dev','novel'))` column, and (c) drop of `ck_tasks_assigned_role_valid` (app-layer validates per active project's lead roster). Two leads seed the multi-domain pattern: dev (1..5 roles), novel (11..12 roles); future leads pick their own ranges. Scaffold service dispatches on `project.lead` to pick role-folder names — per-lead `shared/*` templates are a follow-up (every project still gets the dev template trio).
**Reasoning:** All three changes touch the same migration touchpoints (`tasks` columns, `projects` constraints) and the app rename has to flip on the same deploy as the column rename — splitting them invites a window where schemas mismatch source. Per-lead roster validation is dynamic (can't be expressed as a single static CHECK across all leads), so app-layer enforcement is the source of truth for `assigned_role`.
**Implications:** `DELETE /api/projects/{id}` and `DELETE /api/tasks/{id}` are now public verbs (204; flip `status=0` internally; project DELETE also clears `is_active` if true). List endpoints default-filter `WHERE status=1` with opt-in `?include_deleted=true` (debug; intentionally NOT in api-contracts.md). Detail endpoints return rows regardless of soft-delete status. PATCH does NOT accept the soft-delete `status` flag — `TaskUpdate`/`ProjectUpdate` schemas omit the field; unknown fields are silently ignored (Pydantic default `extra='ignore'`, made explicit via `model_config = ConfigDict(extra='ignore')` on both Update schemas to text-lock the choice); locked by `test_patch_task_silently_ignores_soft_delete_status_field`. Lifecycle status query param renamed `?status=1..5 → ?process_status=1..5`. POST `/api/projects` requires `lead` (422 if missing or unknown). The seeded `agent-teams` row inherits `lead='dev'` automatically via the migration's `DEFAULT 'dev'` backfill — no explicit data-migration UPDATE needed. `assigned_role` Pydantic validator still hardcodes against `TaskRole.ALL = (1..5)` — widening to per-lead roster logic is a Phase 3 follow-up. M5 — PATCH `/api/tasks/{id}` 400 detail strings translate well-known CHECK constraint names to stable wire text; HTTP path is gated by Pydantic 422 first, so the 400 branches are reachable today only via raw-SQL bypass / future schema drift. M9 — re-DELETE on an already-soft-deleted row is a no-op write (skipped) so `tasks_history` doesn't grow on idempotent DELETEs.

## 2026-05-05 — Soft delete via uniform `status` flag (no hard DELETE in app code)
**Scope:** db / shared
**Proposed by:** user
**Decision:** Every business table carries `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0, 1))` (1=active, 0=deleted). Application code never issues SQL DELETE — "delete" endpoints flip the flag. To keep the column name uniform across tables, the existing 1-5 lifecycle column on `tasks` is being renamed `tasks.status → tasks.process_status` (codes unchanged); the new `tasks.status` then carries the same 0/1 semantic as every other table. `tasks_history` is exempt (audit append-only by design).
**Reasoning:** User policy — never lose business data. The audit trigger (`tasks_audit_trg`) snapshots the flag flip as `'U'`, so soft deletes remain traceable. Renaming the lifecycle column rather than picking a different soft-delete name avoids "different soft-delete column per table" sprawl. Reverses the earlier "Soft delete: no" line in db-schema.md Conventions.
**Implications:** Every list endpoint defaults to `WHERE status=1`; opt-in `?include_deleted=true` to see soft-deleted rows. DELETE endpoints become PATCH `{"status": 0}`. Hard DELETE reserved for manual psql cleanup (`tasks_history.operation='D'` becomes rare). Migration tracked as a Kanban task — see standards/postgresql/soft-delete.md (to be drafted) for the operational details.

## 2026-05-04 — Auto-scaffold folder structure on POST /api/projects
**Scope:** backend / shared
**Proposed by:** backend
**Decision:** `POST /api/projects` commits the DB row first, then runs `scaffold_project_folder()` which creates `context/projects/<name>/{shared,frontend,backend,devops,qa,reviewer}/`, copies the 3 shared templates from `api/src/templates/project_shared/`, and drops `.gitkeep` in role folders. Idempotent. Scaffold failure is logged but does NOT roll back the DB row.
**Reasoning:** DB is the source of truth (Bucket 1) — folder gaps can be repaired manually but a row stuck in "created but rolled back" is worse. Scaffold templates ship inside the api package so the API is self-contained.
**Implications:** Lead can trust that creating a project via API also makes the folder; if a deploy breaks the scaffold path, projects can still be created but Lead will hit "missing context dir" later — fix the scaffold path then re-POST (idempotent).

## 2026-05-04 — Integer codes (not enums) for status / priority / assigned_role
**Scope:** backend / shared
**Proposed by:** backend (matches `context/standards/general.md` Kanban schema codes)
**Decision:** `tasks.status`, `tasks.priority`, `tasks.assigned_role` are INTEGER columns with CHECK constraints; canonical names live in `src/constants.py` (Python) and will live in `web/lib/constants.ts` (TypeScript). No PG enum types.
**Reasoning:** PG enums are painful to extend (require schema migration to ADD VALUE; cannot remove). Integer + CHECK is trivially extensible — bump the CHECK and the constants file in lockstep. Numbers are stable across renames.
**Implications:** UI must always render via the constants module — never hardcode the digit. Adding a new code requires updating `general.md`, the migration, AND the constants file in both languages.

## 2026-05-04 — Async SQLAlchemy + asyncpg
**Scope:** backend
**Proposed by:** backend
**Decision:** Use SQLAlchemy 2.0 async ORM with `asyncpg` driver; FastAPI handlers are `async def`.
**Reasoning:** FastAPI is async-first; pairing with sync DB I/O would block the event loop. asyncpg has the best perf and is the de-facto standard for FastAPI + Postgres.
**Implications:** Alembic env.py uses `async_engine_from_config` + `run_sync(do_run_migrations)`. Tests use `pytest-asyncio` + `httpx.AsyncClient(transport=ASGITransport)`.

## 2026-05-04 — BigInteger autoincrement, not UUID, for primary keys
**Scope:** backend / db / shared
**Proposed by:** backend
**Decision:** All tables use `id BIGINT GENERATED BY DEFAULT AS IDENTITY` (SQLAlchemy `BigInteger autoincrement=True`) instead of `uuid DEFAULT gen_random_uuid()`.
**Reasoning:** Single-tenant dogfood app — no need for client-generated/sharded keys. Smaller indexes, sequential locality for cache, easier to reference in URLs and logs ("task #42" reads better than "task 6f3a..."). Overrides the placeholder in `db-schema.md` Conventions and `api-contracts.md` IDs.
**Implications:** All Pydantic schemas type IDs as `int`. UI route params are integers. If the project ever needs distributed write later, we'd add a separate `external_id uuid` column rather than swap PKs.

## 2026-05-04 — Audit trail via PG trigger (not application code)
**Scope:** backend / db
**Proposed by:** backend
**Decision:** `tasks_history` is populated by an AFTER UPDATE OR DELETE trigger that snapshots `to_jsonb(OLD)`. Application code never INSERTs into `tasks_history`.
**Reasoning:** Captures every mutation including out-of-band edits (psql, future admin scripts). Avoids the application-layer bug class where someone forgets to call the audit helper. JSONB snapshot future-proofs against column changes without history-schema migrations.
**Implications:** `tasks_history.task_id` is intentionally NOT a FK — history rows must outlive the row they describe. Application reads of history go through a future `GET /api/tasks/{id}/history` endpoint.

## 2026-05-04 — Initial schema migration generated (not yet applied)
**Scope:** backend / shared
**Proposed by:** backend
**Decision:** Single Alembic migration `2026_05_04_2130_initial_schema.py` creates `projects`, `tasks`, `tasks_history` and the PG `tasks_audit_fn()` + `tasks_audit_trg`.
**Reasoning:** Bundling the v1 schema in one migration keeps the bootstrap atomic — easier for new clones to bring up and easier to drop/recreate during early dev. The trigger is co-located in the same file so the audit invariant cannot be applied without it.
**Implications:** Devops applies via `alembic upgrade head`; subsequent feature work adds incremental migrations only.

<!-- No decisions yet. First entry will be appended above this line. -->
