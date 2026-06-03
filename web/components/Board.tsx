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
  type ProgressStatsResponse,
  type ProjectRead,
  type ProjectStatsEntry,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { sortDoneLane, sortLaneTasks } from "@/lib/sortLaneTasks";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";
import { BoardColumn } from "@/components/BoardColumn";
import { ConnectionStateBadge } from "@/components/ConnectionStateBadge";
import { Icon } from "@/components/Icon";
import { AuditHistorySection } from "@/components/AuditHistorySection";
import { CostSummary } from "@/components/CostSummary";
import { FlagBellBadge } from "@/components/FlagBellBadge";
import { PnlSummaryCard } from "@/components/PnlSummaryCard";
import { ProgressChartsPanel } from "@/components/ProgressChartsPanel";
import { FINANCE_PANELS_ENABLED } from "@/lib/featureFlags";
import { KilledBanner } from "@/components/KilledBanner";
import { KillProjectModal } from "@/components/KillProjectModal";
import { NewTaskDropdown } from "@/components/NewTaskDropdown";
import { PausedBanner } from "@/components/PausedBanner";
import { PauseProjectModal } from "@/components/PauseProjectModal";
import { ProjectConsentBanner } from "@/components/ProjectConsentBanner";
import { PlatformSettingsModal } from "@/components/PlatformSettingsModal";
import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { SourcesBadge } from "@/components/SourcesBadge";
import { TaskDetail } from "@/components/TaskDetail";
import { ThemePicker } from "@/components/ThemePicker";
import { ToastStack, type ToastMessage } from "@/components/Toast";

type Props = {
  initialTasks: TaskRead[];
  hasHeadlessTask: boolean;
  project: ProjectRead;
  // Kanban #1289 — per-project usage panel. 0 entries = project has no stats
  // row yet; 1 entry = scoped stats from GET /api/projects/stats?project_id=<id>.
  projectStats: ProjectStatsEntry[];
  // Kanban #1292 — SSR-fetched burndown + velocity series for the progress
  // charts panel. Always present (the BE zero-fills every bucket).
  progressStats: ProgressStatsResponse;
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

// Nav-row separator — 7 identical occurrences in the header row.
const Sep = () => (
  <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
    ·
  </span>
);

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

// #1781 — compact header icon button with a visible tooltip + aria-label.
// Used for the audit-filter / scheduled / pause / terminate controls so the
// header collapses to a single nav row. `active` paints the on-state color;
// `count` renders a small badge (hidden when undefined).
function HeaderIconBtn({
  icon,
  label,
  onClick,
  active = false,
  ariaPressed,
  count,
  tone = "neutral",
  dataAttr,
}: {
  icon: string;
  label: string;
  onClick?: () => void;
  active?: boolean;
  ariaPressed?: boolean;
  count?: number;
  tone?: "neutral" | "amber" | "red";
  dataAttr?: string;
}) {
  const toneActive =
    tone === "amber"
      ? "border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-200"
      : tone === "red"
        ? "border-red-400 bg-red-100 text-red-900 dark:border-red-700 dark:bg-red-950/40 dark:text-red-300"
        : "border-zinc-400 bg-zinc-100 text-zinc-900 dark:border-zinc-500 dark:bg-zinc-800 dark:text-zinc-100";
  const toneHover =
    tone === "amber"
      ? "hover:border-amber-300 hover:text-amber-700 dark:hover:text-amber-300"
      : tone === "red"
        ? "hover:border-red-300 hover:text-red-700 dark:hover:text-red-300"
        : "hover:border-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100";
  const dataProps = dataAttr ? { [dataAttr]: true } : {};
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={ariaPressed}
      className={`group relative inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-zinc-500 transition-colors min-h-[44px] sm:min-h-0 dark:text-zinc-400 ${
        active
          ? toneActive
          : `border-zinc-200 bg-transparent dark:border-zinc-700 ${toneHover}`
      }`}
      {...dataProps}
    >
      <Icon name={icon} size={15} aria-hidden />
      {count !== undefined && (
        <span className="rounded bg-white/70 px-1 text-[10px] font-semibold tabular-nums text-zinc-600 dark:bg-zinc-900/50 dark:text-zinc-300">
          {count}
        </span>
      )}
      {/* Visible tooltip (sm+) — mobile relies on the 44px tap target + label. */}
      <span
        className="pointer-events-none absolute top-full left-1/2 z-50 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] font-medium text-white opacity-0 transition-opacity sm:group-hover:opacity-100 sm:group-focus-visible:opacity-100 dark:bg-zinc-700"
      >
        {label}
      </span>
    </button>
  );
}

export function Board({ initialTasks, hasHeadlessTask, project, projectStats, progressStats }: Props) {
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

  // Kanban #1787 — needs-attention badge: tasks requiring human action.
  // Predicate: REVIEW (ps=3) | BLOCKED (ps=4) | is_pending=true | halt_reason non-empty.
  // halt_reason catches halted-but-not-BLOCKED tasks (e.g. operator-HOLD tasks
  // parked in TODO with a halt_reason set). TaskRead includes halt_reason (#1001).
  // Derived from the unfiltered tasks list (not visibleTasks) so the count
  // reflects the real state even when the audit-task filter is active.
  const needsAttentionCount = useMemo(
    () =>
      tasks.filter(
        (t) =>
          t.process_status === TaskStatus.REVIEW ||
          t.process_status === TaskStatus.BLOCKED ||
          t.is_pending === true ||
          (t.halt_reason != null && t.halt_reason !== ""),
      ).length,
    [tasks],
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
      {/* #1781 — header is height-capped on desktop (lg:max-h-[40vh] +
          overflow-y-auto) so the flex-1 board below is GUARANTEED >=60vh of
          the lg:h-screen main. The single nav row + compact panels band keep
          the header well under the cap; the cap is the hard structural floor. */}
      <header
        className="mb-4 flex flex-col gap-2 lg:max-h-[40vh] lg:overflow-y-auto lg:pr-1"
        data-board-header
        tabIndex={-1}
      >
        {/* #1781 — single nav row on desktop; flex-wrap still drops controls
            to extra rows on mobile. */}
        <div
          className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
          data-board-nav-row
        >
          <ProjectSwitcher current={project.name} />
          <Sep />
          <Link
            href="/dashboard"
            className="text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Dashboard
          </Link>
          <Sep />
          {/* #1349 — per-project settings (nudge threshold + future knobs).
              KEPT as a text link (distinct from the platform Integrations icon
              in the right cluster). */}
          <Link
            href={`/p/${encodeURIComponent(project.name)}/settings`}
            className="text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Settings
          </Link>
          <Sep />
          {/* #1868 — per-project Milestones surface (X-Project-Id scoped). */}
          <Link
            href={`/p/${encodeURIComponent(project.name)}/milestones`}
            className="text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Milestones
          </Link>
          <Sep />
          <span className="text-zinc-600 dark:text-zinc-400">
            team: <span className="text-zinc-900 dark:text-zinc-100">{project.team}</span>
          </span>
          {project.sources.length > 0 && (
            <>
              <Sep />
              <SourcesBadge sources={project.sources} />
            </>
          )}
          <Sep />
          <span className="text-zinc-500 dark:text-zinc-400 tabular-nums">
            {visibleTasks.length} task{visibleTasks.length === 1 ? "" : "s"}
          </span>
          {/* #1781 — audit-filter as a compact icon button (shield/filter +
              count badge). Still toggles setShowAudit; amber when ON;
              aria-pressed. Hidden when count=0 (#1238 GOV3 behaviour kept). */}
          {auditTaskCount > 0 && (
            <HeaderIconBtn
              icon="shield-filter"
              label={
                showAudit
                  ? `Hide audit tasks (${auditTaskCount})`
                  : `Show audit tasks (${auditTaskCount})`
              }
              onClick={() => setShowAudit((v) => !v)}
              active={showAudit}
              ariaPressed={showAudit}
              count={auditTaskCount}
              tone="amber"
              dataAttr="data-audit-task-toggle"
            />
          )}
          {/* #1781 — scheduled/template noise as a display-only clock icon +
              count badge. Hidden when count=0 (#1726 behaviour kept). */}
          {scheduledTaskCount > 0 && (
            <HeaderIconBtn
              icon="clock"
              label={`Scheduled / template tasks (${scheduledTaskCount})`}
              count={scheduledTaskCount}
              dataAttr="data-scheduled-task-badge"
            />
          )}
          {/* Kanban #1787 — needs-attention badge: tasks where REVIEW | BLOCKED |
              is_pending=true (halted tasks land on ps=4 which BLOCKED covers).
              Display-only count pill; data-needs-attention-count is the smoke anchor. */}
          {needsAttentionCount > 0 && (
            <HeaderIconBtn
              icon="alert"
              label={`Needs attention (${needsAttentionCount})`}
              count={needsAttentionCount}
              tone="amber"
              dataAttr="data-needs-attention-count"
            />
          )}
          <Sep />
          <ConnectionStateBadge
            state={connectionState}
            lastEventAt={lastEventAt}
          />
          <Sep />
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
          {/* #1781 — right cluster: +New dropdown, pause/terminate icon
              buttons, FlagBellBadge, Integrations, ThemePicker. All on the same
              row; ml-auto pushes them right on desktop, full-width wrap on
              mobile. */}
          <span
            className="ml-auto flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto"
            data-board-actions-cluster
          >
            {/* +New ▾ — single dropdown replacing the AI/Manual trigger pair.
                Drives the existing modals via externalOpen (same props/flow). */}
            <NewTaskDropdown project={project} onPushToast={pushToast} />

            {/* Pause / Terminate — icon buttons. Hidden when killed (mutex with
                the KilledBanner revive). Each opens the SAME modal via the
                existing externalOpen state. */}
            {!project.is_killed && (
              <>
                <HeaderIconBtn
                  icon="pause"
                  label={
                    project.is_paused
                      ? "Project paused — click to unpause"
                      : "Pause project"
                  }
                  onClick={() => setPauseModalOpen(true)}
                  active={project.is_paused ?? false}
                  ariaPressed={project.is_paused ?? false}
                  tone="amber"
                  dataAttr="data-pause-icon-btn"
                />
                <PauseProjectModal
                  project={project}
                  mode={project.is_paused ? "unpause" : "pause"}
                  externalOpen={pauseModalOpen}
                  onExternalClose={() => setPauseModalOpen(false)}
                />
                <HeaderIconBtn
                  icon="power"
                  label="Terminate project"
                  onClick={() => setTerminateModalOpen(true)}
                  tone="red"
                  dataAttr="data-terminate-icon-btn"
                />
                <KillProjectModal
                  project={project}
                  mode="kill"
                  externalOpen={terminateModalOpen}
                  onExternalClose={() => setTerminateModalOpen(false)}
                />
              </>
            )}

            {/* ── FlagBellBadge — review notification ───────────────────── */}
            <FlagBellBadge />

            {/* ── PlatformSettingsModal — #1655 / #1781 Integrations (plug
                icon, platform-wide; distinct from the per-project Settings
                nav link) ─────────────────────────────────────────────────── */}
            <PlatformSettingsModal />

            {/* ── ThemePicker — user preference ─────────────────────────── */}
            <ThemePicker />
          </span>
        </div>
        {/* #1781 — compact panels band: Usage / P&L / Progress in ONE row on
            desktop. Usage + P&L are collapsed by default (short); Progress is
            the new compact strip (small charts, tight padding). Grid is
            3-col when finance is on, 2-col (Usage + Progress) when off. */}
        <div
          className={`grid grid-cols-1 gap-3 ${
            FINANCE_PANELS_ENABLED ? "lg:grid-cols-3" : "lg:grid-cols-2"
          }`}
          data-board-panels-band
        >
          {/* Kanban #1289 — per-project usage panel. Collapsed by default. */}
          <CostSummary
            stats={projectStats}
            ariaLabel={`Usage for ${project.name}`}
            defaultCollapsed={true}
            storageKey={`project.${project.id}.panels.usage.expanded`}
          />
          {/* Kanban #1329 (M6 FE) — per-project P&L card (finance-gated). */}
          {FINANCE_PANELS_ENABLED && (
            <PnlSummaryCard
              projectId={project.id}
              projectName={project.name}
              defaultCollapsed={true}
              storageKey={`project.${project.id}.panels.pnl.expanded`}
            />
          )}
          {/* Kanban #1292 / #1781 — burndown + velocity in compact strip form,
              folded into the same band. Click→full modal unchanged. */}
          <ProgressChartsPanel
            data={progressStats}
            projectId={project.id}
            compact
          />
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
