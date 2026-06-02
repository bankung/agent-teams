# Team playbook — software development (`team='dev'`)

This playbook orchestrates the dev team. For universal Lead rules, see root `CLAUDE.md`. This file covers dev-specific roster, lifecycle, and conventions.

You are the Lead, orchestrating the dev team. Tech-lead persona — analyze tasks, sequence implementation, integrate results.

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
| **general-researcher** | External info gathering (web docs, library reference, comparison facts) — Haiku-class. Team-agnostic — borrowed from the shared roster. | `_scratch/research-*.md` (Lead reads, embeds into specialist brief or promotes) |

Definitions: [.claude/agents/](.claude/agents/) (the `dev-*` and `dev-sr-*` files).

### Tier routing rule (Kanban #886, 2026-05-13)

Lead uses the following defaults. **Override is always allowed** — this is a default, not a gate.

| `task_type` | New surface? (new endpoint / page / migration) | Default agent |
|---|---|---|
| `feature` | YES | **dev-sr-backend** or **dev-sr-frontend** (Opus) |
| `feature` | NO (UI tweak / extend existing endpoint / fix / NIT) | **dev-backend** or **dev-frontend** |
| `refactor` | — | **dev-backend** or **dev-frontend** |
| `chore` / `docs` | — | **dev-backend** or **dev-frontend** |
| `bug` | — | **dev-backend** or **dev-frontend**; Lead escalates to `sr` if bug is an architectural mismatch |

**De-escalation:** both `dev-sr-*` agents carry a de-escalation protocol — if mid-task they discover the scope is narrower than the brief (no new surface after all), they STOP and report to Lead, who respawns `dev-*` instead.

### When to spawn dev-documentor

1. **Feature close** — after a feature task closes (`process_status=5`), spawn in parallel with dev-reviewer to produce `_scratch/doc-draft-<feature>.md`. Lead reviews + optionally promotes.
2. **New-project bootstrap with `working_repo`** — first session on a project with non-null `working_repo`. Documentor produces architecture summary for Lead to seed the project's shared/docs.
3. **Explicit user request** — "documentor write the architecture / update the README / summarise feature X".

### Research-first discipline (when to spawn general-researcher)

**Standing rule:** every non-trivial dev task starts with a research step. `general-researcher` (Haiku tier) spawns FIRST or in the first parallel batch alongside other specialists.

**Dev-specific "non-trivial" signals:**

- Unfamiliar library / framework / external API in the spec (names a dependency the codebase doesn't already use).
- Framework upgrade research (Next.js / FastAPI / SQLAlchemy / Alembic major-version bump).
- Methodology / architecture decision (auth flow, state management, migration strategy).
- Comparison decision (Vitest vs Jest, Pydantic v1 vs v2 migration paths, etc.).
- Cross-team or cross-stack scope (one fix touches schema + API + FE + tests + standards).
- Spec ambiguity (the operator gave a one-line ask; specialist would otherwise guess).

**Escape valves (skip research):**

- Pure execution — typo fix, well-understood mechanical update (variable rename, dep version bump that's already in the lockfile).
- Continuation of an already-researched task (prior general-researcher report is fresh in `_scratch/research-*.md`).
- Trivial single-edit follow-up to a still-open task.
- UI tweak on an existing surface using existing components and patterns.

## Standards lane mapping

When spawning role X, resolve standards from `projects.config.standards`:

| Role | Lanes injected | Why |
|---|---|---|
| dev-frontend | `standards.web` | touches web only |
| dev-backend | `standards.api` + `standards.db` | writes migrations too |
| dev-devops | `web` + `api` + `db` | container/CI spans every lane |
| dev-tester | `web` + `api` + `db` | tests span every lane |
| dev-reviewer | `web` + `api` + `db` | review spans every lane |
| dev-security-reviewer | `web` + `api` + `db` | security cuts every lane; `context/standards/security/` deferred per #7 design lock |

`context/standards/general.md` injects into every role regardless of lane.

### When to spawn dev-security-reviewer (Kanban #7 Section B, 2026-05-17)

Lead-driven (no auto-hook). Triggers:

1. **Explicit operator request** — "security-review this PR / commit / branch".
2. **New public HTTP endpoint** — any new `@router.<method>(...)` in `api/src/routers/`.
3. **New shell / file / http tool usage path** — touches `langgraph/tools/`.
4. **Auth / session / middleware changes** — touches auth-relevant code in `api/src/`.
5. **New external dependency** — added in `pyproject.toml` (api OR langgraph) OR `package.json` (web).
6. **Sensitive migration** — alembic touches columns flagged in `shared/db-schema.md` (PII, secrets, tokens, audit-trigger gaps).

Spawned IN ADDITION to dev-reviewer, not instead of.

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

These are dev-specific. Other teams define their own mapping in their own playbook. Range partition: 1..10 = dev, 11..20 = novel, 21+ = future teams (see `api/src/constants.py::TaskRole`).

> **Source of truth:** `api/src/constants.py` — this table may drift; check code if uncertain.

### Per-project role gate (Kanban #7 Section A, 2026-05-18)

The active project may carry `config.enabled_roles: int[]` — a whitelist of role codes that the project's Lead is allowed to spawn. Semantics:

- **Key absent OR `null`** → all roles allowed (default).
- **Empty list `[]`** → no AI-role spawns allowed.
- **Non-empty list** → Lead refuses to spawn agents whose role code is NOT in the list.

**Lead enforcement at spawn time:** before calling `Agent({subagent_type: "<role>", ...})`, resolve `subagent_type` to its TaskRole code and check it against `project.config.enabled_roles`. If not allowed, halt and tell operator to add it to config if desired.

## Subagent model logging (Kanban #887, 2026-05-13)

Dev team tracks subagent tier in `tasks.subagent_models` per universal Lead rules. Every state-transition PATCH Lead sends to the tasks API **must include the full `subagent_models` list** accumulated for that task so far. Bundle it into the same PATCH body as `process_status`, `acceptance_criteria`, `completed_at`, etc.

**What counts as a spawn (include in list):**
- Any `Agent({subagent_type: "<name>", ...})` call that returns real work output — dev-backend, dev-tester, dev-reviewer, etc.

**What does NOT count (do not include):**
- Lead's own Read / Grep / Glob / Bash exploration
- Skill invocations

**Element shape** (REPLACE semantics — Lead sends full accumulated list each PATCH; append is on Lead's side):
```json
{"agent": "dev-backend", "model": "opus", "at": "2026-05-13T09:00:00Z"}
```
- `agent`: the agent's frontmatter `name` (e.g., `dev-backend`, `dev-sr-backend`)
- `model`: one of `"opus"`, `"sonnet"`, `"haiku"` — mirrors the `model:` field in agent frontmatter (no frontmatter `model:` line → Opus default)
- `at`: UTC ISO-8601 timestamp when Lead initiated the spawn

**Example DONE-flip PATCH:**
```json
{
  "process_status": 5,
  "completed_at": "2026-05-13T10:00:00Z",
  "acceptance_criteria": [...],
  "subagent_models": [
    {"agent": "dev-backend", "model": "opus", "at": "2026-05-13T09:00:00Z"},
    {"agent": "dev-tester", "model": "sonnet", "at": "2026-05-13T09:30:00Z"}
  ]
}
```

If a task loops back (DONE → rework → DONE again), keep accumulating — the field records all spawns across the full task lifetime.

## Lifecycle (per task)

1. **Active project + team** are already resolved by the meta-Lead before this playbook is loaded.
2. **Read relevant context** — *lazy-read doctrine (Kanban #1798, 2026-06-02): keep the per-session bootstrap read lean; pull big reference files on demand, not preemptively.*
   - `shared/decisions.md` (always — kept compact; older entries are in `decisions-archive-2026-05.md`, grep on demand)
   - `shared/api-contracts-core.md` (always — the hot endpoints: projects read + tasks CRUD/PATCH)
   - `shared/component-status.md` + `shared/backlog-roadmap.md` (cheap state digest)
   - `<role>/current-state.md` for each role about to be spawned
   - `standards/general.md` always; `standards/<framework>/` per the lane mapping
   - **On-demand ONLY — do NOT full-read at bootstrap:** `shared/api-contracts.md` (full reference — read/grep the relevant SECTION when a task touches a non-hot endpoint); `shared/db-schema.md` (read the relevant section when a task touches the data layer); `decisions-archive-2026-05.md` (grep for historical decisions).
   - **Missing-context guard:** before acting on a non-hot API surface or the data layer, grep/read the relevant `api-contracts.md` / `db-schema.md` section first — don't assume a contract/decision doesn't exist just because it wasn't loaded at bootstrap.
3. **Decide which roles to spawn.** UI only → dev-frontend. API only → dev-backend. Full feature → dev-backend then dev-frontend (sequential if unstable contract; parallel if independent). Migration / deploy / Docker / CI → dev-devops. After implementation → dev-tester + dev-reviewer.
4. **Spawn via the Agent tool** — see [.claude/docs/spawn-template.md](.claude/docs/spawn-template.md). Independent roles can spawn in parallel.
5. **Verify subagent results** — open modified files; review proposed `shared/*` updates and standards insights.
5b. **Tier-1 smoke probe (live API).** When the task touched `api/src/routers/`, `api/alembic/versions/`, `api/src/schemas/`, `api/src/models/`, `api/src/templates/`, `api/src/main.py`, `docker-compose.yml`, or any env / settings file: spawn dev-tester to run scoped `curl localhost:<api-port>` probes. Probes assert behavior (e.g., `updated_at` advances, idempotent re-DELETE, response field shape), not just HTTP status. Methodology: [`context/teams/dev/smoke-methodology.md`](../../context/teams/dev/smoke-methodology.md). Project-specific endpoints / canonical seed values: each project's `shared/smoke-matrix.md`.
5c. **Headless question-gate loop.** When a task runs under `run_mode IN auto_pickup/auto_headless`, subagents must include the ambiguity gate section in every spawn brief (see [`context/teams/dev/autorun-spawn-convention.md`](../../context/teams/dev/autorun-spawn-convention.md)). If a subagent returns a HALT report: create a question/decision blocker task, store `resume_context`, and pick up the next ready task.
6. **Apply per-project shared updates yourself.** Question proposals that conflict with prior decisions; ask the user when unsure. Stamp `decisions.md` entries with date + proposing role.
7. **Update task status in the DB** (Kanban-tracked tasks): `PATCH /api/tasks/<id>` with `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done; `process_status=4` + comment on block.
8. **Handoff or close** — spawn the next role if the previous one flagged a handoff; otherwise summarize to the user (2-3 sentences).
9. **Compaction is automatic** — every subagent updates its own `current-state.md` before returning.

## Release wrap-up flow (Tier-2 gate before publish)

Release wrap-up checklist: customize per project at `context/projects/<active>/shared/release-checklist.md`. Lead seeds the template on project bootstrap. Tier-1 smoke (step 5b above) catches per-task regressions; Tier-2 is the full superset run once before public release.

Methodology (flow, severity scales, wrap-up summary template): [`context/teams/dev/release-methodology.md`](../../context/teams/dev/release-methodology.md). Project-specific endpoint matrix: each project's `shared/release-matrix.md`.

## Task creation discipline

When Lead creates a new task via `POST /api/tasks`, `acceptance_criteria` **must be in the same curl call body** — never create-then-patch-later.

Rule: before writing the curl command, draft at least 3 ACs. If the task is too vague to write ACs, the description needs more clarity first.

AC format:
```json
"acceptance_criteria": [
  {"text": "...", "status": "pending", "verified_by": null, "verified_at": null, "notes": null}
]
```

## Dev-specific anti-patterns

- Spawning dev-frontend + dev-backend in parallel when the API contract isn't stable → **sequential: backend first**.
- Skipping dev-tester after an implementation lands → **incomplete cycle**.
- Letting dev-devops apply a migration before the migration file is reviewed → **review first, then apply**.
- Marking a task done without step 5b when the task touched routers / migrations / schemas / scaffold templates / env config → **live API smoke skipped**.
- Creating a task without `acceptance_criteria` in the same POST call → **AC missing at creation**.

Universal anti-patterns are in root CLAUDE.md and [.claude/docs/lessons.md](.claude/docs/lessons.md).
