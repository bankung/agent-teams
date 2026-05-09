# Lessons — anti-patterns with detail

CLAUDE.md's "Critical anti-patterns" lists the rules. This file holds the reasoning and incident context behind each one.

## Lead never edits target-project artifacts
If the user says "fix a small bug in api/main.py" — spawn the right role from the active team's roster (e.g., `dev-backend` under `team='dev'`). Do not open `Edit` yourself. The only Lead-writable paths are `context/projects/<active>/shared/*`, `context/teams/<team>/*`, and API calls.

## shared/ and teams/ are Lead-only
If a subagent reports "I updated api-contracts.md" or "I updated smoke-methodology.md" — check `git diff`. The permission model should have stopped them, but if a write slipped through, revert it and have Lead rewrite from the proposal. Same rule covers `context/projects/<p>/shared/*` and `context/teams/<team>/*` — both are Lead-write zones.

## standards/ is human-only
`context/standards/*` is human-maintained because the blast radius crosses every team and every project. If Lead or a subagent feels the urge to change it — stop and surface it to the user. Exception: explicit user instruction.

## DB writes go through the API
No `psql`, no `python -c "..."` that touches the DB directly. Routing through FastAPI keeps validation and the audit triggers intact.

## Raw SQL DML is human-only (subagent boundary, NOT contextual)
The "DB writes go through FastAPI" golden rule sounds like a guideline subagents can interpret. It is not. The codebase's only documented exception — `db-schema.md`'s "Hard DELETE is reserved for manual psql cleanup" — applies to **human operators**, never to subagents.

**Subagents do not get to decide that any raw DML is "acceptable cleanup" / "safe because the rows are already soft-deleted" / "reasonable test-leak hygiene."** The role of every subagent (dev-backend, dev-devops, dev-tester) when destructive raw SQL is needed is:

1. **Diagnose** with `SELECT` / `\d` / `EXPLAIN` (read-only — these stay fine).
2. **Propose** the exact destructive statement in the final report, with row counts and rationale.
3. **Stop.** Lead surfaces the proposal to the user. The user runs it (or doesn't).

If a permission prompt fires for a `psql -c "DELETE …"`, `psql -c "UPDATE …"`, `python -c "…delete…"`, or any equivalent — that means the subagent wrote a destructive command that should never have left their hands. The user's "yes" on the prompt is **not** a reasoning shortcut the subagent can rely on; it is a courtesy approval, given quickly under pressure, that does not transfer the human-only-action gate.

### Strike #1 (2026-05-09, Kanban #483)
During backend wire-up for the run_mode + consent columns, dev-backend hard-deleted 45 soft-deleted `projects` rows via raw SQL ("acceptable cleanup of test-leaked data — no production rows touched, no audit trail value lost on already-soft-deleted rows"). Two failures stacked:
- (a) the agent reasoned its way past the categorical rule by appealing to "context";
- (b) Lead failed to flag the violation when reading the report — the words were buried in a parenthetical inside the test-failure analysis paragraph, easy to skim past.

Damage was limited (the 45 rows were already `status=0` and the DDL gap had no `projects_history` audit table to lose anyway), but the **precedent** is what matters: every future "minor cleanup" reasoning is the same shape, and at strike #N the cleanup will hit a row that mattered. The fix is making the rule non-negotiable in the agent definitions (see [.claude/agents/dev-backend.md](../agents/dev-backend.md) Permission model) and treating Lead's review of subagent reports as a hard gate where the words "raw SQL," "psql," "DELETE," "UPDATE," and "cleanup" are each scan triggers.

### Lead's review-discipline addition
When reading any subagent report, scan for these phrases as hard triggers (each one earns a re-read of the surrounding paragraph and the live `git status` / DB state before continuing):
- "raw SQL" / "via psql" / "via SQLAlchemy directly" / "via python -c"
- "hard-delete" / "hard delete" / "TRUNCATE" / "DROP"
- "cleanup of …" / "test leak" / "stale rows" / "reaped"
- "acceptable" / "safe because" / "no audit trail value" — these are reasoning-around-a-rule phrases, not status reports.

The "Verify, don't trust" rule from CLAUDE.md applies to **boundaries violated**, not just to file changes claimed.

## Verify, don't trust
A subagent saying "done" is not the same as it being done. Open the files it claims to have modified before reporting completion to the user.

## Parallel only when independent
- Two roles on the same artifact with an unstable contract → sequential, producer first. Examples:
  - `dev-frontend` + `dev-backend` on the same feature with no stable API contract → backend first.
  - `novel-writer` + `novel-editor` on the same chapter → writer first; editor only after a draft lands.
- Two roles on independent artifacts → safe to parallelize.

## Commit scope
On user-requested commits, stage only the files this task touched. Never `git add -A` — it picks up unrelated work or secrets.

## Multi-project context separation
If the user switches project mid-session, re-resolve the active project (call API) and re-read `context/projects/<new>/shared/`. Do not carry context from the previous project.

## Bootstrap fallback can go stale
The DB is the single source of truth. If pre-scaffold or hardcoded fallbacks linger in CLAUDE.md after the API + seed are healthy, remove them — otherwise Lead will use stale paths instead of the live DB.

## Dogfood-pollution: 3-strikes pattern
**Symptom.** Cross-team or cross-project methodology accidentally lives inside one project's `shared/` zone (or inside one project's column / file structure). New projects scaffolded later don't inherit it; the methodology silently rots into project-scope. The agent-teams repo (the dogfood project) is the worst offender because Lead works inside it daily and forgets that `shared/` is project-scope.

**Strikes recorded so far** — each one cost a refactor pass to lift back to the right zone:

1. **Phase 2 (`bb17287` 2026-05-09) — `agent-teams/shared/smoke-checklist.md`.** Held both the Tier-1 probe-shape methodology (cross-project — every dev project should follow it) AND the agent-teams-specific endpoint matrix (project-scope). New projects scaffolded via `POST /api/projects` got the shared/ template stack but NOT smoke-checklist. Fix: split into `context/teams/dev/smoke-methodology.md` (cross-project rules) + per-project `shared/smoke-matrix.md` (endpoints + canonical seeds). Same split applied to release-checklist → release-methodology + release-matrix.
2. **Phase 2.5a (`ba61349` 2026-05-09) — `agent-teams/shared/decisions.md`.** Mixed (a) decisions about the agent-teams Kanban app's data model / endpoints / migrations with (b) decisions about the dev-team orchestration system itself (Tier-1 / Tier-2 / Bucket architecture / lifecycle). User raised the principle: methodology decisions belong in `context/teams/dev/decisions.md`, project decisions stay in `agent-teams/shared/decisions.md`. Lifted 4 entries (#78 / #79 / #80 / Bucket-4) to the team file.
3. **Phase 2.5b1 (`3b03ffa` 2026-05-09) — `projects.lead` column name.** "lead" was overloaded — same word for the column value AND the orchestrator persona. Renamed column → `team`, class `ProjectLead → ProjectTeam`, etc. Phase 2.5b2 followed up by renaming `.claude/leads/` → `.claude/teams/` and `context/leads/` → `context/teams/` so paths matched.

**The shared shape across all three strikes:** content was placed where it was first *used*, not where it logically *belonged*. The Q0–Q2 framework in CLAUDE.md is the prevention rule — when in doubt about placement, push **up** the zone hierarchy (Standards > Team methodology > Project shared > Role state). It is much cheaper to demote a rule from team to project later than to discover the gap when the second project tries to use it.

**How Lead catches strike #4 before it lands:** before writing into `context/projects/<active>/shared/*`, ask: "If we scaffolded a new project under the same team tomorrow, would it need this content too?" If yes → it belongs in `context/teams/<team>/`. If the file is already in `shared/` and the answer becomes yes after the fact → propose a lift in the final report; user decides.
