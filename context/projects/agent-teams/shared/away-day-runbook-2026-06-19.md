# Away-day autonomous runbook — 2026-06-19

**Operator away all day, driving via Remote Control. Run the queue mostly autonomously; operator taps approve on permission prompts + done-flips.** Prepared 2026-06-18 evening.

## State snapshot (as of 2026-06-18 eve — RE-VERIFY at pickup, do not trust this prose)
- `web/package.json` = `next ^16 / react ^19 / eslint ^9 / vitest ^4 / @vitejs/plugin-react ^6`, `"lint":"eslint ."`. Next 14→16 upgrade **landed + committed** (`a13eea4`).
- Committed today but tasks **left open (in_progress, AC unverified)**: **#2487** (Next16), **#2489** (React-Compiler lint), **#2481/#2482** (agent UI + settings). → Batch 0 closes them.
- ms49 (`set-state-effect-remediation`) plan ready: `shared/set-state-effect-remediation-plan.md`. Tasks #2491(A) #2492(B) #2493(C) #2494(D+E). Lint baseline = **51 warnings (0 errors)** = 50 `set-state-in-effect` + 1 pre-existing `exhaustive-deps` (AcEditor).
- Working tree clean except this runbook + the ms49 plan doc.

## Operating rules (autonomous-batch discipline)
1. **commit-no-push.** NEVER push. Operator reviews + pushes when back. Branch = `dev`.
2. **Verify every batch, Lead-direct** (don't trust subagent claims): `npx tsc --noEmit` EXIT 0 · full `npm run test` (vitest, ≥375 green) · `npm run lint` warning count moved as expected. Re-read the diff before closing.
3. **pytest = operator-run only** (block-pytest hook). This queue is FE/devops = NO api pytest. Backend stretch (#2464) verifies via curl smoke only; do NOT attempt api pytest.
4. **Scoped staging only** (`zb-git-commit`): stage just the files the task touched; never `git add -A`. Keyword-scan the staged diff.
5. **Don't touch `.claude/`** (operator applies via "ii"). Don't `down -v` / DB-destructive. Don't edit settings/hooks.
6. **Per task: spawn → Lead-verify → zb-git-commit (no push) → zb-task-done (AC verdicts) → rail checkpoints** (spawn/commit/close mandatory).
7. **Sequential, not parallel** (each phase's lint/vitest gate needs a clean tree). One batch at a time.
8. **Halt + park for operator** if: vitest goes red and can't be fixed in-scope · lint count doesn't drop as expected (scope leaked) · a decision not covered here is needed · unexpected working-tree changes. Warn, don't push through.

## Queue (ordered; ~full day; stop wherever the day ends)

| # | Batch | Task(s) | Agent | Gate |
|---|---|---|---|---|
| 0 | Close done-but-open | #2487 #2489 #2481 #2482 | Lead-direct | verify objective AC → flip |
| 1 | ms49 Phase A | #2491 | dev-sr-frontend | tsc+vitest, lint −~18 |
| 2 | ms49 Phase B | #2492 | dev-sr-frontend | tsc+vitest, lint −~13 |
| 3 | ms49 Phase C | #2493 | dev-frontend | tsc+vitest, lint −~11 |
| 4 | ms49 Phase D+E | #2494 | dev-frontend | 0 set-state warns → rule=error |
| 5 | pip CVE | #2486 | dev-devops | build + scout, commit Dockerfile |
| 6 | STRETCH (only if time) | #2464 | dev-sr-backend | curl smoke only (pytest=operator) |

Operator-gated (NOT auto): **#2490** self-review skill — Lead drafts to `_scratch/`, operator applies to `.claude/` ("ii"). **#2488** Infisical — needs operator to provision Infisical first; skip today.

---

## Batch 0 — close the 4 done-but-open (Lead-direct, do FIRST)
Run once from `web/`: `npm audit` · `npm run lint` · `npx tsc --noEmit` · `npm run test`. Then per task: copy AC list, mark each passed/na with evidence, PATCH AC, `zb-task-done`.
- **#2487** (Next16): AC1 npm audit Next-cluster cleared (now on 16) · AC4 deps on 19/4 (package.json) · AC5 vitest ≥375 green · AC2/AC3 codemod+eslint-flat (inspect `web/eslint.config.*` exists + async params in the 6 server pages) · AC6 standalone build → if not run, mark **na** "deferred to operator rebuild" with note. Don't fail the close on the render-confirm; mark na + note.
- **#2489** (lint): AC = React-Compiler rules promoted to error + sites fixed → `npm run lint` shows **0 errors** (51 warnings OK, those are the deferred set-state ones). Confirm `web/eslint.config.mjs` has refs/immutability/preserve-manual-memoization at error.
- **#2481 #2482** (agent UI + settings): committed `fc52382`/`2773e22`. Verify vitest green + the new components exist; attach to a milestone if still orphan (ms blank) before closing.

## Batch 1 — ms49 Phase A (#2491) — spawn dev-sr-frontend
```
Task #2491 (agent-teams, project id=1) — ms49 Phase A. Frontend refactor, NEW shared hook.
READ FIRST: context/projects/agent-teams/shared/set-state-effect-remediation-plan.md (Category 1 list = your exact site set) + the task #2491 AC.
WORKING DIR: C:\Users\banku\Documents\Personal\Projects\GitHub\agent-teams\web

BUILD: a shared `usePersistentState` hook on React `useSyncExternalStore` (client+server snapshot; subscribes to `storage` events; NO setState-in-effect; SSR-safe = server snapshot returns the default, client reads localStorage). Also dedupe the repeated collapse-panel readExpanded/onStorage boilerplate into it.
MIGRATE: the ~18 Category-1 sites listed in the plan doc (ThemeProvider, GlassProvider, AdvancedSettingsDisclosure, AuditorActivityPanel x2, AuditorVisibilityToggle, CostSummary, CrossProjectActiveTasksList, MonthlySpendSection, PnlDashboardSection x2, PnlSummaryCard x2, ResourcesPanel:73, InstallPwaNudge, ProductTour:68, DashboardWelcomeBanner). Each: replace the localStorage-hydrate effect with the hook; the set-state-in-effect warning disappears.
SCOPE: Category 1 ONLY. Do NOT touch Cat 2/3/4 sites.
VERIFY (report raw): `npx tsc --noEmit` EXIT 0 · `npm run test` (full vitest, report file/test counts, must be >=375 green) · `npm run lint` BEFORE/AFTER set-state-in-effect warning count (expect ~18 fewer; report exact numbers). SSR check: theme/glass/collapse panels render correctly (no hydration mismatch) — note how verified.
CONSTRAINTS: do NOT commit/push (Lead commits). Report package/file LOC delta. Paste exact errors verbatim if anything fails. Save scratch to _scratch\ (absolute).
REPORT: hook path · sites migrated (count + list) · tsc/vitest/lint(before→after) · any deviation.
```
After: Lead re-verifies (tsc/vitest/lint-count) → `zb-git-commit` `#2491: ms49 Phase A — usePersistentState hook + ~18 Cat-1 sites` (no push) → `zb-task-done`.

## Batch 2 — ms49 Phase B (#2492) — spawn dev-sr-frontend
```
Task #2492 (project id=1) — ms49 Phase B. Independent of A. NEW shared hook.
READ FIRST: shared/set-state-effect-remediation-plan.md (Category 2 list) + #2492 AC. WORKING DIR: web\
BUILD: a shared `useAsyncData(fetcher, deps)` hook = {loading,data,error} state machine + cancellation-on-unmount/dep-change (replaces the inline `let cancelled=false` guard pattern).
MIGRATE: the ~13 Category-2 fetch-on-mount sites in the plan (CalendarTaskPicker:57, FlagBellBadge, InboxBadge, IntegrationsPanel, ResourcePreviewDrawer, ResourceUploadModal:93, TaskComments, TaskOutputs x2, TaskToolCalls, NewTaskModal:261, Board:351, ResourcesPanel:109). Preserve loading indicators, error handling, cancellation semantics exactly.
SCOPE: Category 2 ONLY. VERIFY: tsc EXIT 0 · vitest >=375 green · lint set-state warnings −~13 (report before→after). CONSTRAINTS + REPORT: same as Phase A. No commit/push.
```
After: Lead verify → commit `#2492: ms49 Phase B — useAsyncData hook + ~13 Cat-2 sites` → done.

## Batch 3 — ms49 Phase C (#2493) — spawn dev-frontend
```
Task #2493 (project id=1) — ms49 Phase C. Per-site (no new hook). READ: plan Category 3 list + #2493 AC. WORKING DIR: web\
APPROACH per site (~11 Cat-3 prop-sync/reset-on-open modals): prefer `key`-based remount (parent passes key={task.id}/key={open} → fresh state, the reset effect disappears); derive-during-render where the value is pure; ONLY where neither fits, a scoped justified eslint-disable with a one-line reason. Sites: ApprovalPoliciesEditor, EditProjectModal, MilestoneFormModal, ResourceUploadModal:77, TaskDetail x4, TaskHaltModal, TaskRejectModal, AgentFormModal prefill.
SCOPE: Category 3 ONLY. VERIFY: tsc · vitest >=375 · lint set-state −~11. CONSTRAINTS/REPORT same. No commit/push.
```
After: Lead verify → commit `#2493: ms49 Phase C — key-remount/derive ~11 Cat-3 modals` → done.

## Batch 4 — ms49 Phase D + E (#2494) — spawn dev-frontend
```
Task #2494 (project id=1) — ms49 Phase D (real fixes) + E (re-promote rule). READ: plan Category 4 list + #2494 AC. WORKING DIR: web\
PHASE D (~7 Cat-4 genuine smells — fix for real, not silence): CalendarTaskPicker:99 + MilestoneCombobox:128 (compute clamp during render), CalendarView:199 (reconcile dueOverride), Board:316/:398/:432/:605 (derive view/highlight/pagination from searchParams / during render). Removes redundant re-renders.
PHASE E (exit): once `npm run lint` shows 0 `set-state-in-effect` warnings, flip that rule from warn to ERROR in web/eslint.config.mjs. (The lone AcEditor `exhaustive-deps` warning is a SEPARATE pre-existing rule — leave it; do NOT promote exhaustive-deps.)
VERIFY: tsc · vitest >=375 · `npm run lint` = 0 set-state-in-effect warnings + rule now error + lint still passes (0 errors). CONSTRAINTS/REPORT same. No commit/push.
```
After: Lead verify → commit `#2494: ms49 Phase D real fixes + E re-promote set-state-in-effect to error` → done → ms49 complete.

## Batch 5 — pip CVE (#2486) — spawn dev-devops
```
Task #2486 (project id=1). READ #2486 AC + _scratch/langgraph_cve_residue.md. RUN FROM MAIN REPO DIR.
EDIT langgraph/Dockerfile: add `RUN pip install --upgrade pip` BEFORE the existing `pip install --no-cache-dir -e .`. Rebuild: `docker compose -p agent-teams build --pull langgraph`. Verify: `docker scout cves <image>` shows CVE-2026-1703 cleared; the editable install + langgraph import still build clean. Do NOT restart the running container (build only). No push. Report build tail + scout before/after.
```
After: Lead verify (build success + scout) → commit `#2486: langgraph Dockerfile — upgrade pip (clears CVE-2026-1703)` → done.

## Batch 6 — STRETCH #2464 (only if all above done + time left)
Backend (team-goals/outcomes data model + migration). dev-sr-backend. CAVEATS: pytest=operator-run → verify via curl smoke + confirm migration applies cleanly; migration apply touches live dev DB → be careful, no destructive ops. If unsure, PARK for operator rather than risk it. Keep framing neutral (goals/scoreboard, not strategy prose).

## End-of-day
- Post a rail close + a short summary in chat: what committed (hashes), what's left, lint final count, any halts.
- Everything stays **local on dev** (no push). Operator reviews + pushes when back.
- Recommend the operator clear/new-session after this batch (≥several phases + compactions).
