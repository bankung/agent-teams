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

import { patchTask, type ProjectRead, type TaskRead } from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
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
  for (const bucket of groups.values()) {
    bucket.sort((a, b) => b.priority - a.priority || a.id - b.id);
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
      let newPs: TaskStatusValue | undefined;
      if (typeof over.id === "string") {
        newPs = COLUMN_PS[over.id];
      } else {
        const overTask = tasks.find((t) => t.id === over.id);
        if (overTask === undefined) return;
        newPs = overTask.process_status;
      }
      if (newPs === undefined) return;

      const original = tasks.find((t) => t.id === taskId);
      if (!original) return;
      if (original.task_kind === "ai") return; // belt-and-suspenders (sortable is also disabled)
      if (original.process_status === newPs) return;

      setTasks((prev) =>
        prev.map((t) =>
          t.id === taskId ? { ...t, process_status: newPs } : t,
        ),
      );

      patchTask(project.id, taskId, { process_status: newPs })
        .then((server) => {
          setTasks((prev) => prev.map((t) => (t.id === taskId ? server : t)));
        })
        .catch((err: unknown) => {
          setTasks((prev) =>
            prev.map((t) => (t.id === taskId ? original : t)),
          );
          const msg = err instanceof Error ? err.message : "Update failed";
          pushToast(`Task #${taskId}: ${msg}`);
        });
    },
    [tasks, project.id, pushToast],
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
