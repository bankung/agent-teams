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

## 2026-05-14 — Phase 4 LangGraph headless engine (Kanban #849 chain — #851 + #850)
**Scope:** devops / backend / shared
**Decision (#851 — Docker scaffold):** New `langgraph` Docker service (built locally via `langgraph/Dockerfile`; no upstream prebuilt image) on host port `8465` → container `8000`. Pinned: `langgraph==1.2.0`, `langgraph-checkpoint-postgres==3.1.0`, `langgraph-cli==0.4.26`, `langchain-anthropic==1.4.3`, `langchain-openai==1.2.1`, `fastapi==0.136.1`, `uvicorn==0.46.0`. New envvars in `.env.example`: `LANGGRAPH_PORT`, `LANGGRAPH_LLM_PROVIDER` (anthropic|openai, default anthropic), `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`), `OPENAI_MODEL` (default `gpt-4o`).

**Decision (#850 — graph definition):** Hand-rolled `StateGraph` + `conditional_edges` (skipping the prebuilt `langgraph-supervisor` package per LangChain's current guidance). `AsyncPostgresSaver` checkpointing in a separate `langgraph` schema on the existing `agent_teams` DB. Supervisor (Lead) node routes by `assigned_role` integer (1=frontend, 2=backend, 3=devops, 4=tester, 5=reviewer; mirrors `api/src/constants.py::TaskRole`). #850 ships ONE real specialist (`backend_specialist`) calling `make_chat_model().invoke(...)`; frontend/devops/tester/reviewer/general are stubs returning canned `final_result` (real implementations land via #853 + later).

**Locked rules:**
- **Schema bootstrap = Option B** — app-startup `CREATE SCHEMA IF NOT EXISTS langgraph;` runs in `graph.py` lifespan BEFORE `await saver.setup()`. Portable across existing DB volumes (no initdb script, no `docker compose down -v` required). Documented in `langgraph/README.md`.
- **`DATABASE_URI` is normalized at app layer.** Compose ships `?options=-c%20search_path=langgraph`; psycopg 3.3's URI parser rejects the literal inner `=`, so `graph._normalize_pg_uri()` re-encodes it as `%3D`. Keeps docker-compose.yml unchanged and tolerates any URI source.
- **LLM fail-fast in lifespan** — container refuses to start (lifespan raises `RuntimeError`) if the configured provider's API key is unset OR if `model.invoke("ping")` fails. Better than a healthy container that crashes on first `/invoke`. Documented in spawn brief + reproduced in lifespan log line.
- **`AsyncPostgresSaver` tables live in `langgraph.*` schema, managed by `saver.setup()` — NOT by Alembic.** Do not mirror in `db-schema.md`'s `public.*` table list; do not write Alembic migrations against them.
- **`thread_id = "task-{task_id}"`** is the checkpoint key convention. #852's poll loop must use the same format to resume paused tasks.
- **`/invoke` contract (locked for #852):** `POST {task_id, brief, assigned_role}` → `200 {task_id, assigned_role, final_result, halt_reason, messages[]}`. `halt_reason != null` = pause signal (route to user review); `final_result` = artifact to write back to the Kanban task.
- **`make_chat_model() -> BaseChatModel` signature stable** — #853 replaces the `llm.py` shim from #850 without changing the public contract; specialist nodes don't import `ChatAnthropic`/`ChatOpenAI` directly.

**Reasoning:** Phase 4 goal is provider-agnostic headless execution. Hand-rolled supervisor gives full control over routing + state; prebuilt is being deprecated. `AsyncPostgresSaver` is the only production-grade saver (`MemorySaver` evaporates on restart). Fail-fast on missing API key avoids the "healthy container, sudden first-call death" anti-pattern that misleads ops.

**Implications:** #853 will replace `langgraph/llm.py` (drop-in upgrade of the shim — fuller error messages, model-name validation, unit tests). #852 will add a poll loop that calls `GET /api/tasks/next-autorun` then `POST http://langgraph:8000/invoke` then PATCHes the task; uses the locked `/invoke` contract above. AGENTS.md (#848) is the Codex CLI counterpart and may run independently. Phase 4 umbrella (#849) closes when all four children + an end-to-end smoke land.

## 2026-05-14 — TaskDetail Run button + within-lane snap-back rationale (Kanban #860 + #889 prep)
**Scope:** frontend / shared
**Decision (Run button — #860):** TaskDetail drawer renders a "Run" primary button when `process_status === TODO && task_kind === 'ai' && run_mode === 'manual'`. Click flips `run_mode` from `'manual'` to `'auto_pickup'` via `patchTask`. The FE does NOT directly PATCH `process_status`; the consuming autorun loop (`GET /api/tasks/next-autorun`) stamps `process_status → 2` on pickup.

**Reasoning — `auto_pickup` not `auto_headless`:** both satisfy the next-autorun selector (`api/src/routers/tasks.py:214-231` filter `run_mode IN ('auto_pickup','auto_headless')`). `auto_headless` triggers the consent gate at `services/run_mode.py:assert_consent_for_run_mode` — a project without `auto_run_consent_at` would 400. `auto_pickup` is universally safe (consent check returns immediately for non-`auto_headless`). Run button works uniformly across consented + non-consented projects.

**Reasoning — `run_mode === 'manual'` visibility guard (beyond AC1–3):** hide the button on already-queued tasks. Re-flipping `run_mode` on auto-* rows would be a no-op write bumping `updated_at` for no behavioral change. The `RunModeBadge` in the drawer header already conveys queued state.

**Decision (Board within-lane no-optimistic — #772 supplement):** within-lane drag-reorder in the TODO lane does NOT mutate local state optimistically. dnd-kit's transform shows the new position during the drag; on drop the transform clears and the card snaps back to the prior render order until the server PATCH response merges in. On 422 → no merge → cards stay in pre-drag order ("snap-back"). On 200 → merged task carries the new `sort_order` and `sortLaneTasks` reorders on next render. This is **distinct** from the cross-lane optimistic-update pattern locked at 2026-05-11 (#709) — within-lane is reorder-only (no `process_status` flip) and dnd-kit visual transform already provides the "where will it land" feedback; a second optimistic state mutation would race the server response.

**Forward-looking (Run button):** the actual consuming autorun loop is Phase 4 LangGraph (#849/#850–853) territory and may not be running yet. The button captures intent today; when the loop lands, zero FE changes needed.

**Implications:** any future FE that needs to "start a task" follows the Run button pattern — write `run_mode`, never `process_status`. Direct `process_status` PATCH skips the autorun queue and all agent-loop side effects (AC stamping, `subagent_models` accumulation, smoke probes). Cross-table validators (`task_kind='human'` × non-manual → 400 at `services/task_kind.py`) protect against obvious misuse; the FE's `task_kind === 'ai'` gate keeps requests below that line.

## 2026-05-13 — `tasks.subagent_models` JSONB audit log — Kanban #887
**Scope:** backend / shared / CLAUDE.md (Lead protocol)
**Decision:** Added `tasks.subagent_models JSONB NOT NULL DEFAULT '[]'` — an append-only audit log of subagent spawns per task. Each element: `{agent: str, model: "opus"|"sonnet"|"haiku", at: ISO-8601 UTC datetime}`. PATCH semantics are full-replace (Lead accumulates the list then sends the whole array on each state-transition PATCH; the API does not merge). Field validated by `SubagentModelEntry` Pydantic model with `extra="forbid"`.

JSONB list chosen over a separate `task_spawns` table because: cohort queries we actually need (feature→bug-followup rate by model, per-task spawn count) are easy with JSONB and `parent_task_id`; joins add friction for what will be ad-hoc SQL for the first ~50 tasks of data; the separate-table approach is deferred to when we hit a query the JSONB shape can't answer.

Migration: `0024_tasks_subagent_models` (revision `0024`, down_revision `0023_tasks_task_kind_default_ai`). Applied 2026-05-13. ADD COLUMN is a metadata-only PG 16 operation — no heap rewrite, no downtime. Existing 880+ rows default to `[]`; NULL never appears (verified by count(*) = 0 post-migration). Downgrade drops the column (safe, data is only `[]` before first Lead writes).

Lead protocol (also in CLAUDE.md "Subagent model logging (universal)"): bundle `subagent_models` into every state-transition PATCH alongside `process_status`, `acceptance_criteria`, `completed_at`. One PATCH per transition — never per-spawn. Record every Agent spawn that produces real work output (dev-backend, dev-tester, dev-reviewer, dev-devops, dev-frontend, dev-documentor, dev-researcher, general, etc.). Do NOT record Lead's own Read/Grep/Glob exploration or Skill invocations.

**Timing rationale:** deployed before #885 (tester/reviewer→Sonnet) and #886 (dev-sr-* tier) so the field captures the all-Opus baseline; the before/after comparison is intact in the cohort data.

**Risk:** if the model Literal set (`"opus"|"sonnet"|"haiku"`) ever expands, existing rows with stored values will still pass on GET (Pydantic re-validates on the way out). If a stored model value is removed from the Literal without DB backfill, those GET calls will 500. Document future model-set changes here and backfill `subagent_models` entries if needed.

**Implications:** first AC-10 test (done-flip with `subagent_models` populated) was the #887 close PATCH itself. Reporting endpoint and stats aggregation are explicitly deferred — ad-hoc SQL is sufficient for the first ~50 tasks of data.

## 2026-05-13 — `dev-sr-*` Opus tier + routing rule — Kanban #886
**Scope:** orchestration / team methodology
**Decision:** Added two senior-tier specialist agents — `dev-sr-frontend` and `dev-sr-backend` — that explicitly run on Opus (no `model:` frontmatter line → Opus default) and are reserved for design-heavy / new-surface work. The `dev-frontend` and `dev-backend` agents remain at their current model (Opus by default; Sonnet downgrade is a separate follow-up task once the sr- tier is exercised on 3-5 real feature tasks).

Routing rule (default, overridable by Lead per task):

| task_type | New surface (new endpoint/page/migration)? | Default |
|---|---|---|
| feature | YES | dev-sr-backend / dev-sr-frontend (Opus) |
| feature | NO | dev-backend / dev-frontend |
| refactor / chore / docs | — | dev-backend / dev-frontend |
| bug | — | dev-backend / dev-frontend; sr- only if architectural mismatch |

Both sr- agents carry a **de-escalation protocol**: if mid-task they discover scope is narrower than expected (no new surface after all), they STOP and report — Lead respawns dev-* instead. This prevents paying Opus cost for what turns out to be incremental work.

Files created: `.claude/agents/dev-sr-frontend.md`, `.claude/agents/dev-sr-backend.md`.
Files updated: `.claude/teams/dev.md` (roster + tier routing rule subsection), `.claude/teams/general.md` (roster addition).

**Rollback if rule misfires repeatedly:** if Lead observes that sr- spawns are frequently de-escalating (>50% of spawns) or that dev-* spawns are being escalated post-hoc to sr- on bugs, revisit the rule's "new surface" discriminator — the signal is too coarse. Open a follow-up task.

**Deferred:** `dev-frontend` / `dev-backend` Sonnet downgrade — open a follow-up once sr- tier has been exercised on 3-5 real feature tasks. `tasks.subagent_models` JSONB field (#887) captures the before/after baseline.

## 2026-05-13 — Switch `dev-tester` + `dev-reviewer` to Sonnet model — Kanban #885
**Scope:** orchestration / team methodology
**Decision:** Added `model: sonnet` to `dev-tester.md` and `dev-reviewer.md` frontmatter (after the `description:` line, mirroring `dev-analyst.md:4`). These agents now default to Sonnet 4.6 instead of Opus.

Rationale: AC discipline (Kanban #797) gates correctness for both roles — test assertions are machine-checkable; reviewer checks are diff-bounded. Design judgment is NOT the load-bearing skill for these two roles; that lives in dev-backend / dev-frontend / the new dev-sr-* tier. Sonnet 4.6 is adequate for correctness-bounded work at significantly lower cost. Mirrors the existing pattern: `dev-analyst.md` and `dev-spec-reviewer.md` already use Sonnet; `dev-documentor.md` and `dev-researcher.md` use Haiku.

Risks and mitigations:
- **Sonnet may miss subtle design-flaw issues during review.** Mitigation A: `dev-spec-reviewer` (already Sonnet) catches pre-spawn spec ambiguity. Mitigation B: Lead's verify-don't-trust pass catches gross failures. Mitigation C: rollback criterion below.
- **Tester may miss edge cases that Opus would catch.** Mitigation: Lead monitors rework rate.

**Rollback criterion:** if over the next ~10 tester- or reviewer-touched tasks the rework rate (bug follow-up filed within 3 days of DONE, OR halt_reason='reviewer caught later') rises above the current baseline by a noticeable margin (Lead judgment until measurement infra lands), revert one or both to Opus by removing the `model:` line.

**Note:** changes activate only after Claude Code session restart (new agent files loaded at session start — per memory `agents_load_at_start`).

## 2026-05-13 — Dashboard becomes the default landing; `NEXT_PUBLIC_PROJECT_NAME` knob retired — Kanban #869
**Scope:** frontend
**Decision:** `web/app/page.tsx` now `redirect("/dashboard")` unconditionally. The prior `NEXT_PUBLIC_PROJECT_NAME` env-var that picked a single-project `/p/<name>` landing is removed (not just unused — the read site is gone). Dashboard reshaped: `<section data-aggregate-summary>` (5-lane big-number row + stat strip) → `<section data-cost-summary>` (#871 cost/token rollup) → `<section data-project-grid>` (compact per-project cards), in DOM order locked by Tier-1 smoke byte-offset assertion.
**Reasoning:** task description explicitly framed the dashboard as "the default landing" — operators pick a project via dashboard cards, not via a deploy-time env var. Multi-project session-bound model (#694 Phase 2) made the single-project knob semantically wrong: a deployment may run 3 sessions against 3 projects in parallel, none of which is "the" default. Reviewer flagged the silent knob retirement as WARN-1 (#869); user-intent confirmed in spawn brief.
**Implications:** any deploy that previously customized landing via `NEXT_PUBLIC_PROJECT_NAME` now lands on `/dashboard` regardless of env. If a future single-project mode is needed (e.g., embedded preview), restore via a route group or a feature flag — not the same env var, which is forever-removed. Query-string preservation on `/?foo=bar` is NOT guaranteed by the meta-refresh redirect (NIT, no production consumer). Layout convention: cost section uses amber tints (warning-class hue) distinct from the lifecycle-aggregate's neutral palette; per-card cost strip is one compact line; `session_run_count === 0` → muted "— no usage" (never `$0.00 · 0 tokens`). #871 BE locked `cost_usage.total_cost_usd` as a JSON STRING (Pydantic Decimal default) — FE always parses via `Number(...)`, never `+x`.

## 2026-05-13 — `.claude/settings.json` allowlist tightened — Kanban #867
**Scope:** devops
**Decision:** Removed 22 entries from `permissions.allow` — 10 broad destructive Kanban API patterns (`curl --silent -X PATCH/DELETE http://localhost:8456...`) and 12 one-off smoke-test entries with hard-coded ids (`/projects/576`, `/tasks/866`, etc.). Allow-list 110 → 88 entries. Reads (GET) stay broadly allowed; mutations are prompted via `defaultMode: "default"`. POST patterns left untouched this slice (deferred — see follow-up Kanban for symmetric tightening if the user wants it).
**Reasoning:** broad PATCH/DELETE auto-allow contradicted the permission model ("Bash should prompt and destructive operations should stay gated"). One-off smoke entries are dead by construction — the rows they reference are soft-deleted. Memory note `feedback_permission_prompts.md` already captured the user's prefer-prompt-on-mutate posture; this commit lines the file up with the posture.
**Implications:** future Lead/subagent runs that PATCH or DELETE Kanban rows will see a permission prompt (one prompt per command pattern, then cached for the session). Lifecycle status flips (`process_status=2/5`) will prompt on each session start until the user re-approves. Acceptable friction per the user-preferred posture. Standards-tier rule ("allow-list scope: reads broad, writes prompted") proposed but DEFERRED to 2nd-strike codification per dogfood-pollution discipline.

## 2026-05-12 — Sparse-float sort_order gap-collapse fix — Kanban #819
**Scope:** backend (api/src/routers/tasks.py)
**Decision:** Option 2 — min-gap threshold + full lane re-densification. `_SORT_ORDER_MIN_GAP = 1e-9`: when the computed `new_sort_order` is within 1e-9 of either anchor's sort_order, `_redensify_lane` overwrites ALL sort_orders in the lane with 1.0, 2.0, … (in sort_order ASC NULLS LAST, created_at ASC order) and the recompute runs against the freshly-spaced values. Both operations are atomic within the existing transaction.
**Reasoning:** Option 1 (detect-only, abort with 422) is user-hostile — the user can't resolve a float-precision exhaustion from the UI. Option 3 (LexoRank integer migration) is correct long-term but costly for a failure mode that requires >52 same-interval reorders (~7-8 minutes of continuous identical drags on a 1-row lane). Option 2 is transparent, O(lane-size) which is <30 rows in practice, and eliminates the gap permanently for the lane.
**Implications:** `_redensify_lane` resets all sort_orders to dense integers after a collapse — any unsaved client-side sort state is consistent again. No schema change.

## 2026-05-12 — Live-DB row-count guard widened to all public tables — Kanban #815
**Scope:** qa (api/tests/conftest.py)
**Decision:** `_live_db_row_count_invariant` fixture now snapshots all user tables in the `public` schema via `SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'`, then runs `SELECT count(*)` per table. Changed from a hardcoded `projects`+`tasks`-only check (option 2: per-table exact counts, not `pg_stat_user_tables` estimates).
**Reasoning:** The original guard missed writes to `sessions`, `session_compacts`, `session_runs`, `tasks_history`, and any future tables. The per-table exact count approach (option 2) was chosen over `pg_stat_user_tables.n_live_tup` (option 1) for higher fidelity — autovacuum can shift pg_stat estimates between before/after snapshots without any real write. `alembic_version` excluded because migration head advances legitimately during dev. Error message now names the specific table(s) that drifted.
**Implications:** Guard catches any table write in the public schema. New tables added by future migrations are automatically included (no conftest changes needed).

## 2026-05-12 — Headless Auto-Run schema: `interaction_kind` + `question_payload` + `resume_context` — Kanban #830
**Scope:** backend (migration + ORM + Pydantic + router); foundation for #832 (question API) + #833 (queue endpoint) + #834 (FE drawer).
**Decision:**
Migration `0019_tasks_interaction_kind` adds 3 columns to `tasks`:
- `interaction_kind VARCHAR(16) NOT NULL DEFAULT 'work'` CHECK IN ('work','question','decision') — discriminates agent-executed tasks from user-interaction gates.
- `question_payload JSONB NULL` — validated by `QuestionPayload` / `AnswerHistoryEntry` Pydantic models at API boundary. Full-replace PATCH semantics (same as `acceptance_criteria`). Append-only answer_history logic deferred to #832.
- `resume_context JSONB NULL` — free-form partial-work state for checkpoint re-spawn. No shape constraint.

`interaction_kind IN ('question','decision')` requires `question_payload` — enforced by Pydantic `model_validator` on both `TaskCreate` and `TaskUpdate` (PATCH-safe: only fires when `interaction_kind` is in `model_fields_set` without `question_payload`). `InteractionKindLiteral` lockstep guard at schema module bottom (mirror of `TaskTypeLiteral` pattern, Kanban #803).

**Reasoning:** Separate column (`interaction_kind`) keeps task classification (`task_type`: bug/feature/chore/docs/refactor) orthogonal to interaction pattern. Full-replace PATCH keeps the API consistent with `acceptance_criteria` — append semantics land in #832 as a distinct concern. `resume_context` is free-form JSONB so Lead can store any partial-work snapshot without a rigid schema.

**Implications:**
- **#831 (DevOps):** apply migration 0019 (`alembic upgrade head`, 0018 → 0019). All existing rows backfill cleanly via DB DEFAULT / NULL.
- **#832 (BE question API):** implements append-only answer_history + auto-unblock trigger on question task DONE.
- **#833 (BE queue endpoint):** `GET /api/tasks/next-autorun` reads `interaction_kind` + `halt_reason` to determine what the loop should do next.
- **#834 (FE):** `TaskRead.interaction_kind` always present; drawer branches on `interaction_kind IN ('question','decision')`.
- **441 tests pass** (0 regressions) after this slice.

## 2026-05-12 — `tasks.sort_order` + reorder endpoint + blocker-order constraint — Kanban #772 (backend slice)
**Scope:** api (migration + ORM + Pydantic + router + tests); reuses the 422 status-code policy from #771.
**Decision:**
Migration `0018_tasks_sort_order` adds `sort_order DOUBLE PRECISION NULL` to `tasks` (no CHECK, no FK, no index — measured-first index policy; sparse-float ordering is constraint-free at the DB layer; lane queries already filter by `process_status` + `status`, both indexed). NULL = "use created_at fallback for ordering" within the lane (`ORDER BY sort_order ASC NULLS LAST, created_at ASC`).

New endpoint `POST /api/tasks/{task_id}/reorder` with body `TaskReorder = {before_id?: int, after_id?: int}` (at least one anchor required; same-id rejected; both Pydantic-422 with locked detail strings). Server-side compute:
- Both anchors → `(after.sort_order + before.sort_order) / 2`.
- Single anchor → average against the nearest existing neighbor in the same lane; fall back to `anchor ± 1.0` when no neighbor exists.
- NULL anchor sort_order triggers lane materialization (`_materialize_null_sort_orders_in_lane`) — floor floats 1.0, 2.0, ... assigned in `NULLS LAST, created_at ASC` order; rolled back atomically with the rest of the transaction if validator subsequently fails.

`PATCH /api/tasks/{id}` also accepts `sort_order: float | null` directly (escape hatch for known-value sets / smoke / admin paths). Same constraint validator fires on the resolved-final value.

Cross-row blocker-order constraint (`_enforce_blocker_order_constraint`): when target.blocked_by walks transitively (depth ≤ 10) to B in same lane (process_status=TODO) and both sort_orders non-null, `target.sort_order >= B.sort_order` must hold. Violation → 422 `"task #<T> cannot be ordered before its blocker #<B>"`. Constraint fires on: POST /reorder, PATCH sort_order, PATCH blocked_by (resolved-final).

24 pytest cases in `api/tests/test_tasks_sort_order.py`; 440 passed in 94.45s (was 416 + 24 new = 440; 0 regressions). Migration applied 2026-05-12 (live row counts unchanged 54/161 → 54/161; live API restored from 500 → 200). Tier-1 smoke 8/8 GREEN (4 POSITIVE + 4 NEGATIVE; all detail strings byte-exact in the wire response).

**Reasoning:**

- **Sparse-float lexicographic ordering** lets users insert-between without renumbering. Classic UI-ordering trick. DB-constraint-free; collision risk after ~52 same-position reorders is documented + filed as #819 (deferred follow-up — real users won't hit it before a more involved re-densification design lands).
- **NULL = created_at fallback** keeps existing 161 tasks legacy-compatible without a backfill. The first reorder in any lane densifies NULL lane-mates atomically with the operation — gradual migration, no batch job.
- **Constraint walks the blocker chain transitively.** Sibling pattern to #771's cycle walk; depth=10 budget; same self-FK shape. The reorder-time constraint blocks "place blocked task above its blocker in the queue" — the auto-pickup loop (future #786) will trust that the sort_order respects blocking relationships.
- **WARN-3 chain-exactly-10 fix:** the `for...else` overflow pattern needs `range(1, N+2)` not `range(1, N+1)` so the sentinel iteration can detect `cursor is None` and break cleanly. Without the +2, a chain of exactly 10 blockers (or a cycle closing at hop 10) falsely raises "exceeds maximum depth". `_REORDER_BLOCKER_CHAIN_DEPTH` walker fixed in this slice; the analogous #771 cycle walk (line 724) has the same latent bug — filed as #820 follow-up.
- **WARN-2 fix:** `_opt_int_str` helper renders Optional[int] as JSON-conformant `null` (not Python's `str(None) == "None"`) in wire-contract detail strings. Same-lane mismatch detail string was the trigger; helper at module top is available for future cross-row validators.

**Implications:**

- **#772 FE slice (next spawn)** consumes `POST /api/tasks/{id}/reorder` via dnd-kit drag-drop in the "New tasks" lane. 422 toast + snap-back. AC #4 of #772 is in that slice.
- **Future auto-pickup loop (#786)** reads sort_order as the queue order; the blocker-order constraint guarantees it can dequeue safely (no blocked task above its blocker).
- **Three follow-ups filed this session:**
  - #819 (P1=LOW, backend): sparse-float collision re-densification — defer until the float-64 epsilon collision is observed in the wild.
  - #820 (P2=NORMAL, bug, backend): apply the `range(N+2)` fix to #771's cycle walk for symmetry.
  - WARN-4 was the same as #819 — single Kanban covers both.
- **Live API was down for ~10 min** between backend commit and devops apply (ORM had `sort_order` column but live DB didn't → 500 on any `/api/tasks*` call). Acceptable downtime in dev; flag for any future production-shaped deployment.

## 2026-05-12 — `tasks.blocked_by` schema + API + 422 status-code policy locked — Kanban #771 (backend slice)
**Scope:** api (migration + ORM + Pydantic + router + tests); cross-cuts the wire-contract policy via the new policy header in `routers/tasks.py`.
**Decision:** Two things, locked together this session:

1. **`tasks.blocked_by` lands.** Migration `0017_tasks_blocked_by` adds `blocked_by BIGINT NULL REFERENCES tasks(id) ON DELETE SET NULL` + CHECK `ck_tasks_blocked_by_not_self` + index `ix_tasks_blocked_by`. ORM model + Pydantic schemas (Create/Update/Read) + router validators (POST: existence + same-project; PATCH: same plus self-rejection + cycle walk up to depth=10) + reverse-lookup endpoint `GET /api/tasks/{task_id}/blocks` + 14 pytest cases. 416 passed in 87.62s (was 402; +14, 0 regressions). Migration applied to live DB (`alembic current` shows `0017_tasks_blocked_by (head)`, row counts unchanged 54/158 → 54/158 — metadata-only schema-additive). Tier-1 smoke GREEN on 6 probes (POSITIVE×3 + NEGATIVE×3; cross-project NEGATIVE deferred to pytest per the smoke-methodology option-(a) pattern).

2. **Status-code policy: 422 for cross-row business-rule rejections (NEW writes; legacy 400s remain).** Locked by user 2026-05-12 in response to reviewer's WARN-1. New validators in `routers/tasks.py` (cross-project FK target / soft-deleted target / self-reference / cycle / depth-exceeded) return **422 (Unprocessable Entity)** per RFC 4918 — the input is wire-valid Pydantic-shape; the violation is semantic. The pre-existing `parent_task_id` validators still return **400** (locked 2026-05-08, pre-policy) and are intentionally NOT migrated this slice — separate cleanup task if/when it's worth the test churn. The going-forward rule, codified inline at `api/src/routers/tasks.py` policy header (lines ~55-60): **new same-row cross-validation errors at the router boundary return 422; only request-shape/parse errors return 400.**

**Reasoning:**

- **SET NULL not CASCADE** on `blocked_by` mirrors `spawned_from_task_id` (recurrence lineage). Hard-deleting a blocker must NOT cascade-delete the blocked task — the blocked task should survive with `blocked_by=NULL`. Contrast with `parent_task_id` (CASCADE) where subtasks live-and-die with their umbrella parent. Three self-FKs now exist on `tasks`; each makes a different ON DELETE choice based on semantics, not style.

- **App-layer same-project enforcement + app-layer cycle walk** keep the trigger surface small (one audit trigger handles everything via `to_jsonb(OLD)`) and let the rejection logic live where the detail strings are (router). Same precedent as `parent_task_id` (#238, 2026-05-08). Depth=10 is a defensive bound — real chains are 1-3 deep; the for/else exhaustion branch raises 422 with `"blocked_by chain exceeds maximum depth of 10"`.

- **PATCH-able post-create** (unlike `parent_task_id` / `spawned_from_task_id` which reject re-parenting). The entire point of #771 is letting users set/clear/change the blocker as work progresses — re-blocking IS the feature.

- **422 over 400** for the new validators: 422 is semantically correct (RFC 4918 — well-formed input that violates business rules) and the pytest already pinned 422 in 14 tests. Flipping to 400 would require migrating 4 router sites + 14 test assertions for stylistic consistency with the legacy `parent_task_id` validators — a wire churn that buys nothing. Reviewer leaned 422; user agreed. The legacy 400s are flagged as legacy in the policy header so future readers don't extrapolate them as the going-forward rule.

**Implications:**

- **FE slice (next spawn)** consumes `blocked_by` via `TaskRead` and the new reverse-lookup endpoint. Locks AC #4 on #771: TaskDetail picker (within-project search, excludes self + ancestors/descendants) + TaskCard ⛔ chip when set.
- **#772 (sortable lane + blocker ordering constraint)** is now unblocked. The constraint "task may not be placed before its blocker in same-lane order" reads `blocked_by` from the row; the reject-422-on-violation matches the new 422 policy.
- **Future 422-vs-400 cleanup task** (optional): migrate `parent_task_id` validators from 400 to 422 for repo-wide consistency. Costs ~10 line edits in router + ~5 test flips. NOT in the current queue; file when the legacy 400s become a confusion.
- **History capture is free.** The existing `tasks_audit_trg` (`to_jsonb(OLD)`) auto-captures the new column — no trigger change. End-to-end verified by `test_blocked_by_history_captured_in_tasks_history`.
- **Reviewer NIT-1/2/3 + WARN-1** all addressed in the cleanup pass (commit follows this decision entry).

## 2026-05-12 — Test-DB isolation regression guard added — Kanban #809
**Scope:** api / tests (no `src/` change, no migration, no deps)
**Decision:** Added two complementary regression guards on top of the 2026-05-09 separate-test-DB isolation. The underlying isolation pattern (separate `agent_teams_test` DB built fresh per session, env-override at conftest.py:32-39 before any `src.*` import) remains unchanged.
1. **Cheap import-time canary** — `api/tests/test_db_isolation.py::test_engine_bound_to_test_database` asserts `src.db.engine.url.database == "agent_teams_test"` (parsed dbname attribute, not URL substring). Failure surfaces one explicit pytest line within milliseconds if the conftest rewrite ever breaks.
2. **Session-scope row-count invariant** — `api/tests/conftest.py::_live_db_row_count_invariant` (autouse, defined above `_setup_test_database`). Opens a SEPARATE async engine pointed at the LIVE `agent_teams` DB (re-derived from the same DSN pattern), snapshots `count(*) FROM projects` + `count(*) FROM tasks` before yielding, re-counts on teardown, raises with explicit deltas + diagnostic hint on mismatch. Skips silently if the live DB is unreachable (CI without it).

**Reasoning:** #809 was filed off stale info — investigation showed the 38 zombie `proj-patch-updated-at-*` rows in live `agent_teams.projects` are pre-isolation residue (all dated 2026-05-08 → 2026-05-09T12:03; the isolation landed at 12:03:30). No active leak. Real risk going forward is silent drift: someone moves the env override below a `from src import ...`, a test fixture opens its own engine via raw `create_async_engine(...)`, or a refactor breaks the rewrite. Two-layer defense — cheap canary catches engine-binding drift, broad invariant catches any write-path leak through any other route. Suite grew 401 → 402 with the new canary; live-DB row deltas are 0/0 across the full run (Lead independently re-ran post-tester).

**Implications:**
- Any future test fixture that opens its own engine MUST go through `from src.db import ...` (which IS bound to the test DB after the rewrite) OR explicitly point at `agent_teams_test`. The invariant will fail loudly otherwise.
- The 38 zombie `proj-patch-updated-at-*` rows in live `agent_teams.projects` remain — hard-delete reserved for manual `psql` (raw-SQL-DML is human-only per the strike-#1 incident lessons; subagents cannot execute the DELETE even via the hook-blocked path). Surfaced to user separately.
- Reviewer's NIT-1 (widen count snapshot to all user tables via `pg_stat_user_tables` — would catch leaks to `sessions`/`session_runs`/`session_compacts`/`tasks_history`) filed as a follow-up Kanban for later, not blocking #809 close.

## 2026-05-12 — Phase 3 umbrella #3 closed: Kanban UI scaffold ships
**Scope:** shared (frontend / backend / scope close-out)
**Decision:** Umbrella `#3 Phase 3 — kanban UI scaffold` flipped to `process_status=5`. All 16 child slices closed: V1 web/ scaffold (#405), V2 read-only board (#406), V3 ProjectSwitcher (#407); the recurrence T-series #706–710; the context-persistence CTX-series #716–719; the V2.1/V2.2 UX evolutions #748, #750, #760, #764. The four ACs are: V2 closed ✅ (#406), V3 closed ✅ (#407), browser smoke /p/agent-teams renders + ProjectSwitcher works post-#805 CORS fix ✅ (user-confirmed during 2026-05-12 session — the 42 leaked-row batch-DELETE on #805 was triggered precisely because the dropdown was being used; SSE push verified per #783), and this decisions.md entry.

**Reasoning:** Phase 3 was the bridge from API-only dogfood to interactive UI. The umbrella sat in_progress for 7 days (started 2026-05-08) while it absorbed two unplanned thrusts that materially raised the surface area beyond the original V1/V2/V3 scope: (a) the recurrence subsystem (#706–710 + #748/#750) re-shaped the task model with `task_kind`/`is_pending`/recurrence triggers — Board view had to learn pending semantics + drag-drop-gating; (b) the context-persistence CTX-series (#716–719) added session/heartbeat/compact infra that the UI exposes via a per-card log. The scope-creep was deliberate, not accidental — each addition was filed as a child and reviewed individually rather than being smuggled into the umbrella description.

**Implications:**
- Phase 3 is done; **Phase 4** is the next milestone shell. Candidate Phase 4 scope (NOT locked here — needs its own session): cross-project dashboard (#769), Board↔List toggle (#770), `tasks.blocked_by` FK + sortable lane (#771/#772), per-project enabled_roles (#7). The full-auto bet (#776/#781 umbrellas + MVP children #784–788) is a parallel track, not part of Phase 4 sequencing.
- The Phase 3 close does NOT change any wire contracts — it is purely a scope-bookkeeping decision. No follow-up code, migration, or doc changes are implied by this entry beyond the umbrella PATCH itself.
- The umbrella's children remain the authoritative narrative for each sub-decision (drag-drop gating semantics, ProjectSwitcher loadError reset pattern in #760, per-lane scrollbar in #764). This entry is the index, not the recap.

## 2026-05-12 — dnd-kit research promoted to `shared/docs/` as first perishable doc under new decay policy — Kanban #812
**Scope:** shared (frontend-prep docs)
**Decision:** Promoted `_scratch/research-dnd-kit-api.md` (produced by dev-researcher smoke gate #812) to [`context/projects/agent-teams/shared/docs/research-dnd-kit-api.md`](docs/research-dnd-kit-api.md) with frontmatter declaring `decay_class: perishable`, `decay_after: 2026-08-10`, `decay_trigger: "Kanban #772 closes AND dnd-kit version pinned in web/package.json"`. Also dropped `_scratch/doc-draft-task-type-feature.md` per the same evaluation (read-value too low to promote — content derivable from git log + code).

**Reasoning:** Research doc has cross-role read-value while #772 is pending (frontend specialist will embed it in spawn brief; tester may reference for E2E drag specs). Documentor's task_type summary had read-value only during the same commit; once landed, `git show 8b2e280` + the code itself answer everything the doc would. First concrete application of the decay-class policy (team decisions log dated same day).

**Implications:**
- Frontend specialist spawn brief for #772 should link the promoted doc (NOT inline the full text — let the brief stay concise; specialist reads on demand).
- When #772 closes, Lead distills any unique WHY into this `decisions.md` then prunes or `-superseded-`renames the dnd-kit doc per trigger #2 (code-authoritative).
- Also added Pydantic `Literal` lockstep guard pattern (Standards insight #1 from documentor) to [`context/standards/sqlalchemy/orm.md`](../../../standards/sqlalchemy/orm.md). The other documentor insight (CHECK constraint naming `ck_<table>_<column>_valid`) was already documented in orm.md line 19 — no-op.

## 2026-05-12 — Auto-pickup kickoff trigger gap resolved: `/loop` + ScheduleWakeup self-rearm locked — Kanban #791 methodology only
**Scope:** team methodology (`context/teams/dev/full-auto.md`). No code in agent-teams. Smoke verification deferred to follow-up.

**Decision:** Fork B+C from #791 locked as the kickoff path. Full-auto sessions now require **4** activation conditions (was 3); the new 4th condition is *user invoked `/loop` at session start*. The reactive-Claude-Code constraint is acknowledged as structural — no daemon will be added; instead the loop is bootstrapped by exactly one user message.

**The mechanism (B+C):**
- `/loop check <project-name> queue and pick up next task` is the kickoff line. `/loop` is Claude Code's dynamic-pacing loop skill.
- Lead's body resolves to `CLAUDE.md` bootstrap → MVP-3 pickup loop → on idle, Lead calls `ScheduleWakeup(delaySeconds=1800, prompt="recheck queue")` per `wakeup-30` idle-policy. The `/loop` runtime re-fires Lead with the same prompt on the wakeup → CLAUDE.md re-runs → pickup query re-runs.
- Loop terminates when (a) user interrupts, (b) idle-policy is `stop` and queue empties, or (c) Lead omits the next wakeup call.

**Why not the alternatives:**
- **A (manual kickoff per task):** defeats the unattended use case — user has to keep typing. Acceptable as fallback only.
- **B alone (/loop without ScheduleWakeup self-rearm):** loop runs but doesn't survive idle gracefully; user has to re-type after each empty-queue.
- **C alone (ScheduleWakeup without /loop):** Lead can't kick off without a first user message; reactive gap unbroken.

**Fallback path documented:** if `/loop` is unavailable (older Claude Code build), user types free-form `start auto-pickup on <project-name>`. Lead runs MVP-3 once, no self-rearm — manual kickoff per task. Not recommended; supported.

**Methodology doc changes:**
- `context/teams/dev/full-auto.md` "When this methodology fires" — 3 → 4 conditions (added: `/loop` invocation).
- New `## Kickoff` section between Bootstrap announce and MVP-3.
- Strike #3 entry locks the decision in the strike log.

**Smoke verification deferred:** AC #3 of #791 requires a live `/loop` kickoff smoke that observes auto-pickup triggering WITHOUT mid-session manual prompts. That hasn't run yet. Filed as follow-up #810 to keep the verification gate visible — #791 stays in REVIEW until #810 passes, mirroring the discipline that #788 was the verification gate for the full-auto MVP rather than auto-claimed by #786/#787 alone.

**Implications:**
- Methodology is shippable now — any new full-auto session opened today follows the 4-condition activation.
- Cross-session coordination (Meta-Lead) remains explicitly out of scope (#781 polish umbrella).
- The `/loop` dependency is a Claude-Code-product surface — if upstream removes the skill, the fallback path kicks in but unattended overnight degrades.

## 2026-05-12 — Tier 3 multi-project parallel smoke PASS — Kanban #789 bet closes 6/6 (#807 + #808)
**Scope:** verification gate. No code in agent-teams beyond two trivial smoke files. Outcome capture.

**Decision:** Tier 3 of the zero-config bet (concurrent-session, two-project parallelism) PASS by direct observation. Bet now closes 6/6 — previously logged 5/6 with Tier 3 deferred (see #788 + #794 entries below).

**The smoke:**
- Lead session A: agent-teams (project_id=1, this terminal). Picked up #808 at 05:52:32Z, wrote `_scratch/hello-tier3.md` with that timestamp, PATCH-closed.
- Lead session B: NewsAnalyzer (project_id=567, user opened separate Claude Code at `C:\Users\banku\Documents\Personal\Projects\WebApp\NewsAnalyzer`). Picked up #807, wrote `hello-tier3.md` at 05:50:21Z, PATCH-closed at 05:51:00Z via `lead-newsanalyzer` verified_by.
- Concurrent window ~05:50-05:52Z. Both sessions hit the same FastAPI backend with their own `X-Project-Id` headers. Session B's subsequent queue poll returned `[]` confirming its own queue drained without touching session A's.

**What this validates (last bet gap closed):**
- **No state leak across sessions** — X-Project-Id header is the per-call binding; the FastAPI side filters tasks correctly. Two Leads on two project queues do not see each other's work.
- **Bootstrap CLI scales to ≥2 distinct projects** — agent-teams (dev team, existing) + NewsAnalyzer (dev team, scaffolded via Kanban #777 working_path lineage). Different scaffold paths, different team agent files (both dev here, but novel scaffold proven separately by #794 Writing smoke).
- **Reactive Claude Code is parallel-capable in practice** — each session is its own Read–Eval loop talking to the shared backend; concurrency is solved by the user opening N terminals, not by any in-product daemon. The #791 kickoff-trigger gap is still real (user had to type the kickoff message in terminal 2), but parallelism itself works once both sessions are bootstrapped.

**What this does NOT validate (truly unattended):**
- **Self-starting concurrent sessions** — still requires user to manually open terminal 2 and type a kickoff message. The #791 gap (filed earlier) gates true unattended overnight runs.
- **Multi-task queue depth** — each project's queue had exactly 1 task. Queue-drain ordering across many tasks per session not exercised this slice.
- **Cross-session same-task contention** — the smoke gave each Lead its own task. Two sessions both bound to the same project_id and both trying to pick up the same task would race — not tested. (Soft-gated today by the `task_kind="human"` filter excluding most rows; #786's auto-pickup loop should pin claim-or-skip semantics if/when this becomes relevant.)

**Implications:**
- **Bet #789 closes 6/6.** Public-repo readiness goal cleared. README + CLI + Tier 1/2/3 all green.
- **Next bet candidates** (logged earlier, now ready to pick up): MCP server adapter (#806 filed today), #791 kickoff-trigger gap, post-MVP polish for #776 + #781 umbrellas, Phase 4 dashboard + list view (#769, #770).
- **Concurrency story for README:** the existing 1-command CLI plus "open one terminal per project" recipe is the multi-project workflow today. No daemon, no orchestrator, no Meta-Lead. Worth a paragraph in README before publishing.

## 2026-05-12 — acceptance_criteria JSONB field (Kanban #797 + #801 + #798) — discipline gate after #789 "1.5/4 not WIN" retrospective
**Scope:** shared (data model + agent prompts + standards).

**Decision:** Added structured `acceptance_criteria` JSONB field to tasks + soft-enforce via agent prompts. Tasks may carry `[{text, status, verified_by, verified_at, notes}]` where status ∈ {pending, passed, failed, na}. Optional per-task (no schema constraint). Discipline lives in dev-tester / dev-reviewer / CLAUDE.md / spawn-template prompts — no API done-guard.

**The retrospective trigger:** earlier same day, I (Lead) claimed "Bet WIN" on #794 zero-config validation. User asked for honest count → 1.5/4 of #794's own exit criteria. Failure mode: exit criteria buried in description text → easy to skim → claim done without per-criterion check. Structured field + prompts make the gap visible.

**Design lock (user signoff 2026-05-12, Fork 1C + 2A + 3B):**
- **1C:** JSONB array of `{text, status, verified_by, verified_at, notes}` — full structure, not plain markdown.
- **2A:** Optional field — no schema-level constraint on filing. Trivial tasks can skip; quality-gate tasks should include.
- **3B:** Soft enforce — agents + Lead self-discipline via prompts. API does NOT block process_status=5 when criteria pending. Reason: 700+ legacy tasks have null criteria; hard-enforce breaks flow.

**Implementation:**
- **#797 backend:** Alembic 0014, model + Pydantic `AcceptanceCriterion`, TaskCreate/Update/Read fields. 9 tests in `tests/test_routes_smoke.py`. `mode='json'` not yet applied (bug surfaced via #801).
- **#801 bug fix:** `verified_at: datetime` crashed JSONB write because SA's default `json_serializer` rejects datetime. Fix: `model_dump(mode='json')` on the criteria list in both POST and PATCH handlers (scoped to acceptance_criteria — sibling top-level DateTime columns stay native). 3 new regression tests. Standards rule recorded in `context/standards/sqlalchemy/orm.md` (humans-only zone — user-instructed write per CLAUDE.md exception).
- **#798 agent prompts:** 4 inserts — dev-tester.md `### 2d. Acceptance criteria verification` (mandatory per-criterion table + JSON block in final report), dev-reviewer.md "Acceptance criteria audit" bullet (MAJOR if criterion not satisfied, BLOCKER if pending after dev-tester done), CLAUDE.md `## Acceptance criteria discipline (universal)` section (Lead must copy criteria list + verification source before flipping done), spawn-template.md Constraints bullet (cascades the rule to every subagent). Drafted in `_scratch/798-ac-prompt-patches.md`, user pasted, verified via Select-String.

**Dogfood validation:** Tier 1 task #799 became the FIRST AC-field-bearing task. 3 criteria, all PASS verified (Lead bootstrap, novel-writer spawn, idempotent CLI re-run). End-to-end live exercise of the AC field + #801 bug fix on #802 close PATCH.

**Implications:**
- Going forward, ANY task with substantive verification scope should carry acceptance_criteria. Verification gates (smoke tests, bug fixes, contract changes) are the strongest candidates.
- Spawn-template now requires per-criterion verdict in subagent reports. Subagents that skip → Lead rejects + asks for redo.
- Standards rule for Pydantic+JSONB+datetime is durable — future tasks adding nested datetime to JSONB columns won't re-strike this bug.
- Old tasks (700+) with null criteria are NOT retroactively required to add them — optional field stays optional.

**The honest meta-takeaway:** "claimed WIN at 1.5/4" was the second strike of the same pattern (earlier strike 2026-05-12 morning: "MVP-5 smoke validated 🎲" at 3/8 ACs). Two strikes = pattern. The structural fix (field + prompts) is the response — discipline alone failed twice in one day.

## 2026-05-12 — Zero-config bet WIN via CLI pivot — Kanban #789 MVP closed (#792/#793/#795/#796/#794)
**Scope:** backend (scaffolder service + manifest endpoint + handler wiring) + devops (PowerShell CLI) + verification gate.

**Decision:** Zero-config bootstrap MVP delivered via CLI path (Option A), not auto-scaffold-on-POST (Option D). User-driven smoke on Writing project (#794) PASSED on Steps 1+2 — Steps 3+4 deferred as nice-to-have. Bet outcome: setup time on a fresh project drops from ~20 min manual file-shuffle to ~10 sec CLI invocation. Public-repo onboarding gate cleared for the dev-team and novel-team scaffolds.

**Pivot story (the architectural surprise):**
- Original locked plan (#789 split, 2026-05-12 morning): Option D only — extend POST /api/projects to scaffold orchestration files into `working_path`. #792 (scaffolder service) + #793 (handler wiring) shipped GREEN with 6 + 6 tests.
- Pre-#794 smoke check: agent-teams API runs in Docker; only `.:/repo` is mounted; user project paths (`C:\Users\banku\Documents\Personal\Writing` etc.) live on host filesystem outside the container's reach. The #793 handler's `target.exists()` check would silently return False → scaffold silently no-ops. The 6 #793 tests all used `tempfile.TemporaryDirectory()` (in-container paths) so they passed — but the bet wouldn't validate in production.
- Pivot decision (user signoff): keep #792/#793 as local-dev opt-in, add #795 (server endpoint serving manifest + base64-encoded bytes) + #796 (host-side PowerShell CLI that fetches + decodes + writes).
- Architecture: manifest logic lives in one place (`services/zero_config_scaffold.py`), called by both the #793 in-process handler and the #795 HTTP endpoint. Settings.json substitution extracted to a shared pure-bytes helper.

**The smoke (#794) — Steps 1+2 verified:**
- `bin/agent-teams-init.ps1 -Name Writing2 -WorkingPath C:\Users\banku\Documents\Personal\Writing -Team novel` → 39 files copied, 0 errors.
- Filter correct: only `dev-analyst` + `dev-spec-reviewer` (cross-team utilities) + `novel-editor` + `novel-writer` present in `.claude/agents/`. NO `dev-backend|frontend|devops|reviewer|tester`.
- Settings.json post-substitution: no `by-name/agent-teams` references.
- DB row created via CLI's find-or-create path (POST 201).

**What this validates:**
- **Manifest engine is correct** — file-set per team matches expectation, no orphans, no leakage.
- **Idempotent-add semantics** — smoke companion test (#796) ran twice, second run = 0 copied + 39 skipped, mtimes preserved.
- **Settings.json filter** — pure-bytes helper drops the 4 forbidden substring categories cleanly.
- **CLI <-> API contract** — base64 round-trip works across PowerShell 5.1 + FastAPI JSON serialization.
- **Cross-host scaffold** — files land on Windows host filesystem despite API running in Docker container.

**What this did NOT validate (deferred — out of bet scope):**
- Lead bootstrap on the scaffolded project (would have been Steps 3+4 — opening Claude Code at Writing path + running a novel-team task). The smoke validates the SCAFFOLD; the Lead+team-playbook surface is a separate validation gate.
- POSIX/Bash port of the CLI (Linux/macOS users still need manual setup).
- `agent-teams.exe` standalone binary (deferred).
- Refresh/rescaffold endpoint (deferred — manual delete-then-rerun is the user-driven escape hatch).
- `working_path` collision detection (project #571 Writing2 shares the same path as #568 Writing — duplicate-by-path is allowed and harmless for now, but is a future UX paper-cut).

**The DB-clutter note:**
- Smoke session leaked 3 rows: #569 (dev tempdir test), #570 (dev tempdir test), #571 (Writing2 — novel team, same working_path as #568 Writing). agent-teams has no consumer-facing DELETE endpoint by design; cleanup is human-only via psql. Filed as a future housekeeping pass; not blocking.

**Implications:**
- New surface: `GET /api/scaffold/{team}/files?project_name=X&project_id=N` — manifest + base64 file bytes endpoint. Add to api-contracts.md.
- New host tool: `bin/agent-teams-init.ps1` + companion `.smoke.ps1`. PowerShell 5.1 compatible. Windows-only for MVP.
- Public-repo README can now point at a 1-command setup story instead of a 6-step PowerShell file-copy list. The portfolio/publish goal that motivated this bet (#789 description) is unblocked.
- Future MCP server adapter (next-bet candidate) can layer on top of MVP-D's endpoint by exposing it as an MCP tool — non-Claude-Code clients (Cursor, Cline, ChatGPT) become possible Lead surfaces. Not committed; logged in #789 umbrella post-MVP polish list.

## 2026-05-12 — Full-auto MVP-5 smoke PASS on NewsAnalyzer — Kanban #788 closed, bet VALIDATED
**Scope:** verification gate for the full-auto bet (no code in agent-teams — outcome capture only).

**Decision:** MVP-5 smoke PASSED on NewsAnalyzer (project_id=567). All 5 ACs of #788 hit. Multi-project full-auto orchestration is validated as a working concept. agent-teams as a meta-orchestration product clears its proof-of-concept threshold.

**The run:**
- User opened Claude Code at NewsAnalyzer's working_path with `LEAD_AUTOPICKUP=1` set.
- Lead was silent (Claude Code is reactive — no spontaneous bootstrap; gap filed as #791).
- User typed kickoff message naming the project; Lead resolved, bound, announced auto-pickup mode.
- Lead picked up smoke task #790 (`api/health.py` bootstrap on NewsAnalyzer).
- dev-backend spawned with 1-line brief, wrote the file (auto-approve hook fired WITHOUT prompting — the critical cross-project assertion).
- Lead committed (NewsAnalyzer-local commit `4f6f425`), PATCHed #790 to process_status=5.
- Lead queried queue again → empty → announced idle.

**What this validates:**
- **#784 auto-approve hook** works on a fresh repo (not agent-teams-specific assumptions).
- **#785 halt_reason column** schema is compatible with the auto-pickup query (halt_reason IS NULL filter).
- **#786 pickup loop** logic is followable by Lead end-to-end.
- **#787 decision matrix** wasn't exercised in this smoke (no judgment-call hit) — still un-validated for the 5 default actions, but loop infrastructure around them is sound.
- **#777 schema** (working_path, working_repo, agent_overrides) supported the NewsAnalyzer project row creation and Lead's binding correctly.
- **#779 dev-analyst, #780 dev-spec-reviewer** also weren't exercised on this trivial smoke — the smoke task was small enough that hand-written spec sufficed.

**What this does NOT validate:**
- True unattended overnight runs (requires #791 kickoff-trigger follow-up).
- Halt + unhalt cycles (no judgment call hit during smoke).
- dev-analyst + dev-spec-reviewer in the loop (not exercised).
- Multi-task queue depth (queue had exactly 1 task; idle hit immediately).
- The Writing project (novel team smoke is a separate next run).

**Implications:**
- **Critical path complete.** The 5 MVP tasks (#784/#785/#786/#787/#788) of the full-auto bet are all closed. Umbrella tasks #776 + #781 reopen for polish-only iterations (post-MVP).
- **Publish goal unblocked at MVP level.** The repo can credibly demonstrate working multi-agent orchestration as a portfolio artifact. README + onboarding still needs the zero-config bootstrap from #789 before it's truly evaluator-friendly.
- **Strike #2 logged in `context/teams/dev/full-auto.md`** — preserves the methodology trail across future smokes.
- **Next pickups (in priority order):** #791 (kickoff trigger — gates true unattended), #789 (zero-config bootstrap — gates publish-ready UX), then revisit umbrellas #776 + #781 for polish breakdown.

**Standards-candidates (propose-only — NOT written this slice):**
- `standards/claude-code/reactive-session-bootstrap.md`: any methodology that depends on Lead acting before user input is invalid — Claude Code is reactive. Methodology authors must specify a kickoff trigger (slash command, manual message, or external scheduler). Strike #1 here (surfaced via #788 smoke); tabled.

## 2026-05-12 — Full-auto methodology MVP: pickup loop + top-5 decision matrix — Kanban #786 + #787 closed
**Scope:** Lead-direct methodology doc (`context/teams/dev/full-auto.md` — new file). No code, no schema.

**Decision:** Locked the MVP rules for unattended Lead operation. Activation requires ALL of: `LEAD_AUTOPICKUP=1` env var, `.claude/settings.json` wiring in the #784 auto-approve hook, and Lead bootstrap-announce string. Partial activation defaults to interactive — no half-modes.

**Pickup loop (MVP-3):** on task close, Lead queries `GET /api/tasks?project_id=<p>&process_status=1&order_by=priority,created_at`, picks the first row matching `task_kind != 'human'` AND `halt_reason IS NULL` AND `status = 1`. Idle policies: `wakeup-30` (default) or `stop` via `LEAD_AUTOIDLE=stop`.

**Decision matrix (MVP-4) — top-5 defaults USER SIGNED OFF 2026-05-12:**
1. **Reviewer WARN** — Fold if (≤10 LOC) AND (no public API / wire / shared/ change); else file follow-up + close. *User: accepted.*
2. **Reviewer NIT** — Always defer to consolidated follow-up task. Never fold in auto. *User: accepted.*
3. **Tester new-standard proposal (strike #1)** — Log to `_scratch/` + bullet in `decisions.md` "Standards-candidates". Never auto-write `standards/**`. *User: accepted.*
4. **Option A/B validator ambiguity** — HALT with `halt_reason="Option A/B decision needed: <summary>"`. *User: accepted.*
5. **Cross-task scope creep** — HALT with `halt_reason="Scope creep proposed: <summary>"`. *User: accepted.*

**Reasoning:**
- The 5 defaults derive from real strikes in interactive sessions today (Option A/B from #714, scope creep is a recurring pattern, reviewer fold/defer matches user's habitual choices). Choosing widely-used heuristics minimises surprise on first smoke run.
- Defer-by-default for NITs (rule 2) errs on the conservative side — better to over-batch than to auto-apply polish that turns out wrong. Codified in the strike log so future iterations can reconsider if NIT-fatigue becomes a thing.
- HALT (rules 4 + 5) is the safety net for wire-contract and scope-cost decisions — the two categories where a wrong auto-default cascades widest.
- Standards-candidate logging (rule 3) preserves the humans-only invariant on `context/standards/**` even when no human is present — auto sessions log proposals; user codifies at next interactive session.

**Implications:**
- **MVP-5 smoke (Kanban #788)** is now unblocked. Critical path complete for the bet (all 4 of #784/#785/#786/#787 closed).
- **Halt format prefix discipline** — `halt_reason` strings MUST start with one of the matrix-defined prefixes ("Option A/B decision needed:" or "Scope creep proposed:"). Future matrix extensions add new prefixes. The prefix is the categorical signal.
- **Strike log lives in `context/teams/dev/full-auto.md`** — append entry after each MVP-5 smoke run. Codifies what the matrix actually caught vs missed.

**Out of scope (deferred to umbrella #776 + #781):**
- All decision points beyond top-5.
- `process_status=8` dedicated halted enum value.
- `halted_at` timestamp + FE halted lane.
- Granular Bash auto-approve patterns (MVP allows only Write/Edit).
- `blocked_by` integration in pickup query (#771 P3-deferred).
- Notification webhooks on halt.
- Cross-project Meta-Lead coordination.

**Standards-candidates (propose-only — NOT written this slice):**
- `standards/process/auto-decision-matrix.md`: "every auto-decision rule must have a halt-prefix convention" — prefix is the categorical signal across heterogeneous halts. Tabled, strike #1.

## 2026-05-12 — Full-auto MVP infrastructure: hook + halt schema — Kanban #784 + #785 closed
**Scope:** infra (`.claude/hooks/auto-approve-safe-writes.ps1` + smoke; migration `0013_tasks_halt_reason`, ORM, Pydantic, 7 tests on `test_routes_smoke.py`)

**Decision:** Land 2 of the 5 MVP atomic pieces for the full-auto bet (#776 + #781 umbrella scope):
- **#784** — PowerShell hook `.claude/hooks/auto-approve-safe-writes.ps1` that emits `permissionDecision: "allow"` for `Write`/`Edit` on safe-zone prefixes (`api/`, `web/`, `context/projects/`, `_scratch/`, `.claude/hooks/`); emits `"ask"` on path-traversal (`..`); pass-through (exit 0, no output) on everything else. Companion smoke script with 5 input shapes — all PASS. NOT wired into `agent-teams/.claude/settings.json` this slice; per-project enablement is a separate step on NewsAnalyzer + Writing only.
- **#785** — `tasks.halt_reason TEXT NULL` via migration `0013_tasks_halt_reason`. Free-form halt reason; Lead PATCHes to non-empty string to halt, PATCHes to null to unhalt. Pydantic `min_length=1` rejects empty string (422). Auto-pickup query in #786 will skip rows where `halt_reason IS NOT NULL` regardless of `process_status` (orthogonal to lifecycle, same pattern as `is_pending` from #750).

**Locked contracts:**
- **Hook is ALLOW-or-pass-through only** — never denies. Deny-side responsibility belongs to `block-raw-sql-dml.ps1` and other future deny-hooks. Separation of concerns preserves debuggability.
- **Path-traversal forces "ask"** — `\.\.` regex against raw input (before normalization) catches both forward-slash and Windows-backslash variants. Strictly conservative: a literal filename like `foo..bar.txt` also forces ask; trade-off accepted for zero traversal escapes.
- **`halt_reason` PATCH semantics**: key-absent = leave unchanged; explicit null = clear/unhalt; non-empty string = set; empty `""` = 422. Parity with `description`, `working_path`. NO `_reject_explicit_null` validator (null IS meaningful = unhalt).
- **No new `process_status` enum value** for halted state — `halt_reason IS NOT NULL` is the flag; process_status stays at whatever value the task held when halted. Same orthogonal-flag pattern as `is_pending` (Kanban #750).

**Reasoning — MVP cut points:**
- Skipped `dev-reviewer` + standalone `dev-tester` on #785 BE phase. Mirrors `working_path` pattern from #777 byte-for-byte (Pydantic `str | None` with `min_length=1`, PATCH-null clears, mirror tests). Reviewer's value on #777 was catching the JSONB scalar-null + key-allowlist; halt_reason is plain text with no such surface — no novelty to audit.
- Skipped `process_status=8` enum + `halted_at` timestamp. Both deferred to #781 polish umbrella. MVP only needs the flag, not the lifecycle ceremony.
- Hook NOT wired into agent-teams' own settings.json. Wiring is per-project deliberate — agent-teams (this session) stays prompt-per-call so Lead remains under human review on the dogfood loop.

**Reviewer/tester decisions (subagent denials):**
- 2nd strike: `.claude/hooks/*` writes from subagents denied (after `.claude/agents/*` denied 2026-05-11 on #779/#780). Pattern confirmed: `.claude/**` is humans-only for subagents in this project, regardless of `Write(/.claude/**)` allow rule in settings.json. Memory saved (`feedback_claude_dir_humans_only.md`). Future tasks touching `.claude/**` must use draft-in-`_scratch/` + user-move workflow.

**Implications:**
- **Hook enablement (out of scope this slice)**: when MVP-5 smoke begins on NewsAnalyzer, the user adds a `.claude/settings.json` in that project pointing to the hook. agent-teams keeps the hook installed but UNUSED.
- **Auto-pickup query consumer (#786)**: MUST filter `AND halt_reason IS NULL`. Brief for #786 needs to specify this byte-for-byte.
- **Decision matrix (#787)**: when Lead sets `halt_reason`, the value should be one of a curated set (e.g., `"Option A/B decision needed: <summary>"`, `"Scope creep proposed: <summary>"`) per the matrix. Free-form text at DB level; convention at app/Lead level.

**Standards-candidates (propose-only — NOT written this slice):**
- `standards/claude-code/hook-allow-vs-deny-separation.md`: ALLOW-hooks must never DENY (and vice versa). One responsibility per hook keeps debugging tractable when prompts unexpectedly fire (or unexpectedly don't). Strike #1 here; tabled.

## 2026-05-12 — projects schema: working_path + working_repo + agent_overrides — Kanban #777 closed
**Scope:** backend (migration `0012_projects_path_repo_ovr`, 4 files: migration, ORM model, Pydantic schemas, router; 22 tests on `test_routes_smoke.py`)
**Decision:** Add 3 optional columns to `projects` — `working_path TEXT NULL` (project root on dev machine), `working_repo TEXT NULL` (free-form repo URL/path), `agent_overrides JSONB NULL DEFAULT '{}'::jsonb` (per-project subagent model routing). All optional / metadata-additive — no existing code path consumes them yet; consumers land in Lead bootstrap + #774/#775/#779/#780 role wiring later.

**Locked contracts:**
- **`working_path` orthogonal to `paths_web/api/db`** — keep both. `paths_web/api/db` are LANE-specific sub-paths (dev-team scaffold); `working_path` is the SINGLE project root that wraps them. Don't merge, don't rename. Captured in migration docstring + ORM-comment for the next maintainer.
- **`working_path` / `working_repo` validation = `min_length=1` only.** No host-side path existence check (API host may differ from the host the path points to). No URL regex on `working_repo` — repo identifier is free-form (https/ssh/local-path/anything). Whitespace-only IS accepted (`"   "` passes `min_length=1`); contract is "non-empty string" not "non-blank string". Worth tightening if real abuse appears.
- **`agent_overrides` PATCH-null normalize to `{}`** (WARN-1 Option A). `server_default '{}'::jsonb` fires on INSERT only; without router transform, a PATCH `{"agent_overrides": null}` would land JSONB scalar `'null'` in the column (Pydantic surfaces as `None` on read). Router in `update_project` rewrites `null → {}` before the UPDATE. Locked by `test_777_edge_patch_agent_overrides_null_clears_to_empty_dict`. Consequence: `ProjectRead.agent_overrides` is `dict[str, Any]` (no `| None`) — every response, every consumer, always a dict.
- **`agent_overrides` PATCH replace semantics (NOT deep-merge).** Whole value sent = new value, full-stop. Locked by `test_patch_project_agent_overrides_replace_semantics`. Deep-merge could be added as a separate field/endpoint later, but cannot be silently retracted once shipped — preserve the simpler semantic by default.
- **`agent_overrides` key allowlist `^[a-zA-Z0-9_-]{1,64}$`** (WARN-4) — Pydantic `field_validator` on both `ProjectCreate` + `ProjectUpdate`. Same shape as `project.name`. Forward-compatible with #774/#775/#779/#780 role names; rejects empty key, 65+ char keys, embedded newlines, prototype-pollution-shaped keys. Justification: keys persist to JSONB → free-form risks row bloat / audit-log noise / hypothetical FE prototype pollution if a consumer ever does `Object.assign(obj, agentOverrides)`.
- **`agent_overrides` values = `Literal['haiku'|'sonnet'|'opus']`** on write (ProjectCreate/Update); `dict[str, Any]` on read (ProjectRead) — read-tolerant on VALUE for legacy-backfill resilience.

**Reasoning:**
- Bundle 3 columns into 1 migration because they all add to `projects` (single ALTER TABLE round-trip; one shared migration revision; one shared Pydantic + router patch). User accepted this fold over splitting into 3 separate tasks.
- Option A (router null→{}) chosen over Option B (let null clear to SQL NULL) because the agent_overrides docstring already promised "always a dict at the response boundary" — Option A keeps the wire contract clean; Option B would have required loosening downstream consumer code to handle `None`. Cost: one `if` line in the router. Benefit: every consumer can assume `dict`.
- Apply key allowlist NOW (not deferred) because the tester proved permissiveness held in practice; tightening later would be a breaking change. Forward-compat verified: every planned role name (`dev-analyst`, `dev-spec-reviewer`, `dev-documentor`, `dev-researcher`) fits the regex.

**Implications:**
- **Lead bootstrap (future)**: `GET /api/projects/by-name/{name}` now returns `working_path`, `working_repo`, `agent_overrides`. Bootstrap announce string should include them once Lead-prompt is updated.
- **`scaffold_project_folder`**: currently uses `settings.repo_root` (NOT `working_path`). Verified by `test_777_edge_scaffold_uses_repo_root_not_working_path`. Decision: keep this — scaffold writes to `context/projects/<name>/` (inside the repo); `working_path` is metadata about the target project's location, not where the kanban app scaffolds metadata. Document explicitly here so the next slice doesn't re-litigate.
- **Pending bootstrap** (per #777 description): after this slice closes, create 2 project rows — `NewsAnalyzer` (team=dev, working_path=`C:\Users\banku\Documents\Personal\Projects\WebApp\NewsAnalyzer`) and `Writing` (team=novel, working_path=`C:\Users\banku\Documents\Personal\Writing`). Both are the full-auto experiment targets.
- **FE awareness**: `web/` Grep clean — frontend has no knowledge of the 3 new fields. Acceptable for this slice (BE-only contract); FE Create/Edit Project form gains a follow-up Kanban once #774/#775 stabilize.

**Standards-candidates (propose-only — NOT written this slice):**
- `standards/fastapi/patch-null-on-jsonb-default-column.md`: when a column has `server_default` for INSERT but PATCH semantics must keep it non-null, the router (not Pydantic) MUST normalize null → server-default-value before UPDATE. Schema-level `field_validator` can't do this because explicit-null is meaningful for sibling nullable fields. Strike #1 here; tabled until strike #2 (dogfood-pollution 3-strike discipline).
- `standards/pydantic/dict-key-allowlist.md`: when a `dict[str, X]` field's keys are user-supplied AND persist to DB, add a `field_validator` with a name-shaped regex. Free-form keys are perpetual row-bloat / audit-noise / FE-prototype-pollution risk. Strike #1 here; tabled.

**Reviewer + tester incidents (notes):**
- Reviewer's 4 WARN + 5 NIT triage: 6 applied (1+2+3+4+6 + NIT-3), 1 deferred (NIT-4 _Paths.web/api/db min_length — pre-existing, separate task), 2 superseded (NIT-1 dict|None tightened by WARN-1; NIT-2 by-id GET covered by tester).
- Tester's `test_777_edge_agent_overrides_pathological_keys` (documented permissiveness) replaced by `test_777_edge_agent_overrides_rejects_empty_key` + `test_777_edge_agent_overrides_rejects_long_key` after WARN-4 lock — contract direction reversed mid-slice, tests updated to match.

## 2026-05-11 — #710 polish: WARN-1 + 6 NITs — Kanban #768 closed
**Scope:** frontend polish (5 files: layout.tsx, ThemeProvider.tsx, ThemePicker.tsx, BoardColumn.tsx, loading.tsx)
**Decision:** Apply the 1 WARN + 6 NITs deferred from #710:
- **WARN-1** — `suppressHydrationWarning` on `<html>` only (1 attribute, scope-confined; pattern from `next-themes` + Next.js docs).
- **NIT-1** — FOUC bootstrap boolean reduced from `t==='dark'||((t===null||t==='system'||(t!=='light'&&t!=='dark'))&&matchMedia.matches)` to `t==='dark'||(t!=='light'&&matchMedia.matches)`. Truth-table-equivalent across all 5 input states (`'light'`/`'dark'`/`'system'`/`null`/invalid); ~40 bytes saved.
- **NIT-2** — try/catch around `localStorage.getItem` + `setItem` in ThemeProvider (mirrors the FOUC script's own guard for Safari private-mode / locked-down WebViews / iframe sandboxing). Read failure defaults to `'system'`; write failure silently skips persistence (theme still applies in-memory + on DOM for the session).
- **NIT-3** — ThemePicker switched to WAI-ARIA toggle-group pattern: container `role="group" aria-label="theme"` + bare-enum per-button `aria-label="light"` / `"dark"` / `"system"` (matches in-repo standard `react/aria-label-vs-data-attribute.md` worked example).
- **NIT-4** — container `data-theme` renamed to `data-theme-selected` to disambiguate from per-button `data-theme-option`. Zero stale `data-theme=` references in `web/`.
- **NIT-5** — BoardColumn `aria-label={\`column-${statuses.join("+")}-cards\`}` (hyphenated machine-form) → `\`${label} cards\`` (human form). Reuses existing `label` prop; Board.tsx call sites all pass guaranteed-string label literals (`"New tasks"`, `"In progress"`, `"Review"`, `"Blocked"`, `"Done"`).
- **NIT-6** — loading.tsx viewport-lock chain aligned with Board.tsx (`h-screen + overflow-hidden + min-h-0 flex-1`) — skeleton no longer overflows viewport mid-load. Dark-mode classes from #710 preserved.

**Verification:**
- Tier-1 wire-attestation **PASS independently on first capture** — 11/11 positive markers (`aria-label="light|dark|system"` ×3, `role="group"` ×1, `aria-label="theme"` ×1, `data-theme-selected=` ×1, `data-theme-option=` ×3, `aria-label="<label> cards"` ×5 across the 5 columns, FOUC reduced-boolean exact substring ×1). 6/6 negative markers all =0 (no `"theme light"`, no `"column-"`, no orphan `data-theme=`, no old FOUC). Paired-pair structure → zero spurious-PASS surface.
- tsc clean independent re-confirm.
- Canonical seed `project 1 updated_at:2026-05-09T12:03:27.939263Z` byte-identical (pure FE polish — no API touch).
- **#710 strike-#1 of `feature-wire-attestation.md` did its job:** FE self-attested with grep before handoff; tester independently re-grep'd same markers — alignment proves the rule's pairing-discipline works. No HMR-stall recurrence this slice (dev-frontend hit the stall once during their own attestation, recovered via `docker compose restart web` as the standard prescribes, then handed off clean).

**Reviewer incident (closed as no-action):** dev-reviewer's GREEN report flagged a "brief vs code mismatch" on NIT-1, claiming the file retained the original 3-clause boolean. Re-verification showed the file IS the reduced form, byte-identical to the brief — reviewer misread their own grep output. Reviewer's truth-table analysis was nonetheless useful (independently confirmed all 5 input states are equivalent). No code change needed. Reviewer's own NIT was a misread, NOT a real finding.

**Standards-candidate (propose-only — NOT written this slice):**
- Strengthening clause for `standards/nextjs/feature-wire-attestation.md`: "every form-change NIT in the spawn brief must specify BOTH the positive marker (new form present) AND the negative marker (old form absent); vacuous-pass guard against 'both forms shipped' coincidence." The #768 brief followed this implicitly (every Probe-A entry had a Probe-B mirror); the rule earned its keep on first run. Surfaced by dev-tester; defer to user for the explicit-write decision (humans-only zone).

**Implications:**
- No FE behavior regression. ThemePicker keyboard / mouse interactions unchanged. Drag-drop, card rendering, scrollbar styling, dark-mode tokens all preserved.
- BoardColumn `label` prop is now load-bearing for a11y (was visual-only before). Future column-header refactors (e.g., icon-only headers) must either keep `label` as a string OR decouple a11y-label from header-label. Documented in the file's call-site contract.
- Standards-rule `feature-wire-attestation.md` survived its first independent verification cycle. Strike log entry stays as just #710 — #768 did NOT trigger a strike (FE recovered cleanly within the prescribed flow).

**Superseded:** the 1 WARN + 6 NITs from #710's review block.

---

## 2026-05-11 — Theme picker (light/dark/system) + full dark-mode pass — Kanban #710 closed
**Scope:** frontend (pure FE slice — no API contract change, no migration)
**Decision:** Activate the T5 theme slice now that defer-gate (T1-T4 + #407) is satisfied. Three architectural pillars:

1. **Tailwind `darkMode: 'class'`** — variants gate on explicit `<html class="dark">`, not `prefers-color-scheme`-media. Lets the user override OS preference; FOUC bootstrap script writes the class synchronously before React hydrates.
2. **FOUC bootstrap** — inline `<script dangerouslySetInnerHTML>` in `<head>` of `app/layout.tsx`. Tiny + dependency-free + try/catch'd around `localStorage`. Reads `localStorage.theme` ('light'/'dark'/'system'); resolves 'system' via `matchMedia('(prefers-color-scheme: dark)').matches`; mutates `documentElement.classList`. Prevents first-paint flash. Two new Client Components — `ThemeProvider` (Context + matchMedia listener + localStorage sync) and `ThemePicker` (3-button toggle with sun/moon/monitor SVGs, aria-label space-form `'theme light'` etc., placed in Board.tsx header next to ProjectSwitcher via `ml-auto`).
3. **Dark-mode token map (zinc-based + desaturated semantic pair):** page `bg-white → dark:bg-zinc-950`; column `bg-zinc-50/60 → dark:bg-zinc-900/40`; card `bg-white → dark:bg-zinc-900`; border `zinc-200 → dark:zinc-800`; text primary/secondary/tertiary `900/600/400 → dark:100/400/500`. Semantic accents (blue/red/orange/amber/indigo badges): **desaturated lighter pair** `text-X-700 bg-X-50 → adds dark:text-X-300 dark:bg-X-900/30`. NOT inversion. Scrollbar arbitrary variants gain dark twin: `dark:[&::-webkit-scrollbar-thumb]:bg-zinc-700` + hover `zinc-600`. Toast inverts (`zinc-900 → dark:zinc-100`) as floating chrome.

**Reasoning:**
- `darkMode: 'class'` lets the FOUC script short-circuit the OS-media branch when a user has chosen `light`/`dark` explicitly. Media-mode would force system-only.
- Desaturated-lighter (not full inversion) preserves semantic identity recognition (red still reads "urgent", emerald still reads "consented") while landing in the dark surface's contrast band.
- ThemeProvider's first effect re-reads localStorage + re-calls `applyDarkClass` — idempotent with the FOUC script in the happy path, but covers the failure path (private-mode browser, localStorage throws inside the script's try/catch). Redundancy by design.
- ThemePicker in Board.tsx (Client) is acceptable — wrapping `app/p/[name]/page.tsx` stays Server. Established Server-parent + Client-child boundary preserved.
- The matchMedia listener is conditional on `theme === 'system'`; flipping to `light`/`dark` detaches the listener, flipping back re-attaches. No leak.

**Implications:**
- 16 files touched: tailwind config + layout + 11 components + error/loading + Board (ThemePicker import). 2 new Components. Zero new dependencies; zero functional logic change (TaskCard byte-equality verified: useSortable, drag-drop, aria, data-attrs all preserved — only color classes added).
- Tier-1 wire-level smoke 6/6 GREEN post-restart (FOUC script in `<head>` before `__next_f.push`; 7 ThemePicker markers verbatim; 492 distinct `class=` attributes with `dark:` utilities; SSR `<html>` neutral; tsc clean; healthcheck Up; canonical seed byte-identical).
- **Pre-restart RED state — `next dev` HMR stall (2h stale compiled chunks)** — dev-tester caught it; `docker compose restart web` recovered. Triggered the **strike-#1 lesson** that landed `context/standards/nextjs/feature-wire-attestation.md` THIS slice (rule: dev-frontend handoff attestation MUST grep rendered HTML for ≥1 feature-specific marker, not just tsc+healthcheck+200). User-explicit standards write — overrides the default "humans-only auto-write" rule.

**Deferred (filed as Kanban #768, priority=2):**
- **WARN-1** — `<html>` missing `suppressHydrationWarning`. Real concern: React 18 dev-mode hydration warning every time FOUC script adds `dark` class; React 19 may treat as hydration error. 1-attribute fix.
- **6 NITs** — FOUC boolean reduction, ThemeProvider localStorage try/catch, ThemePicker aria-label prefix-form consistency, `data-theme` overload disambiguation, pre-existing BoardColumn aria-label hyphenated machine-form, loading.tsx viewport-lock drift.

**Standards landed this slice:**
- **NEW** `context/standards/nextjs/feature-wire-attestation.md` — wire-level marker grep mandate for FE handoffs. User-explicit write per Q1-exception in CLAUDE.md.

**Standards-candidate (propose-only, NOT written):**
- `context/standards/tailwind/dark-mode.md` — class-mode opt-in, token map, desaturated-lighter accent pair, scrollbar arbitrary variant. Tabled until 2nd-strike use case (e.g., custom-theme variant) per dogfood-pollution discipline.
- `context/standards/nextjs/fouc-theme-bootstrap.md` — inline-script + suppressHydrationWarning pattern for any "user-prefers-X-before-paint" concern (theme, locale, dyslexic-font, reduced-motion). Tabled until 2nd-strike (e.g., locale-picker FOUC).

**Superseded:** none — first dark-mode decision; first FE-attestation standard.

---

## 2026-05-11 — TaskUpdate hardening (3 reviewer MINs from T1 #706) — Kanban #714 closed
**Scope:** backend (schema + service typing — no migration, no router change)
**Decision:** Three hardenings on `TaskUpdate` + the two cross-table service helpers:
1. **MIN-1 — `_check_template_completeness` model_validator** on TaskUpdate, mirror of TaskCreate's. PATCH that sets `is_template=true` without `recurrence_rule` AND `next_fire_at` self-contained in the same payload → 422 with **byte-for-byte verbatim** detail `"is_template=true requires recurrence_rule and next_fire_at"` (same string used by TaskCreate — single source-text-locked wire contract).
2. **MIN-2 — Literal narrowing on `services/task_kind.py` + `services/run_mode.py`**: param signatures tightened from `str` → `TaskKindLiteral` / `TaskRunModeLiteral` (imported from `src.schemas.task`). Direction services → schemas — verified cycle-free via `python -c "from src.services import task_kind, run_mode"` + grep (`api/src/schemas/` has zero `from src.services` imports). Static-tooling catches drift; no runtime behavior change.
3. **MIN-3 — `_reject_explicit_null_recurrence_timezone` model_validator** on TaskUpdate: PATCH `{"recurrence_timezone": null}` → 422 with source-text-locked detail `"recurrence_timezone cannot be explicitly null — omit the key to leave the existing value, or send a valid IANA TZ string"`. Uses `model_fields_set` to distinguish explicit-null (rejected) from key-absent (no-op, preserves PATCH semantics).

**Option A locked over Option B (MIN-1 design choice):** the TaskUpdate completeness validator does NOT consult the existing row's state — it judges the payload alone. A client flipping `is_template=true` MUST re-send `recurrence_rule + next_fire_at` in the same body, even if the row already carries them. Reasoning: Pydantic validators have no DB access; Option B (validator queries DB or moves to router) is a deeper refactor with no UX dividend (one extra payload key per template-flip is acceptable). DB CHECK `ck_tasks_template_recurrence_complete` remains the ultimate backstop. **Lock test:** `test_patch_task_is_template_true_on_already_complete_row_returns_422_locks_option_a` (test_task_kind_recurrence.py:911) — explicitly creates a fully-templated row, un-templates it (so the row retains rule+fire_at), then PATCHes bare `{"is_template": true}` and asserts 422. Folded into the same slice per dev-reviewer WARN-1.

**Implications:**
- **pytest 310 → 318 GREEN** (+7 wire-contract tests + 1 Option-A lock test).
- **Tier-1 smoke 7/7 GREEN** (matched POSITIVE+NEGATIVE pairs on MIN-1 + MIN-3; MIN-3-C `updated_at` byte-identical to MIN-3-B → no-op semantic proven at the DB-row level; canonical seed project 1 + task 1 `updated_at` unchanged).
- **FE impact:** PATCH endpoints that previously fell through to DB CHECK 400 now return a friendlier 422 on the same input. No FE client is known to send the rejected shapes today.
- **NITs deferred** (not blocker, propose-only):
  - NIT-1: `TaskKindLiteral` / `TaskRunModeLiteral` location — currently in `schemas/task.py` (services import from schemas, precedent: `compact_runner.py:60` mirrors this). Standards-candidate: rule in `standards/python/pydantic-schemas.md` once enum-family parity lands. Defer per dogfood-pollution discipline.
  - NIT-2: comment expansion on `_check_template_completeness` early-return branch (cosmetic).

**Standards-candidate (propose-only):** "Wire-level `Literal` aliases co-locate with their `ALL` tuple in `src.constants`; the import-time lockstep guard moves with them." Tabled until `TaskCreate` / `ProjectCreate` family parity (the deferred `extra='forbid'` audit slice) — at that point the pattern justifies a paragraph in `standards/python/pydantic-schemas.md`.

**Superseded:** Pre-#714 DB-CHECK 400 fallback paths for the two PATCH shapes (`is_template=true` incomplete; `recurrence_timezone=null`). Detail strings now source-text-locked at the schema layer.

---

## 2026-05-11 — Session-family Create schemas → `extra='forbid'` — Kanban #721 closed
**Scope:** backend (schema only — no router / migration / FE change)
**Decision:** Tighten 3 Create-shaped Pydantic schemas in `api/src/schemas/session.py` from `extra='ignore'` (or Pydantic default) to `model_config = ConfigDict(extra="forbid")`: `SessionCreate`, `SessionActivityCreate`, `SessionRunHeartbeat`. Application of the pre-existing `ConsentGrant` decision (#483) — not a new policy.

**Reasoning:** N6 (CTX-1 close-out, 2026-05-10) flagged that smuggled `{"status":"weird"}` on POST `/api/sessions` returned 201 with silent drop instead of 422. Same default applied to the 2 CTX-2 Create siblings (activity/heartbeat). The #483 lock — "deliberate-action UX must fail loud on smuggled fields" — already governs this surface; #721 is just the application slice. Schema-level fix; no router / no migration. Tester N6-re probe: pre-fix `{"project_id":1,"status":"weird"}` → 201; post-fix → 422 with `loc=["body","status"]` + `type="extra_forbidden"`.

**Deliberately untouched:**
- `SessionUpdate` / `SessionRunUpdate` — explicit `extra="ignore"` retained (CTX-1 deliberate: PATCH bodies legitimately carry stale fields).
- `SessionCompactRequest` — left at Pydantic default `ignore` (server-side automation may pass enrichment keys). Reviewer NIT #1: its docstring still says "pending #721's project-wide locked decision" — stale now that #721 chose to skip it. Defer to a follow-up doc-only slice or fold into the next BE touch on this file.

**Implications:**
- pytest 306 → **310 GREEN** (+4 new tests, all 422 + loc assertions mirroring the canonical `test_grant_consent_rejects_extra_fields_422`).
- Tier-1 smoke 8/8 GREEN (matched POSITIVE+NEGATIVE pairs on each of 3 endpoints; canonical seed `project 1 updated_at:2026-05-09T12:03:27.939263Z` byte-identical post-probe).
- **FE risk:** any client sending extra keys on these 3 POSTs now gets 422 instead of silent success. No known FE caller does this (the V2/V3 board uses GET only); CTX-3/CTX-4 master-agent integration not yet wired. Watch when those land.
- `api-contracts.md` updated: removed the "Pydantic extra-policy note" deferral paragraph under POST /api/sessions; added a 422-with-loc bullet in the Errors block; refreshed the `SessionActivityCreate` + `SessionRunHeartbeat` schema entries to drop the "#721 will tighten" forward-references.
- **Probe leakage acknowledged:** sessions API has no DELETE endpoint (close-only, mirrors #716 design); tester left session 12 + run 8 + `_sessions/12/` in place. Not blocker.

**Standards-candidate (propose-only, defer per dogfood-pollution discipline):** "Create-shaped HTTP-exposed Pydantic schemas → `extra='forbid'`; Update / internal-automation schemas → text-locked `extra='ignore'`." Two-pattern split now exemplified by 1 (ConsentGrant) + 3 (this slice) Create instances + 2 Update + 1 internal. Worth a rule paragraph in `standards/python/pydantic-schemas.md` once `TaskCreate` / `ProjectCreate` parity lands.

**Superseded:** Pre-#721 silent-drop behavior on the 3 listed Create schemas. The "Pydantic extra-policy note" paragraph in `api-contracts.md` under POST /api/sessions.

---

## 2026-05-11 — Scheduler INFO/exception logs surface to docker logs — Kanban #739 closed
**Scope:** backend
**Decision:** Attach a `StreamHandler(sys.stdout)` directly to the `src` umbrella logger (NOT root) at module top-level in `api/src/main.py`. `setLevel(INFO)` on `src` so `src.main` + `src.services.*` + `src.routers.*` propagate. Drops the original v1 attempt at `logging.basicConfig(...)` because it routed to **stderr** by default and uvicorn's `--reload` worker subprocess does NOT forward stderr to `docker compose logs api` the same way as stdout — the scheduler boot INFO line was invisible despite the basicConfig being correct in a standalone `python -c` import test.

```python
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_src_logger = logging.getLogger("src")
_src_logger.setLevel(logging.INFO)
if not _src_logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter(_LOG_FORMAT))
    _src_logger.addHandler(_h)
# propagate left as True — see deviation below
```

**Live verification (the fix that PROVED the gap-fix worked, not a "should work" claim):**
- `2026-05-11 14:46:11,215 INFO src.main: recurrence scheduler started — tick every 60s (job_id=recurrence_tick)` — visible in `docker compose logs api` immediately after `Application startup complete.`
- `2026-05-11 14:46:08,892 INFO src.main: recurrence scheduler stopped` — visible on prior container shutdown emit
- `docker compose logs api --tail 80 | grep "BEGIN (implicit)"` — each transaction's `BEGIN` appears exactly **once** (no v1-era duplicate-line regression)

**Reasoning:**
- Handler on `src` umbrella (not root) avoids interaction with uvicorn's stock `LOGGING_CONFIG` (which configures `uvicorn.*` loggers — root untouched in their setup, BUT we want OUR handler to be predictable, not contingent on uvicorn defaults).
- `sys.stdout` (not stderr) is uvicorn-friendly. uvicorn's access logs also go to stdout; docker captures stdout reliably under `--reload`.
- `if not _src_logger.handlers` idempotency guard — uvicorn `--reload` re-imports `src.main` on file change; without the guard each reload would stack another handler → duplicate emits per reload cycle.
- No `basicConfig` call → no surprise root-handler attachment → no surprise duplicate-line regression for any other `INFO`-enabled logger (sqlalchemy.engine with `echo=True`, etc.).

**Deviation from spawn brief — `propagate=True` kept (NOT False).** Setting `propagate=False` (defense-in-depth recommendation in the spawn brief) broke 2 pre-existing tests in `tests/test_sessions.py` (`test_patch_run_unknown_model_logs_warning_and_succeeds`, `test_patch_run_over_budget_sets_warning_and_logs`) — both rely on pytest's root-level caplog to capture WARNING records from `src.routers.sessions`. The spawn brief constrained the BE agent to 1 test file; touching `test_sessions.py` was out of scope. Current risk = zero (production never adds a root handler since we no longer call `basicConfig`); flag for cleanup IF a root JSON-shipping handler is added later — at that point `propagate=False` + caplog-rewrite become a joint slice.

**Implications:**
- **pytest 305 → 306 GREEN** (+1 caplog smoke test verifying `src.*` records surface; includes handler-attachment assertion).
- **Reviewer GREEN-pending-runtime-probe (0 BLOCKER / 0 WARN / 3 NIT)** verdict locked GREEN once Lead ran the live probe.
- **v1 attempt (basicConfig at WARNING)** is an instructive false-positive: pytest GREEN + `python -c` REPL test GREEN + reviewer GREEN-pending → but the actual uvicorn-worker runtime FAILED to surface the line. **Lesson:** logging-config slices NEVER ship without a live `docker compose logs` probe; the diagnostic gap between "pytest captures correctly" and "uvicorn worker emits correctly" is real and load-bearing. Captured as candidate `standards/python/logging.md` rule (proposed by both BE + reviewer, defer per dogfood-pollution discipline until strike #2).
- **No api-contracts.md / db-schema.md / migration touch.**

**Superseded:** v1 attempt within same #739 (basicConfig approach). v2 supersedes; v1 was never pushed.

---

## 2026-05-11 — FE polish v2.2 (per-lane scrollbar + icon badges) — Kanban #764 closed
**Scope:** frontend
**Decision:** Two FE-only polish slices on the Kanban Board, bundled per direct user direction. **Slice A** — viewport-locked layout: `<main h-screen overflow-hidden flex flex-col>` + grid container `flex-1 min-h-0 overflow-hidden` + each `<BoardColumn>` cards-list `flex-1 overflow-y-auto`. Page-level chrome (ProjectSwitcher + h1 + ConsentBanner) stays fixed at top; only the cards inside each column scroll. **Slice B** — replace text-label badges with inline SVG icons: person/robot for `task_kind=human|ai`, M-glyph / A-with-circular-arrow for `run_mode=manual|auto_*`. No new dependencies.

- **Layout primer in `app/layout.tsx`** — `<html h-full>` + `<body h-full overflow-hidden>` is the foundation; without it the viewport-lock chain collapses. All 3 load-bearing `min-h-0` declarations placed correctly (Board grid line 150, BoardColumn section line 28, cards-list line 45) — closes the classic flexbox-overflow gotcha.
- **Scrollbar styling Linear-style** — `[scrollbar-width:thin]` + `[&::-webkit-scrollbar]:w-1.5` + zinc-300 thumb (zinc-400 on hover) + transparent track. Hairline; no glow. Per-column `tabindex=0` + `aria-label="column-<statuses>-cards"` for keyboard a11y.
- **Icon set (4 SVGs total):** all 14×14 viewBox 16, `fill="none"` + `stroke="currentColor"` + stroke-width 1.5. Person (circle head + curved body), robot (rect head + antenna dot + 2 eye dots), A-with-circular-arrow (partial-circle arrow path + A glyph), double-M (manual). All carry `aria-hidden="true"` on the `<svg>`; wrapping `<span>` carries `aria-label` + `title` for screen-readers and hover tooltips.
- **aria-label form decision (CONFIRMED space-form):** `aria-label="auto pickup"` / `"auto headless"` (space form) for screen-reader natural pronunciation; `data-run-mode="auto_pickup"` / `"auto_headless"` (underscore form) preserved verbatim for CSS selectors + JS hooks + DB enum parity. **Codifies a split convention:** human-facing strings use space form; machine-facing attributes use enum-verbatim form. Reviewer + tester both confirmed this is better UX than the spawn-brief's underscored draft.
- **Standards candidates surfaced (propose-only — human MA pending):** (a) NEW `context/standards/web/aria-label-vs-data-attribute.md` codifying the space-vs-underscored split with RunModeBadge as worked example; (b) optional refinement to existing `context/standards/nextjs/server-client-composition.md` — add SVG-icon Server-component worked example (TaskKindBadge + RunModeBadge are pure-presentational, no `"use client"`).

**Reasoning:** Page-level scroll on a Kanban board hides the column headers + switcher when scrolling — bad UX for a horizontally-laid-out tool. Per-lane scroll is the Linear / Notion / Jira norm. Icon badges (vs text) shrink horizontal real-estate per card (~30% smaller badge), letting more title/description fit. The double-form aria-label convention is the natural compromise — screen readers get human-readable strings without forcing the underlying enum to drift.

**Implications:**
- **Tier-1 dev-tester GREEN 5/5** with strong probes: Probe A 12-marker mass attestation (61 task-ids ↔ 61 aria-label="human" ↔ 61 aria-label="manual" 1:1); Probe B `aria-label="ai"=1` exact +1 delta on synthetic AI task; Probe C `aria-label="auto pickup"=1` exact +1 delta on synthetic auto_pickup task; Probe D verbatim cards-list class string extraction proving Slice A markup; Probe E tsc clean. All 3 throwaways DELETE 204 + post-restore byte-identical to baseline + canonical seed `agent-teams id=1 updated_at:2026-05-09T12:03:27.939263Z` intact.
- **Reviewer GREEN 0/0/0/3-NIT** — 3 cosmetic NITs all defer-able (extract scrollbar utility class when 2nd scrollbar surface lands; `h-screen` → `h-[100dvh]` swap if/when mobile in scope; minor comment hygiene). 0 BLOCKER / 0 WARN.
- **Visual scroll behavior (does the lane ACTUALLY scroll on overflow?) NOT directly probed** — requires headless browser; deferred to future tooling slice (companion to #761 residual). Wire-level markup proof accepted per prior #708/#748/#750 posture.
- **`dev-frontend/current-state.md` compact-step gap** — FE agent paused mid-cycle; their role-state file does NOT have a #764 entry. Lead absorbs the FE summary into this decisions.md entry instead of force-writing role-state (Lead doesn't write `<role>/` zone per universal CLAUDE.md). Discipline gap noted; not blocker.

**Superseded:** N/A — additive polish. T3 #708 / T4 #709 / V2 polish 2026-05-10 / #750 pending state all preserved exactly.

---

## 2026-05-11 — Web container-internal port 3000 → 5431 — Kanban #763 closed (full symmetry with api 8456:8456)
**Scope:** devops / shared / standards
**Decision:** Close the asymmetry left by #762 (host-side only). Flip the container-internal Next.js listener from 3000 → 5431 so the compose mapping becomes **`5431:5431` symmetric** — mirrors the api precedent (host = container = 8456). `docker compose exec -T web wget http://localhost:5431` now works identically to host `curl localhost:5431`; no inside-vs-outside port gear-shift.

- **3-file devops diff:**
  - `web/Dockerfile` — `EXPOSE 3000` → `EXPOSE 5431`
  - `web/package.json` — scripts `next dev -p 3000` → `next dev -p 5431`; `next start -p 3000` → `next start -p 5431`
  - `docker-compose.yml` — port mapping `${WEB_PORT:-5431}:3000` → `${WEB_PORT:-5431}:5431`; healthcheck `wget http://localhost:3000` → `localhost:5431`; inline comment example port updated.
- **Image rebuild required** (Dockerfile EXPOSE is metadata, but the `next dev -p 5431` script change requires the image to ship the updated package.json — `docker compose build web` + `up -d web`). Devops confirmed via `docker compose ps web` showing `0.0.0.0:5431->5431/tcp` + `Health:healthy` post-rebuild.
- **Tier-1 verdict GREEN 5/5** with **matched-pair listener-flip proof:** Probe B (`docker exec wget localhost:5431` inside container) PASS + Probe C (`docker exec wget localhost:3000`) refused with `Connection refused EXIT=1`. Together these are causal proof that the listener moved (not just port-forwarded). #407 V3 surface still serves 6/6 markers on the symmetric port.
- **Reviewer NIT from #762 closed in this slice:** the project-scoped-port rule in `context/standards/docker/compose.md` got promoted + the symmetric-port-mapping rule explicitly added (host:container must match unless deliberate-asymmetry justification). Worked examples: api 8456:8456 (from project genesis) + web 5431:5431 (post-#763).
- **Files DELIBERATELY untouched:** `web/lib/api.ts`, `web/components/**`, `web/app/**`, `INTERNAL_API_URL` (api URL, unchanged), `NEXT_PUBLIC_API_URL` (api URL, unchanged), `.env.example` (already correct from #762), `.claude/settings.json` allowlist (already correct from #762), `.claude/hooks/tester-curl-allow.ps1` (regex port-agnostic), `README.md` (already correct from #762). Historical entries preserved.

**Reasoning:** Host:container port asymmetry was a hidden footgun. `docker compose exec` debugging probes silently produced an inside-vs-outside surface (developer types `localhost:5431` outside, must remember `localhost:3000` inside). The api side never had this issue (8456:8456 from genesis); web inherited it from Next.js's default 3000. Symmetry restores the "agent-teams web = 5431, period" mental model. Cost is minimal — 3 files, one image rebuild. The symmetry rule is now codified in `standards/docker/compose.md` so future services / scaffolds get it right at provisioning.

**Implications:**
- **Standards rule landed:** `standards/docker/compose.md` now explicitly says container-side port MUST match host-side port (with the inside-vs-outside gear-shift anti-pattern as the worked example). Future Lead spawn prompts should reference this when scaffolding new services.
- **Healthcheck contract reinforced:** healthcheck command port literal MUST match the container-internal listener. The symmetric mapping makes this automatic; asymmetric mappings silently break healthchecks if devs forget to update both the port mapping AND the healthcheck command.
- **`.env`-override risk unchanged from #762** — local `WEB_PORT=<value>` still overrides the compose default; same caveat as #762 close-out.
- **Slice scope clean** — devops, Lead, tester, reviewer cycles all converged GREEN. No follow-up filed.

**Superseded:** N/A — additive (closes the #762 residual). #762's "container-internal port stays 3000" note in this file's entry is correct historical context for that slice's scope; #763 is the deliberate follow-through.

---

## 2026-05-11 — Web host port 3000 → 5431 — Kanban #762 closed
**Scope:** devops / shared / standards
**Decision:** Bind agent-teams web to host port **5431** (custom, project-scoped). Container-internal port stays **3000** (unchanged: `web/Dockerfile` EXPOSE, `next dev -p 3000`, in-container healthcheck `wget http://localhost:3000`, `INTERNAL_API_URL`). Only the host-side mapping changes via `WEB_PORT` env-var; `docker-compose.yml` default `${WEB_PORT:-3000}:3000` → `${WEB_PORT:-5431}:3000`. Mirrors the **api project-scoped port pattern** (8456 for agent-teams api) — each project picks its own host port at scaffolding time instead of using the framework default.

- **User rationale (2026-05-11):** future scaffolded projects will use Next.js default 3000; collision-free side-by-side dev requires project-scoped host ports. Picking 5431 here reserves the slot for agent-teams web; analogous to api=8456 reserving for agent-teams api.
- **Container-internal port DELIBERATELY unchanged.** Container-side stays framework-native (3000 for next dev) — only host mapping is project-scoped. This minimizes blast radius: `web/Dockerfile`, `web/package.json` scripts, `INTERNAL_API_URL`, in-container healthcheck all untouched.
- **Files edited (7):** `.env.example` (WEB_PORT default + comment), `docker-compose.yml` (default substitution), `.claude/settings.json` (3 allowlist lines for `localhost:5431`), `.claude/hooks/tester-curl-allow.ps1` (2 comment lines; hook regex `(localhost|127\.0\.0\.1):\d+` is port-agnostic — no logic change), `README.md` (host-facing port refs in quickstart + services table + Kanban UI section), `shared/smoke-matrix.md` (Web URL line + #762 cross-link), `standards/docker/compose.md` (example port + project-scoped-port rationale note).
- **Files DELIBERATELY untouched:** `web/Dockerfile`, `web/package.json`, `web/lib/api.ts`, `web/components/**`, `web/app/**`, in-container healthcheck command, `INTERNAL_API_URL`. All container-internal or app-code; port abstraction stops at the compose mapping. Historical entries in `current-state.md` files and prior `decisions.md` entries also UNTOUCHED — audit trail; values were correct at write-time.

**Reasoning:** Default-port collision is a real cost on multi-project workstations (Next.js 3000, Vite 5173, Postgres 5432, FastAPI 8000 are all common defaults). Project-scoped port allocation at scaffolding time avoids retro-incidents later (#762 itself is the retrofit — would have been cheaper to allocate at Phase 3 V1). Pattern now codified in `standards/docker/compose.md` for future project scaffolds.

**Implications:**
- **Tier-1 dev-tester GREEN 5/5** on the new port. All #407 V3 markers (data-task-id ≥ 50, data-project-switcher = 1, data-board="dnd" = 1, data-consent-grant-trigger = 1) intact on `localhost:5431/p/agent-teams`. Old port 3000 returns connection-refused (`STATUS:000 EXIT:7`). Container healthcheck still GREEN (internal port 3000 unchanged).
- **Hook regex port-agnostic** — `tester-curl-allow.ps1` auto-allow continues to work for `localhost:5431` without any port-anchored allowlist gap. Tester observed zero permission prompts across 5 curls on 3 distinct ports.
- **Local `.env` consideration:** if a developer machine has `.env` with `WEB_PORT=3000`, that overrides the compose default. The repo's `.env.example` is updated; individual developers must update their local `.env` (gitignored). The current Lead workstation `.env` does NOT contain `WEB_PORT` — compose-default fall-through; no action needed here.
- **Standards insight surfaced:** project-scoped host ports for dev containers (added to `standards/docker/compose.md`). Mirrors the implicit pattern api=8456 + new web=5431; documenting it closes the loop and shifts the convention from "implicit per project" to "explicit at scaffolding time."

**Superseded:** N/A — additive config + doc. Phase 3 V1 scaffold's choice of port 3000 (originally documented in the 2026-05-08 #406 entry, kept historical) is the prior state.

---

## 2026-05-11 — `BACKEND_FAILURE_INJECT` env-knob — Kanban #761 closed (env-knob slice; Playwright residual deferred)
**Scope:** frontend / shared / team-methodology
**Decision:** Add a test-only env-knob `BACKEND_FAILURE_INJECT` consumed by `web/lib/api.ts` `jsonFetch`. When set to `"true"` AND `NODE_ENV != "production"`, `jsonFetch` throws `new HttpError(500, ...)` BEFORE hitting the real backend. Used by dev-tester to verify the WARN-1 fix from #760 (Server Component catch routes non-404 errors to `app/error.tsx`, NOT `notFound()`). This is the runtime verification path that was deferred from #760.

- **Double-guarded against production:** (a) `process.env.NODE_ENV !== "production"` check inside the code path, AND (b) non-`NEXT_PUBLIC_*` naming so the var is inaccessible to the client bundle (Next.js inlines non-public vars as `undefined` on the client). Single-failure prod-enablement is structurally impossible.
- **Detail / message source-text-locked:** `"BACKEND_FAILURE_INJECT=true (synthetic 500 from web/lib/api.ts)"`. dev-tester asserts the substring + verbatim stack trace chain `jsonFetch → getProjectByName → ProjectBoardPage`.
- **Boolean-only V1 (no per-path scoping).** Original #761 description mentioned `BACKEND_FAILURE_INJECT_PATHS` for surgical injection; deferred — simple boolean is enough for the WARN-1 probe and any future generic SSR-failure smoke. File follow-up if surgical scoping is ever needed.
- **Tier-1 methodology probe C1-live landed in `context/teams/dev/smoke-methodology.md`** — wraps the full enable / probe / restore cycle (docker-compose edit → restart → curl → restore → git diff = empty assertion). Optional probe — run only when task touches Server-Component error handling.

**Reasoning:** Static-code review confirmed the WARN-1 discriminator logic post-#760, but a live runtime assertion was missing. The env-knob is the cheapest mechanism that produces a real non-404 throw from the same `jsonFetch` code path the real backend uses — no mock layer, no test framework. The synthetic `HttpError(500)` traverses the same `app/p/[name]/page.tsx` catch + the same `if (e instanceof HttpError && e.status === 404) notFound(); throw e;` discriminator that a real DB outage would. Tester captured verbatim stack trace + RSC `data-dgst` sentinel + `app/error.tsx` chunk-registration evidence proving the error-boundary path fires.

**Implications:**
- **Tier-1 verdict GREEN 5/5** with `git diff docker-compose.yml` empty post-restore (production-grade restoration gate intact).
- **Methodology gotcha captured:** Next.js dev-mode SSR with a `"use client"` `app/error.tsx` renders the Suspense loading skeleton in the initial HTML, NOT the error UI text — the error.tsx hydrates client-side. The distinguishing wire-level signal is the `<template data-dgst="..." data-msg="..." data-stck="...">` sentinel + RSC graph's error.tsx chunk registration. Captured in the methodology probe so future testers don't waste cycles asserting against the visible-text marker.
- **Playwright residual deferred to a new Kanban ticket** (alpine/musl libc vs glibc blocker — Playwright wants glibc, web image is alpine). Options for the deferred slice: (a) switch `web/Dockerfile` to `node:20-slim` (Debian), (b) add separate `web-e2e` service on `mcr.microsoft.com/playwright` base, (c) other. User-decision when the slice opens.
- **Standards insight (CONFIRM, proposed for `context/standards/nextjs/` or `general.md`):** Test-only env knobs in SSR code MUST be double-guarded — (i) `NODE_ENV !== "production"` runtime check, AND (ii) non-`NEXT_PUBLIC_*` naming. The double-guard is the difference between "dev-only by convention" and "structurally impossible to enable in prod or in-browser." Worked example: `BACKEND_FAILURE_INJECT` in `web/lib/api.ts`.

**Superseded:** N/A — additive infrastructure. Original #761 scope split: env-knob shipped here; Playwright harness + D1-headless UX walk deferred.

---

## 2026-05-11 — Typed `HttpError` + ProjectSwitcher loadError reset — Kanban #760 closed (V3 WARNs)
**Scope:** frontend / shared
**Decision:** Three operational-quality WARNs from #407 V3 reviewer closed in one FE slice. No backend changes. No new dependencies. `tsc --noEmit` clean; #407 V3 Tier-1 baseline re-verified (57 task rows, switcher + grant trigger intact, 404 path renders not-found marker).

- **`web/lib/api.ts`** — exported `class HttpError extends Error { readonly status: number; readonly detail: unknown }`. `jsonFetch` throws `HttpError` instead of bare `Error` on non-2xx. `.message` semantics preserved (formatted detail OR status-line fallback), so all existing `err instanceof Error ? err.message : "..."` catches in Board.tsx + ConsentGrantModal.tsx + ProjectSwitcher.tsx work unchanged. **Discrimination at the throw layer, not the catch layer** — each caller picks its behavior (404 → `notFound()`, others → bubble to `error.tsx`).
- **`extractDetail` removed; new sync `formatDetail(detail: unknown): string | null`** handles BOTH string `detail` (400 / 404 source-text-locked) AND **array `detail`** (Pydantic 422 from `extra='forbid'` + future field validators). Array path joins each error's `msg` field with `"; "`; `JSON.stringify` per-element fallback for unknown shapes. Pre-#760 the modal rendered bare `"422 Unprocessable Entity"` on extra-field smuggle; now renders the actual `"Extra inputs are not permitted"` message.
- **`web/app/p/[name]/page.tsx`** — `catch (e) { if (e instanceof HttpError && e.status === 404) notFound(); throw e; }`. Non-404 errors (500, connection-refused, future 422) bubble to `app/error.tsx` — symmetric with the unguarded `listTasks` below. Closes the WARN-1 footgun where backend outage looked like "wrong project name" to the user.
- **`web/components/ProjectSwitcher.tsx`** — new `onToggle` handler calls `setLoadError(null)` before `setOpen((v) => !v)`. Trigger button wired to `onToggle`. The lazy-fetch effect's `projects.length > 0` short-circuit preserves happy-path no-refetch. Pre-#760, a single failed fetch permanently latched the error state until full-page reload; now every (re)open retries — correct UX for a dropdown.

**Reasoning:** Typed HTTP errors at the throw layer is the canonical TS pattern for letting each caller discriminate without parsing `error.message` strings. The `HttpError extends Error` shape keeps backward-compatibility with every existing `instanceof Error` catch — zero refactor required across the rest of the codebase. The `Server / Client bundle duplication` concern (would `instanceof HttpError` fail across boundaries?) is dismissed by evidence: the Server-Component catch in `page.tsx` lives in the same Node SSR process as the throw site in `lib/api.ts` (same module instance → same class identity); the Client catches (Board / Modal / Switcher) use `err instanceof Error` via the prototype chain, where class identity is irrelevant. If a future Client surface needs `instanceof HttpError`, fall back to duck-typing (`'status' in e && e.status === 404`).

**Implications:**
- **Tier-1 verdict GREEN 5/5.** A (#407 baseline re-confirm), B (404 path via discriminator), C (non-404 → error.tsx — static code review per spawn-brief authorisation since live 500 simulation requires container restart), D (loadError reset — static code review since no headless browser in `web` image), N1 (tsc clean).
- **Live runtime defense-in-depth deferred** (Probes C-live + D-headless) — both fixes have FE + reviewer + static-code triangulation; tester explicitly noted defers are safe. Future tooling slice candidate: add a Playwright harness to the web container + a `BACKEND_FAILURE_INJECT=true` SSR-side knob for deterministic 500 injection.
- **`api-contracts.md` did NOT need an edit** — Pydantic 422 array shape was already documented at the contract level; `formatDetail` matches the documented shape verbatim.
- **Tester surfaced a marker-grep drift in `standards/nextjs/notfound-dev-vs-prod.md` (which Lead wrote 2026-05-11):** the literal `>This page could not be found<` text-node pattern does NOT match in `next dev` SSR streams (markers live inside `__next_f` JSON chunks). The correct substring fingerprint is `could not be found` (without the angle-brackets). Two Tier-1 probes (#407 V3 + #760) hit this trap — surface to user for standards correction.
- **Two new standards candidates surfaced by reviewer** (propose-only — human MA pending): (a) `nextjs/typed-error-catch.md` (Server Component catch must discriminate via typed error — bare `catch { notFound() }` is the anti-pattern); (b) `typescript/typed-errors.md` (class-shape rule for HTTP-error classes — `extends Error`, `readonly status`, optional `readonly detail`, super(message) for legacy compat).

**Superseded:** N/A — additive refactor; existing #407 V3 surface preserved exactly.

---

## 2026-05-11 — `GET /api/projects/{id}` route added — Kanban #691 closed
**Scope:** backend / shared
**Decision:** Wire the direct id-based lookup route. Was 405 Method Not Allowed pre-#691 (only PATCH + DELETE registered on `/{project_id}`). New `GET /{project_id}` mirrors `/by-name/{name}` parity: `get_or_404` with `status=RecordStatus.ACTIVE` — soft-deleted rows 404 (parity). Detail string `f"Project id={project_id} not found"` source-text-locked, **byte-equal with PATCH / DELETE / grant-consent** on the same path (single shared format).

**Reasoning:** FE V3 project switcher (and future external integrations) want id-based GETs; today they must use `/by-name/{name}` or `?...` filters as a workaround. Active-only filter parity with `/by-name/{name}` is the right contract: soft-deleted rows should not be visible via id either; restore is a future admin path.

**Implications:**
- pytest 302 → 305 GREEN (+3 tests: positive on seeded id=1, 404 on missing, 404 on soft-deleted).
- Tier-1 live smoke 5/5 GREEN including route-ordering defense-in-depth check (`/active` still 410, `/by-name/agent-teams` still 200 — new dynamic route did NOT shadow the static segments).
- Route ordering safe by two independent defenses: (a) declaration order at lines 60 (`/active`) + 92 (`/by-name/{name}`) + 109 (`/{project_id}`); (b) `project_id: int` makes Starlette's int-converter reject non-digit segments like `"active"` and `"by-name"` outright (reviewer-confirmed).
- Reviewer 1 cosmetic NIT only (compress 4-line comment block to 2 lines — optional; deferred).

---

## 2026-05-11 — Phase 3 V3 landed — Kanban #407 closed (project switcher + consent grant)
**Scope:** frontend / shared
**Decision:** First mutation surface on the Kanban board (V2 was read-only; T4 #709 added drag-drop; this slice adds project navigation + the consent grant mutation). Route structure split:
- `/` → Server `redirect()` to `/p/${NEXT_PUBLIC_PROJECT_NAME ?? "agent-teams"}` (3-line page).
- `/p/[name]` → dynamic Server Component that `getProjectByName(params.name)` + renders `<Board>`; `notFound()` on the 404 throw.
URL is the project-selection source-of-truth — **NO localStorage** (scope-lock). URL bookmarks are how users share/save project context.

- **`<ProjectSwitcher>`** (Client) lives in the Board header (left of project-name h1). Lazy-fetches `listProjects({status:1})` on first open; client-side `router.push` on selection; outside-click + Escape close; hairline Linear-style dropdown with team chip per row. Stale list is acceptable for V3 (no project create/edit UI yet).
- **`<ProjectConsentGrantModal>`** (Client) embedded in the zinc-banner branch of `<ProjectConsentBanner>` (Server). **Composition pattern** — Server parent imports Client child as sibling, banner stays SSR; only the action is shipped to the browser. Typed-acknowledgment flow per #483: text input must match `project.name` exactly (case-sensitive). Backend 400 detail `"confirm_name must match project name exactly"` renders verbatim in an inline red alert. **NO optimistic update** — deliberate-action mutation class (auditable / consent-binding); wait for 200 then `router.refresh()` re-runs the Server banner so it flips zinc → emerald. Idempotent re-grant returns 200 unchanged on the wire (server side); UI surface for re-grant is structurally unreachable once consented (modal trigger removed from the emerald-branch DOM). No revoke UI — backend endpoint not yet shipped.
- **Two new API helpers** in `web/lib/api.ts`: `listProjects(opts?)` and `grantConsent(projectId, confirmName)`. Both **omit `X-Project-Id`** (project endpoints — project IS the resource).

**Reasoning:** Server/Client composition pattern is the canonical Next.js 14 App Router shape and the textbook anti-pattern is making the parent Client just to embed an interactive child (ships read-only state to the browser unnecessarily). Deliberate-action mutations (consent grant, account delete, payment confirm) MUST NOT use optimistic updates — auditable / legally-binding / hard-to-reverse → wait for server confirmation. V3 #407 grant flow is the worked example; the V2 drag-drop optimistic-update pattern (#709, locked) is the contrast (low-stakes mutation where optimistic IS correct). Both rules surfaced as candidate `context/standards/web/` insights (human MA pending).

**Implications:**
- **Tier-1 dev-tester verdict GREEN 11/11.** Probe pairs causally bound (A vs J: same web server, only diff is consented state → zinc-trigger-present vs emerald-trigger-absent; G + H: same project, idempotence locked via "non-null on first + byte-equal on re-grant"). `?status=1` silently ignored by backend surfaced as YELLOW — code is correct, gap is in the backend (no `status: int | None = Query(None)` plumbing) and api-contracts.md (now documents the silent-ignore explicitly).
- **Dev-mode quirks for testers:** `next dev` renders `notFound()` as HTTP 200 + 404-page body (not wire 404); `next dev` emits `redirect()` as a meta-refresh sentinel + `NEXT_REDIRECT;...;307` template hint (not wire 307). Production `next build && next start` is the only path that emits wire-level 404 / 307. Smoke matrices on V3 routes must assert against rendered markers (e.g., `>This page could not be found<`) OR run a prod build. Captured for `context/standards/web/nextjs/` insight (human MA pending).
- **Three WARNs filed by dev-reviewer for follow-up (do NOT block #407 close):** (a) `app/p/[name]/page.tsx` bare `catch { notFound() }` swallows non-404 backend errors as 404 — fix via `jsonFetch` typed-error refactor (`HttpError extends Error { status: number }`); (b) `ProjectSwitcher.loadError` never reset → permanent failure latch on first-fetch failure, reset on (re)open; (c) `extractDetail` only handles `typeof detail === "string"` — Pydantic 422 array form falls back to `"422 Unprocessable Entity"`, defense-in-depth fix in the same helper. WARN-1 + WARN-3 share the same fix surface and were bundled into one follow-up Kanban ticket; WARN-2 filed separately or bundled together.

**Superseded:** none. Builds on V2 polish (#406+) and inherits T3/T4/#750 selectors unchanged.

---

## 2026-05-11 — `tasks.is_pending` schema slice — Kanban #750 closed (supersedes #748 pending=TODO design error)
**Scope:** backend / frontend / devops / shared
**Decision:** "pending" is a first-class schema flag — `tasks.is_pending BOOLEAN NOT NULL DEFAULT FALSE` — orthogonal to `process_status`. Migration 0011 additive (PG 16 metadata-only via `server_default=false`; 94 rows backfilled). Cross-state rule enforced APP-LAYER at `src/services/is_pending.py`: `is_pending=true` REQUIRES `process_status=2` (in_progress). Backwards process_status transitions do NOT silently mutate is_pending — validator catches invalid pairs at write time.
- **Source-text-locked detail:** `"is_pending=true requires process_status=2 (in_progress)"`. Pinned at 4 rejection sites (POST default-ps, POST explicit-ps=3, PATCH asymmetric drift, PATCH drag-proxy).
- **Resolved-final PATCH pattern** (4th worked example after task_kind/run_mode/scheduled_at): `resolved_is_pending = updates.is_pending if 'is_pending' in updates else task.is_pending`; same for `resolved_process_status`. Validator runs against the pair. Bundled clear `{is_pending:false, process_status:3}` is the documented escape hatch.
- **FE predicate:** `task.is_pending && task.process_status === TaskStatus.IN_PROGRESS` keys yellow card bg + `<PendingBadge>` + `data-card-pending`. Yellow is structurally locked to in_progress (backend rejects impossible pair; FE `=== IN_PROGRESS` is second gate).
- **Pending cards NOT draggable.** `draggable = !isAi && !isPending` on `TaskCard`. Backend cross-state validator already rejects implied PATCH from drag (400 + optimistic rollback + toast); FE surfaces as `cursor-not-allowed` + `data-draggable="false"`. To move a pending card user must first PATCH `is_pending=false`.
- **Known minor a11y NIT (deferred):** `aria-disabled={isAi}` on TaskCard still keys on AI-ness only, not full `!draggable`. One-char fix; not a usability blocker.
- **No DB CHECK constraint this slice** — V1 app-layer enforcement, lockstep with `task_kind`/`run_mode`/`scheduled_at` validators.

**Reasoning:** Corrects the #748 design error where pending was keyed on `process_status === TODO` (mistakenly meaning "not yet picked up"). User clarification 2026-05-11: pending = "in-flight work that hit a problem and is stuck", a sub-state of in_progress alongside the BLOCKED column. Schema column gives FE a real source of truth (vs. stale-by-N-days auto-derivation or reusing BLOCKED). Cross-state validator is load-bearing: without it, the FE marker is a visual lie; with it, semantics are locked at the wire layer for every future client.

**Implications:** `is_pending` is now part of the universal `TaskRead` contract. V3 #407 inherits this slice's mutation primitives (patchTask + Toast + optimistic + rollback from #709) plus the resolved-final validator pattern. Future bundled-PATCH UX (clear pending + advance ps in one gesture) is V3+ scope. **Lesson — semantic-frame-misread:** when a Thai user word is ambiguous (column vs. sub-state), clarify the semantic frame BEFORE spawning. Distinct from dropped-point class; new sibling to `feedback_multi_point_requirements`.

**Superseded:** the prior #748 "V2.1 UX evolution" entry (4-column merge with `process_status === TODO` predicate) is wrong on semantics — kept here for reference only. The 4→5 column restoration landed in #709 (1:1 column↔ps mapping); the yellow/PendingBadge artifacts were stripped pre-#709-close, then recreated under #750 with the corrected predicate.

---

## 2026-05-11 — Kanban #709 closed: T4 drag-drop process_status (human-only, @dnd-kit)
**Scope:** frontend
**Decision:** First FE mutation surface on the Kanban board. Drag a `task_kind='human'` card across any of 5 columns (New / In progress / Review / Blocked / Done) → `PATCH /api/tasks/{id}` with new `process_status`. AI cards are doubly-disabled (`useSortable({disabled})` + `onDragEnd` kind-check guard).
- **Library:** `@dnd-kit/core ^6.3.1` + `@dnd-kit/sortable ^10.0.0` (first new deps since Phase 3 V1 scaffold).
- **Drop-target → ps mapping (LOCKED): 1:1.** `COLUMN_PS["1"]→1 .. "5"→5`. `COLUMN_PS` is **derived** from `COLUMNS` via `Object.fromEntries(...)` — single source of truth, no lockstep drift.
- **`over.id` resolution (LOCKED): typeof discriminator.** `@dnd-kit/core`'s `UniqueIdentifier = string | number` preserves type. Column droppables register with `id: columnId` (string `"1".."5"`); sortable cards with `id: task.id` (number). Resolution: `typeof over.id === "string"` → column key (`COLUMN_PS[over.id]`); otherwise → numeric card id, resolve `newPs` from THAT card's current `process_status`. **Anti-pattern:** `String(over.id)` lookup collides because `String(1) === "1"` — for task.id ∈ {1..5}, drop-on-card silently mis-targets (B1 bug caught by reviewer, missed by curl smoke).
- **Optimistic update + rollback pattern (LOCKED — V1 mutation primitive):** capture `original` per-drag → optimistic `setTasks` → fire `patchTask` → reconcile on success; on failure rollback `setTasks` + push toast with API `detail` string. Canonical FE mutation pattern; V3 #407 + future slices inherit.
- **`patchTask(projectId, id, body)`** added to `web/lib/api.ts`. Body shape: `Partial<Pick<TaskRead, 'process_status'|'priority'|'title'>>` (extensible). `jsonFetch` generalized to accept `method` + `body`.
- **Client-Component boundary at `<Board>`.** `page.tsx` stays Server Component (data fetch); `<Board>` is the single Client orchestrator that owns DnD state + tasks state + toast state. `BoardColumn` + `TaskCard` are Client (use `useDroppable` / `useSortable`).
- **`Toast.tsx`:** `role="status"` + `aria-live="polite"` + 4s auto-dismiss + cleanup on unmount. Style: `fixed bottom-4 right-4 z-50 shadow-sm` (the ONE allowed shadow surface — toast is floating chrome).
- **New smoke selectors:** `data-draggable={!isAi}` and `data-board="dnd"` on `<Board>` root.
- **AI gesture suppression — defense in depth:** `useSortable({disabled})` + `onDragEnd` early-exit + `aria-disabled="true"` + `cursor-not-allowed`.
- **API has NO `task_kind` PATCH restriction.** Only FE drag-handle is gated. API-level enforcement would land as separate slice.

**Reasoning:** T4 sequenced BEFORE V3 #407 (user direction 2026-05-11) so T4 builds the mutation primitives V3 inherits. `started_at`/`completed_at` server-stamping is documented API behavior — `started_at` set on first ps=2 entry NOT cleared on backwards transitions; same for `completed_at` on ps=5.

**Implications:** V3 #407 builds on the same patchTask + Toast + optimistic+rollback primitives. The `data-draggable` + `data-board` selectors are now part of the project's smoke vocabulary. **dnd-kit lesson codified:** `UniqueIdentifier` preserves string/number; future drag-drop slices must use typeof-discriminator, not `String()` coercion. **Limitation:** curl-based smoke does NOT exercise the @dnd-kit JS gesture (mouse/keyboard drag) — only wire layer + markup. Future Playwright suite recommended for keyboard drag, mouse drag, AI gesture rejection, and PATCH-failure-injection rollback. Reviewer's source-level scrutiny was the gate that caught B1.

---

## 2026-05-11 — Kanban #708 closed: T3 task_kind + recurrence badges (read-only) + fetch widening
**Scope:** frontend / shared
**Decision:** Surfaced T1 (#706) + #723 schema fields on the V2 Linear-baseline board as quiet read-only chrome.
- **Tailwind-only, no icon dep.** Codebase convention is text-only badges (`RunModeBadge`, priority/role chips).
- **Violet accent for AI rows.** `text-violet-700 bg-violet-50` — the only new color this slice. Distinct from blue/indigo (roles), orange/red (priority), amber (auto_headless).
- **Quiet null self-suppression for `RecurrenceIndicator`.** Returns `null` (not empty `<span>`) on dominant case (`!is_template && spawned_from_task_id === null`) — preserves V2 polish ~68px card height. Cross-field order deterministic: `is_template` checked before `spawned_from_task_id`.
- **New smoke selectors:** `data-task-kind` (`"ai"|"human"`) and `data-is-template` (`"true"|"false"`) on `<article>` root.
- **`scheduled_at` (#723) added to `TaskRead`** for type completeness but not rendered this slice (T2 one-shot UI is V3+).
- **Sub-fix: `web/app/page.tsx` fetch widening to `{ limit: 500 }`.** Pre-fix, `listTasks(project.id)` defaulted to `limit=50, ORDER BY id ASC` → with 53 active tasks, id-tail rows were structurally invisible. 500 is the API's server-side hard cap (verified 422 on 1000). Pagination UI is a separate UX ticket.

**Reasoning:** T3 is the read-only "wire-up display" slice between T1 schema and T4 mutation. Quiet visual treatment preserves V2 polish density; violet AI chip is the one accent reserved for the discriminator that matters in T4 (AI cards not drag-draggable).

**Implications:** T4 #709's drag-disabled predicate uses `data-task-kind`. V3 #407 can land without T3 reflow risk. **Next.js 14 SSR fingerprint:** SOME DOM strings appear twice in served HTML (className strings; JSX text adjacent to `{interpolated}` like `from #<!-- -->ID`). PLAIN text children (`<span>manual</span>`) and quoted `data-*` attribute values appear ONCE per row. **Use `data-*` attributes for unique-per-row smoke assertions.** **MIN deferred:** `RecurrenceIndicator.tsx` uses `new Date().toLocaleString()` in RSC context, so timestamp renders in container locale (UTC); `title` discloses IANA TZ. Locale-aware client-island formatting deferred until 2nd RSC datetime ships.

---

## 2026-05-10 — Context-management subsystem closed (CTX-1..CTX-4 + 2 audit follow-ups)
**Scope:** schema / api / devops / shared
**Kanban:** #716 (CTX-1 schema) → #717 (CTX-2 store) → #718 (CTX-3 token/cost) → #719 (CTX-4 Haiku compact) + audit follow-ups #722 (sessions ceilings extension) + #723 (tasks.scheduled_at one-shot path)
**Decision:** Session-based context model with hybrid DB+filesystem layout.

**Scope-lock (user-decided 4 directions):**
| # | Question | Locked |
|---|---|---|
| 1 | Session storage backend | **Hybrid** — DB (`sessions`/`session_runs`/`session_compacts`) for metadata + queryability; filesystem (`_sessions/<id>/{session.md, archive/, cards/}`) for markdown content |
| 2 | Session boundary | **Per project × per Claude Code instance** — 1 session = 1 project × 1 process. Multiple active sessions per project allowed |
| 3 | Token budget enforcement | **Soft** — measure + warn + log; never block. Surface `compact_recommended=true` in API response |
| 4 | Compact runner | **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) — ~10x cheaper than Sonnet/Opus. Reads `ANTHROPIC_API_KEY` from env. Provider abstraction deferred |

**CTX-1 #716 — migration 0008 + ORM + 8 endpoints:**
- 3 new tables: `sessions`, `session_runs`, `session_compacts`. Multi-instance partial index `ix_sessions_project_id_active` is **intentional accelerator, NOT uniqueness gate** (mirrors post-#694 freedom on `projects.is_active`).
- **Lockstep tuple in `src/constants.py`, not inline** (drift tests need monkeypatch-survivable target; module-level state gets re-set on `importlib.reload`). Mirrors `TaskRunMode`/`TaskKind` pattern.
- **post-INSERT `session_root_path` update inside single COMMIT** via `flush()` (row never observable with placeholder path).
- **Filesystem skeleton write AFTER `commit()`** — favors audit-row durability; CTX-2 writer creates dir on first append (defensive).
- **MAJ-1 rule:** every new ORM module MUST be imported in BOTH `src/models/__init__.py` AND `alembic/env.py` in the same commit — else `Base.metadata` doesn't see the new tables and next autogenerate produces spurious DROP TABLE diffs.
- **Two `APIRouter`s** from sessions module: `router` → `/api/sessions`, `runs_router` → `/api/session_runs` (run id is global, not nested).
- CTX-1 accepts `total_cost_usd` on PATCH session_run with no validation (CTX-3 replaces with server-authoritative compute).

**CTX-2 #717 — session_store.py + 3 endpoints:**
- `services/session_store.py` is canonical; `session_files.py` is a 20-line back-compat shim.
- **File locking: `filelock` (cross-platform), per-session at `_sessions/<sid>/.lock`.** Picked over POSIX `fcntl` for Windows-test portability. Single-process FastAPI is V1; multi-process (gunicorn workers) deferred to V2+.
- **Reader symmetry:** `read_session_for_prompt` and `get_section_text` ALSO acquire per-session lock (FileLock is exclusive-only; reads serialize behind writes — acceptable V1 tradeoff).
- **`total_bytes` (not `bytes_written`) on heartbeat response** — total file size, NOT bytes appended this call. Honest field name.
- **Section markers exact-match contract:** `## Compacted History` and `## Recent Activity`. `_split_sections` does byte-equal find with newline-boundary check.
- **Markdown round-trip:** append writes `content + "\n"`; replace writes `content` verbatim with no trailing newline.
- 5 source-text-locked detail strings introduced (404/400 closed-session/runless-run patterns).

**CTX-3 #718 — token counter + soft-warn + server-authoritative cost:**
- **Lead-lock: chars/4 LOCAL HEURISTIC, NO real tokenizer.** User-picked 2026-05-10 from 3 options (chars/4 vs tiktoken vs Anthropic SDK). Rationale: api container has no `ANTHROPIC_API_KEY`; soft-warn tolerates ~10-20% inaccuracy on English. Module docstring + locked snapshot test (`count_tokens("hello world") == 2`) defend against silent drift.
- **Server-authoritative cost: client value silently overwritten, NOT 422.** `extra="ignore"` retained on `SessionRunUpdate`.
- **`provider` + `model` NOT persisted** — pure inputs to `compute_cost`. Per-run provenance deferred to future column-add slice.
- **Pricing table (USD per 1M tokens):** opus-4-7 15.0/75.0; sonnet-4-6 3.0/15.0; haiku-4-5 0.8/4.0. Unknown pair → cost SKIPPED, WARNING logged, PATCH still 200.
- **Soft-warn budget:** log + flip `budget_warning` column, never block. Gated on `total_input_tokens` presence (status-only PATCHes don't re-fire).
- **Activity endpoint advisory (additive):** `POST /sessions/{id}/activity` response gains `compact_recommended`, `current_recent_tokens`, `recent_ceiling_tokens`.

**CTX-4 #719 — Haiku 4.5 compact runner + POST /compact:**
- **Run-count trigger DEFERRED to V2.** V1 = manual + size triggers via `POST /api/sessions/{id}/compact` with `trigger_kind`.
- **Compacted History strategy: REPLACE, NOT concat.** Archive captures prior Compacted History VERBATIM as 3rd section (alongside Recent Activity + LLM summary) — immutable forensic record; replay fully reconstructible. Section order: header → prior Compacted → original Recent → LLM summary.
- **Atomic status lock via single-UPDATE:** `UPDATE sessions SET status='compacting' WHERE id=:sid AND status='active' RETURNING id`. Empty RETURNING → 409. Lock release via `try/finally` returns status to `'active'` on every failure path.
- **Anthropic SDK lazy-import** inside function body (NOT module-top) — lets test envs without SDK still import compact_runner; `MissingApiKey` surfaces first as typed app exception.
- **respx HTTPX-transport-layer stub** for tests (one fixture covers sync + async paths).
- **Cost from SDK-reported `usage`, not chars/4** (input_tokens/output_tokens are authoritative including system + cache effects).
- **Provider exception wrapping at boundary:** `_call_anthropic` catches every exception, logs `exc_info=True`, raises typed `AnthropicCallFailed`. Router translates to 502 with locked detail; underlying provider error NOT leaked to client (could contain API key fragments).
- **Trigger-kind literal: single source of truth.** `SessionCompactTriggerLiteral` in `schemas/session.py`; runtime defensive check uses `SessionCompactTrigger.ALL` from `constants.py`.
- **Archive ordinal: max(existing)+1, NOT len(existing)+1** (handles gaps from hand-deletion).
- **`ANTHROPIC_API_KEY` not provisioned today** — 503 path is realistic live state. Provisioning is separate slice (Mode B / Step 2 prep).
- 5 new source-text-locked detail strings (404/400 closed/409 already-compacting/503 no-key/502 API-failed).

**Audit follow-ups (after CTX-1 close, reconciling doc spec):**
- **#722 — migration 0009 + 4 ceilings extended on sessions.** Doc spec'd 4-bucket budget (system prompt ~2k + session.md ~28k + card_detail ~6k + output_budget ~4k = ~40k); CTX-1 modeled only 2. #722 added `card_detail_ceiling_tokens` (default 6000) + `output_budget_tokens` (default 4000) + lifted all 4 to optional Create/Update fields with `le=1_000_000` operator-typo guard. Server_default backfills 3 pre-existing rows. Router pattern: dict-comp over non-None overrides + `**`-splat (NOT explicit `kwarg=None` — would override `server_default` to NULL).
- **#723 — migration 0010 + tasks.scheduled_at one-shot path.** T1 #706 covered cron-recurring only; user Req 1 also asked "ระบุวัน+เวลาที่จะทำ task นี้ได้" for non-recurring. **One-shot is a NEW column on regular task row, NOT a degenerate template.** `tasks.scheduled_at TIMESTAMPTZ NULL` with `is_template=false`. Templates spawn child rows; one-shots transition the existing row's `process_status` 1→2 in place. **3-layer defense-in-depth XOR** (Pydantic + router resolved-final + DB CHECK `ck_tasks_scheduled_xor_template`) — all share source-text-locked `"scheduled_at is incompatible with is_template=true (use recurrence_rule for templates)"`. **Router resolved-final placement: AFTER `assert_run_mode_for_kind`, BEFORE `assert_consent_for_run_mode`** (pure-function checks fire before DB-hitting checks). Partial index `ix_tasks_scheduled_at_pending ON tasks(scheduled_at) WHERE scheduled_at IS NOT NULL AND process_status = 1 AND status = 1` — predicate byte-identical between migration + ORM `__table_args__`.

**Reasoning:** Hybrid storage over filesystem-only — queryability matters once `sessions` rows exceed ~50. Per project × process boundary — matches Lead bootstrap unit-of-work. Soft budget — hard enforcement cascades on compact failure; aligns with "never block on observability". Haiku 4.5 — compact is summarization, not reasoning; provider abstraction deferred until OpenAI/others actually need to plug in (premature-abstraction risk).

**Cross-cutting integration:**
- Sessions are a NEW persistence layer — orthogonal to the existing 5 zones (DB / Standards / Team methodology / Project shared / Role state). Sessions live in their OWN zone (DB+filesystem hybrid, per-project-process scope).
- `_sessions/` at repo root, `.gitignore`-ed, dev-only V1. Production migration to a named Docker volume deferred until Mode B headless ships.
- Audit: `session_runs` complements `tasks_history` — `tasks_history` captures per-row OLD snapshots; `session_runs` captures per-run cost + token + status.
- T2 #707 apscheduler will eventually fire recurring tasks via Mode B headless — that's where session.md becomes load-bearing. CTX-* shipped independently to keep slices small.

**Deferred gaps (acknowledged):**
- **Selective context fetch** (file tree + relevant files + git diff) deferred until Mode B / master-agent runtime ships.
- **Session terminus mismatch with doc** — doc says session ends on clear/compact/isolate; our design: `closed` is the only terminator; compact archives + rebuilds + session continues. Deliberate doc deviation (terminate-on-compact would force fresh bootstrap per compact).

**Implications:**
- Phase 2 Backend layer COMPLETE.
- `ANTHROPIC_API_KEY` still NOT configured — POST /compact returns 503 until provisioned.
- `session.md` Compacted History is REPLACE-only post-compact; prior history preserved ONLY in `_sessions/<sid>/archive/compact_NNN.md`.
- Visibility gap: uvicorn swallows non-uvicorn INFO/WARN logs (no app-level log for 503 path; only wire access log). Follow-up: `logging.basicConfig(level=INFO)` in `src/main.py` OR `--log-config` to uvicorn.

---

## 2026-05-10 → 2026-05-11 — V3+ recurrence + task_kind + drag-drop subsystem (scope-lock + T1/T2)
**Scope:** schema / api / shared
**Kanban:** scope-lock + #706 (T1) + #707 (T2). T3 (#708) + T4 (#709) closed entries above. #710 (T5 theme) DEFERRED.
**Decision:** Lock 4 features in one round (cron-recurring tasks + task_kind + drag-drop + theme).

| # | Feature | Locked |
|---|---|---|
| 1 | Recurring tasks | **Cron string** in `recurrence_rule TEXT` + `recurrence_timezone VARCHAR(64)` (IANA TZ; cron is TZ-sensitive) + `next_fire_at TIMESTAMPTZ`. Templates flagged `is_template=true`. Children carry `spawned_from_task_id` pointing back. Fire creates NEW row, never modifies template |
| 2 | task_kind | `task_kind VARCHAR(8) NOT NULL DEFAULT 'human' CHECK (task_kind IN ('ai','human'))` |
| 3 | Drag-drop | Restricted to `task_kind=human`. AI cards' lifecycle is runner-driven; user must not override |
| 4 | Theme (light/dark/system) | **Deferred** (#710) until T1-T4 + #407 GREEN |

**Cross-cutting locks:**
- **task_kind ↔ run_mode constraint:** app-layer cross-table validator at `services/task_kind.py`: `task_kind == 'human' AND run_mode != 'manual'` → 400 with source-text-locked detail `"task_kind 'human' is incompatible with run_mode '<r>'"`. Fires on POST + PATCH against RESOLVED final values (mirrors `services/run_mode.py` consent pattern). Implication: human-kind cards guaranteed `run_mode=manual`; drag-drop's `task_kind === 'human'` is sufficient — no need to also check `run_mode`.
- **Scheduler runtime:** FastAPI background task + apscheduler `AsyncIOScheduler` in lifespan. NOT separate worker; NOT pg_cron. Single instance per api container; horizontal scale needs future Redis/pg-advisory lock. 60s default tick (`APP_SCHEDULER_TICK_SECONDS`). Same scheduler will host #481 Mode B auto-headless.

**T1 #706 — migration 0007 + ORM/Pydantic/router/service:**
- **Validator-firing order pinned:** POST + PATCH call `assert_run_mode_for_kind` (pure function) BEFORE `assert_consent_for_run_mode` (DB read). Cheaper check first.
- **PATCH resolved-final cross-validator** mirrors `services/run_mode.py` consent pattern: `payload.field if 'field' in updates else task.field`. Asymmetric drift (PATCH only `task_kind='human'` on existing `auto_pickup` row) → 400. Bundled downgrade `{task_kind:'human', run_mode:'manual'}` → 200.
- **Two-key PATCH rejection pattern:** `parent_task_id` (#238) and `spawned_from_task_id` (#706) both use `model_fields_set` membership — explicit-null treated identically to non-null. V1 forbids re-parenting any lineage column.
- **`spawned_from_task_id` settable on POST, rejected on PATCH** (T2 scheduler calls POST to spawn children; FK ON DELETE SET NULL).
- **Adjacency-list pattern hardening:** with 2nd self-FK (`spawned_from_task_id`), ORM relationships now require `foreign_keys=lambda: [Task.parent_task_id]` (lambda required, not bare class ref — class not fully defined at relationship-declaration time).
- **`croniter>=2.0,<7.0`** added; image rebuild required before applying T1 migration.
- **Datetime serialization:** Pydantic v2 normalizes `+00:00` → `Z` on serialize. FE round-trip comparisons must use `Date.parse()`, not string `===`.

**T2 #707 — apscheduler 2-path scheduler:**
- **Scope extended for #723: 2-path tick.** Each `tick_once` runs BOTH in two independent sessions:
  - **Path A (templates):** `is_template=true AND next_fire_at <= now()` → spawn child + advance `next_fire_at` from `now()` (single-fire-on-resume catch-up — overdue daily template spawns ONE child + advances to next future slot, NOT N children).
  - **Path B (one-shots):** `scheduled_at <= now() AND process_status=1 AND is_template=false` → transition in place (ps 1→2, stamp `started_at`, clear `scheduled_at` to NULL per #723).
  - Path A failure does NOT roll back Path B (separate sessions). Per-row try/except + `logger.exception` + `db.rollback()`.
- **Lifespan integration:** `@asynccontextmanager` (NOT deprecated `@app.on_event`). `AsyncIOScheduler(timezone="UTC")` with `max_instances=1, coalesce=True`. Job id `"recurrence_tick"`. `APP_SCHEDULER_DISABLE=true` env knob for pytest.
- **Audit trail through ORM commits.** Both paths write via attribute assignment + `commit()` — fires same `tasks_audit_trg AFTER UPDATE OR DELETE`. Child INSERT in Path A does NOT generate `tasks_history` row (trigger is UPDATE/DELETE only); template's `next_fire_at` UPDATE IS audited. Path B's row transition IS audited.
- **PATCH recompute** — changing `recurrence_rule` (with or without `recurrence_timezone`) re-computes `next_fire_at` from now. Changing only `recurrence_timezone` ALSO recomputes. Honors explicit `next_fire_at` in same payload (does NOT override).
- **`POST /api/tasks/{id}/fire-now`** — manual trigger, bypasses `next_fire_at <= now()`. Locked 400 detail `"Task id=<n> is not a template; fire-now only applies to is_template=true"`. X-Project-Id header gate (#695).
- **Server-side default for missing `next_fire_at` on POST: REJECTED.** Keep T1's strict 422 (`_check_template_completeness`). Auto-fill would silently weaken contract.
- **Visibility gap (known):** uvicorn swallows non-uvicorn INFO logs. Scheduler liveness IS provable via DB query-pair tick observation, but ops-level visibility broken. Fix via `logging.basicConfig(level=INFO)` or `--log-config`.
- **apscheduler 3.11.2** baked into image (pyproject pin `>=3.10,<4.0`).

**Reasoning:** Cron string over RRULE/simple-enum — best balance of expressiveness vs Pydantic-validatable string + library availability. FastAPI bg task over separate worker — ops simplicity; agent-teams is single-process. Constrained kind/run_mode over independent — keeps existing run_mode wire contract stable; drag-drop's enable predicate becomes simple `task_kind === 'human'` check.

**Implications:**
- pytest 124 → 280 across T1+T2 + #722 + #723.
- Scheduler LIVE on `docker compose up` with default 60s tick.
- `tasks` now has 16 user-facing columns + lifecycle/audit. `scheduled_at` joins 5 recurrence template fields.
- T1 dev-reviewer MINs filed as #714 (TaskUpdate template-completeness validator; Literal type narrowing on services; explicit-null on `recurrence_timezone`). None blocking.
- **Operational note:** scratch DB `agent_teams_scratch` left on dev PG after dev-devops round-trip (block-raw-sql-dml.ps1 hook correctly denied `DROP DATABASE`). Cleanup is human-only — #715 filed for manual step. Scratch-DB lifecycle (CREATE + DROP) is propose-only for subagents.

---

## 2026-05-10 — Phase 3 V2 read-only Kanban board landed (#406) + V2 visual baseline locked
**Scope:** frontend / shared
**Decision:** First UI surface on top of Phase 3 scaffold. Conventions locked at project layer:

**#406 Read-only board (Server Component):**
- **API base URL split** in `web/lib/api.ts`: `BROWSER_API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8456'`; `SERVER_API_URL = process.env.INTERNAL_API_URL ?? BROWSER_API_URL`. Runtime selection via `typeof window === 'undefined'`. dev-devops sets `INTERNAL_API_URL=http://api:8456` on `web` service so SSR fetches stay on compose network (Linux compose can't DNS-resolve `localhost:8456` from inside a container).
- **`cache: 'no-store'` on every V2 fetch.** Revalidation strategy deferred to V3+.
- **Within-column ordering:** priority desc, then id asc. No `display_order` column.
- **Project name source:** `process.env.NEXT_PUBLIC_PROJECT_NAME` (default `'agent-teams'`). Single-project board for V2; project switcher is V3 (#407).
- **`<RunModeBadge>` and `<ProjectConsentBanner>`** reused unchanged from #484/#481-C.
- **Defensive detail-string extraction** in `jsonFetch`: `await response.json().catch(() => ({}))` tolerates non-JSON error bodies.
- **NIT fixed in close-out:** magic-string `"auto_headless"` → `TaskRunMode.AUTO_HEADLESS` (lockstep guard at `schemas/task.py` only catches Python ↔ wire drift, not TS literal drift).

**V2 visual baseline (Linear-style minimalism — user picked over bento-dark / IBM Plex editorial):**
- **Style:** hairline 1px borders, NO card shadows, NO gradients. Color reserved for state badges; chrome stays achromatic (`bg-white`/`bg-zinc-50/60`/`border-zinc-200`). Light mode only.
- **Typography:** Inter via `next/font/google` at `app/layout.tsx`. `inter.className` on `<html>`; `antialiased` on `<body>`. Self-hosted at build time; NO new deps.
- **Density tokens:** page `px-6 py-5`; column `p-2.5 rounded-md bg-zinc-50/60`; card `p-2.5 rounded-md border border-zinc-200 bg-white` with `hover:bg-zinc-50 hover:border-zinc-300`; intra-column gap `gap-1.5`; grid gap `gap-3`. Target: 5+ cards per column at 1280px without scroll.
- **Inline header pattern (no chips):** `name · team: <name> · N tasks` with `·` middle-dot `aria-hidden`.
- **Column header:** small-caps `uppercase tracking-wide text-zinc-500` + `·` + count in `tabular-nums`. Hairline `border-b border-zinc-200` divider. NO count pill.
- **Role-badge palette:** frontend = `text-blue-700 bg-blue-50`; backend/devops/qa/reviewer = `text-indigo-700 bg-indigo-50`.
- **`tabular-nums` on every numeric chrome.**
- **Empty-state convention:** em-dash `—` in `text-zinc-400 text-xs text-center py-4`.

**Reasoning:** V2 must surface #481 UI seams (run_mode badge, consent banner) from day 1 so V3 doesn't reflow. API base URL split avoids Linux-compose foot-gun (containers can't DNS-resolve `localhost`; Docker Desktop for Windows happens to route it). `cache: 'no-store'` keeps V2 stateless. Solo-developer dogfood audience values info density + scannability + calm focus — Linear/Height/Vercel chrome.

**Implications:**
- **dev-frontend agent** picked up `ui-ux-pro-max` opt-in skill section (commit `63ce0ec`).
- **Tier-1 smoke methodology:** React SSR splits adjacent static-text + interpolated-variable with HTML comment markers (`>team: <!-- -->dev<` not `>team: dev<`). Smoke probes that grep across that boundary must accept the comment-marker form or split the assertion.
- **dev-devops follow-up #704 DONE:** `INTERNAL_API_URL=http://api:8456` wired on `web` service env (with `${INTERNAL_API_URL:-http://api:8456}` fallback) + mirrored in `.env.example`.
- **Tester hook follow-up #705 DONE:** `.claude/hooks/tester-curl-allow.ps1` regex widened from literal `:8456` to `://(localhost|127\.0\.0\.1):\d+` (any port).
- **Operational note for future agents:** host VS Code TS server emits `Cannot find module 'next'` after every `Edit` because `node_modules` lives inside the `web` container, not on the Windows host. Authoritative check: `docker compose exec -T web sh -c "cd /app && npx tsc --noEmit"`.

---

## 2026-05-10 — API tidy-up: drop single-active invariant (#694 Phase 2) + `?pending=true` shortcut (#697)
**Scope:** api / db / tests / shared

**#694 Phase 2 — drop `ux_projects_active_one` + remove PATCH atomic-clear + `GET /api/projects/active` → 410:**
- **Migration `0006_drop_active_one`** drops partial unique index `ux_projects_active_one ON projects(is_active) WHERE is_active IS TRUE AND status = 1`. Session-scoped active-project model (#694 Phase 1 / #695 Phase 3) makes multi-active-row legitimate — each terminal binds to its own project. ORM `Index(...)` decl removed in same commit. Downgrade restores byte-identical predicate.
- **PATCH `/api/projects/{id}` atomic-clear removed.** `_clear_other_active` helper + both call sites (POST + PATCH) gone. N7 no-op-skip / `updated_at` bump / IntegrityError-409-translation on `ux_projects_name_active` paths untouched.
- **GET `/api/projects/active` → 410 Gone** with source-text-locked detail `"Endpoint deprecated. Use /api/projects/by-name/{name} or /api/projects?status=1 instead."` Route decorator declares `responses={410: {"description": ...}}` so deprecation is publicly discoverable in `/openapi.json` — **runtime `raise HTTPException(...)` does NOT auto-document; explicit decorator kwarg is mandatory.**
- **DELETE `/api/projects/{id}` side effect retained:** `is_active=true → false` clear inside `delete_project` survives. Rationale refreshed — not because of any unique constraint (gone), but because a soft-deleted row should not advertise itself as active in any list/by-name query.

**#697 `?pending=true` on `GET /api/tasks`:**
- `pending: bool = Query(default=False)`. When `pending=true`, filters `WHERE process_status != TaskStatus.DONE` (IN (1,2,3,4)).
- **When BOTH `pending=true` AND `process_status=N` provided: explicit `process_status` wins** via control-flow `elif` (NOT boolean arithmetic). Future drift to `if pending:` would silently re-enable false-positive; precedence test (`test_list_tasks_pending_and_process_status_explicit_wins`) seeds BOTH ps=5 AND ps≠5 rows and asserts `?pending=true&process_status=5` → exactly 1 ps=5 row.
- Uses named constant `Task.process_status != TaskStatus.DONE` (NOT bare literal `5`).
- **Out of scope:** multi-value `process_status` (kept as int), name-based filter (todo/in_progress/...), any change to `process_status` semantics.

**Reasoning:** Single-active invariant was load-bearing on the pre-session bootstrap model; keeping it would silently fail PATCH `is_active=true` on a second project. Keeping atomic-clear would silently STOMP the first session's active flag. 410 (over redirect or 404) keeps deprecation visible — silent fallback would mask migration of every existing client. `?pending=true` shortcut eliminates Lead's "list pending tasks" Python-fallback (~3 prompts/session saved).

**Implications:** Multiple rows may carry `is_active=true` simultaneously. Frontend consumes `/api/projects?status=1` for live-projects list; bootstrap clients use `/api/projects/by-name/{name}`. Lead bootstrap uses `curl -H "X-Project-Id: <id>" "/api/tasks?pending=true"`. **Convention propagated:** future convenience-shortcut bool params on list endpoints MUST cede precedence to the more-specific explicit param via control-flow `elif`, not boolean arithmetic.

---

## 2026-05-09 — `tasks.run_mode` + grant-consent endpoint + cross-table validator (#481-B / #483 closed)
**Scope:** api / tests / shared
**Decision:** Wired migration 0005's `run_mode` + `auto_run_consent_at` through the stack. Cross-team-applicable methodology framing lives in `context/teams/dev/decisions.md` 2026-05-09 'Kanban-driven AI: 2-mode model + per-project consent gate'.
- **Constraint name** mirrored: `ck_tasks_run_mode_valid` in migration AND ORM `CheckConstraint` (lockstep pattern from `_PROJECT_TEAM_ALL` / `ck_projects_team_valid`).
- **`POST /api/projects/{id}/grant-consent`** — body `{"confirm_name": "<name>"}` with Pydantic `extra="forbid"` (NOT default `extra="ignore"`). A typed-acknowledgment endpoint MUST fail loud on smuggled fields. 400 on mismatch with source-text-locked detail `"confirm_name must match project name exactly"`. 404 on missing OR soft-deleted project. 422 on extra fields.
- **Idempotent re-grant:** read `project.auto_run_consent_at` and short-circuit BEFORE assigning `func.now()` if non-null. First consent is the auditable timestamp; re-grant must not bump `auto_run_consent_at` OR `updated_at`.
- **Cross-table validator location:** `src/services/run_mode.py::assert_consent_for_run_mode(db, project_id, run_mode)` — service-layer helper, NOT a DB CHECK (spans tables). Reads only `Project.auto_run_consent_at` with `Project.status == ACTIVE`.
- **PATCH resolved-final-mode rule:** validator fires on RESOLVED final `run_mode` — `payload.run_mode if "run_mode" in updates else task.run_mode`. Downgrade `auto_headless → manual` always succeeds. PATCH on `auto_headless` row when consent gone fails (forces operator to downgrade first OR re-grant).
- **Lockstep guard:** `TaskRunModeLiteral` ↔ `TaskRunMode.ALL` import-time guard at bottom of `schemas/task.py`. Uses `RuntimeError` (not `assert` — survives `python -O`). Drift test in `tests/test_run_mode_consent.py` monkeypatches `TaskRunMode.ALL` → reloads schemas → asserts RuntimeError.
- **Source-text-locks:** 2 new lock tests pin (a) `"confirm_name must match project name exactly"`, (b) consent-required template `"project {project_id} has not granted auto-headless consent"`.
- **MINOR-1 follow-up (filed):** when POST `/api/tasks` carries `run_mode='auto_headless'` AND `project_id` references missing/soft-deleted project, consent error masks FK error. Wire-contract drift, not a bug — acceptable to ship as-is.

**Reasoning:** Idempotent-re-grant rule was specced in team-methodology but implementation needed short-circuit before `func.now()`. PATCH resolved-mode rule prevents PATCH-other-fields-on-headless-task from slipping past. 404-on-soft-deleted consistent with `get_or_404 status=ACTIVE` pattern.

**Implications:** Frontend (#484) types: `run_mode: "manual"|"auto_pickup"|"auto_headless"` (Literal, default `"manual"`), `auto_run_consent_at: string | null`. Grant-consent body `{confirm_name: string}` with `extra="forbid"`. **Advisory pre-existing items observed during Tier-1:** (a) `GET /api/projects/{id}` direct-by-id route returns 405 (clients must use `/api/projects/by-name/{name}` or `?...`). (b) POST `/api/projects` body uses nested `paths:{web,api,db}` + nested `stack:{...}`; PATCH uses flat `paths_web`/`paths_api`/`paths_db` (asymmetry vs PATCH — by design).

---

## 2026-05-09 — Test-database isolation (`agent_teams_test`) — Issue 2 of raw-SQL-DML incident response
**Scope:** api / tests / dev tooling
**Decision:** Tests run against per-pytest-session ephemeral database named `agent_teams_test`. Lifecycle: (1) `tests/conftest.py` sets `DATABASE_URL` at module top — BEFORE any `from src import …` (because `src.db.engine` is built from `get_settings().database_url` at import time). (2) Session-scoped `autouse` fixture `_setup_test_database` connects to maintenance `postgres` DB, runs defensive `pg_terminate_backend`, drops + creates `agent_teams_test`, runs `alembic upgrade head` (subprocess so sync alembic API stays out of async event loop), runs `scripts.seed._seed()`, disposes engine, yields. (3) Teardown drops the test DB. (4) Tests MAY leave data within the test DB during the session (no transaction-rollback wrapper). All 4 pre-existing fixtures preserved. Two contract tests at `tests/test_db_isolation.py` pin the invariant — `engine.url` must contain `agent_teams_test`, AND round-trip via `SessionLocal` must report `current_database() = 'agent_teams_test'`.

**Reasoning:** 2026-05-09 audit found live `agent_teams` DB had grown to **636 tasks (32 active + 604 soft-deleted) + 510 projects (39 active + 471 soft-deleted)** — pytest had been writing every run for ~2 days. End-to-end real-system verification (audit triggers fire, soft-delete partial-unique exercised, FK cascade covered) outweighs intra-session test data leftover (user explicitly accepted: "มี test data ได้เลยไม่ติดปัญหา จะได้รู้ว่ามันทำงานเข้าระบบได้ถูกต้องจริงๆ ด้วย"). Per-test transaction rollback rejected — audit triggers fire-then-roll-back would silently break tests asserting on `tasks_history` row counts. Truncate-per-session rejected — doesn't isolate parallel pytest invocations.

**Implications:** pytest no longer touches live DB. Issue 3 (cleanup of 604 + 471 soft-deleted live rows from prior runs) is one-time human-only work per raw-SQL-DML hard rule. Hook `.claude/hooks/block-raw-sql-dml.ps1` does NOT fire on fixture's CREATE/DROP DATABASE — those go through async SQLAlchemy `text()`, not Bash `psql -c` (different tool boundary).

---

## 2026-05-09 — Rename `projects.lead` → `projects.team` (Phase 2.5b1)
**Scope:** db / backend / frontend / shared
**Decision:** Rename DB column `projects.lead` → `projects.team`, Python class `ProjectLead` → `ProjectTeam`, Pydantic Literal `LeadCode` → `TeamCode`, scaffold constant `LEAD_ROSTERS` → `TEAM_ROSTERS`. Member values `'dev'`/`'novel'` unchanged. Migration `0004_rename_lead_to_team`: pure DDL — drop `ck_projects_lead_valid` → ALTER COLUMN RENAME → create `ck_projects_team_valid`. Web mirror `web/lib/constants.ts` renamed in lockstep. POST with old `lead` key 422 (no alias mapping — Pydantic `extra='ignore'` silently drops `lead` then required-`team` triggers). The orchestrator persona "Lead" (capital-L = meta-orchestrator) and role-tag persona `'lead'` in templates remain unchanged — they are NOT the column.

**Reasoning:** "lead" was overloaded — same word for column value AND orchestrator persona. After Bucket-4 split, the column actually selects **which team of agents** the project gets — `project.team == 'dev'` reads cleanly. Repo name `agent-teams` aligns. User explicitly weighted "accumulate effort during operations > upfront effort".

**Implications:** API contract change: POST request key + `ProjectRead` field key both renamed. Phase 2.5b2 will rename `.claude/leads/` → `.claude/teams/` + `context/leads/` → `context/teams/`. Dogfood-pollution lesson now reinforced 3x (smoke-checklist Phase 2, decisions.md Phase 2.5a, this rename Phase 2.5b1).

---

## 2026-05-08 — Subtask hierarchy on `tasks` (parent_task_id + API support) — Kanban #238 closed
**Historical context (added 2026-05-08 after archaeology):** Requirement was given by user on 2026-05-04 21:19 in a 5-point design message ("Schema: task ให้มี parent ด้วยเพื่อทำ work break down เป็น sub task ได้"). Initial migration `0001_initial_schema` shipped WITHOUT `parent_task_id` — requirement vanished from durable artifacts (no decisions.md entry, no Kanban task, no schema column). Re-surfaced 4 days + 11 commits later when Phase 3 needed subtask split of #3. Caught + fixed here. Lesson codified at `context/standards/general.md` "Multi-point user requirements MUST be propagated point-by-point" + memory entry `feedback_multi_point_requirements.md`.

**Scope:** api / db / shared
**Decision:** Adds `tasks.parent_task_id BIGINT NULL` self-referential FK with full app-layer validation. Migration `0003`: `add_column` + `ON DELETE CASCADE` FK `fk_tasks_parent_task_id` + CHECK `ck_tasks_parent_task_id_not_self` (`parent_task_id IS NULL OR parent_task_id <> id`) + index. ORM uses canonical adjacency-list pattern with string `remote_side="Task.id"` (survives circular-ref import order). Pydantic: `TaskCreate.parent_task_id: int | None = Field(default=None, ge=1)`; `TaskRead.parent_task_id` exposed; **`TaskUpdate.parent_task_id` REJECTED** via `@model_validator(mode='after')` checking `if "parent_task_id" in self.model_fields_set` — explicit-null and explicit-int both 422. Router: POST validates parent existence + `parent.status=ACTIVE` + `parent.project_id == payload.project_id` (locked 400 details `parent_task_id <n> does not exist or is deleted` and `parent_task_id <n> belongs to a different project`). DELETE blocks 409 with locked detail `Cannot delete task — <n> active subtask(s) reference this task` AFTER the idempotent re-DELETE early-return. GET adds `?parent_task_id=N` + `?top_level_only=true` — when both provided, `top_level_only` wins, `parent_task_id` silently ignored.

**Reasoning:**
- **Soft-delete parent with active children → 409 (block, not cascade-soft-delete).** Cascade on 50-child umbrella is too easy to invoke by accident.
- **Same-project enforced at app layer** — composite FK across (project_id, id) would be DB-cleaner but adds two-column FK complexity for a 3-line Python check at the only entry point.
- **Re-parenting NOT allowed in V1** — introduces ordering/cycle questions not worth solving until Phase 3 UI demands. `model_validator` REJECT-BY-PRESENCE pattern (vs `extra='ignore'` silent-drop) required so silent client bugs surface as 422.
- **FK `ON DELETE CASCADE`** — app never hard-deletes (only soft-delete via `status=0`), so CASCADE never fires from app path. Defense-in-depth backstop for raw-SQL drift.
- **No status rollup** — UX may compute derived "umbrella status" on display; baking into DB couples write paths to TBD UX policy.

**Implications:** Phase 3 UI can now create true parent/child task relationships. **Standards-propagation:** (a) codify the Pydantic `model_validator` REJECT-BY-PRESENCE pattern at `pydantic/v2-conventions.md` Settings/Update section — `extra='ignore'` silent-drops + `if x is not None` misses explicit-null + only `model_fields_set` correctly differentiates "not provided" from "provided as anything"; (b) codify SQLAlchemy adjacency-list with string `remote_side` at `sqlalchemy/orm.md`; (c) extend `general.md` Testing — Update-schema-REJECT pattern tests MUST cover BOTH `{field: value}` AND `{field: null}` cases (single-case is the Kanban #76 vacuous-assertion class). **N4 deferred:** `_check_role` validator hardcodes `TaskRole.ALL` (dev roster 1..5) without `lead='novel'` awareness — Phase 3 follow-up.

---

## 2026-05-08 — Phase 3 web/ scaffold landed (scaffold-only) — Kanban #3
**Scope:** frontend / devops / shared
**Decision:** `web/` directory at repo root with minimal Next.js 14 (App Router) + TypeScript (strict) + Tailwind v3 bones. 13 files. Key choices:
- App Router over Pages Router (Next 14 default + matches `projects.stack_web`).
- Tailwind v3 (stable) over v4 (alpha).
- Path alias `@/* → ./*`.
- `lib/constants.ts` mirrors `api/src/constants.py` (`RecordStatus`/`TaskStatus`/`TaskPriority`/`TaskRole`/`ProjectLead`) — `as const` + literal types. `TaskHistoryOperation` deferred (internal audit-trigger payload, no browser-facing use).
- `Dockerfile` single-stage dev on `node:20-alpine`; `next dev -p 3000`.
- `docker-compose.yml` `web` service: `depends_on: api: condition: service_healthy`, `NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8456}` (host-resolvable default — browser runs on host, NOT compose network). Bind-mount `./web:/app` + anonymous `/app/node_modules` (prevents host-shadowed deps). `wget` healthcheck (curl not in node:20-alpine; wget is).
- **Out of scope:** API client, board UI, project switcher, forms, prod multi-stage Dockerfile, integration tests, FE→BE cross-container probes — all V2.

**Reasoning:** Decoupling FE scaffold from first UI feature keeps each slice small. `NEXT_PUBLIC_API_URL` defaults to `http://localhost:8456` in BOTH compose and `.env.example` (earlier `http://api:8456` fallback was browser-unreachable). Bind-mount + anonymous `node_modules` is the canonical Next dev pattern (avoids Linux/Windows binary mismatch).

**Implications:** Phase 3 V2 unblocked. dev-tester Tier-1 smoke extended to web surface (POSITIVE: `curl http://localhost:3000` 200 + body marker; NEGATIVE: unknown route → 404; container `Health=healthy`). #7 (per-project agent roster) remains gated on V2 UI lifecycle.

---

## 2026-05-08 — Backfill #81 + 4 follow-ups closed (#120 + #121 + #122 + #123) + #76 inception
**Scope:** api / db / tests / shared / dev tooling
**Decision:** Discipline-rollout chain (#78 → #79 → #80 → #81) validation. First Tier-2 release-wrap-up DRY-RUN against branch HEAD caught two BLOCKER-class bugs in `routers/tasks.py` that escaped original Kanban #76 fix bundle, plus 2 SECURITY-WARN + SECURITY-NIT bundle.

**#76 (inception, parity bug from cleanup-pass deploy verify):** `routers/projects.py update_project` and `delete_project` (real-write branch only) now explicitly set `project.updated_at = func.now()` — `server_default=func.now()` fires only on INSERT. PATCH adopts N7-style no-op-write skip (`isinstance(value, ClauseElement) or getattr(project, field) != value` guard with `changed` flag) so identical-body PATCHes do NOT bump `updated_at`. DELETE idempotent early-return path untouched. **#79 discipline inception:** every BLOCKER/MAJOR fix demonstrably fails on pre-fix code; M9 was vacuous (asserted `updated_at_after == updated_at_before` after first DELETE — equality held trivially because no DELETE bumped `updated_at`).

**#120 — tasks router updated_at parity (sibling of #76):** Mirror of `routers/projects.py:174-183` and `:225-226` in `routers/tasks.py update_task` (lines 121-130) + `delete_task` (lines 168-171). **#81 caught it:** original #76 fix only patched projects.py; matching defect in tasks.py survived 4 commits because no test asserted the bug-class. Closes the post-#76 propagation gap. **Standards-propagation:** "near-clone audit" review heuristic — when fixing a bug in one of two near-clone modules, search for symmetric pattern in the sibling and either fix in same commit or file explicit follow-up.

**#121 — projects.name path-traversal hardening (SECURITY-WARN W1):** Two-layer defense against path traversal via user-controlled `name` field that flows into `Path(repo_root) / "context" / "projects" / project_name`.
- **Layer 1 (boundary):** `ProjectCreate.name` + `ProjectUpdate.name` gain anchored `pattern=r"^[a-zA-Z0-9_-]{1,64}$"` → 422 `string_pattern_mismatch`.
- **Layer 2 (defense-in-depth):** `scaffold_project_folder` adds forbidden-token short-circuit (`/`, `\`, `..`, `\x00`) BEFORE `Path()` construction + `base.resolve().is_relative_to(projects_root.resolve())` check BEFORE `mkdir`. Both layer-2 guards `return False` (never raise) per existing scaffold contract.
- **Anchored regex (`^...$`) non-negotiable** — unanchored would accept `"../evil_anything_long_enough_to_match"` somewhere in the string.

**#122 — POST /api/tasks 400 detail-string hygiene (SECURITY-WARN W2):** `create_task` now wraps `await session.commit()` in `try/except IntegrityError` with 5-branch constraint-name → stable-detail ladder: `tasks_project_id_fkey` → `f"project_id {payload.project_id} does not exist"`; 3 CHECKs → `"<col> violates <constraint>"`; fallback → `"Task creation violates a database constraint"`. Mirror of M5 pattern from `update_task` modulo extra FK branch. **The leaky `detail=str(exc.orig)` shape is fully gone** — grep confirms no raw asyncpg text reaches `HTTPException(detail=...)` anywhere in `routers/tasks.py`. CHECK branches (3 of 5) are unreachable via HTTP today (`TaskCreate` rejects at 422 first) — defense-in-depth for raw-SQL bypass / future schema drift. FK branch IS reachable (Pydantic accepts any positive int as `project_id`); wire-level test mandatory.

**#123 — SECURITY-NIT bundle (4 items + 1 sub-fix):** (1) **APP_DEBUG fail-CLOSED:** default `True → False` in `settings.py`; `.env.example` keeps `APP_DEBUG=true` for dev convenience. (2) **REPO_ROOT required:** `_DEFAULT_REPO_ROOT` constant removed; `Field(alias="REPO_ROOT")` raises `ValidationError` at startup if unset; `docker-compose.yml` already sets `REPO_ROOT: /repo`. (3) **CVE pytest CVE-2025-71176:** bumped `pytest>=9.0.3,<10.0`. (4) **pip-audit declared dev dep:** `pip-audit>=2.7,<3.0`. (5) **Sub-fix:** `pytest-asyncio>=0.24,<2.0` (0.23.x calls `collector.obj` on `Package` collectors which pytest 9 removed).

**Reasoning:** Discipline rollout was theoretical until tested. #81 was validation: did new workflow actually catch bugs conventional review would have missed? **Yes.** Original #76 only patched projects.py; matching bug in tasks.py survived because no step in old workflow probed the live tasks API the way Tier-1 demands. Same for W1 (would have shipped to Phase 4 unflagged under correctness-only review).

**Implications:** Phase 3 (Kanban UI scaffold, #3) UNBLOCKED end-to-end after #120-#123. **Pattern matured (3 sites: `update_project` 409, `update_task` 400 M5, `create_task` 400 M122) — lift to standard:** IntegrityError-translation 5-step at `fastapi/error-handling.md` (rollback first → capture orig_text → translate well-known constraint names → fallback → never `detail=str(exc.orig)`). **Standards-propagation queue:** (a) paired-tuple source-text-lock idiom at `general.md` Testing — assert constraint name AND detail string per branch (single-case is the #76 vacuous-assertion class); (b) two-layer path-traversal defense at `python/path-handling.md`; (c) uuid-suffix-per-case + pre-clean idiom for FS-mutating regression tests; (d) fail-CLOSED defaults for security-adjacent booleans; (e) Pydantic-required for I/O paths; (f) security audit tooling as declared dev dep. Future Tier-2 wrap-ups: re-run pip-audit on non-transient install, fire `/security-review`, run matrix on release tag.

---

## 2026-05-08 — Cleanup pass on post-rename / post-soft-delete debt (no schema changes)
**Scope:** api / shared / root
**Decision:** Pure debt-cleanup pass. Eleven files touched, zero schema or contract changes. (a) Root + meta playbook: `.claude/leads/dev.md` step-7 PATCH example + `README.md` Example-2 use `process_status=N` (lifecycle) instead of pre-rename `status=N`; Lead-step-7 line gained explicit "`status` is the soft-delete flag — do not PATCH it for lifecycle" reminder. (b) `api/src/constants.py` module docstring renamed `tasks.status` → `tasks.process_status` (class name `TaskStatus` preserved per prior decision). (c) `api/scripts/seed.py` `paths_db` corrected from non-existent `api/migrations/` to `api/alembic/versions/`. (d) Scaffold templates `db-schema.md` + `api-contracts.md` realigned to current locked decisions: `id BIGINT GENERATED BY DEFAULT AS IDENTITY` + `status SMALLINT … 1=active/0=deleted`. (e) Migration `2026_05_08_0300_soft_delete_and_lead.py` rename `_TASK_STATUS_ALL → _TASK_PROCESS_STATUS_ALL`. (f) `api/src/routers/projects.py` aliased `from fastapi import status as http_status` to mirror `tasks.py` (avoid shadowing `RecordStatus`). (g) `api/src/routers/tasks.py` got 2 terse comments — one above M5 400-detail-string chain pointing at lock test; one sharpening `isinstance(value, ClauseElement)` guard. (h) `api/tests/test_in_clause.py` literal column-name `"status"` → `"process_status"`. Plus harness: `Agent(*)` added to `.claude/settings.json` allowlist.

**Reasoning:** 3 large requirement changes (lifecycle column rename, soft-delete adoption, multi-domain lead bundle) shipped over 2 days and left scattered cosmetic / docstring / variable-name drift. Scaffold-templates fix (item d) is the only one with downstream-user impact: every NEW project starts with shared docs that match locked decisions.

**Implications:** Future Lead PATCH on `/api/tasks/{id}` for lifecycle MUST use `process_status` — Pydantic `extra='ignore'` would silently drop stray `status`, and soft-delete `tasks.status` column rejects values outside {0,1}. Standards-file drift remains (humans-only writers): multiple files still reference old column name — surfaced to user as standards-propagation queue.

---

## 2026-05-08 — Multi-domain `lead` column + soft-delete migration bundled (`0002_soft_delete_and_lead`)
**Scope:** db / backend / shared
**Decision:** Single Alembic migration `0002` lands three coupled changes atomically:
- **(a) Soft-delete (decided 2026-05-05):** rename `tasks.status → tasks.process_status`; add `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0,1))` to `projects` + `tasks`; partial unique on `projects.name` gated on `status=1`; tighten `ux_projects_active_one` to `WHERE is_active IS TRUE AND status=1`.
- **(b) Lead column:** `projects.lead TEXT NOT NULL DEFAULT 'dev' CHECK (lead IN ('dev','novel'))`. *(Note: renamed to `team` in Phase 2.5b1; see 2026-05-09 entry.)*
- **(c) Dropped `ck_tasks_assigned_role_valid`** — app-layer validates per active project's lead roster (dynamic; can't express as single static CHECK across all leads).
- Two leads seed multi-domain pattern: dev (1..5 roles), novel (11..12 roles). Scaffold service dispatches on `project.lead` to pick role-folder names.

**Reasoning:** All three changes touch same migration touchpoints; app rename has to flip on same deploy as column rename — splitting invites schema-mismatch window. Per-lead roster validation is dynamic.

**Implications:** `DELETE /api/projects/{id}` and `DELETE /api/tasks/{id}` are now public verbs (204; flip `status=0` internally; project DELETE also clears `is_active` if true). List endpoints default-filter `WHERE status=1` with opt-in `?include_deleted=true` (debug; intentionally NOT in api-contracts.md). Detail endpoints return rows regardless of soft-delete status. PATCH does NOT accept soft-delete `status` flag — `TaskUpdate`/`ProjectUpdate` omit field; unknown fields silently ignored (`extra='ignore'` made explicit via `model_config`); locked by `test_patch_task_silently_ignores_soft_delete_status_field`. Lifecycle query param renamed `?status=1..5 → ?process_status=1..5`. POST `/api/projects` requires `lead` (422 if missing/unknown). Seeded `agent-teams` row inherits `lead='dev'` via DEFAULT backfill. **M5 — PATCH `/api/tasks/{id}` 400 detail strings translate well-known CHECK constraint names to stable wire text;** HTTP path is gated by Pydantic 422 first, so 400 branches reachable today only via raw-SQL bypass / future schema drift. **M9 — re-DELETE on already-soft-deleted row is no-op write (skipped) so `tasks_history` doesn't grow on idempotent DELETEs.**

---

## 2026-05-05 — Soft delete via uniform `status` flag (no hard DELETE in app code)
**Scope:** db / shared
**Decision:** Every business table carries `status SMALLINT NOT NULL DEFAULT 1 CHECK (status IN (0, 1))` (1=active, 0=deleted). Application code never issues SQL DELETE — "delete" endpoints flip the flag. To keep column name uniform across tables, existing 1-5 lifecycle column on `tasks` renamed `tasks.status → tasks.process_status` (codes unchanged); new `tasks.status` carries 0/1 like every other table. `tasks_history` exempt (audit append-only).

**Reasoning:** User policy — never lose business data. Audit trigger snapshots flag flip as `'U'`, so soft deletes remain traceable. Renaming lifecycle column rather than picking different soft-delete name avoids "different soft-delete column per table" sprawl. **Reverses the earlier "Soft delete: no" line in db-schema.md Conventions.**

**Implications:** Every list endpoint defaults `WHERE status=1`; opt-in `?include_deleted=true`. DELETE endpoints become PATCH `{"status": 0}`. **Hard DELETE reserved for manual psql cleanup** — human-only per raw-SQL-DML hard rule (see incident 2026-05-09); subagents propose, user executes.

---

## 2026-05-04 — Foundational backend decisions (initial schema + patterns)
**Scope:** backend / db / shared

- **Auto-scaffold folder on POST /api/projects:** commits DB row first, then runs `scaffold_project_folder()` which creates `context/projects/<name>/{shared,frontend,backend,devops,qa,reviewer}/`, copies 3 shared templates from `api/src/templates/project_shared/`, drops `.gitkeep` in role folders. Idempotent. Scaffold failure logged but does NOT roll back DB row (DB is source of truth — folder gaps repairable manually; row stuck "created but rolled back" is worse).

- **Integer codes (not enums) for status / priority / assigned_role:** `tasks.status`, `tasks.priority`, `tasks.assigned_role` are INTEGER columns with CHECK constraints; canonical names in `src/constants.py` (Python) + `web/lib/constants.ts` (TypeScript). No PG enum types (painful to extend — require schema migration to ADD VALUE; cannot remove). Integer + CHECK is trivially extensible. Adding a new code requires updating `general.md` + migration + constants files in both languages in lockstep.

- **Async SQLAlchemy + asyncpg:** SQLAlchemy 2.0 async ORM with `asyncpg`; FastAPI handlers `async def`. Pairing FastAPI with sync DB I/O would block event loop. Alembic env.py uses `async_engine_from_config` + `run_sync(do_run_migrations)`. Tests use `pytest-asyncio` + `httpx.AsyncClient(transport=ASGITransport)`.

- **BigInteger autoincrement, not UUID, for primary keys:** `id BIGINT GENERATED BY DEFAULT AS IDENTITY` (SQLAlchemy `BigInteger autoincrement=True`). Single-tenant dogfood — no client-generated/sharded keys. Smaller indexes, sequential cache locality, URL-friendly ("task #42" beats "task 6f3a..."). Overrides placeholder in `db-schema.md` Conventions. All Pydantic IDs typed `int`; UI route params integers. Future distributed write → add separate `external_id uuid` column rather than swap PKs.

- **Audit trail via PG trigger (not application code):** `tasks_history` populated by AFTER UPDATE OR DELETE trigger that snapshots `to_jsonb(OLD)`. Application code never INSERTs into `tasks_history`. Captures every mutation including out-of-band edits (psql, future admin scripts). Avoids "forgot to call audit helper" bug class. `tasks_history.task_id` is intentionally NOT a FK (history rows must outlive the row they describe). Application reads of history will go through future `GET /api/tasks/{id}/history`.

- **Initial schema migration:** Single Alembic migration `2026_05_04_2130_initial_schema.py` creates `projects`, `tasks`, `tasks_history`, and PG `tasks_audit_fn()` + `tasks_audit_trg`. Bundling v1 schema in one migration keeps bootstrap atomic — easier for new clones + drop/recreate during early dev. Trigger co-located so audit invariant cannot be applied without it.

<!-- No decisions yet. First entry will be appended above this line. -->
