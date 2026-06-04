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
  listMilestones,
  patchTask,
  reorderTask,
  type MilestoneRead,
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
import { ViewSwitcher } from "@/components/ViewSwitcher";

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

// Wave A (#2) — navigating sibling of HeaderIconBtn. Same compact icon chrome +
// hover tooltip, but renders a Next <Link> so Inbox / Settings stay real
// navigations (not onClick handlers). aria-label + title carry the text the
// former nav labels provided.
function HeaderIconLink({
  href,
  icon,
  label,
}: {
  href: string;
  icon: string;
  label: string;
}) {
  return (
    <Link
      href={href}
      aria-label={label}
      title={label}
      className="group relative inline-flex items-center rounded-md border border-zinc-200 bg-transparent px-2 py-1.5 text-zinc-500 transition-colors min-h-[44px] sm:min-h-0 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100"
    >
      <Icon name={icon} size={15} aria-hidden />
      {/* Visible tooltip (sm+) — mobile relies on the 44px tap target + label. */}
      <span className="pointer-events-none absolute top-full left-1/2 z-50 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] font-medium text-white opacity-0 transition-opacity sm:group-hover:opacity-100 sm:group-focus-visible:opacity-100 dark:bg-zinc-700">
        {label}
      </span>
    </Link>
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

  // #1868 v1.1 — milestone filter. "all" = no filter (default); "none" = only
  // tasks with milestone_id == null; number = only tasks pointing at that
  // milestone. Client-side filter on the in-memory `tasks` snapshot — the
  // board already filters audit + scheduled noise client-side and has no
  // client re-fetch path (initialTasks is SSR + SSE router.refresh()), so a
  // client predicate is the consistent, minimum-viable v1. `milestones` feeds
  // the dropdown; loaded once on mount (failure degrades to a no-op filter).
  const [milestoneFilter, setMilestoneFilter] = useState<"all" | "none" | number>("all");
  const [milestones, setMilestones] = useState<MilestoneRead[]>([]);

  // #1288 — Switch-driven modal open state for project controls group.
  const [terminateModalOpen, setTerminateModalOpen] = useState(false);
  const [pauseModalOpen, setPauseModalOpen] = useState(false);

  // Wave A (#1) — view seed precedence: URL `?view=` param > localStorage >
  // default 'board'. The ViewSwitcher's List link routes to `/p/<name>?view=list`
  // so the param must win on mount; we then mirror it into localStorage so the
  // per-project preference stays consistent with the URL the operator landed on.
  useEffect(() => {
    const fromUrl = searchParams?.get("view");
    if (fromUrl === "list" || fromUrl === "board") {
      setView(fromUrl);
      localStorage.setItem(`kanban-view-${project.name}`, fromUrl);
      return;
    }
    const stored = localStorage.getItem(`kanban-view-${project.name}`);
    if (stored === "list" || stored === "board") setView(stored);
    // searchParams intentionally read once on mount for the seed; the in-board
    // ViewSwitcher updates `view` state directly afterward (no re-navigation).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.name]);

  // #1868 v1.1 — load milestones for the filter dropdown. Client-side fetch on
  // mount (supplementary list, NOT the task list — degrades to an empty list /
  // no-op filter on failure rather than blanking the board). Re-runs on project
  // switch. Mirrors the milestone-load pattern in NewTaskModal / AiTaskModal.
  useEffect(() => {
    let cancelled = false;
    listMilestones(project.id, { limit: 500 })
      .then((rows) => {
        if (!cancelled) setMilestones(rows);
      })
      .catch(() => {
        if (!cancelled) setMilestones([]);
      });
    return () => {
      cancelled = true;
    };
  }, [project.id]);

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
    const noNoise = base.filter((t) => !isScheduledNoise(t));
    // #1868 v1.1 — milestone filter. "all" → no-op; "none" → milestone_id null
    // (treats undefined the same as null for legacy/pre-migration rows); number
    // → exact match.
    if (milestoneFilter === "all") return noNoise;
    if (milestoneFilter === "none")
      return noNoise.filter((t) => t.milestone_id == null);
    return noNoise.filter((t) => t.milestone_id === milestoneFilter);
  }, [tasks, showAudit, milestoneFilter]);

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
          {/* Wave A (#2) — Inbox as an icon link (was a text link). Cross-project
              approval inbox; lightweight <Link> (no polling badge — the
              dashboard carries the live count). */}
          <HeaderIconLink href="/inbox" icon="backlog" label="Inbox" />
          {/* Wave A (#2) — per-project Settings as an icon link (was a text
              link). #1349 nudge-threshold + future knobs. Distinct from the
              platform Integrations plug icon in the right cluster. */}
          <HeaderIconLink
            href={`/p/${encodeURIComponent(project.name)}/settings`}
            icon="agent-config"
            label="Settings"
          />
          <Sep />
          {/* #1868 — per-project Milestones surface (X-Project-Id scoped).
              KEPT as a nav link (Wave A retains the milestones page); Calendar +
              Gantt text links removed — they now live in the ViewSwitcher. */}
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
          {/* Wave A (#5/#3a) — the "NNN tasks" count moved OUT of the nav row
              into the toolbar row directly under the consent banner (alongside
              +New). See data-board-toolbar-row below. */}
          {/* #1868 v1.1 — milestone filter dropdown. Self-hides when the project
              has no milestones (mirrors the audit-toggle's count>0 self-hide).
              "All milestones" = no filter; "No milestone" = milestone_id null;
              each option filters the board's task cards client-side. */}
          {milestones.length > 0 && (
            <label className="inline-flex items-center gap-1 text-xs text-zinc-500 dark:text-zinc-400">
              <span className="sr-only sm:not-sr-only">Milestone</span>
              <select
                value={
                  milestoneFilter === "all" || milestoneFilter === "none"
                    ? milestoneFilter
                    : String(milestoneFilter)
                }
                onChange={(e) => {
                  const v = e.target.value;
                  setMilestoneFilter(
                    v === "all" || v === "none" ? v : Number(v),
                  );
                }}
                className="rounded-md border border-zinc-200 bg-transparent px-2 py-1.5 text-xs text-zinc-600 focus:border-zinc-400 focus:outline-none min-h-[44px] sm:min-h-0 dark:border-zinc-700 dark:text-zinc-300 dark:focus:border-zinc-500"
                data-milestone-filter
              >
                <option value="all">All milestones</option>
                <option value="none">No milestone</option>
                {milestones.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title}
                  </option>
                ))}
              </select>
            </label>
          )}
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
          <Sep />
          <ConnectionStateBadge
            state={connectionState}
            lastEventAt={lastEventAt}
          />
          <Sep />
          {/* Wave A (#1) — unified Board · List · Calendar · Gantt switcher
              replaces the former Board|List toggle. On the board page List is
              the local `view` state (no navigation): clicking List/Board here
              updates the view in place via onSelect; Calendar/Gantt are real
              route links handled inside ViewSwitcher. `active` reflects the live
              board/list view. */}
          <ViewSwitcher
            projectName={project.name}
            active={view}
            onSelect={handleViewChange}
          />
          {/* #1781 — right cluster: +New moved to the toolbar row (#5/#3a);
              this cluster now holds pause/terminate icon buttons, Integrations,
              ThemePicker. (Bell/FlagBellBadge removed in Wave A #6.) ml-auto
              pushes them right on desktop, full-width wrap on mobile. */}
          <span
            className="ml-auto flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto"
            data-board-actions-cluster
          >
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

            {/* Wave A (#6) — FlagBellBadge (the 🔔 "needs attention" review
                notification) removed from the board nav. /review remains
                reachable from the dashboard + ReviewClient's own header. */}

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
            3-col when finance is on, 2-col (Usage + Progress) when off.
            Wave A (#7) — items-stretch + each panel `h-full` makes the three
            equal height inside the row (gap via the band's gap-3, no per-panel
            mb-5). */}
        <div
          className={`grid grid-cols-1 items-stretch gap-3 ${
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
            className="h-full"
          />
          {/* Kanban #1329 (M6 FE) — per-project P&L card (finance-gated). */}
          {FINANCE_PANELS_ENABLED && (
            <PnlSummaryCard
              projectId={project.id}
              projectName={project.name}
              defaultCollapsed={true}
              storageKey={`project.${project.id}.panels.pnl.expanded`}
              className="h-full"
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
      {/* Wave A (#5/#3a) — toolbar row directly under the consent ("Enable
          headless auto-run") banner and above the kanban columns. Left: the
          live "NNN tasks" count (moved out of the nav); right: the +New
          dropdown (moved out of the nav right-cluster). Same NewTaskDropdown
          instance/behaviour as before. */}
      <div
        className="mb-3 flex flex-wrap items-center justify-between gap-2"
        data-board-toolbar-row
      >
        <span
          className="text-sm tabular-nums text-zinc-500 dark:text-zinc-400"
          data-board-task-count
        >
          {visibleTasks.length} task{visibleTasks.length === 1 ? "" : "s"}
        </span>
        <NewTaskDropdown project={project} onPushToast={pushToast} />
      </div>
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
