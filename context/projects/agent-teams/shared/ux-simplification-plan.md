# UX/UI Simplification Plan — v0.7.0 "Onboarding & UX polish"

**Task:** #2368 · **Date:** 2026-06-14 · **Owner:** Lead (synthesised from 2× dev-frontend audits)
**Source drafts (full per-component detail):** [`_scratch/ux-audit-clusterA.md`](../../../../_scratch/ux-audit-clusterA.md) (board/views/creation) · [`_scratch/ux-audit-clusterB.md`](../../../../_scratch/ux-audit-clusterB.md) (detail/dashboard/settings/system)

> This doc is the **v0.7.0 UX workstream anchor**. Tag follow-up tasks with `story: ux-simplification`.

## Scope audited
**13 routes + 93 components.** Both clusters report **0 dead components** — everything has an active caller. The simplification opportunity is **not dead-code removal**; it is **decluttering, relocation, collapsing-the-rare, and IA restructure** so the daily-driver surfaces (board + task detail) are scannable.

## Resolved: `FINANCE_PANELS_ENABLED`
Both auditors flagged this as unknown. **Resolved:** `.env` has `NEXT_PUBLIC_FINANCE_PANELS_ENABLED=true` → PnL panels (`PnlSummaryCard`, `PnlDashboardSection`) **render in the operator's environment — they are live, not dead.** (`.env.example`=false = default-off for fresh installs.) ⇒ the cost-surface overlap (below) is a real, live consolidation problem.

## Cross-cutting themes (the headlines)
1. **The board carries config chrome it shouldn't.** Cost/PnL panels band + ResourcesPanel + AuditHistorySection all sit on the daily Kanban surface (~120px+ of header chrome before the first card; 3 stacked footer sections below the lanes). All belong in Settings / `/review`. (#2358 already tracks the Resources move.)
2. **Cost/spend is shown in 5 overlapping places** (dashboard CostSummary + LlmSpendSection + per-card cost strip + PnlDashboardSection; board CostSummary + PnlSummaryCard). For a solo operator billed as one entity this is redundant — consolidate into one "Usage & Spend" surface.
3. **Default-case badges are noise.** TaskCard shows RunModeBadge (manual = ~95%) + TaskKindBadge (ai = default) on every card → up to 7 badge slots. Suppress the default case → ~4-5 slots.
4. **TaskDetail (1183 lines) buries its primary content.** A kitchen-sink "Status" section (8 controls) + always-expanded Timestamps + always-fetched "Also blocks" push Description/AC/Question below the fold. Split metadata vs controls; reorder so the HITL question/AC lead.
5. **Task-creation modals are over-long + near-duplicated.** NewTaskModal (~14 sections) + AiTaskModal share ~400 LOC of form. Collapse rare fields under "Advanced"; extract a shared form component.
6. **First-run UI lingers for the expert.** AcTipBanner, "Take the tour" button, AgentGallery 3-axis filter+sort (for ≤20 agents) — all earn their place on day 1, become clutter after. Auto-hide / drop.

## QUICK-WINS (sub-day, low-risk — batch candidate)
| # | Win | Where | LOC |
|---|---|---|---|
| Q1 | Cut "team: dev" static label from board nav | Board.tsx:643 | ~3 |
| Q2 | ConnectionStateBadge → dot-only (tooltip carries label) | ConnectionStateBadge.tsx | ~2 |
| Q3 | Suppress RunModeBadge when `manual` | RunModeBadge.tsx + TaskCard | ~1 |
| Q4 | Suppress TaskKindBadge when `ai` | TaskKindBadge.tsx + TaskCard | ~1 |
| Q5 | Cut scheduled-task count chip (display-only, no action) | Board.tsx:863 | ~8 |
| Q6 | Move milestone filter from nav row → toolbar row | Board.tsx:656 | logic-free |
| Q7 | Remove 7 `Sep` separators, use `gap-x-2` | Board.tsx | small |
| Q8 | Cut AcTipBanner from TaskDetail (expert noise after day 1) | TaskDetail.tsx:702 | ~60 |
| Q9 | Lazy/collapse "Also blocks" (kills 1 API call per drawer open) | TaskDetail.tsx:779 | small |
| Q10 | Collapse Timestamps behind `<details>` | TaskDetail.tsx:803 | small |
| Q11 | Merge LlmSpendSection into CostSummary expand (−1 panel, −1 fetch) | dashboard | medium |
| Q12 | Drop AgentGallery sort control (name sort = default) | AgentGallery.tsx | ~40 |

## BIGGER REDESIGNS (multi-day — the v0.7.0 slate)
| ID | Redesign | Surfaces | Notes |
|---|---|---|---|
| R1 | **Board declutter** — move Cost + PnL panels band off board → Project Settings; keep only ProgressChartsPanel | Board.tsx, /p/[name]/settings | absorbs #2358 (Resources); +AuditHistory → /review |
| R2 | **TaskDetail IA restructure** — split "Status" into Metadata strip vs Controls row; reorder so Q/Decision+AC lead for HITL tasks | TaskDetail.tsx:428-603 | biggest scan-ability win |
| R3 | **Task-creation modals** — "Advanced" disclosure (model tier / blocked-by / handoff) + extract shared `<TaskFormFields>` for NewTask/AiTask | NewTaskModal, AiTaskModal | R3a collapse = quick; R3b merge = ~400 LOC, needs tests |
| R4 | **Dashboard cost-surface consolidation** — single "Usage & Spend" replacing CostSummary + LlmSpendSection; PnL stays distinct | dashboard | depends on Q11 |
| R5 | **`/settings` consolidation** — ThemePicker (currently in every route header) + PlatformSettingsModal content → real /settings page | /settings, all headers | dashboard header declutter |
| R6 | **Calendar `+` hover affordance** — surface "new task on date" without right-click | CalendarView.tsx | discoverability |
| R7 | **ProductTour replay auto-hide** after completion | ProductTour.tsx | coordinate with #1582 (in REVIEW) |

## Cross-refs to existing backlog
- **#2358** — move Resources panel off board → Settings (= part of R1; in `v0.7.0`)
- **#2347** — milestone DONE-count bug (separate bug, `v0.7.0`)
- **#1582** — onboarding tour (in REVIEW) — coordinate R7 with it
- **#2125** — Resources panel a11y/perf debt (rides with R1)

## Proposed follow-up tasks (AC#4 — listed; create on operator go)
1. `[ux] v0.7.0 quick-wins batch` — Q1-Q12 grouped (all sub-day, board+card+detail+dashboard declutter)
2. `[ux] Board declutter — Cost/PnL/Audit off the board` (R1; folds #2358 + #2125)
3. `[ux] TaskDetail IA restructure — Metadata/Controls split + HITL reorder` (R2)
4. `[ux] Task-creation modals — Advanced collapse + shared form extract` (R3)
5. `[ux] Dashboard cost-surface consolidation (5 → 1 "Usage & Spend")` (R4)
6. `[ux] /settings consolidation — ThemePicker + integration status` (R5)
7. `[ux] Calendar +-affordance + ProductTour replay auto-hide` (R6+R7; R7 post-#1582)

> Sequencing note: do the **quick-wins batch first** (visible declutter, near-zero risk), then R2 (TaskDetail, highest scan win), then R1/R4 (board+dashboard), then R3/R5 (modals+settings).
