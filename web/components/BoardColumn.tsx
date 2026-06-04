"use client";

import { useDroppable } from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import type { TaskRead } from "@/lib/api";
import type { TaskStatusValue } from "@/lib/constants";
import { TaskCard } from "./TaskCard";

type Props = {
  columnId: string;
  statuses: TaskStatusValue[];
  label: string;
  tasks: TaskRead[];
  onOpenDetail?: (task: TaskRead) => void;
  // #772 — marker prop: which lane wires within-lane reorder. SortableContext
  // remains wrapping every lane (it gives cross-lane drag visual feedback for
  // free), but `data-lane-sortable` on the section lets probes + Board.onDragEnd
  // confirm this is the lane the FE reorders through `POST /api/tasks/{id}/reorder`.
  sortable?: boolean;
  // #1001 follow-up (2026-05-20) — when a deep-link `?task=<id>` matches a
  // card in this column, the Board passes the id down so the card renders
  // a 2-second ring-pulse highlight. null = no highlight.
  highlightedTaskId?: number | null;
  // #pagination — when set, the header count shows this total (not tasks.length)
  // and the footer "Load more" button is rendered while onLoadMore is defined.
  totalCount?: number;
  onLoadMore?: () => void;
};

export function BoardColumn({ columnId, statuses, label, tasks, onOpenDetail, sortable = false, highlightedTaskId = null, totalCount, onLoadMore }: Props) {
  const { isOver, setNodeRef } = useDroppable({ id: columnId });
  const taskIds = tasks.map((t) => t.id);
  const dropHighlight = isOver ? " ring-2 ring-blue-400/50" : "";
  const displayCount = totalCount ?? tasks.length;
  const remaining = totalCount !== undefined ? totalCount - tasks.length : 0;
  return (
    <section
      ref={setNodeRef}
      data-process-status={statuses.join("+")}
      data-lane-sortable={sortable}
      className={`flex min-w-0 flex-col rounded-md bg-zinc-50/60 dark:bg-zinc-900/40 p-2.5 lg:min-h-0${dropHighlight}`}
    >
      <header className="mb-2 flex items-center gap-1.5 border-b border-zinc-200 dark:border-zinc-800 pb-2 px-1">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
          {displayCount}
        </span>
      </header>
      <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
        {/* #954 — mobile: no per-lane scroll (lanes stack, page scrolls); desktop restores bounded inner scroll at lg */}
        <div
          tabIndex={0}
          aria-label={`${label} cards`}
          className="flex flex-col gap-1.5 pr-1 lg:min-h-0 lg:flex-1 lg:overflow-y-auto [scrollbar-width:thin] [&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-thumb]:bg-zinc-300 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar]:w-1.5 hover:[&::-webkit-scrollbar-thumb]:bg-zinc-400 dark:[&::-webkit-scrollbar-thumb]:bg-zinc-700 dark:hover:[&::-webkit-scrollbar-thumb]:bg-zinc-600"
        >
          {tasks.length === 0 ? (
            <p className="px-1 py-4 text-center text-xs text-zinc-400 dark:text-zinc-500">—</p>
          ) : (
            tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onOpenDetail={onOpenDetail}
                highlighted={highlightedTaskId === task.id}
              />
            ))
          )}
          {onLoadMore && (
            <button
              type="button"
              onClick={onLoadMore}
              className="mt-1 w-full rounded py-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800/60 transition-colors"
            >
              Load more ({remaining} more)
            </button>
          )}
        </div>
      </SortableContext>
    </section>
  );
}
