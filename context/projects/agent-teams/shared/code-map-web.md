# Code map — web/ (2026-06-10)

## Totals

| metric | value |
|---|---|
| total TS/TSX/JS files | 140 |
| total LOC | 31 506 |
| non-test LOC | ~27 179 |
| app routes (pages) | 10 |
| components | 87 |
| lib modules | 18 |
| test files (__tests__ + lib/*.test.*) | 13 — 3 496 LOC |
| e2e test files (e2e/) | 2 — 550 LOC |
| prod deps (package.json) | 5 |
| dev deps | 14 |

---

## Modules

### App routes

| route file | LOC | purpose | key deps | status |
|---|---|---|---|---|
| `app/page.tsx` | 9 | Root redirect → /dashboard | next/navigation redirect | active |
| `app/layout.tsx` | 64 | Root layout: Inter font, ThemeProvider, ClientProviders, ServiceWorkerRegister, FOUC mitigation inline script | ClientProviders, ThemeProvider, ServiceWorkerRegister | active |
| `app/loading.tsx` | — | Next.js Suspense loading UI | — | active (shell) |
| `app/error.tsx` | — | Top-level error boundary | — | active (shell) |
| `app/dashboard/page.tsx` | 530 | Cross-project dashboard. Server Component; parallel-fetches stats + flags + active tasks. Renders project grid cards, aggregate summary, cost strip, auditor panel. `FINANCE_PANELS_ENABLED` gates PnlDashboardSection (dynamic import). | getProjectsStats, getCrossProjectActiveTasks, listAuditFlags, getAuditDailyRollup; CostSummary, LlmSpendSection, CrossProjectActiveTasksList, ProductTour | active |
| `app/p/[name]/page.tsx` | 62 | Per-project Kanban board. Server Component; parallel-fetches active tasks + first DONE page + stats + progress. Passes all to Board client component. Keyset DONE pagination (#2112). | listAllTasks, listDoneLanePage, getProjectsStats, getProjectProgressStats; Board | active |
| `app/p/[name]/calendar/page.tsx` | 109 | Calendar view. SSR-fetches tasks in date window + milestones. | listTasks, listMilestones; CalendarView | active |
| `app/p/[name]/gantt/page.tsx` | 92 | Gantt view. SSR-fetches milestones + detail rows. | listMilestones, getMilestone; GanttView | active |
| `app/p/[name]/settings/page.tsx` | 58 | Per-project settings page. SSR-fetches project by name. | getProjectByName; ProjectSettingsPanel | active |
| `app/tasks/[id]/page.tsx` | 126 | Task focus view (full-page). SSR-fetches task + project by id. Also used by approve/[id] alias. | getTask, getProjectById; TaskFocusView | active |
| `app/approve/[id]/page.tsx` | — | Re-exports default + `dynamic` from `../../tasks/[id]/page`. Alias route for approval deep-links. | tasks/[id]/page | active |
| `app/inbox/page.tsx` | 216 | Cross-project HITL inbox. SSR: listProjects → per-project listTasks filtered for pending-answer/review status. No dedicated component — inline Server Component. | listProjects, listTasks | active |
| `app/review/page.tsx` | 16 | Audit flag review page. SSR fetches listAuditFlags; delegates to ReviewClient. | listAuditFlags; ReviewClient | active |
| `app/settings/page.tsx` | 42 | Platform-wide settings page shell. Delegates to PlatformSettingsModal-like inline layout. | — | active |

### Components

#### Board / view layer

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `Board.tsx` | 893 | Main Kanban orchestrator. Client. Holds task state, SSE refresh, view switching (board/list), DONE pagination, milestone filter, deep-link handling, drag callbacks. Renders nav header + all panels. | BoardDndCanvas (dynamic), ListView, TaskDetail, NewTaskDropdown, ViewSwitcher, ResourcesPanel, ProgressChartsPanel, PnlSummaryCard, CostSummary, AuditHistorySection; useRowChangedEvents, sortLaneTasks, featureFlags | `app/p/[name]/page.tsx` | Board.donePagination.test.tsx | active |
| `BoardDndCanvas.tsx` | — | dnd-kit drag surface for board view. Dynamically imported from Board.tsx (code-split, ssr:false — Kanban #2111). Owns all @dnd-kit imports. Delegates cross-lane PATCH + same-lane reorder to Board via callbacks. | @dnd-kit/core, @dnd-kit/sortable; BoardColumn | Board.tsx (dynamic) | — | active |
| `BoardColumn.tsx` | — | Single Kanban lane column. Receives tasks + dnd context from BoardDndCanvas. | @dnd-kit/sortable; TaskCard | BoardDndCanvas | — | active |
| `ListView.tsx` | 390 | Table/list view of tasks. Sortable columns; status filter chips. Rendered by Board when view=list. | api types, constants; TaskCard | Board.tsx | — | active |
| `CalendarView.tsx` | 999 | Month/week calendar. dnd-kit for due-date reorder. Inline sub-components: DayContextMenu, TaskChip, MilestoneChip, DroppableDay, MonthGrid, WeekStrip. Opens NewTaskModal. | patchTask; CalendarTaskPicker, NewTaskModal | `app/p/[name]/calendar/page.tsx` | CalendarView.test.tsx | active |
| `GanttView.tsx` | 825 | Gantt bar chart over milestones. Inline sub-components: RailRow, TaskChip, UnassignedPool. Opens MilestoneFormModal, MilestoneDeleteModal. | api; MilestoneFormModal, MilestoneDeleteModal | `app/p/[name]/gantt/page.tsx` | GanttView.test.tsx | active |
| `ViewSwitcher.tsx` | — | Board / List / Calendar / Gantt toggle buttons. | — | Board.tsx | — | active |
| `TaskCard.tsx` | — | Card rendered in both BoardColumn and ListView. Shows title, badges, priority, status chip. | PendingBadge, RecurrenceIndicator, StepCounter, TaskKindBadge, Icon | BoardColumn, ListView | — | active |

#### Task detail / focus

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `TaskDetail.tsx` | 1 235 | Right-side drawer opened from Board. Inline sub-components: AcTipBanner, CostStrip, Section, QuestionInteractionSection, AcceptanceCriteriaSection, BlockerPicker. Full field editing via patchTask. | patchTask, cancelTask, submitAnswer, invalidateAnswer, listMilestones, getTaskBlocks; DatePicker, DecisionInteractionView, MilestoneCombobox, ModelTierSelect, TaskComments, TaskMuteToggle, TaskToolCalls, PendingBadge, RunModeBadge, TaskKindBadge | Board.tsx | TaskComments.test.tsx (partial) | active |
| `TaskFocusView.tsx` | 549 | Full-page task orchestrator for /tasks/[id]. Approve / reject / halt / decide flow. | resolveHitlTask, decideTask, patchTask; TaskActionButtons, TaskHaltModal, TaskRejectModal, TaskDetail | `app/tasks/[id]/page.tsx` | — | active |
| `TaskComments.tsx` | 366 | Comment thread for a task. Fetches + posts via getTaskComments / postTaskComment. Renders markdown via safeMarkdown. | getTaskComments, postTaskComment; safeMarkdown | TaskDetail | TaskComments.test.tsx | active |
| `TaskToolCalls.tsx` | 358 | Tool-call audit log section inside TaskDetail. Fetches getTaskToolCalls. | getTaskToolCalls | TaskDetail | — | active |
| `TaskActionButtons.tsx` | — | Approve / reject / halt CTA row within TaskFocusView. | — | TaskFocusView | — | active |
| `TaskHaltModal.tsx` | — | Halt-reason confirm modal. Uses ModalShell. | ModalShell | TaskFocusView | — | active |
| `TaskRejectModal.tsx` | — | Reject-reason confirm modal. Uses ModalShell. | ModalShell | TaskFocusView | — | active |
| `TaskMuteToggle.tsx` | — | Toggle notifications-muted flag. patchTask optimistic + revert. | patchTask | TaskDetail | — | active |

#### Task creation

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `NewTaskDropdown.tsx` | 205 | "New task" split-button dropdown — opens NewTaskModal or AiTaskModal. Also opens MilestoneFormModal. | AiTaskModal, NewTaskModal, MilestoneFormModal | Board.tsx | — | active |
| `NewTaskModal.tsx` | 889 | Manual task-creation form. Field-rich (title, role, priority, status, run_mode, milestone, date, AC, templates). Uses ModalShell. | createTask, listTaskTemplates, listMilestones; ActionTemplatePicker, TaskTemplatePicker, HandoffTemplatePicker, DatePicker, MilestoneCombobox, ModelTierSelect, PauseOverrideBlock, ModalShell | NewTaskDropdown, CalendarView | NewTaskModal.template.test.tsx | active |
| `AiTaskModal.tsx` | 811 | AI-assisted task creation via parseTaskText two-step. Near-identical field set to NewTaskModal. Known sibling (comments cite: "same shape as NewTaskModal"). | parseTaskText, createTask; ActionTemplatePicker, HandoffTemplatePicker, DatePicker, MilestoneCombobox, ModelTierSelect, PauseOverrideBlock, ModalShell | NewTaskDropdown | — | active |
| `ActionTemplatePicker.tsx` | — | Chip list of action templates. Used inside NewTaskModal + AiTaskModal. | templates.list (api) | NewTaskModal, AiTaskModal | — | active |
| `TaskTemplatePicker.tsx` | — | Chip list of task templates (prefilled field sets). Used inside NewTaskModal only. | listTaskTemplates | NewTaskModal | — | active |
| `HandoffTemplatePicker.tsx` | — | Chip list of handoff templates. Used inside NewTaskModal + AiTaskModal. | handoffTemplates.list (api) | NewTaskModal, AiTaskModal | — | active |
| `CalendarTaskPicker.tsx` | — | Modal picker for moving a task to a calendar date. Uses ModalShell. | listTasks; ModalShell | CalendarView | — | active |

#### Project management

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `NewProjectModal.tsx` | 335 | Create-project form. Fetches teams list. Uses ModalShell. | createProject, getTeams; ModalShell | dashboard page | — | active |
| `EditProjectModal.tsx` | 619 | Edit project form (name, sources, working_path, standards, caps). diff-on-save pattern. Uses ModalShell. | updateProject; ModalShell | dashboard page | — | active |
| `KillProjectModal.tsx` | 339 | Kill / revive project confirm. Force-kill toggle. Uses ModalShell. | killProject, reviveProject; ModalShell | Board.tsx, dashboard page | — | active |
| `PauseProjectModal.tsx` | 252 | Pause / unpause project confirm. Uses ModalShell. | pauseProject, unpauseProject; ModalShell | Board.tsx, dashboard page | — | active |
| `ProjectSettingsPanel.tsx` | — | Per-project settings panel (working_path, recurrence, consent). | updateProject | `app/p/[name]/settings/page.tsx` | — | active |
| `ProjectSwitcher.tsx` | — | Project switcher dropdown in board nav. Lazy-fetches listProjects. | listProjects | Board.tsx | — | active |
| `ProjectConsentBanner.tsx` | — | Server component. Shows consent state; wraps ProjectConsentGrantModal. | ProjectConsentGrantModal | Board.tsx | — | active |
| `ProjectConsentGrantModal.tsx` | — | Grant auto-run consent modal. Uses ModalShell. | grantConsent; ModalShell | Board.tsx, ProjectConsentBanner | — | active |
| `KilledBanner.tsx` | — | "Project killed" status banner. | — | Board.tsx | — | active |
| `PausedBanner.tsx` | — | "Project paused" status banner (mirrors KilledBanner pattern). | — | Board.tsx | — | active |
| `PauseOverrideBlock.tsx` | — | Override-reason field shown when submitting during pause. | — | NewTaskModal, AiTaskModal | — | active |

#### Milestones

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `MilestoneFormModal.tsx` | 296 | Create / edit milestone form. Uses ModalShell. | createMilestone, updateMilestone; DatePicker, ModalShell | GanttView, NewTaskDropdown | — | active |
| `MilestoneDeleteModal.tsx` | — | Delete milestone confirm. Uses ModalShell. | deleteMilestone; ModalShell | GanttView | — | active |
| `MilestoneCombobox.tsx` | — | Milestone selector combobox for task forms. | listMilestones | NewTaskModal, AiTaskModal, TaskDetail | — | active |
| `MilestoneStatusBadge.tsx` | — | Small status chip for milestone lifecycle. | api types | multiple | MilestoneStatusBadge.test.tsx | active |

#### Review / audit

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `ReviewClient.tsx` | 306 | Client shell for /review page. SSE-driven refresh. Mass-action bar + flag cards. | resolveFlag; MassActionBar, ProjectFlagCard, TerminateFlagModal, useRowChangedEvents | `app/review/page.tsx` | — | active |
| `ProjectFlagCard.tsx` | 349 | Renders one audit flag with resolve/terminate actions. Opens AdjustFlagForm inline. | resolveFlag; AdjustFlagForm | ReviewClient | — | active |
| `AdjustFlagForm.tsx` | 403 | Form for adjusting a flag's verdict / threshold. | resolveFlag | ProjectFlagCard | — | active |
| `TerminateFlagModal.tsx` | 279 | Terminate-flag confirm modal — shows which gate is unmet (Kanban #2095). Uses ModalShell. | ModalShell | ReviewClient | — | active |
| `MassActionBar.tsx` | — | Batch resolve/terminate bar in ReviewClient. Uses ModalShell. | ModalShell; resolveFlag | ReviewClient | — | active |
| `ReviewSummaryWidget.tsx` | — | Compact pending-flag count widget on dashboard. | listAuditFlags | dashboard page | — | active |
| `FlagBellBadge.tsx` | — | Bell icon + count badge. Polls + SSE (WildcardSSEContext) to refresh flag count. | listAuditFlags; useWildcardRowChanged | Board.tsx | — | active |
| `AuditHistorySection.tsx` | — | Collapsed panel of project audit history tasks. | listProjectAuditTasks | Board.tsx | — | active |
| `AuditorActivityPanel.tsx` | — | Auditor activity breakdown panel on dashboard. | collapseState | dashboard page | — | active |
| `AuditorVisibilityToggle.tsx` | — | Toggle to show/hide auditor activity panel. | — | dashboard page | — | active |
| `DecisionInteractionView.tsx` | 249 | Decision-type task interaction: shows option cards, submits choice. | decideTask; OptionCard | TaskDetail, TaskFocusView | — | active |
| `OptionCard.tsx` | — | Single decision option card. | — | DecisionInteractionView | — | active |

#### Finance / analytics (gated by FINANCE_PANELS_ENABLED)

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `CostSummary.tsx` | 320 | Token + cost summary panel. Expandable (collapseState). | collapseState | Board.tsx, dashboard page | — | active |
| `PnlSummaryCard.tsx` | 451 | Per-project P&L card. getProjectPl; range selector; collapseState. Mirrors CostSummary expand pattern. | getProjectPl, plRangePresets, money; collapseState | Board.tsx | — | gated |
| `PnlDashboardSection.tsx` | 343 | Cross-project P&L section on dashboard. Dynamic import (code-split, Kanban #2111). | getCrossProjectPl, plRangePresets; collapseState | dashboard page (dynamic) | — | gated |
| `LlmSpendSection.tsx` | — | Daily spend bar chart section on dashboard. | getDailyUsage | dashboard page | LlmSpendSection.test.tsx | active |
| `ProgressChartsPanel.tsx` | 710 | Burndown + velocity SVG charts. Expandable, inline SVG geometry helpers. Uses ModalShell for expanded chart view. | ModalShell | Board.tsx | — | active |
| `BudgetBar.tsx` | — | Compact horizontal spend-vs-cap bar per project card. Server-safe (no hooks). | api types | dashboard page | — | active |

#### Resources

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `ResourcesPanel.tsx` | 497 | Collapsible file/link resources list. Lazy-fetch on first expand. Tag filters. Opens ResourcePreviewDrawer + ResourceUploadModal. | listResources, deleteResource; collapseState, ResourcePreviewDrawer, ResourceUploadModal | Board.tsx | ResourcesPanel.test.tsx | active |
| `ResourcePreviewDrawer.tsx` | 318 | Side drawer showing resource preview (image/PDF/text). | getResourcePreview | ResourcesPanel | — | active |
| `ResourceUploadModal.tsx` | 381 | Upload file or add link modal. Uses ModalShell. | createResourceFile, createResourceLink; ModalShell | ResourcesPanel | — | active |

#### Dashboard / cross-project

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `CrossProjectActiveTasksList.tsx` | 379 | Active tasks across all projects panel. Collapsible. | getCrossProjectActiveTasks; collapseState | dashboard page | — | active |
| `DashboardRefresher.tsx` | — | Invisible client component; subscribes to WildcardSSEContext and calls router.refresh() on events. | useWildcardRowChanged | dashboard page | — | active |
| `DashboardWelcomeBanner.tsx` | — | One-shot welcome banner. localStorage dismiss pattern. | — | dashboard page | — | active |

#### Settings / PWA

| component | LOC | purpose | key deps | used-by | tests | status |
|---|---|---|---|---|---|---|
| `PlatformSettingsModal.tsx` | 425 | Platform integrations + security settings modal. Reads getIntegrations. Uses ModalShell. | getIntegrations; ModalShell | Board.tsx | — | active |
| `ThemePicker.tsx` | — | Light / dark / system theme picker. Used in 8 locations (nav + dashboard header). | ThemeProvider | Board.tsx + dashboard page + settings page | — | active |
| `ThemeProvider.tsx` | — | React context wrapper for theme. | — | app/layout.tsx | — | active |
| `ServiceWorkerRegister.tsx` | — | Registers /sw.js on hydration. | — | app/layout.tsx | — | active |
| `InstallPwaNudge.tsx` | — | iOS standalone install prompt. | push.isIosNonStandalone | dashboard page | — | active |
| `PushNotificationsPanel.tsx` | 346 | Web Push opt-in/out + subscription list. | push lib; PushSubscriptionRow | `app/settings/page.tsx` | — | active |
| `PushSubscriptionRow.tsx` | — | Individual push subscription row. | push lib | PushNotificationsPanel | — | active |

#### Shared UI primitives

| component | LOC | purpose | used-by | status |
|---|---|---|---|---|
| `ModalShell.tsx` | — | Shared modal chrome (backdrop + panel + ESC + a11y). Used by 17 modal components. | 17 components | active |
| `Toast.tsx` | — | Toast notification + ToastStack. | Board.tsx | active |
| `Icon.tsx` | — | SVG sprite icon wrapper (`/public/agentboard-icons.svg`). | many | active |
| `DatePicker.tsx` | 270 | Date-picker popover. role="dialog" inline (not ModalShell). | NewTaskModal, AiTaskModal, TaskDetail, MilestoneFormModal | active |
| `Switch.tsx` | — | Toggle switch primitive. | Board.tsx, ProjectSettingsPanel | active |
| `ConnectionStateBadge.tsx` | — | SSE connection state indicator. | Board.tsx, ReviewClient | active |
| `InboxBadge.tsx` | — | Pending-task count badge. Polls + WildcardSSEContext. | Board.tsx | active |
| `ModelTierSelect.tsx` | — | Select for model tier (Claude model). | NewTaskModal, AiTaskModal, TaskDetail | active |
| `RunModeBadge.tsx` | — | Badge chip for task run_mode. | TaskDetail, TaskCard | active |
| `TaskKindBadge.tsx` | — | Badge chip for task_kind (ai/human). | TaskCard, TaskDetail | active |
| `PendingBadge.tsx` | — | Badge for tasks awaiting operator action. | TaskCard, TaskDetail | active |
| `RecurrenceIndicator.tsx` | — | Recurrence icon chip on task cards. | TaskCard | active |
| `StepCounter.tsx` | — | "3/5 steps done" counter chip. | TaskCard | active |
| `MilestoneStatusBadge.tsx` | — | Milestone lifecycle status chip. | multiple | active |
| `SourcesBadge.tsx` | — | Shows project sources count. | Board.tsx | active |

#### Product tour

| component | LOC | purpose | used-by | status |
|---|---|---|---|---|
| `ProductTour.tsx` | 301 | Driver.js guided tour (dynamic import, client-only). | dashboard page | active |
| `ProductTourBoardResume.tsx` | 208 | Board-specific tour resume (driver.js dynamic import). | Board.tsx | active |

---

### Lib modules

| module | LOC | purpose | used-by (count) | tests |
|---|---|---|---|---|
| `lib/api.ts` | 2 321 | All fetch wrappers + TypeScript types for the FastAPI backend. Dual-URL (INTERNAL_API_URL for SSR, NEXT_PUBLIC_API_URL for browser). ~80 exported functions and types. | ~65 files | — |
| `lib/WildcardSSEContext.tsx` | 210 | Shared SSE connection for wildcard channel (Kanban #2111). One EventSource; consumers subscribe via useWildcardRowChanged. Board uses its own scoped connection via useRowChangedEvents directly. | DashboardRefresher, FlagBellBadge, InboxBadge; ClientProviders | — |
| `lib/useRowChangedEvents.ts` | 167 | Per-project SSE hook (EventSource + debounce + hard-cap). Used by Board and ReviewClient for their scoped channels. | Board.tsx, ReviewClient.tsx | — |
| `lib/safeMarkdown.tsx` | 428 | Custom Markdown renderer (no third-party parser). Inline + block parsing, safe URL filtering. | TaskComments | safeMarkdown.test.tsx |
| `lib/calendarDates.ts` | 323 | Calendar date computation helpers (month grid, week strip). | CalendarView | calendarDates.test.ts |
| `lib/constants.ts` | 92 | Mirror of api/src/constants.py. TaskStatus, TaskPriority, TaskRole, ProjectTeam, TaskRunMode, TaskKind enums + option arrays. | ~19 files | — |
| `lib/collapseState.ts` | 30 | localStorage expand/collapse state helpers (readExpanded, writeExpanded). | CostSummary, PnlSummaryCard, PnlDashboardSection, AuditorActivityPanel, ResourcesPanel | — |
| `lib/featureFlags.ts` | 7 | FINANCE_PANELS_ENABLED (NEXT_PUBLIC_FINANCE_PANELS_ENABLED). | Board.tsx, dashboard page, LlmSpendSection | — |
| `lib/push.ts` | 276 | Web Push utilities: subscribe, unsubscribe, isIosNonStandalone. NEXT_PUBLIC_VAPID_PUBLIC_KEY. | PushNotificationsPanel, InstallPwaNudge | — |
| `lib/plRangePresets.ts` | 178 | P&L period preset labels + helpers. | PnlSummaryCard, PnlDashboardSection | — |
| `lib/money.ts` | 41 | formatMoney, formatSignedPercent, parseMoney. | PnlSummaryCard | — |
| `lib/sortLaneTasks.ts` | 47 | sortLaneTasks (TODO–REVIEW lanes), sortDoneLane (DONE lane). | Board.tsx | sortLaneTasks.test.ts |
| `lib/cycleExclusion.ts` | 69 | computeBlockedByExclusionSet — prevents cycle in blocked_by picker. | TaskDetail | cycleExclusion.test.ts |
| `lib/parseSteps.ts` | 19 | Parse step-count from task title/description. | (one reference) | parseSteps.test.ts |
| `lib/enabledRoles.ts` | 54 | filterRoleOptions — filters role options per project's enabled_roles. | AiTaskModal, NewTaskModal, ListView | — |
| `lib/time.ts` | 27 | formatRelative, formatDuration. | dashboard page, TaskDetail (×5 total) | — |
| `lib/tour.ts` | 114 | Product tour step definitions for driver.js. | ProductTour, ProductTourBoardResume | — |
| `lib/errors.ts` | 11 | extractErrorMessage helper. | Board.tsx, ResourcesPanel, TaskDetail | — |

---

## Oversized files

### `lib/api.ts` — 2 321 LOC

The single API client module for the entire frontend. Regions (approximate line ranges):

- L1–253: Types only — ProjectRead, TaskRead, AcceptanceCriterion, AnswerHistoryEntry, tool-call types, flag types, push types, milestone types, resource types, usage types, etc.
- L254–343: HttpError class, URL helpers (apiBaseUrl, formatDetail, buildPath, applyActor).
- L344–674: Project CRUD endpoints — getProjectByName, getProjectById, listProjects, getProjectsStats, getAuditDailyRollup, createProject, updateProject, grantConsent, killProject, reviveProject, pauseProject, unpauseProject, listProjectAuditTasks.
- L675–1170: Task endpoints — listTasks, listAllTasks, getTask, listDoneLanePage, createTask, parseTaskText, patchTask, reorderTask, getTaskBlocks, submitAnswer, invalidateAnswer, cancelTask, decideTask, resolveHitlTask, getTaskToolCalls, resolveFlag.
- L1171–1365: Push subscription endpoints + listAuditFlags.
- L1366–1545: Finance endpoints — PL types, getProjectPl, getProjectProgressStats, getCrossProjectPl, getCrossProjectActiveTasks.
- L1546–1705: Snooze, templates (ActionTemplate, HandoffTemplate), getTeams.
- L1706–1875: Integrations, UserPending, Milestone types + CRUD.
- L1876–2110: Task templates, comments (getTaskComments, postTaskComment).
- L2107–2321: Resources — listResources, createResourceFile, createResourceLink, getResourcePreview, getDailyUsage, deleteResource.

### `components/TaskDetail.tsx` — 1 235 LOC

- L1–33: Imports + constants (AC_TIP_LS_KEY).
- L34–120: Inline helpers: AcTipBanner, truncate, formatTokens.
- L121–387: Main TaskDetail function — state, effects, 8 patchTask handlers (status, priority, role, run_mode, milestone, blocked_by, due_date, mute), cancelTask, invalidateAnswer, submitAnswer.
- L388–734: JSX — header, metadata fields, CostStrip, question/decision section (L576), AC section (L600+), comments, tool calls.
- L735–776: CostStrip, Section sub-components.
- L778–1042: QuestionInteractionSection sub-component (answer submission flow).
- L1043–1235: AcceptanceCriteriaSection, BlockerPicker sub-components.

### `components/CalendarView.tsx` — 999 LOC

- L1–114: Imports + status color maps + placement helpers (resolvePlacement, sortPlaced).
- L163–594: Main CalendarView export — state (month/week nav, drag, context-menu), dnd-kit setup, 3 useEffects (mount, dnd auto-scroll, key-close).
- L595–677: DayContextMenu sub-component.
- L678–757: TaskChip sub-component.
- L736–796: MilestoneChip sub-component.
- L758–906: DroppableDay sub-component + MonthGrid.
- L908–999: WeekStrip sub-component.

### `components/Board.tsx` — 893 LOC

- L1–55: Imports (including dynamic BoardDndCanvas).
- L56–220: Types, constants (COLUMNS, Sep, isScheduledNoise, groupByStatus, HeaderIconBtn, HeaderIconLink).
- L222–530: Board function — state (tasks, toasts, selectedTask, view, showAudit, milestoneFilter, doneHasMore, modal flags), 4 useEffects, event callbacks (onCrossLaneDrop, handleLoadMoreDone, onSameLaneReorder), filtered/grouped task memos.
- L531–893: JSX — nav header with all icon buttons, main panel switcher (board/list), sidebar (TaskDetail), analytics panels (CostSummary, PnlSummaryCard, ProgressChartsPanel, AuditHistorySection), modals (KillProject, Pause, ProjectConsent, PlatformSettings).

### `components/NewTaskModal.tsx` — 889 LOC

- L1–41: Imports.
- L42–105: Type defs, constants, helpers.
- L106–460: NewTaskModal function — form state (title, status, priority, role, run_mode, milestone, dates, AC list, template, override), 3 useEffects (milestone load, template load, pause-state), 10+ handlers.
- L460–889: JSX — ModalShell wrapping form with all field groups.

### `components/AiTaskModal.tsx` — 811 LOC

- L1–45: Imports.
- L46–105: Type defs.
- L106–420: AiTaskModal function — two-step (parse prompt → confirm), state mirrors NewTaskModal. Known duplicate of NewTaskModal logic (Kanban comment trail).
- L420–811: JSX — step 1 (prompt), step 2 (confirm form, same fields as NewTaskModal).

### `components/GanttView.tsx` — 825 LOC

- L1–101: Imports, status color maps.
- L102–545: Main GanttView export — fetch milestones + tasks, timeline geometry helpers.
- L546–753: RailRow, TaskChip, UnassignedPool sub-components.
- L755–825: renderBar SVG helper.

### `lib/safeMarkdown.tsx` — 428 LOC

- L1–47: Imports, type defs.
- L48–100: URL safety validators (hasUnsafeUrlChars, hasUserinfo, isSafeLinkUrl, isSafeImgUrl).
- L101–250: parseInline — inline markdown parser (bold, italic, code, links, images).
- L251–424: parseBlocks + renderBlock — block-level parser (headings, code fence, blockquote, lists, tables, paragraphs).
- L425–428: renderMarkdown export.

---

## Cross-cutting observations (facts only)

### Feature flags (NEXT_PUBLIC_*)

| var | default | gates |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `""` | backend URL for browser fetch + wildcard SSE base |
| `NEXT_PUBLIC_FINANCE_PANELS_ENABLED` | `false` | PnlSummaryCard in Board, PnlDashboardSection on dashboard (dynamic import), LlmSpendSection comment notes it is ungated |
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | unset | Web Push subscription; push.ts logs warning when absent |
| `INTERNAL_API_URL` | falls back to `NEXT_PUBLIC_API_URL` | SSR-side fetch (not `NEXT_PUBLIC_` so not in featureFlags.ts) |

`LlmSpendSection.tsx:7` comment states it is "below" the FINANCE_PANELS_ENABLED gate, but the import at `dashboard/page.tsx:41` is outside the conditional block — LlmSpendSection renders regardless of the flag.

### SSE architecture (two channels)

Board.tsx uses `useRowChangedEvents` directly with `projectId` — a scoped per-project EventSource.

Dashboard-level consumers (InboxBadge, FlagBellBadge, DashboardRefresher) share one wildcard EventSource through `WildcardSSEProvider` (mounted in `ClientProviders`) via `useWildcardRowChanged`. This was consolidated in Kanban #2111 (previously 3 separate connections).

ReviewClient.tsx uses `useRowChangedEvents` (same as Board) with no projectId for the review page.

### dnd-kit code-split (#2111)

`Board.tsx` dynamic-imports `BoardDndCanvas` with `ssr: false`. BoardDndCanvas owns the entire `@dnd-kit/core` + `@dnd-kit/sortable` import graph. The bundle for list-view and all other pages does not load dnd-kit.

`CalendarView.tsx` imports dnd-kit directly (not dynamic) — the calendar chunk includes dnd-kit.

### Modal infrastructure

`ModalShell` is the shared modal chrome. 17 components import it. Two components implement their own modal chrome without ModalShell:

- `DatePicker.tsx`: inline `role="dialog"` popover (not a full modal).
- `TaskDetail.tsx`: right-side drawer with its own backdrop + ESC handling.

No second parallel modal abstraction exists; ModalShell adoption is near-complete.

### Duplicate UI patterns (acknowledged in source comments)

- **Expand/collapse with localStorage**: CostSummary, PnlSummaryCard, PnlDashboardSection, AuditorActivityPanel, ResourcesPanel all duplicate the readExpanded/writeExpanded + local ChevronDown/Right icons pattern. The readExpanded/writeExpanded helpers in `lib/collapseState.ts` are shared but the icon definitions are inline-duplicated in PnlSummaryCard and PnlDashboardSection.

- **NewTaskModal / AiTaskModal**: 800+ LOC each. Field set, override logic, template wiring, and 423-toast pattern are near-duplicates. Source comments cite this explicitly (e.g. "same shape as NewTaskModal", "same override-pair semantics").

- **KilledBanner / PausedBanner**: same "1-minute interval against project timestamp" pattern.

- **InboxBadge / FlagBellBadge**: same "60s poll + SSE invalidation" pattern.

### Components imported by exactly one caller

Components that have a single import-site (tightly coupled to one parent):

`Board.tsx` as sole importer: AuditHistorySection, CostSummary (also dashboard), KilledBanner, PausedBanner, ProductTourBoardResume, ProjectConsentBanner, PlatformSettingsModal, ResourcesPanel, SourcesBadge.

`TaskDetail.tsx` as sole importer: TaskComments, TaskMuteToggle, TaskToolCalls.

`TaskFocusView.tsx` as sole importer: TaskActionButtons, TaskHaltModal, TaskRejectModal.

`ReviewClient.tsx` as sole importer: MassActionBar, TerminateFlagModal.

`ProjectFlagCard.tsx` as sole importer: AdjustFlagForm.

`ProjectConsentBanner.tsx` as sole importer: (ProjectConsentGrantModal also imported by Board.tsx directly — 2 callers).

### lib/api.ts exports referenced vs. not referenced in app/components

Functions confirmed called from app/ or components/: all major CRUD + query functions are reached. 

Functions with no observed call site outside tests or lib itself:
- `getTaskBlocks` — imported in TaskDetail.tsx but no `await getTaskBlocks` call found in scan; may be conditional. Requires deeper read to confirm.
- `listAllTasks` without `pending:true` flag — `listAllTasks` is called in inbox page with no pending filter; in board page with `pending:true`. The non-pending full-set variant is effectively retired from pages (inbox currently uses `listTasks` per-project with a status filter).

### DONE-lane keyset pagination (#2112)

`app/p/[name]/page.tsx` SSR-fetches only the first 50 DONE tasks via `listDoneLanePage`. Board.tsx appends further pages client-side via `handleLoadMoreDone`. The `visibleDoneCount` and `doneHasMore` state resets on SSE refresh.

### All routes are `force-dynamic`

Every page in `app/` except the root redirect exports `dynamic = "force-dynamic"` — no static generation, no ISR. All data is fetched at request time.

---

## Open questions

- `getTaskBlocks` (`lib/api.ts:940`) — imported in TaskDetail.tsx but no confirmed `await getTaskBlocks(...)` call found in the grep scan. Need to confirm whether it is dead at the call-site or conditionally reached (e.g. inside a handler that wasn't captured by the grep pattern).
- `lib/parseSteps.ts` — only one referencing file found by grep but the grep pattern was broad. Confirm which component uses it (likely TaskCard or TaskDetail step counter).
- `app/settings/page.tsx` (42 LOC) — appears to be a thin shell; the actual settings UI was not confirmed (PushNotificationsPanel + ThemePicker referenced in board nav, not clearly wired to this page in the scan).
- `LlmSpendSection` ungated behavior: comment at `LlmSpendSection.tsx:7` says it lives "below the FINANCE_PANELS_ENABLED gate" but `dashboard/page.tsx:41` imports it unconditionally. Confirm whether the JSX render is inside or outside the `{FINANCE_PANELS_ENABLED && ...}` block.
- `lib/collapseState.ts` ChevronDown/Right icon duplication in PnlSummaryCard + PnlDashboardSection — these are local copies, not Icon sprite references. Confirm whether Icon.tsx sprite has chevron entries that could replace them.

## Followups

- NewTaskModal / AiTaskModal share 800+ LOC of near-identical logic. A shared `TaskFormFields` component or hook could halve both files; out of scope for this map.
- `lib/api.ts` at 2 321 LOC: candidates for domain split (project, task, finance, resources, push, milestones, templates). Purely additive refactor; out of scope.
- CalendarView dnd-kit is imported statically while Board's is dynamic — if CalendarView grows in usage this becomes a bundle-size consideration.

## Standards insights (proposal only — Lead applies)

- **Collapse-panel sub-pattern**: readExpanded/writeExpanded shared, but ChevronDown/Right + toggle logic are re-implemented in 5 places. A `CollapsiblePanel` component wrapping the shared hook + icon would eliminate repetition.
- **Dual task-creation modals**: NewTaskModal and AiTaskModal diverge slowly over time (Kanban trail confirms intentional parity). A shared form-fields hook or compound component could prevent the two from drifting further.
