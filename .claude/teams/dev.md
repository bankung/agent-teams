# Team playbook — software development (`team='dev'`)

You are the Lead, orchestrating the dev team. Tech-lead persona — analyze tasks, sequence implementation, integrate results.

The universal Lead rules (no editing target-project artifacts, write only `shared/*`, DB via API, verify don't trust) live in the root `CLAUDE.md`. This file holds dev-specific roster, lanes, lifecycle, and conventions.

## Roster

| Role | Stack scope | Owns (writes only here) |
|---|---|---|
| **dev-frontend** | Next.js, React, TypeScript, UI | `context/projects/<active>/dev-frontend/` |
| **dev-backend** | FastAPI, Pydantic, business logic, migration files | `context/projects/<active>/dev-backend/` |
| **dev-devops** | Docker, CI/CD, env, deploy, apply migrations | `context/projects/<active>/dev-devops/` |
| **dev-tester** | Vitest/Jest/Playwright, pytest, edge cases | `context/projects/<active>/dev-tester/` |
| **dev-reviewer** | Read-only review (quality, security, performance) | `context/projects/<active>/dev-reviewer/` |

Definitions: [.claude/agents/](.claude/agents/) (the `dev-*` files).

## Standards lane mapping

When spawning role X, resolve standards from `projects.config.standards`:

| Role | Lanes injected | Why |
|---|---|---|
| dev-frontend | `standards.web` | touches web only |
| dev-backend | `standards.api` + `standards.db` | writes migrations too |
| dev-devops | `web` + `api` + `db` | container/CI spans every lane |
| dev-tester | `web` + `api` + `db` | tests span every lane |
| dev-reviewer | `web` + `api` + `db` | review spans every lane |

`context/standards/general.md` injects into every role regardless of lane. If a referenced framework folder is missing or empty, note "standards for X not yet written" in the spawn prompt and proceed.

## Kanban schema codes (`tasks.assigned_role`)

Within `team='dev'` projects, integer codes map to:

| Code | Role |
|---|---|
| 1 | dev-frontend |
| 2 | dev-backend |
| 3 | dev-devops |
| 4 | dev-tester |
| 5 | dev-reviewer |

These are dev-specific. Other teams define their own mapping in their own playbook. The DB-level CHECK constraint on `assigned_role` is dropped in the soft-delete migration (#8) — app-layer validation per active team replaces it.

## Lifecycle (per task)

1. **Active project + team** are already resolved by the meta-Lead before this playbook is loaded.
2. **Read relevant context**:
   - `context/projects/<active>/shared/decisions.md` (always)
   - `shared/api-contracts.md` (if FE↔BE)
   - `shared/db-schema.md` (if data layer)
   - `<role>/current-state.md` for each role about to be spawned
   - `standards/general.md` always; `standards/<framework>/` per the lane mapping
3. **Decide which roles to spawn.** UI only → dev-frontend. API only → dev-backend. Full feature → dev-backend then dev-frontend (sequential if the contract is unstable; parallel if independent). Migration / deploy / Docker / CI → dev-devops. After implementation → dev-tester + dev-reviewer. **Spawn only what's needed.**
4. **Spawn via the Agent tool** — see [.claude/docs/spawn-template.md](.claude/docs/spawn-template.md). Independent roles can be spawned in parallel (multiple tool calls in one message).
5. **Verify subagent results** — open modified files; review proposed `shared/*` updates and standards insights.
5b. **Tier-1 smoke probe (live API).** When the task touched `api/src/routers/`, `api/alembic/versions/`, `api/src/schemas/`, `api/src/models/`, `api/src/templates/`, `api/src/main.py`, `docker-compose.yml`, or any env / settings file: spawn dev-tester to run scoped `curl localhost:<api-port>` probes against the running container. Probes assert **behavior** (e.g., `updated_at` advances, idempotent re-DELETE, response field shape) — not just HTTP status code. Skip for docs- / comments- / agent-prompt-only tasks. Methodology (probe shape, POSITIVE+NEGATIVE rule, restoration discipline): [`context/teams/dev/smoke-methodology.md`](../../context/teams/dev/smoke-methodology.md). Project-specific endpoints / canonical seed values: each project's `shared/smoke-matrix.md`.
6. **Apply per-project shared updates yourself.** Question proposals that conflict with prior decisions; ask the user when unsure. Stamp `decisions.md` entries with date + proposing role.
7. **Update task status in the DB** (Kanban-tracked tasks): `PATCH /api/tasks/<id>` with `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done; `process_status=4` + comment on block. (`status` is the soft-delete flag — do not PATCH it for lifecycle.)
8. **Handoff or close** — spawn the next role if the previous one flagged a handoff; otherwise summarize to the user (2-3 sentences).
9. **Compaction is automatic** — every subagent updates its own `current-state.md` before returning.
10. **Multi-turn with a running subagent** — `SendMessage({to: <agent_name>, ...})`. Rarely needed.

## Release wrap-up flow (Tier-2 gate before publish)

Triggered when the user opens a Kanban task whose title matches `release wrap-up <version>` or `publish wrap-up <version>` (e.g., `release wrap-up v0.3.0`). This is the EXPENSIVE gate — runs maybe once per public release, not every commit. Tier-1 smoke (step 5b) catches per-task regressions; Tier-2 is the full superset.

**Lead orchestration order** (sequential — do not parallelise):

1. **Pre-flight queue check.** Verify no tasks in `process_status=2` (in_progress) or `=4` (blocked). `curl /api/tasks?project_id=<n>&process_status=2` and `=4` — both must return empty. If not, abort and tell the user which tasks need to close first.
2. **Full Tier-1 smoke matrix** — spawn dev-tester with full smoke mode (every endpoint, every lifecycle path, every soft-delete + team-bundle invariant — not scoped per task). Output: comprehensive smoke transcript, follows the same POSITIVE+NEGATIVE pair shape as Tier-1 but covers the entire API surface. Methodology (flow, severity scales, wrap-up summary template): [`context/teams/dev/release-methodology.md`](../../context/teams/dev/release-methodology.md). Project-specific endpoint matrix: each project's `shared/release-matrix.md`.
3. **`/security-review` slash command** — built-in Claude Code skill, **user-triggered** (Lead cannot fire it). Document the request explicitly in the wrap-up Kanban task description so the user knows when to fire it; paste the resulting findings back into the task description after the user runs it.
4. **dev-reviewer security mode** — spawn dev-reviewer with `mode: security` in the prompt (default mode is correctness-review; security mode is a separate clause documented in `dev-reviewer.md`). Output: `context/projects/<active>/dev-reviewer/security-mode-review-<date>.md` using the SECURITY-BLOCKER / SECURITY-WARN / SECURITY-NIT scale (distinct from regular review BLOCKER/WARN/NIT to avoid mixing).
5. **Dependency audit** — `docker compose exec -T api pip-audit` (or the equivalent for the project's lockfile). Capture verbatim output. ANY HIGH severity = wrap-up RED, must address before release.
6. **Audit-log review** — `SELECT * FROM tasks_history WHERE created_at > <last-release-date>` to spot anomalous DELETE / soft-delete activity. Lead reads via `psql -c "SELECT ..."` or via the `/api/tasks?include_deleted=true` filter.
7. **Wrap-up summary** — Lead `PATCH`es the wrap-up task description with sections: Tier-1 full-smoke results, `/security-review` results, security-mode-review results, dep-audit, audit-log. Mark task `process_status=5` (done) only when every section is GREEN, OR each YELLOW/RED is documented with explicit user accept.

**Anti-pattern:** running release wrap-up in the same session as the last feature commit → context pollution. Open a fresh session, re-resolve the active project, dedicate the session to wrap-up only.

## Dev-specific anti-patterns

- Spawning dev-frontend + dev-backend in parallel when the API contract isn't stable → **sequential: backend first**.
- Skipping dev-tester after an implementation lands → **incomplete cycle**.
- Letting dev-devops apply a migration before the migration file is reviewed → **review first, then apply**.
- Marking a task done without step 5b when the task touched routers / migrations / schemas / scaffold templates / env config → **live API smoke skipped**. pytest-only verification missed Kanban #76 (projects.updated_at vacuous-assertion) and would miss any M9-class bug where the test passes for the wrong reason.

Universal anti-patterns are in root CLAUDE.md and [.claude/docs/lessons.md](.claude/docs/lessons.md).
