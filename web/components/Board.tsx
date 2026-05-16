"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

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
import { Icon } from "@/components/Icon";
import { AiTaskModal } from "@/components/AiTaskModal";
import { NewTaskModal } from "@/components/NewTaskModal";
import { ProjectConsentBanner } from "@/components/ProjectConsentBanner";
import { ProjectSwitcher } from "@/components/ProjectSwitcher";
import { SourcesBadge } from "@/components/SourcesBadge";
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

export function Board({ initialTasks, hasHeadlessTask, project }: Props) {
  const router = useRouter();
  const [tasks, setTasks] = useState<TaskRead[]>(initialTasks);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const toastIdRef = useRef(1);

  // View toggle — default 'board'; persisted per-project in localStorage.
  // SSR-safe: initial state always 'board'; hydrated from localStorage in useEffect.
  const [view, setView] = useState<ViewMode>("board");

  useEffect(() => {
    const stored = localStorage.getItem(`kanban-view-${project.name}`);
    if (stored === "list" || stored === "board") setView(stored);
  }, [project.name]);

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
            const msg = err instanceof Error ? err.message : "Update failed";
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
            {tasks.length} task{tasks.length === 1 ? "" : "s"}
          </span>
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
          <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
            <AiTaskModal projectId={project.id} />
            <NewTaskModal projectId={project.id} />
            <ThemePicker />
          </span>
        </div>
        <ProjectConsentBanner
          project={project}
          hasHeadlessTask={hasHeadlessTask}
        />
      </header>
      {view === "list" ? (
        <ListView tasks={tasks} onOpenDetail={onOpenDetail} />
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
              />
            ))}
          </div>
        </DndContext>
      )}
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
