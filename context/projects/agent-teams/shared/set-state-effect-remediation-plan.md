# `set-state-in-effect` remediation plan (follow-on to #2489)

**Created:** 2026-06-18 · **Owner:** dev team (FE) · **Status:** planned (tasks opened under the `set-state-effect-remediation` milestone)

## Context
Next 16 + `eslint-config-next@16` pulled in `eslint-plugin-react-hooks@7` (the React-Compiler rule family). #2487 landed the upgrade; #2489 fixed the genuinely-actionable rules (`refs`, `immutability`, `preserve-manual-memoization` → **error**, sites fixed) and **kept `set-state-in-effect` at `warn`** because its ~50 sites are mostly idiomatic. This plan works through those ~50 in 4 phases, then re-promotes the rule to `error`.

> Baseline: `npm run lint` from `web/` = `✖ 51 problems (0 errors, 51 warnings)` — 50 `set-state-in-effect` + 1 live `exhaustive-deps` (AcEditor), all `warn`.

## Review — the ~50 sites by category

### Category 1 — Hydrate-from-localStorage / client-only mount init (~18) — *idiomatic*
Read `localStorage`/`window` in an effect then `setState` (can't read storage during SSR → hydration-safe).
`ThemeProvider:52`, `GlassProvider:56`, `AdvancedSettingsDisclosure:61`, `AuditorActivityPanel:123`+`:135`, `AuditorVisibilityToggle:90`, `CostSummary:130`, `CrossProjectActiveTasksList:282`, `MonthlySpendSection:206`, `PnlDashboardSection:105`+`:127`, `PnlSummaryCard:161`+`:182`, `ResourcesPanel:73`, `InstallPwaNudge:31`, `ProductTour:68`, `DashboardWelcomeBanner:46`.

### Category 2 — Async data-fetch (mount / param-change) (~13) — *idiomatic*
`setLoading`/reset → fetch → `setState` in `.then` (+ the repeated `let cancelled = false` guard).
`CalendarTaskPicker:57`, `FlagBellBadge:43`, `InboxBadge:81`, `IntegrationsPanel:227`, `ResourcePreviewDrawer:49`, `ResourceUploadModal:93`, `TaskComments:72`, `TaskOutputs:139`+`:395`, `TaskToolCalls:71`, `NewTaskModal:261`, `Board:351`, `ResourcesPanel:109`.

### Category 3 — Prop-sync / reset-on-open (modal prefill, derive-from-prop) (~11) — *mixed*
`ApprovalPoliciesEditor:103`, `EditProjectModal:152`, `MilestoneFormModal:71`, `ResourceUploadModal:77`, `TaskDetail:89`+`:102`+`:113`+`:178`, `TaskHaltModal:42`, `TaskRejectModal:50`, `AgentFormModal` prefill effect.

### Category 4 — "You might not need an effect" (clamp / derive / URL-sync) (~7) — *genuine smell*
`CalendarTaskPicker:99` (clamp activeIdx), `MilestoneCombobox:128` (clamp highlight), `CalendarView:199` (reconcile dueOverride), `Board:316` (URL→view), `Board:398` (URL→highlight), `Board:432` (SSR re-sync), `Board:605` (reset pagination).

## Remediation — 5 phases, ordered by leverage

| Phase | Scope | Approach | ~Sites |
|---|---|---|---|
| **A** | Category 1 | New `usePersistentState` hook on **`useSyncExternalStore`** (React-sanctioned external store; client+server snapshot; no setState-in-effect). Also dedupes the repeated `readExpanded`/`onStorage` collapse-panel boilerplate. | ~18 |
| **B** | Category 2 | New `useAsyncData(fetcher, deps)` hook encapsulating `{loading/data/error}` + cancellation. Removes the inline fetch-state effects + dedupes boilerplate. | ~13 |
| **C** | Category 3 | Per-site: `key`-based remount for full-reset modals (parent passes `key={task.id}`/`key={open}` → fresh state, effect disappears); derive-during-render where pure; scoped justified-disable only where neither fits. | ~11 |
| **D** | Category 4 | Per-site real fixes: compute clamps during render (removes redundant re-renders), derive `Board` view from `searchParams`, evaluate the rest case-by-case. | ~7 |
| **E** | exit | Once all phases land (0 `set-state-in-effect` warnings), flip the rule to **`error`** in `web/eslint.config.mjs`. | — |

**Sequencing:** A and B are independent and highest-leverage (2 hooks clear ~31 of ~50). C and D are per-site. Each phase is independently shippable + `vitest`-checkable; re-run the full suite after each. This is genuine quality work — Phases A/B remove duplicated boilerplate, Phase D removes redundant renders — not just lint-silencing.

## Verify (each phase)
`tsc --noEmit` EXIT 0 · full `vitest` green (≥375) · `npm run lint` warning count drops by the phase's site count · no behavior regression (operator render-confirm on the next web rebuild).
