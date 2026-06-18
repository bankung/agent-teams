"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";

import { ListView } from "@/components/ListView";

import {
  getMilestone,
  listDoneLanePage,
  listMilestones,
  patchTask,
  reorderTask,
  type MilestoneDetail,
  type MilestoneRead,
  type ProgressStatsResponse,
  type ProjectRead,
  type ProjectStatsEntry,
  type TaskRead,
} from "@/lib/api";

// Kanban #2111 Part 3c — @dnd-kit loads only for the board view.
// BoardDndCanvas owns all dnd-kit imports; this dynamic() call ensures the
// chunk is excluded from the list-view (and SSR) bundle.
const BoardDndCanvas = dynamic(
  () => import("@/components/BoardDndCanvas").then((m) => m.BoardDndCanvas),
  { ssr: false },
);
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { useAsyncData } from "@/lib/useAsyncData";
import { sortDoneLane, sortLaneTasks } from "@/lib/sortLaneTasks";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";
import { ConnectionStateBadge } from "@/components/ConnectionStateBadge";
import { CostSummary } from "@/components/CostSummary";
import { Icon } from "@/components/Icon";
import { PnlSummaryCard } from "@/components/PnlSummaryCard";
import { ProgressChartsPanel } from "@/components/ProgressChartsPanel";
import { KilledBanner } from "@/components/KilledBanner";
import { KillProjectModal } from "@/components/KillProjectModal";
import { NewTaskDropdown } from "@/components/NewTaskDropdown";
import { PausedBanner } from "@/components/PausedBanner";
import { PauseProjectModal } from "@/components/PauseProjectModal";
import { ProjectConsentGrantModal } from "@/components/ProjectConsentGrantModal";
import { ProductTourBoardResume } from "@/components/ProductTourBoardResume";
import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { SourcesBadge } from "@/components/SourcesBadge";
import { TaskDetail } from "@/components/TaskDetail";
import { ToastStack, type ToastMessage } from "@/components/Toast";
import { ViewSwitcher } from "@/components/ViewSwitcher";
import { FINANCE_PANELS_ENABLED } from "@/lib/featureFlags";

type Props = {
  initialTasks: TaskRead[];
  // Kanban #2112 — whether the server has more DONE rows beyond the first 50.
  // Drives server-side keyset pagination on "Load more" instead of slicing
  // a client-side array.
  initialDoneHasMore: boolean;
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

// #2477 — merged ps=8 (HALTED_PENDING_USER) into Blocked lane (5→5 columns).
// The separate "Halted / Pending user" lane is dropped; ps=8 tasks render under
// Blocked with a badge in TaskCard. ALL_STATUSES keeps ps=8 so groupByStatus
// still buckets it — both 4 and 8 buckets are flatMapped by BoardDndCanvas.
const COLUMNS: Column[] = [
  { statuses: [TaskStatus.TODO], label: "New tasks", key: "1" },
  { statuses: [TaskStatus.IN_PROGRESS], label: "In progress", key: "2" },
  { statuses: [TaskStatus.REVIEW], label: "Review", key: "3" },
  { statuses: [TaskStatus.BLOCKED, TaskStatus.HALTED_PENDING_USER], label: "Blocked", key: "4" },
  { statuses: [TaskStatus.DONE], label: "Done", key: "5" },
];

const ALL_STATUSES: TaskStatusValue[] = [
  TaskStatus.TODO,
  TaskStatus.IN_PROGRESS,
  TaskStatus.REVIEW,
  TaskStatus.BLOCKED,
  TaskStatus.HALTED_PENDING_USER,
  TaskStatus.DONE,
];

// #1726 — recurrence noise: templates (is_template=true) and scheduled-fire
// instances (title prefix "[schedule:") are excluded from the visible board.
const isScheduledNoise = (t: TaskRead) =>
  t.is_template || t.title.startsWith("[schedule:");

// Kanban #2127 — operator-gate predicate. Mirrors the BE ?operator_gate=any
// filter: task-level `operator_gate` non-null, OR ≥1 AC item with
// gate==='operator' AND status==='pending'. Defined at module scope so it is
// stable across renders (safe for useMemo deps).
const isOperatorGated = (t: TaskRead): boolean => {
  if (t.operator_gate != null) return true;
  if (!t.acceptance_criteria) return false;
  return t.acceptance_criteria.some(
    (ac) => ac.gate === "operator" && ac.status === "pending",
  );
};

function groupByStatus(tasks: TaskRead[]) {
  const groups = new Map<TaskStatusValue, TaskRead[]>();
  for (const s of ALL_STATUSES) groups.set(s, []);
  for (const task of tasks) {
    const bucket = groups.get(task.process_status);
    if (bucket) {
      bucket.push(task);
    } else if (process.env.NODE_ENV !== "production") {
      console.warn(
        `Board.groupByStatus: task #${task.id} has process_status=${task.process_status} with no lane — dropped. Add it to ALL_STATUSES/COLUMNS.`,
      );
    }
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

/** Pure helper — exported for unit testing (Kanban #2346 FE-m2).
 * Only "all" has a server-side rollup in projectStats; "none" and numeric ids
 * have no matching rollup row, so return undefined → BoardColumn uses loaded count. */
export function computeDoneTotalCount(
  milestoneFilter: "all" | "none" | number,
  projectStats: ProjectStatsEntry[],
  projectId: number,
): number | undefined {
  if (milestoneFilter === "all") return projectStats.find((s) => s.id === projectId)?.counts["5"];
  return undefined;
}

export function Board({ initialTasks, initialDoneHasMore, hasHeadlessTask, project, projectStats, progressStats }: Props) {
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

  // Kanban #2127 — operator-gate filter. "On you (N)" badge shows tasks that
  // require operator action. Default OFF (show all tasks); operator toggles ON
  // to see ONLY gated tasks. Session-scoped; no localStorage persistence.
  const [showOperatorGateOnly, setShowOperatorGateOnly] = useState(false);

  // #1868 v1.1 — milestone filter. "all" = no filter (default); "none" = only
  // tasks with milestone_id == null; number = only tasks pointing at that
  // milestone. Client-side filter on the in-memory `tasks` snapshot — the
  // board already filters audit + scheduled noise client-side and has no
  // client re-fetch path (initialTasks is SSR + SSE router.refresh()), so a
  // client predicate is the consistent, minimum-viable v1. `milestones` feeds
  // the dropdown; loaded once on mount (failure degrades to a no-op filter).
  const [milestoneFilter, setMilestoneFilter] = useState<"all" | "none" | number>("all");
  const [milestones, setMilestones] = useState<MilestoneRead[]>([]);
  // Kanban #2347 — when a numeric milestone filter is active, fetch its server
  // rollup DONE count so the DONE-lane header shows the true total (not just
  // what's loaded client-side). undefined = still loading or no rollup available.
  //
  // #2492 — fetch + cancel-guard via useAsyncData (was a hand-rolled
  // cancelled-flag effect). The fetcher resolves null for a non-numeric filter
  // (so the rollup clears immediately on filter change, no stale leak); on a
  // fetch error it degrades to null → the render falls back to the loaded
  // count. `data` (number | null) maps back to the prior `undefined` contract.
  const { data: rollupData } = useAsyncData<number | null>(
    () => {
      if (typeof milestoneFilter !== "number") return Promise.resolve(null);
      return getMilestone(project.id, milestoneFilter).then(
        (detail: MilestoneDetail) => detail.rollup.by_process_status["5"] ?? 0,
      );
    },
    [milestoneFilter, project.id],
    { resetDataOnReload: true },
  );
  const milestoneDoneRollup = rollupData ?? undefined;

  // Kanban #2112 — DONE-lane server pagination state.
  // doneHasMore: server has more DONE rows beyond what's loaded (init from SSR prop).
  // doneLoadingMore: a fetch is in-flight (prevents double-fetch on rapid clicks).
  // visibleDoneCount: how many of the in-memory DONE tasks to render. Grows as
  //   pages are appended; resets to DONE_PAGE on SSE refresh (accepted behavior).
  const DONE_PAGE = 50;
  const [visibleDoneCount, setVisibleDoneCount] = useState(DONE_PAGE);
  const [doneHasMore, setDoneHasMore] = useState(initialDoneHasMore);
  const [doneLoadingMore, setDoneLoadingMore] = useState(false);

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

  // Toast helpers — declared before the deep-link effect that consumes
  // pushToast (react-hooks/immutability: a useCallback must not be referenced
  // before its declaration). Both close over only the stable toastIdRef +
  // setToasts setter, so the memoization is preserved across renders.
  const pushToast = useCallback((text: string) => {
    const id = toastIdRef.current++;
    setToasts((prev) => [...prev, { id, text }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

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

  // Sync local tasks state to server snapshot on each RSC refresh (SSE router.refresh).
  // Also resets DONE pagination so the next Load-more starts from the fresh SSR cursor.
  useEffect(() => {
    setTasks(initialTasks);
    setDoneHasMore(initialDoneHasMore);
    setVisibleDoneCount(DONE_PAGE);
    // initialDoneHasMore and DONE_PAGE are intentionally omitted: they always
    // update together with initialTasks on the same SSR render, so re-running
    // on them independently would produce spurious resets mid-session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // Kanban #2111 Part 3c — callbacks passed to BoardDndCanvas (owns dnd-kit).
  // Cross-lane: optimistic setTasks + PATCH; revert on error (mirrors original onDragEnd).
  const onCrossLaneDrop = useCallback(
    (taskId: number, newPs: TaskStatusValue, original: TaskRead) => {
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
    },
    [project.id, pushToast],
  );

  // Kanban #2112 — server-side DONE Load-more handler.
  // Cursor = last task in the DONE bucket sorted by sortDoneLane (updated_at DESC,
  // id DESC). We re-derive it from the current `tasks` snapshot rather than
  // relying on the `grouped` memo (which is declared below) to avoid a
  // "used before declaration" error — the result is identical since
  // sortDoneLane is a pure function.
  const handleLoadMoreDone = useCallback(async () => {
    if (!doneHasMore || doneLoadingMore) return;
    const sortedDone = sortDoneLane(
      tasks.filter((t) => t.process_status === TaskStatus.DONE),
    );
    const lastDone = sortedDone[sortedDone.length - 1];
    if (!lastDone) return;
    setDoneLoadingMore(true);
    try {
      const page = await listDoneLanePage(project.id, {
        limit: DONE_PAGE,
        before_updated_at: lastDone.updated_at,
        before_id: lastDone.id,
      });
      if (page.length > 0) {
        setTasks((prev) => {
          const existingIds = new Set(prev.map((t) => t.id));
          const novel = page.filter((t) => !existingIds.has(t.id));
          return [...prev, ...novel];
        });
        setVisibleDoneCount((n) => n + page.length);
      }
      setDoneHasMore(page.length === DONE_PAGE);
    } catch (_) {
      pushToast("Failed to load more done tasks. Try again.");
    } finally {
      setDoneLoadingMore(false);
    }
  }, [doneHasMore, doneLoadingMore, tasks, project.id, pushToast]);

  // Same-lane: no optimistic mutation (dnd-kit transform handles visual; snap-back on 422). Details: shared/decisions.md 2026-05-14
  const onSameLaneReorder = useCallback(
    (taskId: number, overTaskId: number, laneIds: number[]) => {
      const original = tasks.find((t) => t.id === taskId);
      if (!original) return;
      const overTask = tasks.find((t) => t.id === overTaskId);
      if (!overTask) return;
      const oldIndex = laneIds.indexOf(original.id);
      const newIndex = laneIds.indexOf(overTask.id);
      if (oldIndex === -1 || newIndex === -1) return;
      const body =
        oldIndex < newIndex
          ? { after_id: overTask.id }
          : { before_id: overTask.id };
      reorderTask(project.id, taskId, body)
        .then((server) => {
          setTasks((prev) => prev.map((t) => (t.id === taskId ? server : t)));
        })
        .catch((err: unknown) => {
          const msg = extractErrorMessage(err, "Reorder failed");
          pushToast(`Task #${taskId}: ${msg}`);
        });
    },
    [tasks, project.id, pushToast],
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

  // Kanban #2127 — operator-gate count. Mirrors the BE predicate for
  // ?operator_gate=any: task-level operator_gate non-null OR ≥1 AC item with
  // gate==='operator' AND status==='pending'. Computed against the unfiltered
  // list so the badge shows the real count even when a milestone filter is on.
  // Deduplication is implicit: each task contributes at most 1 to the count.
  const operatorGateCount = useMemo(
    () => tasks.filter(isOperatorGated).length,
    [tasks],
  );

  const visibleTasks = useMemo(() => {
    const base = showAudit ? tasks : tasks.filter((t) => t.task_type !== "audit");
    const noNoise = base.filter((t) => !isScheduledNoise(t));
    // Kanban #2127 — operator-gate filter. When active, show ONLY gated tasks.
    const gateFiltered = showOperatorGateOnly
      ? noNoise.filter(isOperatorGated)
      : noNoise;
    // #1868 v1.1 — milestone filter. "all" → no-op; "none" → milestone_id null
    // (treats undefined the same as null for legacy/pre-migration rows); number
    // → exact match.
    if (milestoneFilter === "all") return gateFiltered;
    if (milestoneFilter === "none")
      return gateFiltered.filter((t) => t.milestone_id == null);
    return gateFiltered.filter((t) => t.milestone_id === milestoneFilter);
  }, [tasks, showAudit, showOperatorGateOnly, milestoneFilter]);

  const grouped = useMemo(() => groupByStatus(visibleTasks), [visibleTasks]);

  // Kanban #2346/#2347 — true DONE total for the column header badge.
  // "all": projectStats[0]?.counts["5"] is the server total (SSR-fetched).
  // numeric id: use the milestone rollup fetched by the useEffect above (undefined
  //   while loading → BoardColumn falls back to loaded count; no flicker/overstate).
  // "none" (milestone_id IS NULL): no server rollup → loaded count fallback.
  // NOTE: client-only toggles (audit/operator-gate) may make "all" approximate.
  const doneTotalCount = useMemo<number | undefined>(() => {
    if (typeof milestoneFilter === "number") return milestoneDoneRollup;
    return computeDoneTotalCount(milestoneFilter, projectStats, project.id);
  }, [milestoneFilter, milestoneDoneRollup, projectStats, project.id]);

  // Reset the client-side DONE display window (visibleDoneCount) ONLY when the
  // filter inputs change. Keyed on the filter state directly — NOT on the DONE
  // bucket contents — so appending a server page (which grows doneTasks) does
  // NOT reset, and Load-more terminates correctly. doneHasMore is NOT reset here;
  // it reflects the server's has-more for the lane and is owned by the
  // initialTasks-sync effect (SSE refresh) + handleLoadMoreDone. (#2112 regression
  // fix: the prior content-keyed effect reverted both on every append.)
  useEffect(() => {
    setVisibleDoneCount(DONE_PAGE);
  }, [milestoneFilter, showAudit, showOperatorGateOnly]);

  // #2412 — blocker-badge suppression. Build the set of task ids that are
  // still active (non-terminal). A blocker ABSENT from this set is necessarily
  // terminal (DONE/CANCELLED or beyond the first-50 loaded DONE rows) and must
  // NOT show the red chip. Terminal = DONE(5) or CANCELLED(6).
  const blockingTaskIds = useMemo(() => {
    const s = new Set<number>();
    for (const t of tasks) {
      if (t.process_status !== TaskStatus.DONE && t.process_status !== TaskStatus.CANCELLED) s.add(t.id);
    }
    return s;
  }, [tasks]);

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

  return (
    // #954 — mobile: page scrolls (h-auto, overflow-y-auto); desktop preserves the fixed-viewport board (h-screen, overflow-hidden)
    // #2453 — `glass-board` is the hook for the glass theme: under html.glass it
    // anchors the single page-level blob backdrop (the ::before pseudo) and frosts
    // the board surface. No-op when glass is off (flat theme renders unchanged).
    <main className="glass-board relative flex min-h-screen flex-col overflow-y-auto bg-white dark:bg-zinc-950 px-4 py-4 sm:px-6 sm:py-5 lg:h-screen lg:min-h-0 lg:overflow-hidden">
      {/* #1781 capped the header (lg:max-h-[40vh] + overflow-y-auto) to floor
          the board at >=60vh. #2404 (operator decision 2026-06-15) — cap REMOVED
          so the usage/P&L/Graph band never shows a scrollbar when expanded.
          Panels stay VISIBLE (defaultCollapsed=false) + collapsible. Trade-off:
          the header takes natural height; the board below gets the remaining
          flex-1 space (no longer a guaranteed >=60vh floor). */}
      <header
        className="mb-4 flex flex-col gap-2"
        data-board-header
        tabIndex={-1}
      >
        {/* #1781 — single nav row on desktop; flex-wrap still drops controls
            to extra rows on mobile. #2404 — 3-zone layout: left cluster (flex-1)
            · centered ViewSwitcher (shrink-0) · right cluster (flex-1 + justify-end). */}
        <div
          className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm"
          data-board-nav-row
        >
          {/* LEFT zone — Dashboard first, then ProjectSwitcher, then status dot, then SourcesBadge */}
          <span className="flex flex-1 flex-wrap items-center gap-x-2 gap-y-1">
            <Link
              href="/dashboard"
              className="text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
            >
              Dashboard
            </Link>
            <ProjectSwitcher current={project.name} />
            <ConnectionStateBadge
              state={connectionState}
              lastEventAt={lastEventAt}
            />
            {project.sources.length > 0 && (
              <SourcesBadge sources={project.sources} />
            )}
          </span>
          {/* CENTER zone — ViewSwitcher horizontally centered (#2404). Wave A (#1)
              On the board page List is the local `view` state (no navigation):
              clicking List/Board updates the view in place via onSelect;
              Calendar/Gantt are real route links handled inside ViewSwitcher. */}
          <span className="shrink-0">
            <ViewSwitcher
              projectName={project.name}
              active={view}
              onSelect={handleViewChange}
            />
          </span>
          {/* RIGHT zone — #1781: pause/terminate icon buttons, Integrations, ThemePicker.
              flex-1 + justify-end pins this cluster to the right edge. */}
          <span
            className="flex flex-1 flex-wrap items-center justify-end gap-2"
            data-board-actions-cluster
          >
            {/* Wave A.2a (#1) — Inbox icon link in the right cluster so the
                left nav stays lean (Dashboard · Milestones). Inbox =
                cross-project approval inbox. (Settings consolidated below.) */}
            <HeaderIconLink href="/inbox" icon="mail" label="Inbox" />
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

            {/* #2380 (R-merge) — single consolidated Settings gear. Opens the
                global /settings page WITH this project's section pre-rendered
                via ?project= (theme + integrations + push live there too; the
                per-project gear was merged in). The dashboard gear stays plain
                /settings = global only. */}
            <HeaderIconLink
              href={`/settings?project=${encodeURIComponent(project.name)}`}
              icon="agent-config"
              label="Settings"
            />
          </span>
        </div>
        {/* #2380 (R-merge) — 3-column panels band: Usage (40%) · P&L (40%) ·
            Progress charts (20%). Usage + P&L moved back from the project
            settings page; ProgressChartsPanel kept in the narrow 3rd column. */}
        <div
          className="grid grid-cols-1 gap-3 items-stretch lg:grid-cols-[2fr_2fr_1fr]"
          data-board-panels-band
        >
          {/* Col 1 (40%) — Kanban #1289 per-project usage panel.
              #2404 — defaultCollapsed=false (operator: panels visible by default);
              still collapsible, storageKey persists per-project preference. */}
          <CostSummary
            stats={projectStats}
            ariaLabel={`Usage for ${project.name}`}
            defaultCollapsed={false}
            storageKey={`project.${project.id}.panels.usage.expanded`}
            className="h-full min-w-0"
          />
          {/* Col 2 (40%) — Kanban #1329 per-project P&L card (finance-gated).
              FINANCE_PANELS_ENABLED is a GLOBAL env flag; when off, every
              project shows the placeholder (expected with current infra).
              #2404 — defaultCollapsed=false to match Usage panel; storageKey persists per-project. */}
          {FINANCE_PANELS_ENABLED ? (
            <PnlSummaryCard
              projectId={project.id}
              projectName={project.name}
              defaultCollapsed={false}
              storageKey={`project.${project.id}.panels.pnl.expanded`}
              className="h-full min-w-0"
            />
          ) : (
            <div
              className="flex h-full min-w-0 items-center justify-center rounded-md border border-zinc-200 p-3 text-center text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
              data-pnl-placeholder
            >
              P&amp;L not available for this project
            </div>
          )}
          {/* Col 3 (20%) — Kanban #1292 / #1781 burndown + velocity. */}
          <div className="min-w-0 h-full">
            <ProgressChartsPanel
              data={progressStats}
              projectId={project.id}
              compact
            />
          </div>
        </div>
        {/* #1209 GOV1 D5 — red strip above the consent banner when killed.
            (Renders nothing when is_killed=false.) */}
        <KilledBanner project={project} />
        {/* #1211 / #1238 GOV3 — amber strip above the consent banner when paused.
            (Renders nothing when is_paused=false.) */}
        <PausedBanner project={project} />
      </header>
      {/* Wave A.1 — toolbar row: left cluster (audit + scheduled chips),
          centre (inline headless control), right (+New).
          Audit/scheduled moved here from nav row; headless banner condensed
          from standalone full-width section. */}
      <div
        className="mb-3 flex flex-wrap items-center gap-2"
        data-board-toolbar-row
      >
        {/* Audit-filter chip — amber toggle; hidden when count=0. */}
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
        {/* Kanban #2127 — operator-gate toggle chip; hidden when count=0. */}
        {operatorGateCount > 0 && (
          <HeaderIconBtn
            icon="alert"
            label={
              showOperatorGateOnly
                ? `Show all tasks (${operatorGateCount} on you)`
                : `On you (${operatorGateCount})`
            }
            onClick={() => setShowOperatorGateOnly((v) => !v)}
            active={showOperatorGateOnly}
            ariaPressed={showOperatorGateOnly}
            count={operatorGateCount}
            tone="amber"
            dataAttr="data-operator-gate-toggle"
          />
        )}
        {/* #1868 v1.1 — milestone filter; moved from nav row to toolbar (v0.7.0 UX). */}
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
        {/* Inline headless control — replaces the standalone
            ProjectConsentBanner section. Shows consent date when granted;
            shows a compact "Headless: off · Enable" chip when not granted
            (clicking opens the same ProjectConsentGrantModal). The
            hasHeadlessTask warning is surfaced as an amber inline badge. */}
        {project.auto_run_consent_at !== null ? (
          <span
            className="inline-flex items-center gap-1.5 rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
            data-headless-status="granted"
          >
            Headless: on · {project.auto_run_consent_at.slice(0, 10)}
            {hasHeadlessTask && (
              <span className="font-semibold text-amber-700 dark:text-amber-300">⚠ active</span>
            )}
          </span>
        ) : (
          <span
            className="inline-flex items-center gap-0 rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
            data-headless-status="off"
          >
            Headless: off
            {hasHeadlessTask && (
              <span className="ml-1.5 text-amber-700 dark:text-amber-300">⚠</span>
            )}
            <ProjectConsentGrantModal
              project={{ id: project.id, name: project.name }}
            />
          </span>
        )}
        {/* +New — pushed to the right end of the toolbar row. */}
        <span className="ml-auto">
          <NewTaskDropdown project={project} onPushToast={pushToast} />
        </span>
      </div>
      {view === "list" ? (
        <ListView
          tasks={visibleTasks}
          onOpenDetail={onOpenDetail}
          highlightedTaskId={highlightedTaskId}
        />
      ) : (
        <BoardDndCanvas
          columns={COLUMNS}
          tasks={tasks}
          grouped={grouped}
          visibleDoneCount={visibleDoneCount}
          doneHasMore={doneHasMore}
          doneLoadingMore={doneLoadingMore}
          doneTotalCount={doneTotalCount}
          onOpenDetail={onOpenDetail}
          highlightedTaskId={highlightedTaskId}
          onLoadMoreDone={handleLoadMoreDone}
          onCrossLaneDrop={onCrossLaneDrop}
          onSameLaneReorder={onSameLaneReorder}
          projectId={project.id}
          blockingTaskIds={blockingTaskIds}
        />
      )}
      {/* #2371 (R1) — AuditHistorySection moved to project settings page. */}
      {/* #2358 — ResourcesPanel moved to /settings?project=<name>. */}
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
      {/* #1582 — board phase of the first-visit product tour. Renders null
          unless the dashboard phase handed off (localStorage baton); then runs
          the board + task-drawer steps and finalizes the tour. projectName gates
          the phase to the demo-tour sample project (#1582 H-1/M-1): a stale
          baton landing on a real board clears itself instead of firing. */}
      <ProductTourBoardResume projectName={project.name} />
    </main>
  );
}
