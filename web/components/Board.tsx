"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
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
  HttpError,
  patchTask,
  reorderTask,
  type ProjectRead,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { sortDoneLane, sortLaneTasks } from "@/lib/sortLaneTasks";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";
import { BoardColumn } from "@/components/BoardColumn";
import { ConnectionStateBadge } from "@/components/ConnectionStateBadge";
import { ProjectConsentBanner } from "@/components/ProjectConsentBanner";
import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { TaskDetail } from "@/components/TaskDetail";
import { ThemePicker } from "@/components/ThemePicker";
import { ToastStack, type ToastMessage } from "@/components/Toast";

type Props = {
  initialTasks: TaskRead[];
  hasHeadlessTask: boolean;
  project: ProjectRead;
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

function groupByStatus(tasks: TaskRead[]) {
  const groups = new Map<TaskStatusValue, TaskRead[]>();
  for (const s of ALL_STATUSES) groups.set(s, []);
  for (const task of tasks) {
    const bucket = groups.get(task.process_status);
    if (bucket) bucket.push(task);
  }
  // #772 — render order per lane matches the backend ORDER BY:
  // sort_order ASC NULLS LAST, created_at ASC. The legacy priority/id sort
  // is preserved as a tiebreaker for lanes where nothing has sort_order set
  // yet (the bulk of pre-#772 data) by composing the two stable sorts.
  // Run priority/id first, then sortLaneTasks — stable sort guarantees ties
  // on sort_order/created_at keep the priority-ordered position.
  //
  // #826 — Done lane breaks the pattern: it sorts by `updated_at DESC`
  // (newest-closed on top). priority/id pre-sort is skipped there because
  // updated_at is the dominant signal and breaking ties on id DESC inside
  // sortDoneLane is the deterministic fallback.
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

export function Board({ initialTasks, hasHeadlessTask, project }: Props) {
  const router = useRouter();
  const [tasks, setTasks] = useState<TaskRead[]>(initialTasks);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const toastIdRef = useRef(1);

  // Sync local Board state to fresh server-rendered initialTasks whenever the
  // RSC fetch re-runs (triggered by router.refresh() below on SSE events).
  // initialTasks identity changes per RSC render, so a referential-equality
  // effect is the right hook here — no diff needed; the prop IS the canonical
  // snapshot at refresh time.
  useEffect(() => {
    setTasks(initialTasks);
  }, [initialTasks]);

  // Real-time push (Kanban #783). On any tasks-row change for this project,
  // call router.refresh() — re-runs the RSC fetch, sends new initialTasks down
  // via the prop sync effect above. Hook handles 100ms debounce + 5-event /
  // 250ms hard-cap burst coalescing internally; one flush triggers one
  // router.refresh() (Next 14 dedupes anyway, but we keep the surface narrow).
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

  const grouped = useMemo(() => groupByStatus(tasks), [tasks]);

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
      if (original.task_kind === "ai") return; // belt-and-suspenders (sortable is also disabled)

      // Resolve drop target: either a column (over.id is the column key string)
      // or another task (over.id is a numeric task id).
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

      // Cross-lane drag → PATCH process_status (existing #709 path).
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
            const msg = err instanceof Error ? err.message : "Update failed";
            pushToast(`Task #${taskId}: ${msg}`);
          });
        return;
      }

      // Same-lane drop. Within-lane reorder is wired ONLY for the TODO lane
      // per #772 spawn brief. Other lanes: silent no-op (no PATCH, no toast —
      // visual transform snaps back when the SortableContext clears its drag
      // state on drop).
      if (newPs !== TaskStatus.TODO) return;
      if (!overTask) return;
      if (overTask.id === original.id) return;

      // Decide before_id vs after_id from the rendered lane order. The lane
      // array passed to BoardColumn is the canonical SortableContext order
      // (sortLaneTasks-sorted). oldIndex < newIndex means active moved
      // downward → it should land AFTER over. oldIndex > newIndex means it
      // moved upward → BEFORE over. Equal would mean dropped on itself
      // (handled above by the id-equality guard).
      const laneIds = (grouped.get(TaskStatus.TODO) ?? []).map((t) => t.id);
      const oldIndex = laneIds.indexOf(original.id);
      const newIndex = laneIds.indexOf(overTask.id);
      if (oldIndex === -1 || newIndex === -1) return;
      const body =
        oldIndex < newIndex
          ? { after_id: overTask.id }
          : { before_id: overTask.id };

      // NOTE: NO optimistic local mutation here. The dnd-kit transform shows
      // the new position visually during the drag; on drop the transform
      // clears and the card snaps back to the original rendered position
      // until the server response merges in. On 422, no merge happens →
      // cards remain in the pre-drag order (snap-back). On 200, the merged
      // task carries the new sort_order and sortLaneTasks (in groupByStatus)
      // re-orders the lane on the next render.
      reorderTask(project.id, taskId, body)
        .then((server) => {
          setTasks((prev) => prev.map((t) => (t.id === taskId ? server : t)));
        })
        .catch((err: unknown) => {
          const msg =
            err instanceof HttpError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Reorder failed";
          pushToast(`Task #${taskId}: ${msg}`);
        });
    },
    [tasks, grouped, project.id, pushToast],
  );

  return (
    <main className="flex h-screen flex-col overflow-hidden bg-white dark:bg-zinc-950 px-6 py-5">
      <header className="mb-4 flex flex-col gap-2">
        <div className="flex items-center gap-2 text-sm">
          <ProjectSwitcher current={project.name} />
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <span className="text-zinc-600 dark:text-zinc-400">
            team: <span className="text-zinc-900 dark:text-zinc-100">{project.team}</span>
          </span>
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <span className="text-zinc-500 dark:text-zinc-400 tabular-nums">
            {tasks.length} task{tasks.length === 1 ? "" : "s"}
          </span>
          <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
            ·
          </span>
          <ConnectionStateBadge
            state={connectionState}
            lastEventAt={lastEventAt}
          />
          <span className="ml-auto">
            <ThemePicker />
          </span>
        </div>
        <ProjectConsentBanner
          project={project}
          hasHeadlessTask={hasHeadlessTask}
        />
      </header>
      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <div
          data-board="dnd"
          className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden md:grid-cols-3 lg:grid-cols-5"
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
            />
          ))}
        </div>
      </DndContext>
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
