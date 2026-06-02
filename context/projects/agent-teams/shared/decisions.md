# Architectural & process decisions — agent-teams (the Kanban app)

> **Lead is the only writer of this file.** Subagents propose updates in their final report — Lead reviews, may ask the user, then writes the entry.
>
> **Scope:** decisions about **agent-teams the Kanban app itself** — its data model, endpoints, UI, migrations, deps, schema choices. agent-teams is also the dogfood project for the dev-team orchestration system, but **methodology decisions** (Tier-1 / Tier-2 / lifecycle / zone architecture / agent prompts) live in `context/teams/dev/decisions.md` (the **Team-methodology zone**) — not here. When a project-specific incident produces a methodology lesson, the incident decision goes here and the methodology decision goes in the team file, cross-linked.
>
> Format: append-only log. Newest entry at the top. Each entry has a date, scope, and the locked decision + reasoning + downstream implications. Granular commit-narrative (per-agent lifecycle, pytest counts, file lists) belongs in `git log`, not here.

<!--
Template:

## YYYY-MM-DD — <short title>
**Scope:** frontend | backend | devops | qa | reviewer | shared
**Decision:** <what we decided>
**Reasoning:** <constraints, tradeoffs, alternatives considered>
**Implications:** <downstream coupling>
-->

> **Archive:** entries dated ≤ 2026-05-19 are in [`decisions-archive-2026-05.md`](decisions-archive-2026-05.md) (split 2026-06-02, Kanban #1583, to shrink the bootstrap context read). Grep the archive for historical / closed decisions.

## 2026-06-02 — Bootstrap-context reduction (api-contracts split) + Mode-B Option-1 decision — #1798, #1652
**Scope:** shared / process + engine

**Decision:** (1) **#1798** — `api-contracts.md` split into `api-contracts-core.md` (31KB; hot: projects read + tasks CRUD/PATCH; bootstrap-read) + `api-contracts.md` (52KB; full reference; on-demand). Part of the lazy-read bootstrap doctrine (methodology decision in `context/teams/dev/decisions.md`); `db-schema.md` also deferred to on-demand. (2) **#1652** — operator chose **Option 1** (per-project `runtime_config` + engine-built per-project image) as the END-STATE fix for the Mode-B binary-dependency gap, **PHASED**: Phase 1 = Option-2 host-prereq guard (fail-clear; binary-dep projects Mode-A-only) to unblock Mode B NOW; Phase 2 = build Option 1 when a real binary-dep project needs headless. Full design: `shared/design/mode-b-runtime-options.md`.

**Implications:**
- Bootstrap read drops ~153KB → ~68KB/session.
- Mode-B workstream (#1728 / #776 / #781 / #1191) unblocks once the Phase-1 guard lands (binary-dep = documented Mode-A-only).
- Phase-2 build is Medium-Large + security-gated (image build from adopter config) and blocked on an operator-vs-AI auth distinction for `runtime_config` writes.

## 2026-06-02 — Per-project progress charts (#1292) + project-board header redesign (#1781)
**Scope:** backend + frontend

**Decision:** (1) **#1292** — new read-only `GET /api/projects/{id}/progress-stats?bucket=day|week&days=N` (burndown + velocity from the `tasks` table; `/pl`-style `X-Project-Id`==path auth; single SELECT + Python bucketing, no N+1). FE renders **hand-rolled inline-SVG** mini-charts (NO chart lib added — repo has none) in the Board header, click → full `ModalShell`. Burndown = all-open backlog as of each bucket-end (no `created_at` lower bound — classic remaining-work, so an ongoing project's line *rises*). (2) **#1781** — Board header redesigned for density: nav one-row; Show-audit / Scheduled / Pause / Terminate / Integrations(gear) → icon buttons; the two task-create buttons → a single **"+ New ▾" dropdown** driving the existing `AiTaskModal`/`NewTaskModal` via a new optional `externalOpen` prop; USAGE/P&L/PROGRESS folded into one compact band (expanded stat fonts `text-lg` in the collapsible board context, unchanged on the dashboard portfolio view). Hard rule: **Kanban board ≥60% viewport height**, enforced by capping `<header lg:max-h-[40vh]>` so the `flex-1` board claims the remainder.

**Reasoning:** Operator: the board page lacked at-a-glance progress AND the header chrome was squeezing the Kanban to almost nothing. Inline SVG avoids an npm-install/container-rebuild + bundle bloat. Capping the header (not per-element shrinking) is what *guarantees* the ≥60% floor regardless of banner/panel state. `externalOpen` mirrors the existing Pause/Kill modal pattern → no flow change, backward-compatible (self-managed trigger still renders when the prop is absent).

**Implications:**
- `progress-stats` velocity verified EXACT vs SQL (`date_trunc('week')` DONE counts: 40/195/119/81/1). v1 ignores `tasks_history`; exact-transition counting deferred.
- `AiTaskModal`/`NewTaskModal` now accept optional `externalOpen`/`onExternalClose` — used only by `NewTaskDropdown` today; standalone trigger preserved when the prop is undefined.
- **FE-verify gotcha (cross-link `feedback_no_next_build_live_dev`):** on this Windows+Docker host, bind-mounted FE edits do NOT hot-reload — `docker restart agent-teams-web` is required BEFORE render-verify, and SSR grep-verification must use unique `data-*` markers (feature words leak from task-card text the board renders). This caused a Lead misreport mid-#1292 before correction (#1292 charts initially looked rendered because the board card for #1292 itself contained the words "Burndown/Velocity").

## 2026-05-30 — Cost display G1: surface ESTIMATED cost (not metered) — Kanban #1688
**Scope:** backend + frontend

**Decision:** The dashboard + project "Usage" panel now surfaces an **estimated** cost — `SUM(tasks.estimated_cost_usd)` exposed as a new per-project `estimated_cost` aggregate on `GET /api/projects/stats` — shown ALONGSIDE the metered cost (`cost_usage`, from `session_runs`). The estimate is clearly LABELED "Estimated" + "heuristic estimate — metered cost coming soon", visually distinct from "Metered". It must NOT be read as actual spend.

**Reasoning:** The cost infra (pricing `cost_tracker.py`, `tasks.estimated_cost_usd` + token cols, `compute_cost`, `session_runs`, `/stats`, `/pnl`, display) all EXISTED but showed ~$0 because `session_runs` are rarely fed real token counts — and in **Mode A the platform does not make the LLM calls** (Claude Code does) so it cannot auto-meter. `tasks.estimated_cost_usd` IS populated (heuristic) on each done-flip, so surfacing it (honestly labeled) gives a real-ish number now without faking metering. Phased per operator: G1 = estimates now; **G2 (#1689) = real metering** (instrument in-platform LLM calls — langgraph/ai_task_parser/compact — + a Mode-A usage-reporting path; ties #1652).

**Implications:** Estimated vs metered are two distinct UI figures (don't conflate). `estimated_cost.total_cost_usd` is serialized as a **Decimal STRING** (same as `cost_usage.total_cost_usd`) — parse via `parseUsd()` before arithmetic. (A type-vs-runtime mismatch — FE typed it `number`, BE sent string → would have string-concatenated — was caught in pre-commit spot-verify; tsc missed it because the type lied.)

## 2026-05-29 — Platform "Integrations" settings popup — Kanban #1655
**Scope:** backend + frontend + devops

**Decision:** New global surface `/api/settings/integrations` (GET list + PATCH `/{id}` toggle) + `PlatformSettingsModal` (gear in Board header) listing OPTIONAL integrations, each OFF by default. Option A (operator-chosen): keys **stay in .env** — NO key entry/storage via the UI/API. The DB table `platform_integration_settings` (migration `0052_integration_settings`) stores ONLY the per-integration `enabled` toggle; `configured`/`present` are computed LIVE from `os.environ` at request time and returned as presence BOOLEANS (the `IntegrationRead` model physically cannot carry a secret value). Registry of 11 optional integrations is static Python (`services/integrations_registry.py`); CORE keys (DATABASE_URL, REPO_ROOT, CREDENTIALS_MASTER_KEY, LANGGRAPH_PROJECT_ID) are deliberately excluded (platform won't boot without them — never toggleable).

**Reasoning:** Feature keys were ALREADY optional in code (degrade gracefully), so this is a visibility+guidance layer, not a re-architecture. Live-compute avoids a stored-secret surface entirely + no cache-invalidation. Static registry keeps the catalog under code review, not operator data.

**Implications:**
- The toggle persists operator INTENT + drives the verify/guidance UX — it is **NOT a live consumer kill-switch** this round (consumers still gate on env presence as today). In-UI encrypted key entry (Option B) is a deferred follow-up.
- **`configured` reflects the API container's `os.environ`.** Keys consumed only by the `langgraph` container (e.g. `ANTHROPIC_API_KEY` via the headless engine) may read as "Not configured" here even when the feature works — known limitation of single-container env read; acceptable for v1. Documented so the operator isn't surprised.
- **Auth posture:** the endpoint is global/unauthenticated (parity with `/api/teams`); it exposes WHICH integrations are configured (presence only, never values). Acceptable for the single-operator local app; would be an info-disclosure concern on a multi-tenant deployment — revisit if the app ever goes multi-tenant.
- Migration ids must stay ≤32 chars (`alembic_version.version_num` is VARCHAR(32)) — `0052_platform_integration_settings` (34) failed the version-stamp; shortened to `0052_integration_settings`. (Methodology note candidate for `lessons.md`.)

## 2026-05-29 — Weekly release cadence: dev branch + weekly merge-to-main + vMAJOR.MINOR.PATCH (trial) — Kanban #1646
**Scope:** shared / process

**Decision:** Switch agent-teams from continuous-push-to-main to a weekly release cadence. Develop on `dev`; `main` is the published release (the curated weekly snapshot a recruiter/user sees), updated only by a weekly merge from `dev` (or a hotfix merge). Versioning `vMAJOR.MINOR.PATCH`: MAJOR starts 0, bumped only on operator command; MINOR = running number per normal (weekly) release (bump + reset PATCH=0); PATCH = hotfix number (resets per weekly release). Version of record = the annotated git tag (gh CLI not installed → tags are the mechanism; formal GitHub Releases once gh lands). Full runbook: `shared/release-workflow.md`. Weekly trigger = recurring template task #1647 (Fri 18:00 Asia/Bangkok).

**Reasoning:** Team proposal — 1 publish/week. Chose dev-branch + weekly-merge-to-main (over continuous-main + weekly-tags) so `main` stays a clean, stable, curated line for the public/portfolio audience while churn lives on `dev`. Builds on the existing Tier-2 release-wrap-up gate. For THIS repo this supersedes the old solo-dev "always-main / no-branch" default.

**Implications:**
- All sessions/worktrees now push to `dev`, NOT `main` directly.
- `main` HEAD always == the latest release tag; never force-push `main`.
- Trial run: first release `v0.1.0` cut from main today (2026-05-29); hotfix (0.1.1) + first weekly bump (0.2.0) being exercised manually during the trial. Promote to dev-team methodology (`context/teams/dev/`) only if it proves out over a few weeks.

## 2026-05-29 — Public-repo hygiene: removed internal working notes + pre-push keyword guard — Kanban #1637
**Scope:** shared / privacy

**Decision:** Removed a few dated internal working notes and genericized some incidental references that carried early-stage private planning detail not intended for a public repository. Chose edit-forward remediation (remove/genericize at HEAD + add a prevention hook) over a git-history rewrite + force-push — the rewrite would break active worktrees/clones and offers diminishing returns (rewrite is not a full guarantee against caches/forks).

**Implications:**
- Internal/early-stage planning notes stay in the DB + local-only zones, never in tracked repo files.
- A pre-push keyword guard now blocks pushes that would reintroduce the flagged terms into tracked files.
- Prior content remains in git history; a future history-rewrite stays available if ever warranted.

## 2026-05-28 — api suite determinism: triage closed, 0051 downgrade regression fixed, concurrent-invocation lock added — Kanban #1599
**Scope:** qa / backend / shared

**Decision:** Closed the #1599 suite-flakiness triage with three findings + one guard:
1. **Problem A (20 named failures) — already resolved.** All 7 named failing groups (kill_switch, notification_router, sessions, subagent_models, template_auto_run_confirm, user_next_action, + integration) now pass (136/136). The failures from the 2026-05-27 #1284 run were cleared by intervening work (#1266/#1269/#1271 + later). No new fix needed.
2. **Real regression found by the FULL-suite run:** `test_tool_calls.py::test_migration_downgrade_then_upgrade_leaves_clean_state` failed because #1620's migration 0051 had a no-op `downgrade()`. Fixed (0051 now recreates the then-current 7-team CHECK — see the #1620 entry's updated implication line).
3. **Problem B (non-deterministic counts 20→155→472→623) root cause:** the suite is single-process with deterministic collection order (no xdist, no pytest-randomly), so a single run is inherently deterministic. The historical variance came from **concurrent pytest invocations** colliding on the HARDCODED `agent_teams_test` DB name: a second run's `_setup_test_database` does `pg_terminate_backend` + `DROP DATABASE agent_teams_test` while the first is mid-suite, killing its connections → cascade.

**Isolation mechanism chosen (AC3):** serial execution (already the design — single-process) + a **`filelock.FileLock`** (existing dep) wrapping `_setup_test_database` in conftest. Concurrent invocations now serialize: the second blocks on the lock until the first finishes teardown, instead of corrupting it. OS-level lock → auto-released on process death (no permanent stuck-lock risk). 900s timeout raises a clear RuntimeError naming the collision.

**Reasoning:** Empirically proved determinism — 3 consecutive full runs all reported an identical **1385 passed / 0 failed**. Chose a fixture lock over per-test rollback (the suite relies on a session-scoped seeded DB + unique-name discipline, not transactional rollback; retrofitting rollback would be a large, risky rewrite) and over unique-per-invocation DB names (orphan-DB cleanup complexity on crash). The lock is ~10 LOC, uses an existing dep, and matches this repo's prevention-layer culture (L1–L19).

**Implications:**
- The suite is now a reliable 0/deterministic regression signal again. Full-suite green = 1385 passed.
- Concurrent pytest invocations on one host no longer corrupt each other — the second waits.
- The 0051 regression slipped past #1620 because that task validated with SCOPED selectors, not full files (same gap that let #1618 break test_777). Methodology reinforcement in `context/teams/dev/decisions.md`.

## 2026-05-28 — web 500 (.next hot-reload corruption): heal-script + runbook, not autoheal sidecar — Kanban #1625
**Scope:** devops / shared

**Decision:** Mitigate the recurring `web` 500 (`TypeError: e[o] is not a function` at `.next/server/webpack-runtime.js`, seen twice after rapid multi-file FE edits) with a deterministic, operator/agent-triggered heal: `bin/web-heal.ps1` + `bin/web-heal.sh` (`docker compose -p agent-teams restart web`, plus a `--clean`/`-Clean` mode that wipes `web/.next`), plus a `## Troubleshooting` runbook entry in `readme_dev.md`. No change to `docker-compose.yml`.

**Reasoning:** Root cause is a webpack chunk/runtime-manifest desync — `next dev` Fast-Refresh incremental recompiles racing against coalesced/out-of-order filesystem events over the Windows Docker-Desktop bind mount. The `next dev` process does NOT crash (it serves 500s while alive), so `restart: on-failure` and the existing healthcheck can't recover it; only an explicit restart does. Rejected an autoheal sidecar (e.g. willfarrell/autoheal): a 5s healthcheck timeout is shorter than a legit cold `next dev` compile, so autoheal would false-positive-restart mid-compile and *worsen* churn — disproportionate for a dev-only, low-urgency, 1-command-fix issue. The real pain was *diagnosis* ("white page, no error"), which the runbook removes.

**Implications:**
- If it recurs after repeated heals, the documented next lever is `WATCHPACK_POLLING=true` on the `web` service env (reliable polling over inotify-on-bind-mount). Not applied now (adds recompile churn).
- `web` still has no `restart:` policy (intentional — wouldn't help this failure mode, which is process-alive).

## 2026-05-28 — projects.team CHECK dropped; team enum is app-validated single-source — Kanban #1620
**Scope:** backend / schema / shared

**Decision:** Dropped `ck_projects_team_valid` CHECK (migration 0051). `projects.team` is now a plain NOT NULL DEFAULT 'dev' string validated at the API boundary: Pydantic `TeamCode` Literal auto-derived from `ProjectTeam.ALL` + an explicit 422 gate in BOTH `create_project` and `update_project`. Single source of truth = `api/src/constants.py` `ProjectTeam.ALL` + `TEAM_ROSTERS`. New `GET /api/teams` (global, no X-Project-Id) serves the registry; `GET /api/scaffold/{team}/files` gains `role_folders`; `zero_config_scaffold._resolve_manifest` is convention-derived from the roster (`.claude/agents/{role}.md` + `.claude/teams/{team}.md` when present + `context/teams/{team}/**`). FE `NewProjectModal` and `bin/agent-teams-init.ps1` consume the API instead of hardcoded team/roster copies.

**Reasoning:** Adding a team previously required ~11 coordinated edits including a per-team migration — the CHECK constraint was the thing forcing the migration. Rejected a `teams` DB table as over-engineered (no UI/runtime team-management need; settled over 5 design-review rounds). Dropping the CHECK + app-layer validation is a strictly stronger gate (clean 422 vs the prior mistranslated 409 from the IntegrityError handler) on a single-owner DB where raw DML is human-only. Bonus: fixes the wrong-409-on-unknown-team bug in create + update.

**Implications:**
- Add a team = edit `constants.py` (ProjectTeam value + TEAM_ROSTERS entry) + drop `.claude/teams/<t>.md` + agent `.md`s for new roles. NO migration, no ORM/FE/ps1 edits.
- Unknown team → 422 everywhere (was: silent dev-fallback at scaffold; wrong-409 at create/update).
- `content` roster is INFERRED (no `content.md` playbook exists yet) — followup to author it.
- Migration 0051 `downgrade()` recreates the then-current 7-team CHECK (UPDATED #1599 — was a no-op, which broke the down→up roundtrip test `test_migration_downgrade_then_upgrade_leaves_clean_state`: the no-op left 0044's bare `DROP CONSTRAINT ck_projects_team_valid` with nothing to drop. Recreate-then-current-set was an explicitly-permitted option in the locked #1620 design). Same caveat as every team migration: the downgrade fails if a row carries a team outside that 7-set (operator re-teams/soft-deletes first — never raw SQL DML).
- Add-team / add-agent methodology + the TaskRole-code coupling floor: see `context/teams/dev/decisions.md` (same date).

## 2026-05-22 — Env-var wiring trap documented (root .env + compose mapping) — Kanban #1449
**Scope:** shared / infra docs

**Decision:** Wrote `shared/runbooks/env-var-setup.md` documenting the env-var flow that bit #1217 (operator put Gmail SMTP vars in `api/.env` thinking it was the right file; docker compose only reads root `.env`; vars also need to be `${VAR}`-mapped in `docker-compose.yml` service `environment:` block). Runbook covers: 3-step add-a-var workflow, restart-vs-up-d distinction, 4 gotcha categories from real incidents (trailing comments, password spaces, BOM, split-brain `.env.example`), full env-var inventory, 5-step debug checklist.

**Implications:**
- Future env-var additions reference the runbook before editing files
- The `api/.env.example` vs root `.env.example` split-brain remains (cosmetic followup) — runbook flags it
- `api/.env.example` header doesn't currently redirect to root; deferred to dev-devops desktop session (target-project edit, Lead can't do)
- Antivirus-quarantine incident caught during the same window — 78 `context/` files deleted from working tree (recovered via `git restore context/`); cause suspected Bitdefender during `docker compose up -d --build api` for itsdangerous rebuild; investigation followup filed

---

## 2026-05-22 — Mobile push provider pick: ntfy — Kanban #1192
**Scope:** shared / notification

**Decision:** ntfy picked over Pushover / APNs / Pushy. Code work blocked on operator infra (topic name + iOS/Android app install + optional self-host).

**Rationale:**
- **ntfy**: free; HTTP-based (curl-able); self-hostable via Docker (fits Tailscale model); no Apple Developer account; no custom app build. iOS/Android apps exist (free). Cons: 3rd-party app on phone (not OS-native), iOS app UX is functional-not-polished.
- **Pushover** (alternative): $5 one-time per platform; polished native UX; reliable delivery; closed-source. Acceptable upgrade if ntfy UX is rough.
- **APNs**: requires Apple Developer account ($99/yr) + custom iOS app build + push entitlement. Major operator-cost; not justified for personal use.
- **Pushy**: $19/mo + still requires own app; commercial managed proxy. Wrong scale for personal-niche v1.

**Implementation pattern (when infra ready):** `notify_ntfy.py` at `api/src/services/` mirrors `notify_telegram.py` / `notify_web_push.py` / `notify_email.py` (the latter just landed via #1217). EmailSender-style interface extended to push channel. Provider swap = 1 file.

**Implications:**
- #1218 (push digest) gates on #1192 infra setup
- Tailscale half of #1192 is operator-side: install Tailscale on agent-teams host + phone; secures phone-to-API direct access. Code-side just exposes the existing API endpoints; no Tailscale-specific code needed.
- Operator-action checklist filed inline in #1192 status_change_reason (4 steps)

---

## 2026-05-22 — Cron scheduling: Path A pick + 5 standard schedules + quiet hours parking — Kanban #1283
**Scope:** shared / scheduling

**Decision:** Path A (harness-side `mcp__scheduled-tasks__`) picked for v1. agent-teams DB seeds 5 task-templates (`is_template=true`, `recurrence_rule` set, `recurrence_timezone=Asia/Bangkok`) as the declarative source-of-truth for what should fire when. Harness `mcp__scheduled-tasks__` entries become the execution engine (created Day-0 via the same Lead, or deferred to operator-driven `create_scheduled_task` calls).

**Path A rationale:** zero infra build (tool exists out-of-box). Acknowledged con: schedules only fire while Claude Code is running. Mitigated by (a) v1 operator runs CC on a host that stays up most active hours; (b) promote to Path B (#852 langgraph worker as cron executor) when worker activates — DB templates already exist, only the executor swaps.

**5 standard schedules** (cron expressions in Asia/Bangkok local time):
- `0 8 * * *` — 08:00 daily — secretary email triage (Pattern 1)
- `0 12 * * *` — 12:00 daily — news / RSS digest (Pattern 6)
- `0 18 * * *` — 18:00 daily — Lead synthesizes day's digest (Pattern 4)
- `0 23 * * *` — 23:00 daily — project-auditor sweep (per #1213)
- `0 10 * * 0` — Sun 10:00 weekly — cross-channel rollup (Pattern 7)

**Quiet hours JSONB placement:** parked under `projects.health_thresholds.quiet_hours` (existing JSONB column; semantic mismatch acknowledged — health_thresholds is for health-check alerting, but it's the only extant JSONB on `projects` that doesn't already have a different purpose). v1 shape:
```json
{
  "quiet_hours": {
    "start": "22:00",
    "end": "07:00",
    "tz": "Asia/Bangkok",
    "emergency_override_allowed": true
  }
}
```
Followup: promote to dedicated `projects.scheduling_config` JSONB column via migration when scheduling concerns expand beyond quiet_hours (per-schedule overrides, holiday calendar, blackout windows). Filed as followup task on #1283 closure.

**Override mechanism (AC4) — uses existing endpoints, no new build:**
- Disable a schedule template: `PATCH /api/tasks/<template_id>` with `{"is_template": false}`
- Adjust cron: `PATCH /api/tasks/<template_id>` with `{"recurrence_rule": "<new cron>"}`
- One-off trigger: `PATCH /api/tasks/<template_id>` with `{"next_fire_at": "<ISO timestamp>"}`, OR create a clone task with `parent_task_id=<template_id>`
- Harness MCP side: `mcp__scheduled-tasks__update_scheduled_task(taskId=..., cronExpression=..., enabled=...)`

**Implications:**
- 5 task-templates seeded as Kanban task ids on POST (rendered in #1283 close-out)
- `projects.health_thresholds` PATCHed on project_id=1 with `quiet_hours` JSON shape above
- Smoke test (AC5) deferred to a followup — register +2min schedule + verify fire + quiet-hours skip
- When #852 langgraph worker activates, the worker becomes cron executor reading these templates; harness MCP entries can be retired then
- Path A→B migration cost is low (DB templates unchanged; new executor reads same `recurrence_rule`/`recurrence_timezone` columns)

---

## 2026-05-20 — Compact + reward-hacking pass on dev-*.md agents — Kanban #1293 PILOT GATE
**Scope:** agent prompts / methodology
**Status:** PILOT LANDED — operator review required BEFORE batch (per AC11). Files in place: `.claude/agents/_dev-shared.md` (90L NEW) + `.claude/agents/dev-backend.md` (102L, was 98L → +4 net; +reward-hacking-self-check + boundary clause + migration-timing pointer, –raw-SQL boilerplate to shared). Standards draft staged at `_scratch/standards-draft-reward-hacking-patterns.md` (187L, 9 patterns A-I) for human promotion to `context/standards/general/reward-hacking-patterns.md`.

**Decision (pending operator confirm at pilot gate):**

1. **Shared-include pattern** (AC0): `.claude/agents/_dev-shared.md` carries the universal boilerplate every `dev-*` role inherits — standards/shared write prohibitions, raw-SQL DML 1-line pointer (no more 40-line duplication across 4 files), permission model, reply skeleton, Compact step skeleton, halt-and-ask, file-path discipline, Karpathy lane. Role files reference it via the line `Reads _dev-shared.md for the common substrate (Lead injects at spawn time).` near the top.

2. **Model-tier table** (AC1) — explicit `model:` on every `dev-*.md` post-batch:
   - `dev-sr-backend`, `dev-sr-frontend` → **opus** (design judgment; new surfaces, architecture)
   - `dev-backend`, `dev-frontend`, `dev-devops`, `dev-reviewer`, `dev-security-reviewer`, `dev-spec-reviewer`, `dev-tester` → **sonnet** (routine implementation; modifications + reviews)
   - `dev-documentor` → **haiku** (existing — read-heavy, write-light)
   - `dev-analyst` → **sonnet** (per existing baseline — spec ambiguity expansion is structured, not design-heavy)

3. **Test-writing boundary** (AC7) — default proposal adopted as-is by pilot:
   > dev-backend writes 1-3 first-pass contract-smoke tests (happy path + status code + response shape). dev-tester writes the rigorous suite (edge / regression / e2e). Same clause copied to dev-sr-backend at batch.

4. **Reward-hacking framework** (AC3, AC4, AC5, AC6, AC9):
   - Producer self-check before DONE — 6-item checklist in `dev-backend` (will mirror to `dev-frontend` + 2 sr-* at batch).
   - Reviewer audit — pattern-grep checklist into `dev-reviewer.md` multi-pass review at batch.
   - Spec-reviewer "hackable AC" check into `dev-spec-reviewer.md` audit category (6) at batch.
   - Tester anti-hackable-test sub-clause into `dev-tester.md` spurious-PASS at batch.
   - Standards doc draft staged for operator promotion.

5. **Boundary preservation** (AC8): pilot diff is reductive on boilerplate + additive on reward-hacking + boundary. Net LOC delta this pilot is +4 (98 → 102) for dev-backend because additions partly offset extractions; the ≥15% net reduction lands across the batch when the other 10 files extract their share to `_dev-shared.md`. Pre-batch snapshot of all 11 files captured at `_scratch/before-rewrite/`.

**Reasoning:** Composer 2.5 launch coverage 2026-05-18 surfaced reward-hacking as an observed scaled risk (Cursor blog cited verbatim in the standards draft). Audit found zero mentions of reward-hacking / cheat / shortcut across all 11 dev-*.md prompts. Bundle the reward-hacking addition with the boilerplate compaction so quality-gain and maintenance-debt-reduction happen in one cohesive pass rather than fragmenting into 2 tasks that risk dropping rules between waves.

**Operator decision points at pilot gate:**

- (a) Is the `_dev-shared.md` extraction shape correct? Anything universal that should land there but didn't? Anything role-specific that landed there but shouldn't?
- (b) Does `dev-backend.md` at 102L (4 over target band 75-95) read clean? Trim the migration-vs-ORM note inline OR collapse the boundary clause's second paragraph? OR accept as-is.
- (c) Boundary clause (point 3): default proposal lands as-is — confirm or pick alternative ("dev-backend writes ALL backend tests, dev-tester only edge/e2e" or "dev-backend writes ZERO tests, dev-tester writes everything").
- (d) Reward-hacking standards draft (187L, 9 patterns) — is the depth right for `context/standards/general/`? Trim to A-G+H+I = 9, or focus on the 5 most-likely?

**Implications:** approved batch then proceeds to the other 10 dev-*.md files; dev-reviewer runs the AC9 side-by-side diff audit (every original hard rule + incident ref + workflow step appears in new file OR in `_dev-shared.md`); live smoke gate AC12 spawns post-batch dev-backend with a canned task to verify shared-include is actually read, self-check produces visible output, reply skeleton matches shared, boundary behavior matches choice (c). If operator rejects pilot shape, revise + redo pilot; do NOT batch.

---

