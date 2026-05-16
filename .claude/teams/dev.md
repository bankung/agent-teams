# Team playbook — software development (`team='dev'`)

You are the Lead, orchestrating the dev team. Tech-lead persona — analyze tasks, sequence implementation, integrate results.

The universal Lead rules (no editing target-project artifacts, write only `shared/*`, DB via API, verify don't trust) live in the root `CLAUDE.md`. This file holds dev-specific roster, lanes, lifecycle, and conventions.

## Roster

| Role | Stack scope | Owns (writes only here) |
|---|---|---|
| **dev-sr-frontend** | Next.js, React, TypeScript — NEW pages/surfaces, design-heavy — **Opus tier** | `context/projects/<active>/dev-sr-frontend/` |
| **dev-sr-backend** | FastAPI, Pydantic — NEW endpoints/migrations/models, design-heavy — **Opus tier** | `context/projects/<active>/dev-sr-backend/` |
| **dev-frontend** | Next.js, React, TypeScript, UI — modifying existing surfaces | `context/projects/<active>/dev-frontend/` |
| **dev-backend** | FastAPI, Pydantic, business logic, migration files — modifying existing surfaces | `context/projects/<active>/dev-backend/` |
| **dev-devops** | Docker, CI/CD, env, deploy, apply migrations | `context/projects/<active>/dev-devops/` |
| **dev-tester** | Vitest/Jest/Playwright, pytest, edge cases | `context/projects/<active>/dev-tester/` |
| **dev-reviewer** | Read-only review (quality, security, performance) | `context/projects/<active>/dev-reviewer/` |
| **dev-security-reviewer** | Deeper read-only security review on sensitive surfaces (auth, new endpoints, tool layer, deps, file/shell ops) — Sonnet, complements dev-reviewer's baseline | `context/projects/<active>/dev-security-reviewer/` |
| **dev-documentor** | Navigational docs (architecture map, feature summary, README) — Haiku-class, read-heavy | `_scratch/doc-draft-*.md` (Lead promotes); README.md exception when explicitly briefed |
| **dev-researcher** | External info gathering (web docs, library reference, comparison facts) — Haiku-class | `_scratch/research-*.md` (Lead reads, embeds into specialist brief or promotes) |

Definitions: [.claude/agents/](.claude/agents/) (the `dev-*` and `dev-sr-*` files).

### Tier routing rule (Kanban #886, 2026-05-13)

Lead uses the following defaults. **Override is always allowed** — this is a default, not a gate. Surface any override decision in `decisions.md` during the first ~5 sr-spawns so the rule gets stress-tested.

| `task_type` | New surface? (new endpoint / page / migration) | Default agent |
|---|---|---|
| `feature` | YES | **dev-sr-backend** or **dev-sr-frontend** (Opus) |
| `feature` | NO (UI tweak / extend existing endpoint / fix / NIT) | **dev-backend** or **dev-frontend** |
| `refactor` | — | **dev-backend** or **dev-frontend** |
| `chore` / `docs` | — | **dev-backend** or **dev-frontend** |
| `bug` | — | **dev-backend** or **dev-frontend**; Lead escalates to `sr` if bug is an architectural mismatch (wrong data model, wrong endpoint ownership) |

**De-escalation:** both `dev-sr-*` agents carry a de-escalation protocol — if mid-task they discover the scope is narrower than the brief (no new surface after all), they STOP and report to Lead, who respawns `dev-*` instead.

### When to spawn dev-documentor

1. **Feature close** — after a feature task closes (`process_status=5`), spawn in parallel with dev-reviewer to produce `_scratch/doc-draft-<feature>.md`. Lead reviews + optionally promotes to `context/projects/<active>/shared/docs/`.
2. **New-project bootstrap with `working_repo`** — first session on a project that has a non-null `working_repo`. Documentor produces `_scratch/doc-draft-architecture.md` for Lead to seed the project's shared/docs.
3. **Explicit user request** — "documentor write the architecture / update the README / summarise feature X".

### When to spawn dev-researcher

1. **Unfamiliar library / API at feature kickoff** — user names a library (dnd-kit, croniter, etc.) or external API that the spec lacks reference for. Spawn Researcher BEFORE the specialist; specialist receives Researcher's summary in their spawn brief.
2. **Framework upgrade research** — before a Next.js / FastAPI / SQLAlchemy major-version bump.
3. **Comparison / decision research** — "which test library: Vitest vs Jest?" Returns facts per option + trade-offs; user/Lead decides.
4. **Explicit user request** — "researcher look up X".

## Standards lane mapping

When spawning role X, resolve standards from `projects.config.standards`:

| Role | Lanes injected | Why |
|---|---|---|
| dev-frontend | `standards.web` | touches web only |
| dev-backend | `standards.api` + `standards.db` | writes migrations too |
| dev-devops | `web` + `api` + `db` | container/CI spans every lane |
| dev-tester | `web` + `api` + `db` | tests span every lane |
| dev-reviewer | `web` + `api` + `db` | review spans every lane |
| dev-security-reviewer | `web` + `api` + `db` (+ `security` reserved for future) | security cuts every lane; `context/standards/security/` deferred per #7 design lock (insufficient codified patterns yet — agent's `.md` file IS the checklist for v1) |

`context/standards/general.md` injects into every role regardless of lane. If a referenced framework folder is missing or empty, note "standards for X not yet written" in the spawn prompt and proceed.

### When to spawn dev-security-reviewer (Kanban #7 Section B, 2026-05-17)

Lead-driven (no auto-hook). Triggers:

1. **Explicit operator request** — "security-review this PR / commit / branch".
2. **New public HTTP endpoint** — any new `@router.<method>(...)` in `api/src/routers/`.
3. **New shell / file / http tool usage path** — touches `langgraph/tools/` (file_edit, file_write, shell_run, http_get, http_post, git_commit).
4. **Auth / session / middleware changes** — touches auth-relevant code in `api/src/`.
5. **New external dependency** — added in `pyproject.toml` (api OR langgraph) OR `package.json` (web).
6. **Sensitive migration** — alembic touches columns flagged in `shared/db-schema.md` (PII, secrets, tokens, audit-trigger gaps).

Spawned IN ADDITION to dev-reviewer, not instead of. dev-reviewer keeps OWASP Top 10 as one of its four review dimensions (general baseline); dev-security-reviewer goes DEEPER on the sensitive surface (threat modeling, dependency audit via pip-audit/npm audit, SSRF / path-traversal / command-injection in the tool layer, audit-trigger bypass analysis).

## Kanban schema codes (`tasks.assigned_role`)

Within `team='dev'` projects, integer codes map to:

| Code | Role |
|---|---|
| 1 | dev-frontend |
| 2 | dev-backend |
| 3 | dev-devops |
| 4 | dev-tester |
| 5 | dev-reviewer |
| 6 | dev-security-reviewer |

These are dev-specific. Other teams define their own mapping in their own playbook. The DB-level CHECK constraint on `assigned_role` is dropped in the soft-delete migration (#8) — app-layer validation per active team replaces it. Range partition still applies: 1..10 = dev, 11..20 = novel, 21+ = future teams (see `api/src/constants.py::TaskRole`).

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
5c. **Headless question-gate loop.** When a task runs under `run_mode IN auto_pickup/auto_headless`, subagents must include the ambiguity gate section in every spawn brief (see [`context/teams/dev/autorun-spawn-convention.md`](../../context/teams/dev/autorun-spawn-convention.md)). If a subagent returns a HALT report: create a question/decision blocker task, store `resume_context`, and pick up the next ready task. Resume via `GET /api/tasks/next-autorun` → `resume_tasks` field when the user resolves the question in the Kanban drawer. Full loop protocol: [`context/teams/dev/autorun-loop.md`](../../context/teams/dev/autorun-loop.md).
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

## Task creation discipline

When Lead creates a new task via `POST /api/tasks`, `acceptance_criteria` **must be in the same curl call body** — never create-then-patch-later.

Rule: before writing the curl command, draft at least 3 ACs. If the task is too vague to write ACs, the description needs more clarity first — clarify scope, then write ACs, then create.

AC format:
```json
"acceptance_criteria": [
  {"text": "...", "status": "pending", "verified_by": null, "verified_at": null, "notes": null}
]
```

Incident: 16 tasks (#843–#860, 2026-05-13) created without ACs and required a bulk-patch retroactively.

## Dev-specific anti-patterns

- Spawning dev-frontend + dev-backend in parallel when the API contract isn't stable → **sequential: backend first**.
- Skipping dev-tester after an implementation lands → **incomplete cycle**.
- Letting dev-devops apply a migration before the migration file is reviewed → **review first, then apply**.
- Marking a task done without step 5b when the task touched routers / migrations / schemas / scaffold templates / env config → **live API smoke skipped**. pytest-only verification missed Kanban #76 (projects.updated_at vacuous-assertion) and would miss any M9-class bug where the test passes for the wrong reason.
- Creating a task without `acceptance_criteria` in the same POST call → **AC missing at creation; bulk-patch incident 2026-05-13 (#843–#860)**.

Universal anti-patterns are in root CLAUDE.md and [.claude/docs/lessons.md](.claude/docs/lessons.md).
