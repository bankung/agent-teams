---
story: ux-simplification
version: 2
updated: 2026-06-14
updated_by: Lead
---

# v0.7.0 UX/UI simplification (milestone 34)

Anchor plan: [`shared/ux-simplification-plan.md`](../ux-simplification-plan.md) (#2368 — 13 routes / 93 components audited, 0 dead). Workstream = declutter + relocate + collapse-the-rare + IA restructure so the daily-driver surfaces (board + task detail) stay scannable. Quick-wins first, then redesigns R1–R7.

## Current state
- **#2370 quick-wins Q1–Q12** — DONE (commit 1d8b070, pre-session). Card badge suppression, nav declutter, lazy fetches, Q11 LlmSpend→CostSummary merge.
- **#2371 R1 board declutter** — DONE (d97f01e). Cost/PnL/AuditHistory panels off the board → per-project settings page; ProgressChartsPanel + ResourcesPanel stay on board.
- **#2372 R2 TaskDetail IA** — DONE (0d6dd1e). Status section split into read-only Metadata strip + Controls block; HITL tasks surface Question/Decision+AC above Controls (isHitl branch).
- **#2373 R3 creation modals** — DONE (a84c481). Shared `<TaskFormFields>` (prefix-parametrized data-* keeps new-task/ai-task contracts byte-identical) + Advanced disclosure for rare fields.
- **#2374 R4 dashboard cost** — DONE (cbbdf32). Panel renamed "Usage & Spend"; orphaned LlmSpendSection.tsx deleted (merge already done by Q11). PnL stays separate.
- **#2375 R5 /settings** — DONE (3479c25). Theme + Integrations (new `IntegrationsPanel`, extracted from deleted PlatformSettingsModal) consolidated onto global /settings; per-header ThemePicker removed from 10 headers; **/settings was previously unreachable** → added Settings nav link in dashboard + board headers.
- **#2376 R6+R7** — DONE (4351d44). Calendar day-cell hover "+" (surfaces right-click-only new-task-on-date); ProductTour header replay auto-hides after completion + reachable via new `TourReplayButton` on /settings.

Test baseline grew 268 → 282 (FE vitest, all green). tsc + lint clean throughout.

## Open threads
- none. (#1582 closed; stale-comment cleanup closed as #2377.)

## Closed since v1
- **#1582 product tour** — DONE 2026-06-14. Operator ran the live incognito walk: auto-fire on fresh load, 6-step walk, persistence across reload, R7 header-hide + /settings replay all confirmed. 7/7 ACs passed.
- **#2377 stale-comment cleanup** — DONE (655de0f). Zero `PlatformSettingsModal` refs left in web/.

## Gotchas
- `/settings` (global) had NO nav link before R5 — only reachable by typing the URL. Any future header declutter must preserve a Settings entry.
- The two creation modals use distinct data-* prefixes (`data-new-task-*` vs `data-ai-task-*`); the shared TaskFormFields parametrizes the prefix — never hardcode one.
- ProductTour steps anchor to DASHBOARD elements; the /settings replay path therefore clears the completed flag + navigates to /dashboard rather than running the tour in place.
- Board tests `vi.mock` CostSummary/ThemePicker/PlatformSettingsModal as `() => null` — stale after removals but harmless; left in place.

## Decisions pointer
See `shared/decisions.md` (v0.7.0 entries) + the anchor plan `shared/ux-simplification-plan.md`.

## Changelog
- v1 (2026-06-14, Lead): story opened at batch close; #2370–#2376 DONE, #1582 AC7 pending operator walk.
- v2 (2026-06-14, Lead): #1582 closed (operator live walk passed 7/7) + #2377 closed; no open threads.
