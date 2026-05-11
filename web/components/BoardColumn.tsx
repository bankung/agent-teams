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
      className={`flex min-w-0 flex-col rounded-md bg-zinc-50/60 p-2.5${dropHighlight}`}
    >
      <header className="mb-2 flex items-center gap-1.5 border-b border-zinc-200 pb-2 px-1">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          {label}
        </span>
        <span aria-hidden className="text-zinc-300">
          ·
        </span>
        <span className="text-xs tabular-nums text-zinc-500">
          {tasks.length}
        </span>
      </header>
      <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col gap-1.5">
          {tasks.length === 0 ? (
            <p className="px-1 py-4 text-center text-xs text-zinc-400">—</p>
          ) : (
            tasks.map((task) => <TaskCard key={task.id} task={task} />)
          )}
        </div>
      </SortableContext>
    </section>
  );
}
