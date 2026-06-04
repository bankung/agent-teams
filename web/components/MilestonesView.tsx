"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  type MilestoneStatusValue,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { MilestoneStatusBadge } from "./MilestoneStatusBadge";
import { MilestoneFormModal } from "./MilestoneFormModal";
import { MilestoneDeleteModal } from "./MilestoneDeleteModal";

// MilestonesView — per-project milestones list (Kanban #1868 FE, v1).
//
// Wave B (#3b) — milestone card left-accent border keyed to milestone_status,
// mirroring the MilestoneStatusBadge hue vocabulary:
//   planned   → zinc    (neutral)
//   active    → amber   (in flight)
//   released  → emerald (done)
//   cancelled → red     (terminal)
//
// Wave D (#10) — drag a task onto a milestone card to (re)assign it.
//   - One DndContext wraps the whole view (drag source in card/pool A, drop
//     target on card/pool B), mirroring Board's sensor/pattern conventions
//     (PointerSensor distance:4 + KeyboardSensor).
//   - Milestone cards + an "Unassigned" pool are droppables; the task rows in
//     each expanded list (and in the pool) are draggables.
//   - On drop → PATCH the task's milestone_id (or null on the Unassigned zone)
//     → optimistic move between lists, revert on failure, router.refresh() so
//     the server recomputes the milestone rollups.
//
// State-ownership note (Wave D): the per-milestone task lists + the Unassigned
// pool are LIFTED into this parent (was per-card local state) because a single
// drag mutates TWO lists (source + target). They live in `taskLists` keyed by
// milestone id (plus the UNASSIGNED key). Lazy-load is preserved — a list is
// fetched on first expand / pool-open, cached thereafter.
//
// Server component (page.tsx) fetches every milestone WITH its rollup
// (MilestoneDetail[]) and the project name; this client view owns:
//   - the milestone cards (status badge, date window, progress bar, task count)
//   - the New / Edit / Delete modal flows (router.refresh() on success so the
//     server re-fetches the authoritative list + fresh rollups)
//   - inline expand → lazy GET /api/tasks?milestone_id= to surface a
//     milestone's task list (one fetch per expand, cached in local state)
//   - DnD assignment (Wave D #10)
//
// a11y fallback: pointer + keyboard DnD are both wired (KeyboardSensor), but the
// existing MilestoneCombobox (in TaskDetail / NewTaskModal / AiTaskModal) remains
// the primary non-DnD assign path. See REPORT note.
//
// No board group-by, no calendar, no gantt — those are separate future slices.

// Wave B (#3b) — left-accent border per milestone_status. Applied as a 4px
// left border on the card; right/top/bottom keep the standard zinc-200 border.
const MILESTONE_ACCENT: Record<MilestoneStatusValue, string> = {
  planned: "border-l-4 border-l-zinc-400 dark:border-l-zinc-600",
  active: "border-l-4 border-l-amber-400 dark:border-l-amber-500",
  released: "border-l-4 border-l-emerald-500 dark:border-l-emerald-400",
  cancelled: "border-l-4 border-l-red-400 dark:border-l-red-500",
};

const STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
};

// Wave D (#10) — drop-target id scheme. Milestone droppable ids are namespaced
// so they never collide with the Unassigned pool. Draggable task ids carry the
// numeric task id in `data` (see TaskChip) rather than parsing the id string.
const UNASSIGNED = "unassigned";
const dropIdForMilestone = (id: number) => `milestone-${id}`;

// taskLists key for the Unassigned pool; milestone lists key on their numeric id.
type ListKey = number | typeof UNASSIGNED;

function formatDateRange(start: string | null, target: string | null): string {
  if (start && target) return `${start} → ${target}`;
  if (start) return `${start} → —`;
  if (target) return `— → ${target}`;
  return "no dates set";
}

// Stable sort for an expanded/pool task list: process_status then id.
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

export function MilestonesView({ projectId, projectName, milestones }: Props) {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<MilestoneRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MilestoneRead | null>(null);

  // router.refresh() re-runs the server component, which re-fetches the
  // milestone list + rollups. We don't merge optimistically for modal CRUD —
  // the rollup is server-computed, so a refresh is the simplest correct path.
  const onMutated = useCallback(() => {
    router.refresh();
  }, [router]);

  // ── Wave D (#10) — DnD shared state ──────────────────────────────────────
  // Per-key task lists (milestone id → TaskRead[] | null; UNASSIGNED → pool).
  // null = not yet fetched (lazy); array = loaded snapshot we mutate optimistically.
  const [taskLists, setTaskLists] = useState<Record<string, TaskRead[] | null>>(
    {},
  );
  const [loadingKeys, setLoadingKeys] = useState<Record<string, boolean>>({});
  const [errorKeys, setErrorKeys] = useState<Record<string, string | null>>({});
  // The task currently being dragged (for the DragOverlay preview).
  const [activeTask, setActiveTask] = useState<TaskRead | null>(null);
  // Toast surface for PATCH failures (mirrors Board.pushToast — minimal here).
  const [dndError, setDndError] = useState<string | null>(null);

  const keyStr = (k: ListKey) => String(k);

  // Lazy-load a list for a key (milestone id or UNASSIGNED). Idempotent: skips
  // if already loaded or in flight. UNASSIGNED fetches milestone_id-null tasks
  // (the BE has no explicit "null" filter param, so we fetch a top-level page
  // and filter client-side to milestone_id == null — mirrors Board's "none"
  // filter predicate). Milestone keys use the ?milestone_id= server filter.
  const loadList = useCallback(
    (key: ListKey) => {
      const ks = keyStr(key);
      // Guard against duplicate in-flight fetches (and re-fetch of a loaded
      // list). setLoadingKeys' updater is the single source of truth so the
      // guard is race-safe under React batching.
      let skip = false;
      setLoadingKeys((prev) => {
        if (prev[ks]) {
          skip = true; // already in flight
          return prev;
        }
        return { ...prev, [ks]: true };
      });
      if (skip) return;

      setErrorKeys((prev) => ({ ...prev, [ks]: null }));

      const fetchPromise =
        key === UNASSIGNED
          ? listTasks(projectId, { limit: 500 }).then((rows) =>
              rows.filter((t) => t.milestone_id == null),
            )
          : listTasks(projectId, { milestone_id: key, limit: 500 });

      fetchPromise
        .then((rows) => {
          setTaskLists((prev) => ({ ...prev, [ks]: sortTasks(rows) }));
        })
        .catch((err: unknown) => {
          setErrorKeys((prev) => ({
            ...prev,
            [ks]: extractErrorMessage(err, "Failed to load tasks"),
          }));
          setTaskLists((prev) => ({ ...prev, [ks]: [] }));
        })
        .finally(() => {
          setLoadingKeys((prev) => ({ ...prev, [ks]: false }));
        });
    },
    [projectId],
  );

  // Sensors mirror Board: pointer with a 4px activation threshold (so a click
  // to expand/navigate isn't swallowed as a drag) + keyboard for a11y.
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
      // dest list is already loaded — an unloaded dest stays null so its first
      // expand fetches the authoritative list, which will include this task).
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
          // Reconcile with the server row in whichever list it now lives in.
          setTaskLists((prev) => {
            const next = { ...prev };
            if (Array.isArray(next[destStr])) {
              next[destStr] = sortTasks(
                next[destStr]!.map((t) => (t.id === server.id ? server : t)),
              );
            }
            return next;
          });
          // Server recomputes milestone rollups (progress bars / counts).
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

  return (
    <DndContext
      sensors={sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {milestones.length} milestone{milestones.length === 1 ? "" : "s"}
        </h2>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-1.5 rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
          data-new-milestone-trigger
        >
          New milestone
        </button>
      </div>

      {/* Wave D (#10) — inline DnD failure notice (revert already happened). */}
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

      {/* Wave D (#10) — drag tasks here to unassign (milestone_id → null). */}
      <UnassignedPool
        projectName={projectName}
        tasks={taskLists[UNASSIGNED] ?? null}
        loading={loadingKeys[UNASSIGNED] ?? false}
        error={errorKeys[UNASSIGNED] ?? null}
        onOpen={() => loadList(UNASSIGNED)}
      />

      {milestones.length === 0 ? (
        <p
          className="rounded border border-dashed border-zinc-200 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
          data-milestones-empty
        >
          No milestones yet. Create one to group tasks for release planning.
        </p>
      ) : (
        <ul className="flex flex-col gap-3" data-milestones-list>
          {milestones.map((m) => (
            <MilestoneCard
              key={m.id}
              milestone={m}
              projectName={projectName}
              tasks={taskLists[keyStr(m.id)] ?? null}
              loading={loadingKeys[keyStr(m.id)] ?? false}
              error={errorKeys[keyStr(m.id)] ?? null}
              onExpand={() => loadList(m.id)}
              onEdit={() => setEditTarget(m)}
              onDelete={() => setDeleteTarget(m)}
            />
          ))}
        </ul>
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
    </DndContext>
  );
}

// ── Wave D (#10) — a single draggable task row, shared by cards + pool ──────
// `sourceKey` lets onDragEnd remove the task from the correct list optimistically.
function TaskChip({
  task,
  projectName,
  sourceKey,
}: {
  task: TaskRead;
  projectName: string;
  sourceKey: ListKey;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `task-${task.id}`,
    data: { task, sourceKey },
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
        aria-label={`Drag task #${task.id} to reassign milestone`}
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

// ── Wave D (#10) — Unassigned pool: droppable + draggable list of null tasks ─
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
            Tasks with no milestone — drop a task here to unassign it.
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
                <TaskChip
                  key={t.id}
                  task={t}
                  projectName={projectName}
                  sourceKey={UNASSIGNED}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function MilestoneCard({
  milestone,
  projectName,
  tasks,
  loading,
  error,
  onExpand,
  onEdit,
  onDelete,
}: {
  milestone: MilestoneDetail;
  projectName: string;
  tasks: TaskRead[] | null;
  loading: boolean;
  error: string | null;
  onExpand: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { rollup } = milestone;
  const pct = Math.max(0, Math.min(100, rollup.progress_pct));

  const [expanded, setExpanded] = useState(false);

  // Wave D (#10) — the card is a drop target for task chips.
  const { isOver, setNodeRef } = useDroppable({
    id: dropIdForMilestone(milestone.id),
    data: { milestoneId: milestone.id },
  });

  const toggleExpand = useCallback(() => {
    const next = !expanded;
    setExpanded(next);
    if (next && tasks === null) onExpand();
  }, [expanded, tasks, onExpand]);

  // Sort happens in the parent (lists are stored pre-sorted); memo guards a
  // stable identity for the render below.
  const sortedTasks = useMemo(() => tasks, [tasks]);

  const dropHighlight = isOver
    ? " ring-2 ring-blue-400/60 border-blue-300 dark:border-blue-700"
    : "";

  return (
    <li
      ref={setNodeRef}
      data-milestone-card
      data-milestone-id={milestone.id}
      data-milestone-status={milestone.milestone_status}
      data-drop-over={isOver || undefined}
      className={`flex flex-col gap-2 rounded-lg border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700 ${MILESTONE_ACCENT[milestone.milestone_status]}${dropHighlight}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {milestone.title}
            </h3>
            <MilestoneStatusBadge status={milestone.milestone_status} />
          </div>
          <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
            {formatDateRange(milestone.start_date, milestone.target_date)}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onEdit}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-milestone-edit
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-600 hover:border-red-300 hover:text-red-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-red-800 dark:hover:text-red-300"
            data-milestone-delete
          >
            Delete
          </button>
        </div>
      </div>

      {milestone.description && (
        <p className="whitespace-pre-wrap text-sm text-zinc-700 dark:text-zinc-300">
          {milestone.description}
        </p>
      )}

      {/* Progress bar — done / total (excluding cancelled) + progress_pct. */}
      <div className="flex flex-col gap-1" data-milestone-progress>
        <div className="flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
          <span>
            {rollup.done}/{rollup.total} done
          </span>
          <span>{pct.toFixed(1)}%</span>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${milestone.title} progress`}
        >
          <div
            className="h-full rounded-full bg-emerald-500 transition-[width] dark:bg-emerald-400"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Expand → lazy task list */}
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={toggleExpand}
          aria-expanded={expanded}
          className="self-start text-xs font-medium text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          data-milestone-expand
        >
          {expanded ? "Hide tasks" : `View tasks (${rollup.total})`}
        </button>
        {/* Convenience deep-link to the board (full task context). */}
        <Link
          href={`/p/${encodeURIComponent(projectName)}`}
          className="text-xs text-zinc-500 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          Open board →
        </Link>
      </div>

      {expanded && (
        <div
          className="mt-1 border-t border-zinc-100 pt-2 dark:border-zinc-800"
          data-milestone-tasks
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
          ) : sortedTasks === null || sortedTasks.length === 0 ? (
            <p className="text-xs text-zinc-500 italic dark:text-zinc-400">
              No tasks assigned to this milestone.
            </p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {sortedTasks.map((t) => (
                <TaskChip
                  key={t.id}
                  task={t}
                  projectName={projectName}
                  sourceKey={milestone.id}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}
