# Over-Engineering Review — agent-teams (2026-06-10)
Scope: api/ (40,413 LOC, 145 files), web/ (31,506 LOC, 87 components), langgraph/ (8,455 prod LOC, 11,362 test LOC)
Lens: single-operator localhost dogfood app. Complexity is justified only by real incidents, actual daily use, or intentional platform experiment.

---

## 1. Executive Verdict

The codebase is moderately over-engineered in specific pockets, not uniformly. The weight sits in three places:

**Where weight is warranted (~60% of LOC):**
- The safety layer (L16/L17/L22/L23) traces to documented incidents and red-team findings. Every line of it earned its place.
- The task lifecycle surface (`tasks.py` 3,030 LOC, 16 service deps) mirrors genuine complexity: DnD reorder, keyset pagination, HITL, budget gates, recurrence, audit, handoff — all in daily use.
- The email/calendar subsystem (4,274 LOC, Gmail + Outlook × 2 protocols) is fully exercised by the secretary workflow; the dual-client duplication is inherent to the two incompatible APIs.
- The test suite at 1.45× source LOC is a feature, not a smell, for a platform that dogfoods its own agents on itself.

**Where real over-engineering lives (~40% of problematic LOC):**
- **Payments dead zone:** 554 LOC webhook router + 193 + 127 + 222 = ~1,100 LOC (Stripe/PayPal) with zero active callers. Never configured, never tested in CI.
- **Dual pricing tables:** `cost_tracker.py::PRICING` (12 models, Anthropic+OpenAI+Ollama+Google) and `pricing.py::MODEL_PRICING` (broader vendor table including DeepSeek, GPT-4.1, o1, o3-mini, local LLM) are maintained separately, with different key shapes, different vendor naming (`"google"` vs `"gemini"`), different model identifiers. Neither imports the other. Drift is confirmed.
- **DeepSeek dead path:** fully implemented in `llm.py` (3 env vars, model factory, base_url logic) but cancelled by Kanban #1838. Still documented in the provider matrix. Zero test coverage in regression pack.
- **NewTaskModal / AiTaskModal duplication:** 889 + 811 = 1,700 LOC with confirmed near-identical field state, override logic, template wiring. Source comments acknowledge it. Slow drift is already happening (AiTaskModal field count is lower by ~26 matches on key terms).
- **5 collapse-panel re-implementations:** ChevronDown/Right SVG icons defined inline in PnlSummaryCard and PnlDashboardSection, duplicating Icon.tsx sprite path. Shared `collapseState.ts` helpers exist but the toggle+icon pattern is copy-pasted across 5 components.
- **3 mirrored safety modules with confirmed drift:** `content_safety.py` (langgraph, 164 LOC) vs `content_moderation.py` (api, 231 LOC) — GRANT/REVOKE patterns intentionally absent from langgraph copy (documented), but `approval_evaluator.py` (langgraph, 179 LOC) vs (api, 275 LOC) has an extra 96 LOC in the api copy with unknown delta.
- **`iteration_limit.py` shim:** 10-LOC re-export stub, no production caller, kept for "backward compat" with nothing.
- **`LlmSpendSection` finance-flag inconsistency:** renders unconditionally at `dashboard/page.tsx:469` while `PnlDashboardSection` is gated at line 476. Comment on `LlmSpendSection.tsx:7` incorrectly claims it lives below the gate.
- **`safeMarkdown.tsx` hand-rolled parser:** 428 LOC, no markdown library in package.json (zero third-party markdown deps). Justified only if the custom URL safety filter is required. That filter is genuinely needed (XSS risk), but the inline/block parser could be replaced by a battle-tested library with a custom renderer.
- **HITL_DEMO_ENABLED defaults to `1` in dev compose:** demo branches live in production `nodes.py` (not test fixtures), triggered by task title prefix. Structurally a test fixture in prod code.

---

## 2. Ranked Simplification Candidates

### Rank 1 — DELETE: Stripe/PayPal payments subsystem
**Files:** `api/src/routers/webhooks.py` (554), `api/src/services/webhook_verifiers.py` (193), `api/src/services/webhook_rate_limit.py` (127), `api/src/services/webhook_templates.py` (222), `api/src/schemas/` webhook schema (~80 LOC), `api/alembic/versions/` migration adding `transactions` table (read carefully before deleting)
**LOC at risk:** ~1,200 LOC of service + router + schema code
**Classification:** SPECULATIVE — built for a payment integration use-case that never materialised. Zero callers: no FE, no worker, no agent. `BACKUP_S3_BUCKET`-gated backup is analogous but at least serves the operator's personal infra need. This does not.
**Recommendation:** DELETE — router, verifiers, rate_limit, templates, webhook schemas. KEEP `transactions.py` router and model (the income/expense ledger is used by the P&L flow). FREEZE the `transactions` table migration.
**Effort:** M — need to confirm `transactions` table is not written by webhooks.py (it is: `Transaction` is written only from `webhooks.py` router; the P&L calculator reads it via ORM). If P&L/income tracking is wanted without payments, transactions can be entered manually via the API; the router is separate from the webhook router.
**Risk:** LOW — no active caller. Confirm no alembic migration depends on the webhook tables before removing.
**Payoff:** ~1,200 LOC removed, 4 service files gone, 1 router gone, test burden reduced, no maintenance when Stripe API changes.

---

### Rank 2 — MERGE: Dual pricing tables
**Files:** `api/src/services/cost_tracker.py::PRICING` (~35 LOC dict), `api/src/pricing.py::MODEL_PRICING` (~60 LOC dict)
**Classification:** DUPLICATED — two separate pricing tables with different key shapes. `cost_tracker.py` uses `(provider, model)` tuples. `pricing.py` uses nested `{vendor: {model: {input_per_M, output_per_M}}}`. Neither imports the other. The two tables are already diverged: `pricing.py` has GPT-4.1, o1, o3-mini, DeepSeek, local LLM tiers; `cost_tracker.py` does not. `pricing.py` uses `"gemini"` as vendor key; `cost_tracker.py` uses `"google"`. `resource_verify.py` uses `pricing.py::lookup_price`; `sessions.py` and `task_cost_estimator.py` use `cost_tracker.py::PRICING`.
**Recommendation:** MERGE — pick `cost_tracker.py` as the canonical table (it's the actual billing source), extend it with a `lookup_price` API matching `pricing.py`'s signature, redirect `resource_verify.py` to the merged module, delete `pricing.py`.
**Effort:** M — requires reconciling the two key schemas and adding a backward-compat lookup_price wrapper. All callers are in api/src so the change is contained.
**Risk:** MEDIUM — the rate numbers differ slightly between the tables for the same model (e.g. `pricing.py` haiku = $0.80/$4.00 vs `cost_tracker.py` haiku = $1.00/$5.00 generic alias). Must choose canonical rates before merge.
**Payoff:** ~60 LOC removed, single source of truth for pricing, eliminates silent drift for new model additions.

---

### Rank 3 — MERGE: NewTaskModal / AiTaskModal
**Files:** `web/components/NewTaskModal.tsx` (889 LOC), `web/components/AiTaskModal.tsx` (811 LOC)
**Classification:** DUPLICATED — source comments confirm it ("same shape as NewTaskModal", "same override-pair semantics"). Field count diff: AiTaskModal has ~26 fewer hits on {run_mode, milestone, priority, override, acceptance} terms vs NewTaskModal. The two are already diverging. Both use ModalShell, ActionTemplatePicker, HandoffTemplatePicker, DatePicker, MilestoneCombobox, ModelTierSelect, PauseOverrideBlock.
**Recommendation:** MERGE — extract a `useTaskForm` hook holding shared state (form fields, template loading, milestone load, pause state, AC list, override logic). Both modals become thin shells: NewTaskModal renders the form directly, AiTaskModal prepends the parse-prompt step-1 then renders the same form fields in step-2. Estimated result: ~500 LOC each instead of ~800.
**Effort:** L — the extraction is non-trivial because the two modals have different entry/exit flows (step-1 AI parse in AiTaskModal). But the payoff is preventing the divergence from widening further.
**Risk:** LOW for functionality; MEDIUM for test coverage (only NewTaskModal has tests; AiTaskModal has none, so regressions would be invisible until manually tested).
**Payoff:** ~600-800 LOC removed, single form-field maintenance surface, prevents future AC drift between the two creation paths.

---

### Rank 4 — DELETE: DeepSeek dead code path
**Files:** `langgraph/llm.py` (lines ~265-430, ~40 LOC), `langgraph/pricing.py` (DeepSeek entry), `docker-compose.yml` env comment
**Classification:** DORMANT — cancelled by Kanban #1838. Code path is fully implemented (model factory, API key resolution, base_url override, 3 env vars: `DEEPSEEK_API_KEY`, `LANGGRAPH_DEEPSEEK_MODEL`, `LANGGRAPH_DEEPSEEK_BASE_URL`). Zero test coverage in regression pack. The `pricing.py` table includes DeepSeek rates that are never reached by any active code path.
**Recommendation:** DELETE the `deepseek` branch from `resolve_provider`, `resolve_model`, `make_chat_model`. Remove the 3 env var references. Remove DeepSeek from `_SUPPORTED_PROVIDERS` literal and `ProviderName` type. Remove from pricing.py. Update provider matrix comment.
**Effort:** S — isolated to `llm.py`; callers pass `provider` string so no downstream breakage.
**Risk:** LOW — decision is documented in Kanban #1838; if re-enabled later, the git history is sufficient.
**Payoff:** ~40 LOC removed from core model factory, 3 env vars eliminated, type literal cleaned up.

---

### Rank 5 — DELETE: `iteration_limit.py` shim
**Files:** `langgraph/tools/iteration_limit.py` (10 LOC)
**Classification:** DORMANT — self-describes as a "compatibility shim" with no production importer. `grep` confirms zero production imports; only `tools/base.py` references the old location in a comment. The constant has already moved to `base.py`.
**Recommendation:** DELETE the file. Update the comment in `base.py` to remove the backward-compat note.
**Effort:** S (15 minutes).
**Risk:** NONE — no production caller.
**Payoff:** Trivial LOC, but eliminates a misleading file that implies a still-live migration path.

---

### Rank 6 — SIMPLIFY-IN-PLACE: `LlmSpendSection` finance-flag inconsistency
**Files:** `web/app/dashboard/page.tsx` (lines 41 and 469), `web/components/LlmSpendSection.tsx` (line 7 comment)
**Classification:** DUPLICATED (logic error) — `LlmSpendSection` is imported unconditionally at line 41 and rendered unconditionally at line 469. `PnlDashboardSection` IS gated at line 476. The comment on `LlmSpendSection.tsx:7` incorrectly claims it's gated. This is a confirmed bug in the feature flag logic.
**Recommendation:** Wrap `<LlmSpendSection />` in `{FINANCE_PANELS_ENABLED && ...}` OR make it permanently ungated and remove the misleading comment. The operator needs to decide: is daily spend visible regardless of the finance flag, or only when finance panels are on? If the former: delete the comment. If the latter: add the gate.
**Effort:** S (10 minutes once decision is made).
**Risk:** NONE — cosmetic at worst.
**Payoff:** Eliminates silent feature-flag divergence that will confuse future reviewers.

---

### Rank 7 — MERGE: 5 collapse-panel ChevronDown/Right duplicates
**Files:** `web/components/PnlSummaryCard.tsx` (inline `ChevronDownIcon`, `ChevronRightIcon` at lines 73–107), `web/components/PnlDashboardSection.tsx` (same pattern), `web/components/CostSummary.tsx`, `web/components/AuditorActivityPanel.tsx`, `web/components/ResourcesPanel.tsx`
**Classification:** DUPLICATED — `lib/collapseState.ts` (30 LOC) provides `readExpanded/writeExpanded` but the toggle+icon pattern is re-implemented 5 times. `Icon.tsx` sprite exists but chevrons appear to be inline SVG in at least 2 components.
**Recommendation:** Create `CollapsiblePanel` component wrapping: `readExpanded/writeExpanded`, ChevronDown/Right from the Icon sprite, a `title` + `children` slot. Replace all 5 usages. The `storageKey` and `defaultCollapsed` props already exist in callers.
**Effort:** M — 5 callsites, each slightly different (some have header actions alongside the chevron).
**Risk:** LOW — pure UI extraction, no data logic.
**Payoff:** ~100-150 LOC removed across 5 components, single collapse animation/icon to update when design changes.

---

### Rank 8 — FREEZE: `ingest.py` + email-ingest service
**Files:** `api/src/routers/ingest.py` (715 LOC), `api/src/services/email_ingest.py` (266 LOC), `api/src/schemas/email_ingest.py` (~40 LOC)
**Classification:** DORMANT in normal workflow (no FE, no worker caller). Live only if Mailgun is configured. The generic webhook ingest at `POST /api/ingest/webhook/{project_id}/{tag}` is similarly inactive.
**Recommendation:** FREEZE — do not delete (the plumbing is correct and someone may want to hook Mailgun eventually), but explicitly mark in a `# DORMANT` module docstring, exclude from future refactor passes, and do not add features here until the integration is actually configured. Do NOT freeze the router mount in `main.py` — it's harmless.
**Effort:** S — annotation only.
**Risk:** NONE.
**Payoff:** Cognitive load reduction; reviewers stop asking "is this tested?"

---

### Rank 9 — FREEZE + NOTE: `approval_evaluator.py` mirror drift
**Files:** `langgraph/approval_evaluator.py` (179 LOC), `api/src/services/approval_evaluator.py` (275 LOC)
**Classification:** DUPLICATED with confirmed divergence — api copy has 96 more LOC. The extra content in api is the `_AMOUNT_RE` regex preamble and additional predicate matching logic introduced after the initial copy. The langgraph worker runs the langgraph copy; the api router runs the api copy. They can silently diverge on policy evaluation results for the same input.
**Recommendation:** Short-term FREEZE: add a `# MIRROR OF api/src/services/approval_evaluator.py` header to both files with a note that they must be kept in sync manually. Medium-term: extract a `langgraph-common` pip-installable package with the three mirrored modules (`approval_evaluator`, `content_safety`/`content_moderation`, `agent_context_sanitizer`), installable by both containers. This eliminates drift structurally. The containers share a bind-mount at `/repo` already — a local editable install would work with no Docker rebuild.
**Effort:** FREEZE = S. Shared-package = L.
**Risk:** Drift risk is REAL and currently invisible. A policy rule added to the api copy silently doesn't fire in the worker.
**Payoff:** Eliminates a class of silent correctness bugs in the approval gate.

---

### Rank 10 — SIMPLIFY-IN-PLACE: HITL demo branches in production `nodes.py`
**Files:** `langgraph/nodes.py` lines 985–1060 (demo branches inside `general_node`, ~75 LOC)
**Classification:** SPECULATIVE structure — demo fixtures embedded in the production node. `HITL_DEMO_ENABLED` defaults to `1` in the dev compose overlay (`docker-compose.yml:275`), meaning on every dev deployment tasks with title prefix "HITL demo —" or "AUDITOR retry demo —" follow a hardcoded fake path.
**Recommendation:** SIMPLIFY-IN-PLACE — move the demo branches to a `general_node_demo.py` module that is registered only when `HITL_DEMO_ENABLED=1`, replacing the inline conditional with a node-level override at graph-build time (`_build_graph` in `graph.py` already runs at startup). This keeps prod `general_node` clean and makes the demo opt-in at the graph topology level, not buried in runtime conditionals.
**Effort:** M — requires modifying `_build_graph` to conditionally swap the node.
**Risk:** LOW — behavior unchanged; the env gate already exists.
**Payoff:** Production `general_node` becomes a clean representative path; demo branches don't bloat the hot code path or confuse future node readers.

---

### Rank 11 — SIMPLIFY-IN-PLACE: `safeMarkdown.tsx` hand-rolled parser
**Files:** `web/lib/safeMarkdown.tsx` (428 LOC)
**Classification:** EARNS-ITS-KEEP-BUT-HEAVY — the URL safety filter is legitimate (XSS risk in task comments). But the full inline + block markdown parser (428 LOC with no external dependency) is carrying more complexity than needed. `package.json` has zero markdown library deps.
**Recommendation:** SIMPLIFY-IN-PLACE — add `marked` or `micromark` (both tiny, no transitive deps) and reduce to a custom renderer/sanitizer that only overrides the link/image render to apply `isSafeLinkUrl/isSafeImgUrl`. The full parser drops from ~350 LOC to ~50 LOC of custom renderer. The `safeMarkdown.test.tsx` suite should be ported to cover the new implementation.
**Effort:** M — requires test migration.
**Risk:** MEDIUM — the custom parser's edge-case behavior (partial table support, etc.) needs to be tested against the existing test suite before switching.
**Payoff:** ~300 LOC removed, battle-tested parser handles edge cases better, future markdown feature requests (strikethrough, etc.) are free.

---

### Rank 12 — SIMPLIFY-IN-PLACE: `tasks.py` DnD reorder block
**Files:** `api/src/routers/tasks.py` lines 868–1169 (~300 LOC)
**Classification:** EARNS-ITS-KEEP-BUT-HEAVY — the DnD reorder logic (`_enforce_blocker_order`, `_materialize_null_sort_orders`, `_redensify_lane`, `reorder_task`) is in the router file rather than a service, making `tasks.py` a 3,030 LOC file where half is business logic.
**Recommendation:** SIMPLIFY-IN-PLACE — extract the DnD reorder block into `api/src/services/task_reorder.py`. The router becomes a thin dispatch layer; the service is independently testable. No behavior change.
**Effort:** M — mechanical extraction; the block has clear entry/exit points.
**Risk:** LOW — the logic is self-contained (reads/writes only the `tasks` table via passed session).
**Payoff:** `tasks.py` drops from 3,030 to ~2,730 LOC. New service is independently testable. Reduces cognitive load when debugging DnD-specific issues.

---

### Rank 13 — KEEP (FREEZE candidate): `stale_doc_curator.py` + `skill_stub_detector.py`
**Files:** `api/src/services/stale_doc_curator.py` (428 LOC), `api/src/services/skill_stub_detector.py` (398 LOC)
**Classification:** EARNS-ITS-KEEP — called from `digest.py` on every manual digest fire. Both write to `_scratch/auditor/`. They are the self-improvement machinery that generates new skill stubs and flags stale context docs. In a platform that dogfoods itself, these are load-bearing. Not a simplification candidate.
**Note:** both have path-escape guards and safety checks — do not simplify those.

---

### Rank 14 — SIMPLIFY-IN-PLACE: `os.environ` sprawl (88 raw calls, 9 Settings fields)
**Files:** `api/src/settings.py` (9 Pydantic-settings fields), 46+ `os.getenv` calls scattered across services
**Classification:** DUPLICATED (configuration pattern) — env vars for backup, health monitor, HITL nudge, operator auth, VAPID, Telegram, SMTP are read raw in each service. No central declaration, no startup validation, no schema.
**Recommendation:** SIMPLIFY-IN-PLACE — extend `Settings` with the remaining env vars (group them: backup, notifications, scheduler, operator-auth, integrations). Each service imports `get_settings()` instead of calling `os.getenv`. The existing `settings.py` pattern already works; it just wasn't applied consistently.
**Effort:** L — 46 call sites, need to audit each for default values and fail-modes.
**Risk:** LOW — refactor only, no logic change.
**Payoff:** Startup-time validation catches misconfigured deployments before they silently fail at runtime. Single config schema for `.env.example` generation.

---

## 3. Explicit KEEP List

These items look heavy but have earned their complexity. Do not relitigate.

| Item | LOC | Why it stays |
|---|---|---|
| Safety layers L16/L17/L22/L23 | ~520 across 4 modules | Each traces to a documented incident or red-team finding. Removing any one creates a real attack surface. |
| `tasks.py` lifecycle surface (minus DnD block) | ~2,700 | 16 service deps are all actively used. HITL, budget, recurrence, handoff, audit, DnD are daily-use features. |
| Email/calendar subsystem (4,274 LOC) | 4,274 | Gmail + Outlook are incompatible APIs. The dual-client duplication is inherent, not lazy. Secretary uses all of it daily. |
| `health_monitor.py` (687 LOC) | 687 | Auto-pauses unhealthy projects. Single APScheduler caller. The LOC is in the health scoring algorithm, not boilerplate. |
| `pause_switch.py` (653 LOC) | 653 | Three genuinely distinct state machines (pause/unpause, flag raise, flag resolve). Not over-abstracted — the operations have different DB side-effects. |
| `backup.py` (626 LOC) | 626 | Dormant unless `BACKUP_S3_BUCKET` set, but the age-encryption via `age` CLI is correct infra. S3 backup is a legitimate personal need. |
| `content_safety.py` vs `content_moderation.py` divergence | 164+231 | Intentional divergence documented in module docstrings. Langgraph copy deliberately excludes GRANT/REVOKE (to avoid false positives on legitimate refusal language). The divergence is by design, not drift. |
| `scenarios/regression_pack.py` (1,001 LOC) | 1,001 | S1–S6 end-to-end scenarios are the only integration tests that hit the real API+worker stack. Do not delete. |
| `worker.py` (1,479 LOC) | 1,479 | Kanban poll loop is inherently stateful. The LOC is in exception taxonomy, retry logic, HITL resume, and finalize body construction — all tested. |
| `nodes.py` auditor_node (lines 1103–1661, ~560 LOC) | 560 | Heuristic pre-filter + LLM classify + escalate/resolve is the core audit mechanism. Not over-engineered; it's a genuine two-stage classifier. |
| `tool permission_gate.py` + `sandbox.py` | 96+259 | Platform safety guarantees. Removing or simplifying either risks allowing unintended WRITE/DESTRUCTIVE tool executions. |
| `operator_auth.py` (184 LOC) | 184 | Operator-proof key gate. The 184 LOC includes HITL escalation paths and audit logging, not just a key check. Caught a real write-gate case in testing. |
| `notification_router.py` (422 LOC) with 4 adapters | 422 | ntfy, web push, Telegram, email — all four serve different operator contexts (phone, browser, terminal, digest). The fan-out design means adding a 5th adapter is trivial. |
| `WildcardSSEContext.tsx` + `useRowChangedEvents.ts` | 210+167 | Consolidated from 3 separate connections (#2111). Two channels are genuinely needed: scoped per-project (Board/Review) and wildcard (dashboard badges). |
| `BoardDndCanvas.tsx` code-split | — | Keeps dnd-kit out of the initial bundle for all non-board pages. Justified by bundle size. |

---

## 4. Suggested Execution Order (if operator approves)

### Phase 1 — Quick wins (S effort, no behavioral risk)

1. **Rank 5:** DELETE `iteration_limit.py` shim — 15 minutes, zero risk.
2. **Rank 6:** FIX `LlmSpendSection` finance-flag inconsistency — 10 minutes, clarifies intent.
3. **Rank 4:** DELETE DeepSeek dead path from `llm.py` — 30 minutes, isolated change.
4. **Rank 8:** FREEZE `ingest.py` + add module docstring — 15 minutes, documentation only.

### Phase 2 — Data integrity (M effort, high payoff per risk)

5. **Rank 2:** MERGE dual pricing tables — reconcile rates, pick canonical key schema, redirect `resource_verify.py`. Must verify pricing numbers first.
6. **Rank 9:** FIX `approval_evaluator.py` drift — add mirror header first (S); schedule shared-package extraction as a separate task.

### Phase 3 — Structural cleanup (M-L effort)

7. **Rank 1:** DELETE payments subsystem — confirm `transactions` table write path before removing; keep ledger router.
8. **Rank 7:** MERGE collapse-panel pattern into `CollapsiblePanel` component.
9. **Rank 10:** MOVE HITL demo branches out of `general_node` into conditional registration.
10. **Rank 12:** EXTRACT DnD reorder block to `task_reorder.py` service.

### Phase 4 — Large extractions (L effort, do after Phase 3 settles)

11. **Rank 3:** MERGE NewTaskModal / AiTaskModal via shared `useTaskForm` hook.
12. **Rank 11:** REPLACE `safeMarkdown.tsx` parser with library + custom renderer.
13. **Rank 14:** CONSOLIDATE `os.environ` calls into `Settings`.

---

## 5. Open Verification Questions

1. **`transactions` table write path:** Is `Transaction` written exclusively by `webhooks.py`, or does any other service write to it? If yes, deleting `webhooks.py` orphans the write path. The `pl_calculator` only reads. Need to confirm no other writer exists before deleting the webhook router.

2. **`pricing.py` vs `cost_tracker.py` canonical rates:** For the same model, the two tables show different rates (haiku: $0.80/$4 in `cost_tracker` generic alias vs $1.00/$5; GPT-4.1, o1, o3-mini are only in `pricing.py`). Which table was updated more recently? Which is the billing source of record?

3. **`approval_evaluator.py` api copy extra 96 LOC:** The api copy has `_AMOUNT_RE` and additional `_match_predicate` logic not in the langgraph copy. Are there policy rules in active projects that rely on the amount-matching logic? If so, the langgraph worker is silently skipping those rules today.

4. **`getTaskBlocks` dead call-site:** `lib/api.ts:940` is imported in `TaskDetail.tsx` but no confirmed `await getTaskBlocks(...)` call was found in scan. Is this truly dead or inside a conditional branch not reached by grep?

5. **`resource_verify.py` uses `pricing.py::lookup_price`:** After merging pricing tables, `resource_verify.py` uses the price to estimate cost of a linked resource. The current path is `pricing.lookup_price("anthropic:sonnet", "input")` style. Confirm the merged table's lookup signature is backward-compatible.

6. **`LlmSpendSection` render — intentionally ungated?** The component fetches `getDailyUsage`. Is it acceptable for this to render (and make an API call) even when `FINANCE_PANELS_ENABLED=false`? The current behavior is: yes, it always renders. If that's intentional, the comment on `LlmSpendSection.tsx:7` should be removed. If it's a bug, line 469 needs the gate.

7. **HITL_DEMO_ENABLED=1 as compose default:** Is the demo being used actively, or is it a historical artifact? If the operator never creates tasks with "HITL demo —" prefix, the branches are dead code at runtime even though the gate is open.

---

## Lead verification notes (2026-06-10, post-review spot-checks)

- **Rank 6 (LlmSpendSection gate) — WITHDRAWN, not a bug.** The component comment explicitly documents 'sibling of CostSummary, OUTSIDE the FINANCE_PANELS_ENABLED gate' - unconditional render is the designed behavior (operator wants spend always visible). Reviewer misread the comment.
- **Rank 8 (approval_evaluator mirror) — DOWNGRADED.** Lead verified both copies implement the IDENTICAL six predicate branches (text_contains/_all/_any, amount_usd_lt/_gt, options_include); the 96-LOC delta is documentation. No correctness gap today. Residual = latent drift risk: add a cheap parity unit test or extract a shared package. Low urgency.
- **Rank 10 (safeMarkdown) — RECOMMEND KEEP, contradicting the review.** The zero-dep custom renderer is a recorded security decision (#1005: avoids the HTML-sink + sanitizer-misconfig bug class; XSS audit 0 findings). Swapping to marked/micromark reintroduces dependency + sink surface for ~300 LOC saved - bad trade under this project's posture.

