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
};

export function BoardColumn({ columnId, statuses, label, tasks }: Props) {
  const { isOver, setNodeRef } = useDroppable({ id: columnId });
  const taskIds = tasks.map((t) => t.id);
  const dropHighlight = isOver ? " ring-2 ring-blue-400/50" : "";
  return (
    <section
      ref={setNodeRef}
      data-process-status={statuses.join("+")}
      className={`flex min-h-0 min-w-0 flex-col rounded-md bg-zinc-50/60 dark:bg-zinc-900/40 p-2.5${dropHighlight}`}
    >
      <header className="mb-2 flex items-center gap-1.5 border-b border-zinc-200 dark:border-zinc-800 pb-2 px-1">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
          {tasks.length}
        </span>
      </header>
      <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
        <div
          tabIndex={0}
          aria-label={`${label} cards`}
          className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto pr-1 [scrollbar-width:thin] [&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-thumb]:bg-zinc-300 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar]:w-1.5 hover:[&::-webkit-scrollbar-thumb]:bg-zinc-400 dark:[&::-webkit-scrollbar-thumb]:bg-zinc-700 dark:hover:[&::-webkit-scrollbar-thumb]:bg-zinc-600"
        >
          {tasks.length === 0 ? (
            <p className="px-1 py-4 text-center text-xs text-zinc-400 dark:text-zinc-500">—</p>
          ) : (
            tasks.map((task) => <TaskCard key={task.id} task={task} />)
          )}
        </div>
      </SortableContext>
    </section>
  );
}
