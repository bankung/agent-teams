"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";

import {
  listTasks,
  patchTask,
  type MilestoneDetail,
  type MilestoneRead,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import {
  epochDay,
  epochDayToKey,
  monthTickLabel,
  startOfMonthEpochDay,
  nextMonthEpochDay,
  todayKey,
} from "@/lib/calendarDates";
import { MilestoneStatusBadge } from "./MilestoneStatusBadge";
import { MilestoneFormModal } from "./MilestoneFormModal";
import { MilestoneDeleteModal } from "./MilestoneDeleteModal";

// GanttView — milestone-level Gantt timeline + the MILESTONE HOME (#1874 M3 +
// Wave A.2c: the dedicated /milestones page was removed; Gantt now owns milestone
// management).
//
// Server component (gantt/page.tsx) SSR-fetches every milestone WITH its rollup.
// This client view computes the time axis from min(start_date) → max(target_date)
// across all DATED milestones and positions one horizontal bar per milestone
// (start → target). A milestone with only a target_date renders a diamond at the
// deadline; a milestone with NO dates stays in the left rail with the bar area
// labeled "no dates".
//
// Tasks are NOT plotted on the timeline (locked design — milestone-level only).
// The rail shows each milestone's task count (done/total).
//
// ── Wave A.2c — milestone management folded in (was MilestonesView) ──────────
//   - Each rail row gets Edit + Delete affordances → MilestoneFormModal (edit) +
//     MilestoneDeleteModal. A "New milestone" button in the header → create.
//     router.refresh() on any change so the server re-fetches the authoritative
//     list + fresh rollups.
//   - Drag-task→milestone (relocated from MilestonesView Wave D): rail rows are
//     DROP TARGETS; the "Unassigned" pool below the header is the drag SOURCE
//     (a compact panel of milestone_id-null tasks, each row draggable). Dropping
//     a task onto a rail row → PATCH milestone_id (optimistic move, revert on
//     failure, router.refresh() to recompute rollups). Dropping back onto the
//     pool → milestone_id null (unassign).
//     SCOPE TRIM (per brief escape valve): we did NOT wire a task-drag source
//     into the timeline itself — only rail-row drop targets + the Unassigned
//     pool source. MilestoneCombobox (TaskDetail / NewTaskModal / AiTaskModal)
//     remains the primary, non-DnD assign path.
//
// Geometry: CSS-positioned absolute divs over a min-width track; the track
// scrolls horizontally when the span is long. Bars use left% / width% so they
// reflow with the container — except the track has a px min-width floor.

// Layout constants.
const ROW_H = 56; // px per milestone row (rail + timeline aligned). Taller than
// the original 44 so the rail row fits the title/status line + the progress +
// edit/delete affordance line without clipping.
const AXIS_H = 28; // px axis header height
const PX_PER_DAY_MIN = 6; // min pixels per day → drives the track min-width
const TRACK_MIN_PX = 640; // absolute floor for the timeline track width

// ── DnD constants (migrated from MilestonesView Wave D) ─────────────────────
// Drop-target id scheme: milestone droppable ids are namespaced so they never
// collide with the Unassigned pool.
const UNASSIGNED = "unassigned";
const dropIdForMilestone = (id: number) => `milestone-${id}`;

// taskLists key for the Unassigned pool; milestone lists key on their numeric id.
type ListKey = number | typeof UNASSIGNED;

const STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
  [TaskStatus.HALTED_PENDING_USER]: "halted",
};

// Stable sort for the Unassigned pool list: process_status then id.
function sortTasks(rows: TaskRead[]): TaskRead[] {
  return [...rows].sort(
    (a, b) => a.process_status - b.process_status || a.id - b.id,
  );
}

type Props = {
  projectId: number;
  projectName: string;
  milestones: MilestoneDetail[];
};

type DatedSpan = {
  milestone: MilestoneDetail;
  startDay: number | null; // epoch-day index; null = no start (diamond at target)
  endDay: number | null; // epoch-day index; null = undated
};

export function GanttView({ projectId, projectName, milestones }: Props) {
  const router = useRouter();
  const today = useMemo(() => todayKey(), []);
  const todayDay = useMemo(() => epochDay(today), [today]);

  // ── Milestone CRUD modal state (migrated from MilestonesView) ─────────────
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<MilestoneRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MilestoneRead | null>(null);

  // router.refresh() re-runs the server component → re-fetches milestones +
  // rollups. No optimistic merge for modal CRUD (rollup is server-computed).
  const onMutated = useCallback(() => {
    router.refresh();
  }, [router]);

  // ── DnD state (migrated from MilestonesView Wave D) ───────────────────────
  // Per-key task lists. Only the UNASSIGNED key is surfaced as a draggable list
  // (the drag SOURCE); milestone keys exist so an optimistic add lands in the
  // right bucket if that list is ever loaded (we don't render milestone task
  // lists in the Gantt, so milestone keys stay unloaded → optimistic add is a
  // no-op for them, and the router.refresh() reconciles the rollup).
  const [taskLists, setTaskLists] = useState<Record<string, TaskRead[] | null>>(
    {},
  );
  const [loadingUnassigned, setLoadingUnassigned] = useState(false);
  const [errorUnassigned, setErrorUnassigned] = useState<string | null>(null);
  const [activeTask, setActiveTask] = useState<TaskRead | null>(null);
  const [dndError, setDndError] = useState<string | null>(null);

  const keyStr = (k: ListKey) => String(k);

  // Lazy-load the Unassigned pool (milestone_id-null tasks). The BE has no
  // explicit "null" filter param, so we fetch a top-level page and filter
  // client-side to milestone_id == null (mirrors Board's "none" predicate).
  const loadUnassigned = useCallback(() => {
    let skip = false;
    setLoadingUnassigned((prev) => {
      if (prev) {
        skip = true;
        return prev;
      }
      return true;
    });
    if (skip) return;

    setErrorUnassigned(null);
    listTasks(projectId, { limit: 500 })
      .then((rows) => rows.filter((t) => t.milestone_id == null))
      .then((rows) => {
        setTaskLists((prev) => ({ ...prev, [UNASSIGNED]: sortTasks(rows) }));
      })
      .catch((err: unknown) => {
        setErrorUnassigned(extractErrorMessage(err, "Failed to load tasks"));
        setTaskLists((prev) => ({ ...prev, [UNASSIGNED]: [] }));
      })
      .finally(() => {
        setLoadingUnassigned(false);
      });
  }, [projectId]);

  // Sensors mirror Board: pointer with a 4px activation threshold (so a click
  // to navigate isn't swallowed as a drag) + keyboard for a11y.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );

  const onDragStart = useCallback((event: DragStartEvent) => {
    const t = event.active.data.current?.task as TaskRead | undefined;
    setActiveTask(t ?? null);
  }, []);

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveTask(null);
      const { active, over } = event;
      if (!over) return;

      const task = active.data.current?.task as TaskRead | undefined;
      const sourceKey = active.data.current?.sourceKey as ListKey | undefined;
      if (!task || sourceKey === undefined) return;

      // Resolve the destination key from the droppable id.
      const overId = String(over.id);
      let destKey: ListKey;
      let newMilestoneId: number | null;
      if (overId === UNASSIGNED) {
        destKey = UNASSIGNED;
        newMilestoneId = null;
      } else if (overId.startsWith("milestone-")) {
        const parsed = Number(overId.slice("milestone-".length));
        if (!Number.isInteger(parsed)) return;
        destKey = parsed;
        newMilestoneId = parsed;
      } else {
        return;
      }

      // No-op: dropped back where it came from.
      if (keyStr(sourceKey) === keyStr(destKey)) return;

      const destStr = keyStr(destKey);
      const srcStr = keyStr(sourceKey);
      const updatedTask: TaskRead = { ...task, milestone_id: newMilestoneId };

      // Optimistic move: remove from source list, add to dest list (only if the
      // dest list is already loaded — milestone lists are never rendered in the
      // Gantt so they stay unloaded; the pool is the only loaded list).
      setTaskLists((prev) => {
        const next = { ...prev };
        if (Array.isArray(next[srcStr])) {
          next[srcStr] = next[srcStr]!.filter((t) => t.id !== task.id);
        }
        if (Array.isArray(next[destStr])) {
          next[destStr] = sortTasks([
            ...next[destStr]!.filter((t) => t.id !== task.id),
            updatedTask,
          ]);
        }
        return next;
      });

      patchTask(projectId, task.id, { milestone_id: newMilestoneId })
        .then((server) => {
          setTaskLists((prev) => {
            const next = { ...prev };
            if (Array.isArray(next[destStr])) {
              next[destStr] = sortTasks(
                next[destStr]!.map((t) => (t.id === server.id ? server : t)),
              );
            }
            return next;
          });
          // Server recomputes milestone rollups (rail progress / counts).
          router.refresh();
        })
        .catch((err: unknown) => {
          // Revert: restore the task to its source list, drop from dest.
          setTaskLists((prev) => {
            const next = { ...prev };
            if (Array.isArray(next[destStr])) {
              next[destStr] = next[destStr]!.filter((t) => t.id !== task.id);
            }
            if (Array.isArray(next[srcStr])) {
              next[srcStr] = sortTasks([
                ...next[srcStr]!.filter((t) => t.id !== task.id),
                task,
              ]);
            }
            return next;
          });
          const msg = extractErrorMessage(err, "Assignment failed");
          setDndError(`Task #${task.id}: ${msg}`);
        });
    },
    [projectId, router],
  );

  // Resolve each milestone to its day-index span.
  const spans: DatedSpan[] = useMemo(
    () =>
      milestones.map((m) => ({
        milestone: m,
        startDay: epochDay(m.start_date),
        endDay: epochDay(m.target_date),
      })),
    [milestones],
  );

  // Axis domain: min start (or target when no start) → max target across all
  // dated milestones. Undated milestones contribute nothing to the domain.
  const domain = useMemo(() => {
    let min: number | null = null;
    let max: number | null = null;
    for (const s of spans) {
      const lo = s.startDay ?? s.endDay; // diamond-only uses its target as both ends
      const hi = s.endDay ?? s.startDay;
      if (lo != null) min = min == null ? lo : Math.min(min, lo);
      if (hi != null) max = max == null ? hi : Math.max(max, hi);
    }
    if (min == null || max == null) return null;
    // Pad the domain a touch so edge bars aren't flush against the frame, and
    // guard the zero-width case (single dated day) → 1-day span minimum.
    if (max <= min) max = min + 1;
    return { min: min - 1, max: max + 1 };
  }, [spans]);

  // Total day span → track width (px). Floor at TRACK_MIN_PX so short spans
  // still render a usable track.
  const totalDays = domain ? domain.max - domain.min : 0;
  const trackWidthPx = domain
    ? Math.max(TRACK_MIN_PX, totalDays * PX_PER_DAY_MIN)
    : TRACK_MIN_PX;

  // Day-index → percent across the domain (0..100).
  const pctOf = (day: number): number => {
    if (!domain || totalDays <= 0) return 0;
    return ((day - domain.min) / totalDays) * 100;
  };

  // Month tick marks across the domain (first-of-month boundaries).
  const monthTicks = useMemo(() => {
    if (!domain) return [];
    const ticks: { day: number; label: string }[] = [];
    let cursor = startOfMonthEpochDay(domain.min);
    // Guard against a runaway loop on absurd domains.
    let guard = 0;
    while (cursor <= domain.max && guard < 240) {
      if (cursor >= domain.min) {
        ticks.push({ day: cursor, label: monthTickLabel(epochDayToKey(cursor)) });
      }
      cursor = nextMonthEpochDay(cursor);
      guard++;
    }
    return ticks;
  }, [domain]);

  return (
    <DndContext
      sensors={sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <section data-gantt-view aria-label={`Gantt timeline for ${projectName}`}>
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {milestones.length} milestone{milestones.length === 1 ? "" : "s"}
          </h2>
          <div className="flex items-center gap-2">
            {milestones.length > 0 && !domain && (
              <span className="hidden text-xs text-zinc-400 sm:inline dark:text-zinc-500">
                No dated milestones — set start/target dates to populate the timeline.
              </span>
            )}
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="inline-flex items-center gap-1.5 rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
              data-new-milestone-trigger
            >
              New milestone
            </button>
          </div>
        </div>

        {/* DnD failure notice (revert already happened). */}
        {dndError !== null && (
          <p
            className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
            role="alert"
            data-milestone-dnd-error
          >
            {dndError}
            <button
              type="button"
              onClick={() => setDndError(null)}
              className="ml-2 underline hover:no-underline"
            >
              dismiss
            </button>
          </p>
        )}

        {/* Unassigned pool — drag SOURCE for the assign-by-drag flow. Drop a
            task back here to unassign it (milestone_id → null). */}
        <UnassignedPool
          projectName={projectName}
          tasks={taskLists[UNASSIGNED] ?? null}
          loading={loadingUnassigned}
          error={errorUnassigned}
          onOpen={loadUnassigned}
        />

        {milestones.length === 0 ? (
          <p
            className="rounded border border-dashed border-zinc-200 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
            data-gantt-empty
          >
            No milestones yet. Create one with the “New milestone” button to see
            it on the timeline.
          </p>
        ) : (
          <div className="flex overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
            {/* ── Left rail — one row per milestone (drop target). ─────────── */}
            <div className="w-64 shrink-0 border-r border-zinc-200 dark:border-zinc-800">
              {/* Rail header aligns with the timeline axis row. */}
              <div
                className="flex items-center border-b border-zinc-200 bg-zinc-50 px-3 text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
                style={{ height: AXIS_H }}
              >
                Milestone
              </div>
              {spans.map((s) => (
                <RailRow
                  key={s.milestone.id}
                  milestone={s.milestone}
                  onEdit={() => setEditTarget(s.milestone)}
                  onDelete={() => setDeleteTarget(s.milestone)}
                />
              ))}
            </div>

            {/* ── Right timeline — horizontally scrollable track. ─────────── */}
            <div className="min-w-0 flex-1 overflow-x-auto">
              <div
                data-gantt-track
                className="relative"
                style={{ width: domain ? trackWidthPx : "100%", minWidth: TRACK_MIN_PX }}
              >
                {/* Axis header: month tick labels + boundary lines. */}
                <div
                  className="relative border-b border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900"
                  style={{ height: AXIS_H }}
                >
                  {domain &&
                    monthTicks.map((tk) => (
                      <div
                        key={tk.day}
                        className="absolute top-0 flex h-full items-center"
                        style={{ left: `${pctOf(tk.day)}%` }}
                      >
                        <span className="border-l border-zinc-200 pl-1 text-[10px] text-zinc-500 dark:border-zinc-700 dark:text-zinc-400 whitespace-nowrap">
                          {tk.label}
                        </span>
                      </div>
                    ))}
                  {!domain && (
                    <span className="flex h-full items-center pl-3 text-[11px] text-zinc-400 dark:text-zinc-500">
                      No timeline (no dated milestones)
                    </span>
                  )}
                </div>

                {/* Body: month gridlines + today line + one bar/diamond per row. */}
                <div className="relative">
                  {/* Month gridlines spanning the full body height. */}
                  {domain &&
                    monthTicks.map((tk) => (
                      <div
                        key={`grid-${tk.day}`}
                        aria-hidden
                        className="absolute top-0 bottom-0 w-px bg-zinc-100 dark:bg-zinc-800/60"
                        style={{ left: `${pctOf(tk.day)}%` }}
                      />
                    ))}

                  {/* Today line (only when in-domain). */}
                  {domain &&
                    todayDay != null &&
                    todayDay >= domain.min &&
                    todayDay <= domain.max && (
                      <div
                        aria-hidden
                        data-gantt-today-line
                        className="absolute top-0 bottom-0 z-10 w-px bg-sky-500 dark:bg-sky-400"
                        style={{ left: `${pctOf(todayDay)}%` }}
                      >
                        <span className="absolute -top-0 left-0.5 rounded-sm bg-sky-500 px-1 text-[9px] font-semibold text-white dark:bg-sky-400 dark:text-zinc-900">
                          today
                        </span>
                      </div>
                    )}

                  {spans.map((s) => (
                    <div
                      key={s.milestone.id}
                      data-gantt-row={s.milestone.id}
                      className="relative border-b border-zinc-100 last:border-b-0 dark:border-zinc-800/60"
                      style={{ height: ROW_H }}
                    >
                      {renderBar(s, domain, pctOf)}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* DragOverlay — a clean floating preview of the dragged task chip. */}
        <DragOverlay dropAnimation={null}>
          {activeTask ? (
            <div className="pointer-events-none rounded border border-zinc-300 bg-white px-2 py-1 text-xs shadow-lg dark:border-zinc-600 dark:bg-zinc-800">
              <span className="font-mono text-zinc-500 dark:text-zinc-400">
                #{activeTask.id}
              </span>{" "}
              <span className="text-zinc-800 dark:text-zinc-200">
                {activeTask.title}
              </span>
            </div>
          ) : null}
        </DragOverlay>

        {/* Create */}
        <MilestoneFormModal
          projectId={projectId}
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onSaved={onMutated}
        />
        {/* Edit — keyed on the target id so the form re-seeds per milestone */}
        <MilestoneFormModal
          key={editTarget?.id ?? "edit-closed"}
          projectId={projectId}
          open={editTarget !== null}
          onClose={() => setEditTarget(null)}
          onSaved={onMutated}
          milestone={editTarget ?? undefined}
        />
        {/* Delete confirm */}
        <MilestoneDeleteModal
          projectId={projectId}
          open={deleteTarget !== null}
          onClose={() => setDeleteTarget(null)}
          onDeleted={onMutated}
          milestone={deleteTarget}
        />
      </section>
    </DndContext>
  );
}

// ── RailRow — one milestone row in the left rail: a DROP TARGET carrying the
// title/status + progress + edit/delete affordances. ────────────────────────
function RailRow({
  milestone,
  onEdit,
  onDelete,
}: {
  milestone: MilestoneDetail;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { rollup } = milestone;
  const { isOver, setNodeRef } = useDroppable({
    id: dropIdForMilestone(milestone.id),
    data: { milestoneId: milestone.id },
  });

  const dropHighlight = isOver
    ? " bg-blue-50 ring-1 ring-inset ring-blue-300 dark:bg-blue-950/30 dark:ring-blue-700"
    : "";

  return (
    <div
      ref={setNodeRef}
      data-gantt-rail-row={milestone.id}
      data-milestone-id={milestone.id}
      data-milestone-status={milestone.milestone_status}
      data-drop-over={isOver || undefined}
      className={`group flex flex-col justify-center gap-0.5 border-b border-zinc-100 px-3 transition-colors last:border-b-0 dark:border-zinc-800/60${dropHighlight}`}
      style={{ height: ROW_H }}
    >
      <div className="flex items-center gap-1.5">
        <span
          className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-200"
          title={milestone.title}
        >
          {milestone.title}
        </span>
        <MilestoneStatusBadge status={milestone.milestone_status} />
      </div>
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] text-zinc-500 tabular-nums dark:text-zinc-400">
          {rollup.done}/{rollup.total} done · {rollup.progress_pct.toFixed(0)}%
        </span>
        {/* Edit / Delete — compact text buttons; reveal-on-hover on desktop,
            always visible on touch (no hover). */}
        <span className="flex shrink-0 items-center gap-1 opacity-100 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
          <button
            type="button"
            onClick={onEdit}
            className="rounded px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
            data-milestone-edit
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 hover:bg-red-50 hover:text-red-700 dark:text-zinc-400 dark:hover:bg-red-950/40 dark:hover:text-red-300"
            data-milestone-delete
          >
            Del
          </button>
        </span>
      </div>
    </div>
  );
}

// ── A single draggable task row in the Unassigned pool (drag SOURCE). ────────
function TaskChip({
  task,
  projectName,
}: {
  task: TaskRead;
  projectName: string;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `task-${task.id}`,
    data: { task, sourceKey: UNASSIGNED },
  });

  return (
    <li
      ref={setNodeRef}
      data-milestone-task-chip
      data-task-id={task.id}
      className={`flex items-center gap-2 rounded border border-transparent px-1 py-1 text-xs transition-colors hover:border-zinc-200 hover:bg-zinc-50 dark:hover:border-zinc-700 dark:hover:bg-zinc-800/50 ${
        isDragging ? "opacity-40" : ""
      }`}
    >
      {/* Drag handle — carries the dnd listeners so the row link stays
          clickable. cursor-grab signals the affordance. */}
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={`Drag task #${task.id} to assign a milestone`}
        className="shrink-0 cursor-grab touch-none rounded px-1 text-zinc-400 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 active:cursor-grabbing dark:text-zinc-500 dark:hover:text-zinc-200"
        data-milestone-task-handle
      >
        ⠿
      </button>
      <Link
        href={`/p/${encodeURIComponent(projectName)}?task=${task.id}`}
        className="flex min-w-0 flex-1 items-center gap-2 hover:underline"
      >
        <span className="font-mono text-zinc-500 dark:text-zinc-400">
          #{task.id}
        </span>
        <span className="flex-1 truncate text-zinc-800 dark:text-zinc-200">
          {task.title}
        </span>
      </Link>
      <span className="shrink-0 font-mono text-[10px] uppercase text-zinc-500 dark:text-zinc-400">
        {STATUS_LABEL[task.process_status] ?? `ps${task.process_status}`}
      </span>
    </li>
  );
}

// ── Unassigned pool: droppable (unassign target) + draggable list (assign
// source) of milestone_id-null tasks. Migrated from MilestonesView. ──────────
function UnassignedPool({
  projectName,
  tasks,
  loading,
  error,
  onOpen,
}: {
  projectName: string;
  tasks: TaskRead[] | null;
  loading: boolean;
  error: string | null;
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { isOver, setNodeRef } = useDroppable({ id: UNASSIGNED });

  const toggle = useCallback(() => {
    const next = !open;
    setOpen(next);
    if (next && tasks === null) onOpen();
  }, [open, tasks, onOpen]);

  const dropHighlight = isOver
    ? " ring-2 ring-blue-400/60 border-blue-300 dark:border-blue-700"
    : "";

  return (
    <section
      ref={setNodeRef}
      data-milestone-unassigned-zone
      data-drop-over={isOver || undefined}
      className={`mb-3 flex flex-col gap-2 rounded-lg border border-dashed border-zinc-300 bg-zinc-50/60 p-4 transition-colors dark:border-zinc-700 dark:bg-zinc-900/40${dropHighlight}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-200">
            Unassigned
          </h3>
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            Tasks with no milestone — drag one onto a milestone row to assign it,
            or drop a task here to unassign.
          </span>
        </div>
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          className="shrink-0 self-start text-xs font-medium text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          data-milestone-unassigned-expand
        >
          {open ? "Hide tasks" : "View tasks"}
        </button>
      </div>

      {open && (
        <div
          className="border-t border-zinc-200 pt-2 dark:border-zinc-800"
          data-milestone-unassigned-tasks
        >
          {loading ? (
            <p
              className="text-xs text-zinc-400 italic dark:text-zinc-500"
              role="status"
            >
              Loading tasks…
            </p>
          ) : error !== null ? (
            <p className="text-xs text-red-700 dark:text-red-300" role="alert">
              {error}
            </p>
          ) : tasks === null || tasks.length === 0 ? (
            <p className="text-xs text-zinc-500 italic dark:text-zinc-400">
              No unassigned tasks.
            </p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {tasks.map((t) => (
                <TaskChip key={t.id} task={t} projectName={projectName} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

// renderBar — the bar / diamond / "no dates" content for one timeline row.
function renderBar(
  s: DatedSpan,
  domain: { min: number; max: number } | null,
  pctOf: (day: number) => number,
): React.ReactNode {
  const { milestone } = s;

  // No dates at all → label in the bar lane (kept on the rail per the AC).
  if (s.startDay == null && s.endDay == null) {
    return (
      <span
        data-gantt-nodates={milestone.id}
        className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] italic text-zinc-400 dark:text-zinc-500"
      >
        no dates
      </span>
    );
  }

  // Domain should exist whenever at least one milestone is dated; guard anyway.
  if (!domain) return null;

  // Target-only (no start) → diamond at the deadline.
  if (s.startDay == null && s.endDay != null) {
    return (
      <span
        data-gantt-diamond={milestone.id}
        title={`${milestone.title} — target ${milestone.target_date}`}
        className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] bg-violet-500 dark:bg-violet-400"
        style={{ left: `${pctOf(s.endDay)}%` }}
      />
    );
  }

  // Start-only (no target) → diamond at start (degenerate but supported).
  if (s.startDay != null && s.endDay == null) {
    return (
      <span
        data-gantt-diamond={milestone.id}
        title={`${milestone.title} — start ${milestone.start_date} (no target)`}
        className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] bg-amber-500 dark:bg-amber-400"
        style={{ left: `${pctOf(s.startDay)}%` }}
      />
    );
  }

  // Full bar: start → target. Both non-null here.
  const left = pctOf(s.startDay as number);
  const right = pctOf(s.endDay as number);
  const width = Math.max(right - left, 0.6); // min visible width for 1-day spans
  const released = milestone.milestone_status === "released";
  const cancelled = milestone.milestone_status === "cancelled";
  const barColor = cancelled
    ? "bg-red-400/70 dark:bg-red-500/50"
    : released
      ? "bg-emerald-500 dark:bg-emerald-400"
      : "bg-sky-500 dark:bg-sky-400";

  return (
    <div
      data-gantt-bar={milestone.id}
      title={`${milestone.title}: ${milestone.start_date} → ${milestone.target_date}`}
      className={`absolute top-1/2 flex h-5 -translate-y-1/2 items-center overflow-hidden rounded px-1 ${barColor}`}
      style={{ left: `${left}%`, width: `${width}%` }}
    >
      <span className="truncate text-[10px] font-medium text-white">
        {milestone.title}
      </span>
    </div>
  );
}
