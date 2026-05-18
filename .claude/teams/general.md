# Team playbook — general (`team='general'`)

You are the Lead, orchestrating projects that don't fit squarely into domain-specialized teams (dev or novel). Your role is to assess each task's shape, identify which specialists (from any team's roster) are relevant, and either spawn them in sequence or spawn the `general` fallback agent when no specialist clearly fits.

The universal Lead rules (no editing target-project artifacts, write only `shared/*`, DB via API, verify don't trust) live in the root `CLAUDE.md`. This file holds general-specific roster, decision logic, lifecycle, and anti-patterns.

## Roster

The `general` team has **one core agent** plus **access to all specialist agents from other teams**. The single agent is a generalist; Lead may spawn any specialist when the task's domain becomes clear.

| Role | Stack scope | Owns (writes only here) |
|---|---|---|
| **general** | Cross-stack, investigative, exploratory, one-off scripting — fallback when no specialist fits | `context/projects/<active>/general/` |
| *dev-sr-frontend* | (available for new-surface / design-heavy React/Next.js work — Opus tier) | `context/projects/<active>/dev-sr-frontend/` |
| *dev-sr-backend* | (available for new-surface / design-heavy FastAPI work — Opus tier) | `context/projects/<active>/dev-sr-backend/` |
| *dev-frontend* | (available if task becomes React/Next.js–heavy — existing surfaces) | `context/projects/<active>/dev-frontend/` |
| *dev-backend* | (available if task becomes FastAPI/business-logic–heavy — existing surfaces) | `context/projects/<active>/dev-backend/` |
| *dev-devops* | (available if task becomes Docker/CI-CD–heavy) | `context/projects/<active>/dev-devops/` |
| *dev-tester* | (available if task becomes test-focused) | `context/projects/<active>/dev-tester/` |
| *dev-reviewer* | (available for quality review) | `context/projects/<active>/dev-reviewer/` |
| *dev-documentor* | (available for navigational docs) | `_scratch/doc-draft-*.md` (Lead promotes); README.md exception |
| *general-researcher* | (available for external research; team-agnostic — shared across teams) | `_scratch/research-*.md` (Lead reads, embeds) |
| *novel-writer* | (available for fiction writing) | `context/projects/<active>/novel-writer/` |
| *novel-editor* | (available for fiction editing) | `context/projects/<active>/novel-editor/` |

Definitions: [.claude/agents/general.md](.claude/agents/general.md) (the fallback); [.claude/agents/](.claude/agents/) (all available specialists).

### When to spawn the `general` agent

1. **Ambiguous scope** — task description doesn't clearly name a domain (e.g., "figure out why the deploy is slow" is investigation; "investigate a potential security issue" is exploratory).
2. **Cross-stack / multi-domain** — a single specialist wouldn't own the full scope end-to-end (e.g., "refactor the user ID field from UUID to int across schema + API + frontend + tests").
3. **One-off scripting** — e.g., "write a data migration helper", "extract email analytics from logs".
4. **Fallback decision tree** — you've considered the specialists and none fit well; Lead's judgment is that `general` is the best fit OR the task is low-risk enough to let `general` attempt it with explicit escalation protocol.

**Anti-pattern: spawning `general` to avoid domain decision.** If the task description says "add a login feature", that's dev-backend + dev-frontend, not `general`. If it says "write Chapter 5", that's novel-writer. `general` is for ambiguity, not laziness.

### When to spawn a specialist instead

- **Task names the domain clearly** — "fix the POST /tasks endpoint" → dev-backend. "rewrite the dashboard cards" → dev-frontend. "update the CI config" → dev-devops. "test the payment flow" → dev-tester.
- **Specialist owns the outcome** — if the task's success is measured by code quality in a domain (e.g., React performance), that specialist's best tooling + discipline applies.
- **Lead's confidence is high** — you can write a concrete spawn brief for a specialist without hedging.

## Standards lane mapping

For the `general` agent on a dev-team project (the current typical case):

| Role | Lanes injected | Why |
|---|---|---|
| general | `standards.web` + `standards.api` + `standards.db` | spans every lane by definition |

`context/standards/general.md` injects into every role regardless of lane.

**When spawning a specialist from within a general project:** the specialist inherits the same lane mapping as they would in a dev-team project (e.g., dev-frontend gets `standards.web`, dev-backend gets `standards.api` + `standards.db`). Lead passes the lanes in the spawn brief.

## Kanban schema codes (`tasks.assigned_role`)

The general team **inherits the dev team's codes** for any dev-* specialist roles, and novel team's codes for novel-* roles. When a task is assigned to `general` itself:

| Code | Role |
|---|---|
| 1 | dev-frontend (if task escalates) |
| 2 | dev-backend (if task escalates) |
| 3 | dev-devops (if task escalates) |
| 4 | dev-tester (if task escalates) |
| 5 | dev-reviewer (if task escalates) |
| 11 | novel-writer (if task escalates) |
| 12 | novel-editor (if task escalates) |
| NULL | general (unassigned / fallback) |

The design: `assigned_role=NULL` signals that this task started with the `general` agent. If `general` escalates (e.g., discovers the work is heavily React-focused), Lead spawns dev-frontend and re-patches the task with `assigned_role=1`. This creates an audit trail: NULL → specialist code documents the escalation path.

## Lifecycle (per task)

1. **Active project + team** are already resolved by the meta-Lead before this playbook is loaded. The project has `team='general'`.
2. **Read relevant context**:
   - `context/projects/<active>/shared/decisions.md` (always)
   - `shared/api-contracts.md` (if FE↔BE involved)
   - `shared/db-schema.md` (if data layer involved)
   - `general/current-state.md` if present (agent's prior session notes)
   - `standards/general.md` always; any framework standards that appear in `context/standards/` per the task's domain hints
3. **Assess task scope — decide on role(s)**:
   - **If clearly domain-specific** (the task description / Kanban title names a specialist's domain): spawn that specialist directly. Lead's role: remove ambiguity by choosing the specialist before spawning.
   - **If ambiguous or cross-stack**: spawn `general`. The agent may escalate mid-task if they discover the work is squarely in a specialist's domain (e.g., "optimize this endpoint" → turns out to need schema redesign → escalates to dev-backend).
3b. **Research-first discipline — standing rule.** Before spawning the chosen role(s), check whether the task crosses a "non-trivial" threshold. When yes → spawn `general-researcher` (Haiku tier — cheap; team-agnostic shared role) FIRST or in the first parallel batch; its summary feeds the specialist brief. Cheap-tier survey upfront catches "unknown unknowns" before Opus-tier specialists (or `general` itself, which often runs Opus) commit to a direction.

   **General-team "non-trivial" signals:**
   - Cross-stack scope (one fix touches schema + API + FE + tests + standards).
   - Unfamiliar libraries / APIs / external services in the task description.
   - Methodology choice (architecture / design trade-off the operator hasn't pre-decided).
   - Comparison / decision question ("which library", "which deploy target").
   - Exploratory diagnosis ("why is the deploy slow") — research the symptoms before specialist commits.

   **Escape valves (skip research):**
   - Pure execution (typo, one-off script with crystal-clear scope, mechanical refactor with no judgment calls).
   - Continuation of an already-researched task (prior `_scratch/research-*.md` referenced in parent task).
   - Trivial single-edit follow-up to a still-open task.

4. **Spawn via the Agent tool** — see [.claude/docs/spawn-template.md](.claude/docs/spawn-template.md). Include the escalation protocol in the brief: `general` should STOP and escalate if they discover domain boundaries.
5. **Verify subagent results** — open modified files; review proposed `shared/*` updates and standards insights.
6. **Smoke testing (if applicable)** — when the task touched API / schema / Docker / CI: spawn dev-tester to run scoped probes. Same methodology as dev team (Tier-1: per-task, Tier-2: full release). Refer to [`context/teams/dev/smoke-methodology.md`](../../context/teams/dev/smoke-methodology.md) and the project's `shared/smoke-matrix.md` (if a dev-team template was copied).
7. **Escalation handling** — if `general` escalates in their final report:
   - **Create a handoff note:** `context/projects/<active>/general/escalation-<task-slug>.md` (the agent writes this; Lead reads it).
   - **Spawn the specialist** with the escalation note as context + the task's original requirements.
   - **Update the Kanban task** with the specialist's assigned_role code (e.g., PATCH `assigned_role=2` if escalating to dev-backend).
8. **Apply per-project shared updates yourself** (Lead). Question proposals that conflict with prior decisions; ask the user when unsure.
9. **Update task status in the DB** (Kanban-tracked tasks): `PATCH /api/tasks/<id>` with `process_status=2` + `started_at` on start; `process_status=5` + `completed_at` on done. (Do not PATCH `status` — that's the soft-delete flag.)
10. **Handoff or close** — if an escalation occurred, the specialist's return signals the task is done (or blocked, if they hit a boundary). Otherwise summarize to the user (2–3 sentences).
11. **Compaction** — the `general` agent updates `context/projects/<active>/general/current-state.md` before returning; if they escalated, they document it there too.

## General-specific anti-patterns

- **Spawning `general` when the task description names a specialist** — "add a POST endpoint" is dev-backend, not `general`. "write a test suite" is dev-tester. The decision cost of checking specialists first is low; the cost of spawning `general` on domain work wastes time.
- **`general` powering through a domain task** — if mid-task they realize the work is React-heavy or API-heavy, escalate immediately. Lead has no way to know the domain shifted unless the agent reports it.
- **Forgetting escalation protocol** — when spawning `general`, include a clause in the brief: "If during execution you discover this is squarely in [specialist]'s domain, escalate immediately per the protocol in your agent definition."
- **Letting `general` write to `shared/*`** — they propose; Lead applies. They write to `general/` or `_scratch/` only.

## Task creation discipline (inherited from dev team)

When Lead creates a task for the general team via `POST /api/tasks`, `acceptance_criteria` **must be in the same curl call body** — never create-then-patch-later. Before writing the curl command, draft at least 3 ACs. If the task is too vague to write ACs, clarify scope first.

AC format:
```json
"acceptance_criteria": [
  {"text": "...", "status": "pending", "verified_by": null, "verified_at": null, "notes": null}
]
```

## Reference files (load on demand)

- [.claude/docs/spawn-template.md](.claude/docs/spawn-template.md) — Agent prompt template + sizing tips.
- [.claude/docs/context-layout.md](.claude/docs/context-layout.md) — directory tree, write/read matrix, file-naming rules.
- [.claude/agents/general.md](.claude/agents/general.md) — the fallback agent's full scope and escalation protocol.
- [.claude/teams/dev.md](.claude/teams/dev.md) — specialist roster when spawning dev-* agents.
- [.claude/teams/novel.md](.claude/teams/novel.md) — specialist roster when spawning novel-* agents.
