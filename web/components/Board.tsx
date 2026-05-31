"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ListView } from "@/components/ListView";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";

import {
  patchTask,
  reorderTask,
  type ProjectRead,
  type ProjectStatsEntry,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { readEnabledRoles } from "@/lib/enabledRoles";
import { extractErrorMessage } from "@/lib/errors";
import { sortDoneLane, sortLaneTasks } from "@/lib/sortLaneTasks";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";
import { BoardColumn } from "@/components/BoardColumn";
import { ConnectionStateBadge } from "@/components/ConnectionStateBadge";
import { Icon } from "@/components/Icon";
import { AiTaskModal } from "@/components/AiTaskModal";
import { AuditHistorySection } from "@/components/AuditHistorySection";
import { CostSummary } from "@/components/CostSummary";
import { FlagBellBadge } from "@/components/FlagBellBadge";
import { PnlSummaryCard } from "@/components/PnlSummaryCard";
import { FINANCE_PANELS_ENABLED } from "@/lib/featureFlags";
import { KilledBanner } from "@/components/KilledBanner";
import { KillProjectModal } from "@/components/KillProjectModal";
import { NewTaskModal } from "@/components/NewTaskModal";
import { PausedBanner } from "@/components/PausedBanner";
import { PauseProjectModal } from "@/components/PauseProjectModal";
import { ProjectConsentBanner } from "@/components/ProjectConsentBanner";
import { PlatformSettingsModal } from "@/components/PlatformSettingsModal";
import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { SourcesBadge } from "@/components/SourcesBadge";
import { TaskDetail } from "@/components/TaskDetail";
import { Switch } from "@/components/Switch";
import { ThemePicker } from "@/components/ThemePicker";
import { ToastStack, type ToastMessage } from "@/components/Toast";

type Props = {
  initialTasks: TaskRead[];
  hasHeadlessTask: boolean;
  project: ProjectRead;
  // Kanban #1289 — per-project usage panel. 0 entries = project has no stats
  // row yet; 1 entry = scoped stats from GET /api/projects/stats?project_id=<id>.
  projectStats: ProjectStatsEntry[];
};

type Column = { statuses: TaskStatusValue[]; label: string; key: string };

const COLUMNS: Column[] = [
  { statuses: [TaskStatus.TODO], label: "New tasks", key: "1" },
  { statuses: [TaskStatus.IN_PROGRESS], label: "In progress", key: "2" },
  { statuses: [TaskStatus.REVIEW], label: "Review", key: "3" },
  { statuses: [TaskStatus.BLOCKED], label: "Blocked", key: "4" },
  { statuses: [TaskStatus.DONE], label: "Done", key: "5" },
];

const ALL_STATUSES: TaskStatusValue[] = [
  TaskStatus.TODO,
  TaskStatus.IN_PROGRESS,
  TaskStatus.REVIEW,
  TaskStatus.BLOCKED,
  TaskStatus.DONE,
];

const COLUMN_PS: Record<string, TaskStatusValue> = Object.fromEntries(
  COLUMNS.flatMap((col) => col.statuses.map((s) => [col.key, s] as const)),
);

// #1726 — recurrence noise: templates (is_template=true) and scheduled-fire
// instances (title prefix "[schedule:") are excluded from the visible board.
const isScheduledNoise = (t: TaskRead) =>
  t.is_template || t.title.startsWith("[schedule:");

function groupByStatus(tasks: TaskRead[]) {
  const groups = new Map<TaskStatusValue, TaskRead[]>();
  for (const s of ALL_STATUSES) groups.set(s, []);
  for (const task of tasks) {
    const bucket = groups.get(task.process_status);
    if (bucket) bucket.push(task);
  }
  // #772 #826 — lane sort: sortLaneTasks (TODO..REVIEW), sortDoneLane (DONE); details in shared/decisions.md
  for (const [ps, bucket] of groups.entries()) {
    if (ps === TaskStatus.DONE) {
      const sorted = sortDoneLane(bucket);
      bucket.length = 0;
      bucket.push(...sorted);
      continue;
    }
    bucket.sort((a, b) => b.priority - a.priority || a.id - b.id);
    const sorted = sortLaneTasks(bucket);
    bucket.length = 0;
    bucket.push(...sorted);
  }
  return groups;
}

type ViewMode = "board" | "list";

export function Board({ initialTasks, hasHeadlessTask, project, projectStats }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [tasks, setTasks] = useState<TaskRead[]>(initialTasks);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  // #1001 follow-up (2026-05-20) — `?task=<id>` deep-link state. Set when
  // a freshly-loaded board has a matching task id; cleared after the scroll
  // + highlight settles via router.replace (so a manual F5 doesn't re-fire).
  const [highlightedTaskId, setHighlightedTaskId] = useState<number | null>(null);
  // De-duplicate the deep-link effect within a single mount — Strict mode +
  // hot reload would otherwise re-fire it and re-scroll mid-edit.
  const deepLinkHandledRef = useRef(false);
  const toastIdRef = useRef(1);

  // View toggle — default 'board'; persisted per-project in localStorage.
  // SSR-safe: initial state always 'board'; hydrated from localStorage in useEffect.
  const [view, setView] = useState<ViewMode>("board");

  // #1238 GOV3 — audit-task lane filter. Audit tasks are governance noise on
  // the main Kanban (they're not operator-actionable from the board — the
  // /review page resolves the resulting flag). Default OFF (filter them out);
  // operator toggles ON to see the full audit trail inline. Session-scoped
  // pref only — no localStorage persistence for v1 (keeps the toggle visible
  // as a discoverable affordance every session).
  const [showAudit, setShowAudit] = useState(false);

  // #1288 — Switch-driven modal open state for project controls group.
  const [terminateModalOpen, setTerminateModalOpen] = useState(false);
  const [pauseModalOpen, setPauseModalOpen] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(`kanban-view-${project.name}`);
    if (stored === "list" || stored === "board") setView(stored);
  }, [project.name]);

  // #1001 follow-up (2026-05-20) — `?task=<id>` deep-link handler.
  //
  // On mount (or first ready `tasks` snapshot), read the URL search param.
  // If `task` parses to an int AND a matching card is in the loaded list:
  //   1. setHighlightedTaskId — triggers the ring-pulse class on the card.
  //   2. scrollIntoView (smooth, center) — pulls the card into view.
  //   3. router.replace — strip the query param so a manual F5 doesn't
  //      re-fire the highlight (the operator probably scrolled away by then).
  //   4. setTimeout(2200) — clear the highlight state so the pulse animation
  //      ends cleanly (it's a 2s keyframe; the +200ms buffer lets the last
  //      frame paint).
  // If task id doesn't exist in the loaded list, render an inline toast.
  // Effect re-runs only when searchParams changes — internal task list
  // updates (SSE) don't re-trigger the scroll.
  useEffect(() => {
    if (deepLinkHandledRef.current) return;
    const raw = searchParams?.get("task");
    if (!raw) return;
    const parsed = Number(raw);
    if (!Number.isInteger(parsed) || parsed < 1) return;

    deepLinkHandledRef.current = true;
    const match = tasks.find((t) => t.id === parsed);
    if (!match) {
      pushToast(`Task #${parsed} not found in this project`);
      // Strip the query param so the toast doesn't re-fire on re-render.
      router.replace(pathname);
      return;
    }

    setHighlightedTaskId(parsed);

    // Defer the scroll one tick so React commits the card render first.
    // requestAnimationFrame is friendlier than setTimeout(0) for paint sync.
    if (typeof window !== "undefined") {
      requestAnimationFrame(() => {
        const card = document.querySelector<HTMLElement>(
          `[data-task-card-id="${parsed}"]`,
        );
        if (card) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
    }

    // Clear the highlight after the 2s pulse animation finishes.
    const clearAt = setTimeout(() => setHighlightedTaskId(null), 2200);
    // Strip the query param now — by the time the operator interacts again
    // a stale param would confusingly re-pulse. Pathname keeps the URL clean.
    router.replace(pathname);
    return () => clearTimeout(clearAt);
    // tasks intentionally in deps so the lookup runs once initialTasks is
    // ready; the handledRef guard ensures one-shot per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, tasks]);

  function handleViewChange(v: ViewMode) {
    setView(v);
    localStorage.setItem(`kanban-view-${project.name}`, v);
  }

  // Sync local tasks state to server snapshot on each RSC refresh
  useEffect(() => {
    setTasks(initialTasks);
  }, [initialTasks]);

  // #783 — SSE-driven router.refresh(); 100ms debounce + 5-event hard cap in hook
  const onRowChange = useCallback(() => {
    router.refresh();
  }, [router]);
  const { connectionState, lastEventAt } = useRowChangedEvents({
    projectId: project.id,
    onTaskChange: onRowChange,
    onProjectChange: onRowChange,
  });

  const pushToast = useCallback((text: string) => {
    const id = toastIdRef.current++;
    setToasts((prev) => [...prev, { id, text }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // #1238 GOV3 — audit-task tally is computed against the unfiltered list so
  // the toggle chip can show "Show audit tasks (N)" even when the filter is
  // active. AuditHistorySection consumes the full audit list separately (via
  // listProjectAuditTasks on its own fetch path); the board-local count here
  // only needs the in-memory tasks snapshot.
  const auditTaskCount = useMemo(
    () => tasks.filter((t) => t.task_type === "audit").length,
    [tasks],
  );
  const auditTasks = useMemo(
    () =>
      [...tasks.filter((t) => t.task_type === "audit")].sort((a, b) => {
        const aDone = a.completed_at ?? "";
        const bDone = b.completed_at ?? "";
        if (aDone === bDone) return b.id - a.id;
        return aDone < bDone ? 1 : -1;
      }),
    [tasks],
  );
  // #1726 — scheduled/template task count (computed against full list so the
  // chip shows the real number even when the board is otherwise filtered).
  const scheduledTaskCount = useMemo(
    () => tasks.filter(isScheduledNoise).length,
    [tasks],
  );
  const visibleTasks = useMemo(() => {
    const base = showAudit ? tasks : tasks.filter((t) => t.task_type !== "audit");
    return base.filter((t) => !isScheduledNoise(t));
  }, [tasks, showAudit]);

  const grouped = useMemo(() => groupByStatus(visibleTasks), [visibleTasks]);

  const selectedTask = useMemo(
    () =>
      selectedTaskId === null
        ? null
        : tasks.find((t) => t.id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
  );

  const onOpenDetail = useCallback((task: TaskRead) => {
    setSelectedTaskId(task.id);
  }, []);

  const onPatchedTask = useCallback((updated: TaskRead) => {
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }, []);

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over) return;
      const taskId = Number(active.id);
      const original = tasks.find((t) => t.id === taskId);
      if (!original) return;
      if (original.task_kind === "ai") return;

      // Drop target: column key string = cross-lane, number = same/cross-lane task
      let newPs: TaskStatusValue | undefined;
      let overTask: TaskRead | undefined;
      if (typeof over.id === "string") {
        newPs = COLUMN_PS[over.id];
      } else {
        overTask = tasks.find((t) => t.id === over.id);
        if (overTask === undefined) return;
        newPs = overTask.process_status;
      }
      if (newPs === undefined) return;

      // Cross-lane: PATCH process_status; same-lane: reorderTask via sort_order
      if (original.process_status !== newPs) {
        setTasks((prev) =>
          prev.map((t) =>
            t.id === taskId ? { ...t, process_status: newPs } : t,
          ),
        );
        patchTask(project.id, taskId, { process_status: newPs })
          .then((server) => {
            setTasks((prev) =>
              prev.map((t) => (t.id === taskId ? server : t)),
            );
          })
          .catch((err: unknown) => {
            setTasks((prev) =>
              prev.map((t) => (t.id === taskId ? original : t)),
            );
            const msg = extractErrorMessage(err, "Update failed");
            pushToast(`Task #${taskId}: ${msg}`);
          });
        return;
      }

      // Same-lane reorder only in TODO lane (#772); other lanes: silent no-op
      if (newPs !== TaskStatus.TODO) return;
      if (!overTask) return;
      if (overTask.id === original.id) return;

      // after_id: active moved down (anchor below); before_id: moved up (#772)
      const laneIds = (grouped.get(TaskStatus.TODO) ?? []).map((t) => t.id);
      const oldIndex = laneIds.indexOf(original.id);
      const newIndex = laneIds.indexOf(overTask.id);
      if (oldIndex === -1 || newIndex === -1) return;
      const body =
        oldIndex < newIndex
          ? { after_id: overTask.id }
          : { before_id: overTask.id };

      // #772 — within-lane: no optimistic mutation (dnd-kit transform handles visual; snap-back on 422). Details: shared/decisions.md 2026-05-14
      reorderTask(project.id, taskId, body)
        .then((server) => {
          setTasks((prev) => prev.map((t) => (t.id === taskId ? server : t)));
        })
        .catch((err: unknown) => {
          const msg = extractErrorMessage(err, "Reorder failed");
          pushToast(`Task #${taskId}: ${msg}`);
        });
    },
    [tasks, grouped, project.id, pushToast],
  );

  return (
    // #954 — mobile: page scrolls (h-auto, overflow-y-auto); desktop preserves the fixed-viewport board (h-screen, overflow-hidden)
    <main className="flex min-h-screen flex-col overflow-y-auto bg-white dark:bg-zinc-950 px-4 py-4 sm:px-6 sm:py-5 lg:h-screen lg:min-h-0 lg:overflow-hidden">
      <header className="mb-4 flex flex-col gap-2">
        {/* #954 — header wraps on mobile so badges/toggle drop to a second row instead of overflowing horizontally */}
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <ProjectSwitcher current={project.name} />
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <Link
            href="/dashboard"
            className="text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Dashboard
          </Link>
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          {/* #1349 — per-project settings (nudge threshold + future knobs). */}
          <Link
            href={`/p/${encodeURIComponent(project.name)}/settings`}
            className="text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Settings
          </Link>
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <span className="text-zinc-600 dark:text-zinc-400">
            team: <span className="text-zinc-900 dark:text-zinc-100">{project.team}</span>
          </span>
          {project.sources.length > 0 && (
            <>
              <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
                ·
              </span>
              <SourcesBadge sources={project.sources} />
            </>
          )}
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <span className="text-zinc-500 dark:text-zinc-400 tabular-nums">
            {visibleTasks.length} task{visibleTasks.length === 1 ? "" : "s"}
          </span>
          {auditTaskCount > 0 && (
            <>
              <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
                ·
              </span>
              {/* #1238 GOV3 — audit-task filter toggle. Default OFF; chip click
                  flips the local pref. Hidden entirely when the project has
                  no audit tasks at all so it doesn't add chrome for nothing. */}
              <button
                type="button"
                onClick={() => setShowAudit((v) => !v)}
                aria-pressed={showAudit}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors min-h-[36px] sm:min-h-0 ${
                  showAudit
                    ? "border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-200"
                    : "border-zinc-200 bg-transparent text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800"
                }`}
                data-audit-task-toggle
              >
                <span>{showAudit ? "Hide" : "Show"} audit tasks</span>
                <span className="rounded bg-white/60 px-1 text-[10px] tabular-nums text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300">
                  {auditTaskCount}
                </span>
              </button>
            </>
          )}
          {/* #1726 — scheduled/template noise chip. Display-only; always hidden
              when count=0 so it adds no chrome for projects without recurrence. */}
          {scheduledTaskCount > 0 && (
            <>
              <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
                ·
              </span>
              <span className="inline-flex items-center gap-1 rounded-full border border-zinc-200 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-700 dark:text-zinc-400 min-h-[36px] sm:min-h-0">
                <span>scheduled</span>
                <span className="rounded bg-white/60 px-1 text-[10px] tabular-nums text-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-300">
                  {scheduledTaskCount}
                </span>
              </span>
            </>
          )}
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <ConnectionStateBadge
            state={connectionState}
            lastEventAt={lastEventAt}
          />
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">·</span>
          {/* #954 — 44px min tap target on mobile for view-mode toggle */}
          <span className="inline-flex items-center rounded-md border border-zinc-200 dark:border-zinc-700 overflow-hidden text-xs">
            {(["board", "list"] as const).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => handleViewChange(v)}
                aria-pressed={view === v}
                className={`inline-flex items-center px-3 py-2 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 capitalize transition-colors ${
                  view === v
                    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 font-semibold"
                    : "bg-transparent text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                }`}
              >
                <Icon name={v === "board" ? "view-board" : "view-list"} size={14} aria-hidden />
                <span className="ml-1.5">{v}</span>
              </button>
            ))}
          </span>
          {/* #1288 — header right cluster: two visually distinct groups
              (Task actions | Project controls) + standalone ThemePicker. */}
          <span className="ml-auto flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">

            {/* ── Group A: Task actions ─────────────────────────────────── */}
            <div
              className="flex items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900"
              aria-label="Task actions"
            >
              <span className="mr-1 text-[9px] font-semibold uppercase tracking-widest text-zinc-400 dark:text-zinc-500 select-none">
                Tasks
              </span>
              {/* #7 §A AC#3 — per-project role whitelist; null/empty → all roles */}
              <AiTaskModal
                projectId={project.id}
                enabledRoles={readEnabledRoles(project.config)}
                project={project}
                onPushToast={pushToast}
              />
              <NewTaskModal
                projectId={project.id}
                enabledRoles={readEnabledRoles(project.config)}
                project={project}
                onPushToast={pushToast}
              />
            </div>

            {/* ── Divider ───────────────────────────────────────────────── */}
            <span aria-hidden className="h-5 w-px bg-zinc-200 dark:bg-zinc-700" />

            {/* ── Group B: Project controls ─────────────────────────────── */}
            <div
              className="flex items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900"
              aria-label="Project controls"
            >
              <span className="mr-1 text-[9px] font-semibold uppercase tracking-widest text-zinc-400 dark:text-zinc-500 select-none">
                Project
              </span>
              {/* #1288 — pause Switch. ON = live, OFF = paused.
                  Switch click → opens PauseProjectModal (existing flow).
                  Hidden when project is already terminated (mutex). */}
              {!project.is_killed && (
                <>
                  <Switch
                    label="Pause"
                    checked={project.is_paused ?? false}
                    colorOn="amber"
                    onClick={() => setPauseModalOpen(true)}
                    aria-label={project.is_paused ? "Project paused — click to unpause" : "Pause project"}
                  />
                  <PauseProjectModal
                    project={project}
                    mode={project.is_paused ? "unpause" : "pause"}
                    externalOpen={pauseModalOpen}
                    onExternalClose={() => setPauseModalOpen(false)}
                  />
                </>
              )}
              {/* #1288 — terminate Switch. ON = live, OFF = terminated.
                  Switch click → opens KillProjectModal (existing flow).
                  Visible only when not currently terminated; revive lives
                  inline in KilledBanner. */}
              {!project.is_killed && (
                <>
                  <Switch
                    label="Terminate"
                    checked={false}
                    colorOn="red"
                    onClick={() => setTerminateModalOpen(true)}
                    aria-label="Terminate project"
                  />
                  <KillProjectModal
                    project={project}
                    mode="kill"
                    externalOpen={terminateModalOpen}
                    onExternalClose={() => setTerminateModalOpen(false)}
                  />
                </>
              )}
            </div>

            {/* ── FlagBellBadge — review notification, outside both groups ─ */}
            <FlagBellBadge />

            {/* ── PlatformSettingsModal — #1655 integrations gear, outside
                both groups (platform-wide, not per-project) ────────────── */}
            <PlatformSettingsModal />

            {/* ── ThemePicker — user preference, outside both groups ────── */}
            <ThemePicker />
          </span>
        </div>
        {/* Kanban #1392 — Usage + P&L side-by-side on md+; stacked on sm.
            P&L panel gated by NEXT_PUBLIC_FINANCE_PANELS_ENABLED flag;
            when off, Usage renders full-width (no grid wrapper). */}
        <div className={FINANCE_PANELS_ENABLED ? "grid grid-cols-1 md:grid-cols-2 gap-3" : ""}>
          {/* Kanban #1289 — per-project usage panel. Collapsed by default on the
              project board (dense page). storageKey scoped per project so each
              project remembers its own expand state independently. */}
          <CostSummary
            stats={projectStats}
            ariaLabel={`Usage for ${project.name}`}
            defaultCollapsed={true}
            storageKey={`project.${project.id}.panels.usage.expanded`}
          />
          {/* Kanban #1329 (M6 FE) — per-project P&L card. Sources
              /api/projects/{id}/pl; period selector + localStorage default. */}
          {FINANCE_PANELS_ENABLED && (
            <PnlSummaryCard
              projectId={project.id}
              projectName={project.name}
              defaultCollapsed={true}
              storageKey={`project.${project.id}.panels.pnl.expanded`}
            />
          )}
        </div>
        {/* #1209 GOV1 D5 — red strip above the consent banner when killed.
            (Renders nothing when is_killed=false.) */}
        <KilledBanner project={project} />
        {/* #1211 / #1238 GOV3 — amber strip above the consent banner when paused.
            (Renders nothing when is_paused=false.) */}
        <PausedBanner project={project} />
        <ProjectConsentBanner
          project={project}
          hasHeadlessTask={hasHeadlessTask}
        />
      </header>
      {view === "list" ? (
        <ListView
          tasks={visibleTasks}
          onOpenDetail={onOpenDetail}
          highlightedTaskId={highlightedTaskId}
        />
      ) : (
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          {/* #954 — mobile: page scrolls (no overflow-hidden, no min-h-0); desktop restores the fixed-height bounded lanes at lg */}
          <div
            data-board="dnd"
            className="grid flex-1 grid-cols-1 gap-3 md:grid-cols-3 lg:min-h-0 lg:grid-cols-5 lg:overflow-hidden"
          >
            {COLUMNS.map((col) => (
              <BoardColumn
                key={col.key}
                columnId={col.key}
                statuses={col.statuses}
                label={col.label}
                tasks={col.statuses.flatMap((s) => grouped.get(s) ?? [])}
                onOpenDetail={onOpenDetail}
                sortable={col.statuses.includes(TaskStatus.TODO)}
                highlightedTaskId={highlightedTaskId}
              />
            ))}
          </div>
        </DndContext>
      )}
      {/* #1238 GOV3 — Audit History archive below the lanes. Self-collapses;
          shows "No audit history yet." when the project has no audit_task rows.
          Sources from the in-memory tasks snapshot (no extra fetch — every
          audit task is already in `tasks` via the initial /api/tasks limit=500
          fetch + SSE refresh). */}
      <AuditHistorySection auditTasks={auditTasks} />
      {selectedTask && (
        <TaskDetail
          task={selectedTask}
          allTasks={tasks}
          projectId={project.id}
          onClose={() => setSelectedTaskId(null)}
          onPatch={onPatchedTask}
          onError={pushToast}
        />
      )}
      <ToastStack messages={toasts} onDismiss={dismissToast} />
    </main>
  );
}
