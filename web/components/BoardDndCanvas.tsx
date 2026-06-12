"use client";

// Kanban #2111 Part 3c — dnd-kit lazy wrapper.
// All @dnd-kit/core and @dnd-kit/sortable imports are isolated here so they
// are only bundled when the board view is active (loaded via next/dynamic in
// Board.tsx; never loaded for list view).
//
// Owns: sensor setup, DndContext, BoardColumn rendering, drag-end routing.
// Delegates: cross-lane PATCH + same-lane reorder to Board.tsx via callbacks
// (preserving Board's optimistic-update setTasks logic).
//
// NOTE: task #2112 will touch Board.tsx after this slice. Dnd-kit stays
// strictly inside this file — Board.tsx imports via next/dynamic only.

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useMemo } from "react";

import type { TaskRead } from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { BoardColumn } from "@/components/BoardColumn";

type Column = { statuses: TaskStatusValue[]; label: string; key: string };

// Drag-end callbacks — Board.tsx owns setTasks / optimistic mutation; we call
// back into Board for both cross-lane and same-lane operations.
export type OnCrossLaneDrop = (taskId: number, newStatus: TaskStatusValue, original: TaskRead) => void;
export type OnSameLaneReorder = (taskId: number, overTaskId: number, laneIds: number[]) => void;

type Props = {
  columns: Column[];
  tasks: TaskRead[];
  grouped: Map<TaskStatusValue, TaskRead[]>;
  visibleDoneCount: number;
  // Kanban #2112 — server-pagination signals for the DONE lane.
  doneHasMore: boolean;
  doneLoadingMore: boolean;
  onOpenDetail: (task: TaskRead) => void;
  highlightedTaskId: number | null;
  onLoadMoreDone: () => void;
  onCrossLaneDrop: OnCrossLaneDrop;
  onSameLaneReorder: OnSameLaneReorder;
};

// #2122 N1 — derive the column-key→process_status map from the columns prop
// instead of a hardcoded literal, so a rename/add in Board.tsx propagates here
// automatically. Each column's statuses[0] is the canonical status for that lane;
// cross-lane drops (over.id = col.key string) resolve through this map.
export function buildColumnPs(columns: Column[]): Record<string, TaskStatusValue> {
  const map: Record<string, TaskStatusValue> = {};
  for (const col of columns) {
    if (col.statuses.length > 0) map[col.key] = col.statuses[0];
  }
  return map;
}

export function BoardDndCanvas({
  columns,
  tasks,
  grouped,
  visibleDoneCount,
  doneHasMore,
  doneLoadingMore,
  onOpenDetail,
  highlightedTaskId,
  onLoadMoreDone,
  onCrossLaneDrop,
  onSameLaneReorder,
}: Props) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const columnPs = useMemo(() => buildColumnPs(columns), [columns]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const taskId = Number(active.id);
    const original = tasks.find((t) => t.id === taskId);
    if (!original) return;
    if (original.task_kind === "ai") return;

    let newPs: TaskStatusValue | undefined;
    let overTask: TaskRead | undefined;
    if (typeof over.id === "string") {
      newPs = columnPs[over.id];
    } else {
      overTask = tasks.find((t) => t.id === over.id);
      if (overTask === undefined) return;
      newPs = overTask.process_status;
    }
    if (newPs === undefined) return;

    if (original.process_status !== newPs) {
      onCrossLaneDrop(taskId, newPs, original);
      return;
    }

    // Same-lane reorder — only in TODO lane (#772)
    if (newPs !== TaskStatus.TODO) return;
    if (!overTask) return;
    if (overTask.id === original.id) return;

    const laneIds = (grouped.get(TaskStatus.TODO) ?? []).map((t) => t.id);
    onSameLaneReorder(taskId, overTask.id, laneIds);
  };

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      {/* #954 — mobile: page scrolls (no overflow-hidden, no min-h-0); desktop restores the fixed-height bounded lanes at lg */}
      <div
        data-board="dnd"
        className="grid flex-1 grid-cols-1 gap-3 md:grid-cols-3 lg:min-h-0 lg:grid-cols-5 lg:overflow-hidden"
      >
        {columns.map((col) => {
          const colTasks = col.statuses.flatMap((s) => grouped.get(s) ?? []);
          const isDone = col.statuses.includes(TaskStatus.DONE);
          const renderedTasks = isDone ? colTasks.slice(0, visibleDoneCount) : colTasks;
          // Kanban #2112 — show Load-more when:
          //   (a) client-side slice still has hidden rows, OR
          //   (b) server has more pages (doneHasMore).
          // totalCount reflects only what's loaded client-side (not the server
          // total, which is unknown); the column header shows loaded count.
          const hasClientRemainder = isDone && colTasks.length > visibleDoneCount;
          const showLoadMore = isDone && (hasClientRemainder || doneHasMore);
          return (
            <BoardColumn
              key={col.key}
              columnId={col.key}
              statuses={col.statuses}
              label={col.label}
              tasks={renderedTasks}
              totalCount={isDone ? colTasks.length : undefined}
              onLoadMore={showLoadMore ? onLoadMoreDone : undefined}
              loadMoreLoading={isDone ? doneLoadingMore : undefined}
              onOpenDetail={onOpenDetail}
              sortable={col.statuses.includes(TaskStatus.TODO)}
              highlightedTaskId={highlightedTaskId}
            />
          );
        })}
      </div>
    </DndContext>
  );
}
