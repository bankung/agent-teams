"use client";

import { CSS } from "@dnd-kit/utilities";
import { useSortable } from "@dnd-kit/sortable";

import type { TaskRead } from "@/lib/api";
import { TaskPriority, TaskRole, TaskStatus } from "@/lib/constants";
import { parseSteps } from "@/lib/parseSteps";
import { RunModeBadge } from "./RunModeBadge";
import { TaskKindBadge } from "./TaskKindBadge";
import { PendingBadge } from "./PendingBadge";
import { RecurrenceIndicator } from "./RecurrenceIndicator";
import { StepCounter } from "./StepCounter";

type Props = {
  task: TaskRead;
  onOpenDetail?: (task: TaskRead) => void;
};

const PRIORITY_LABEL: Record<number, string> = {
  [TaskPriority.LOW]: "low",
  [TaskPriority.NORMAL]: "normal",
  [TaskPriority.HIGH]: "high",
  [TaskPriority.URGENT]: "urgent",
};

const PRIORITY_CLASS: Record<number, string> = {
  [TaskPriority.LOW]: "text-zinc-500 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800",
  [TaskPriority.NORMAL]: "text-zinc-600 bg-zinc-100 dark:text-zinc-300 dark:bg-zinc-800",
  [TaskPriority.HIGH]: "text-orange-700 bg-orange-50 dark:text-orange-300 dark:bg-orange-900/30",
  [TaskPriority.URGENT]: "text-red-700 bg-red-50 dark:text-red-300 dark:bg-red-900/30",
};

const ROLE_LABEL: Record<number, string> = {
  [TaskRole.FRONTEND]: "frontend",
  [TaskRole.BACKEND]: "backend",
  [TaskRole.DEVOPS]: "devops",
  [TaskRole.QA]: "qa",
  [TaskRole.REVIEWER]: "reviewer",
};

const ROLE_CLASS: Record<number, string> = {
  [TaskRole.FRONTEND]: "text-blue-700 bg-blue-50 dark:text-blue-300 dark:bg-blue-900/30",
  [TaskRole.BACKEND]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.DEVOPS]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.QA]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.REVIEWER]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
};

export function TaskCard({ task, onOpenDetail }: Props) {
  const isAi = task.task_kind === "ai";
  const isPending = task.is_pending && task.process_status === TaskStatus.IN_PROGRESS;
  const inProgress = task.process_status === TaskStatus.IN_PROGRESS;
  const steps = inProgress ? parseSteps(task.description) : null;
  const draggable = !isAi && !isPending;
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: task.id, disabled: !draggable });

  const cardBg = isPending
    ? "bg-yellow-50 hover:bg-yellow-100 hover:border-yellow-300 dark:bg-yellow-900/30 dark:hover:bg-yellow-900/40 dark:hover:border-yellow-700"
    : "bg-white hover:bg-zinc-50 hover:border-zinc-300 dark:bg-zinc-900 dark:hover:bg-zinc-800/50 dark:hover:border-zinc-700";
  const baseCard = `rounded-md border border-zinc-200 dark:border-zinc-800 ${cardBg} p-2.5 transition-colors`;
  const cursor = draggable ? " cursor-grab active:cursor-grabbing" : " cursor-not-allowed";
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  return (
    <article
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onClick={onOpenDetail ? () => onOpenDetail(task) : undefined}
      aria-disabled={isAi}
      aria-label={`Task ${task.id}: ${task.title}`}
      data-run-mode={task.run_mode}
      data-task-id={task.id}
      data-task-kind={task.task_kind}
      data-is-template={task.is_template}
      data-draggable={draggable}
      data-card-pending={isPending}
      data-blocked-by={task.blocked_by ?? undefined}
      className={baseCard + cursor}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[11px] text-zinc-400 dark:text-zinc-500">#{task.id}</span>
        <div className="flex flex-wrap items-center gap-1.5">
          {steps && <StepCounter done={steps.done} total={steps.total} />}
          {task.blocked_by !== null && (
            <span
              title={`Blocked by #${task.blocked_by}`}
              data-blocked-by-chip
              className="inline-flex items-center rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700 dark:bg-red-900/30 dark:text-red-300"
            >
              ⛔ #{task.blocked_by}
            </span>
          )}
          {(task.interaction_kind === "question" || task.interaction_kind === "decision") && (
            <span
              title={task.interaction_kind === "decision" ? "Decision needed" : "Question for user"}
              data-interaction-kind={task.interaction_kind}
              className="inline-flex items-center rounded bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-700 dark:bg-violet-900/30 dark:text-violet-300"
            >
              {task.interaction_kind === "decision" ? "⚡" : "❓"}
            </span>
          )}
          <RunModeBadge mode={task.run_mode} />
          <TaskKindBadge kind={task.task_kind} />
          <PendingBadge task={task} />
        </div>
      </div>
      <h3 className="mt-1 line-clamp-2 text-sm font-medium leading-snug text-zinc-900 dark:text-zinc-100">
        {task.title}
      </h3>
      <RecurrenceIndicator task={task} />
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ${
            PRIORITY_CLASS[task.priority] ?? "text-zinc-600 bg-zinc-100 dark:text-zinc-300 dark:bg-zinc-800"
          }`}
        >
          {PRIORITY_LABEL[task.priority] ?? `p${task.priority}`}
        </span>
        {task.assigned_role !== null && (
          <span
            className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ${
              ROLE_CLASS[task.assigned_role] ?? "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30"
            }`}
          >
            {ROLE_LABEL[task.assigned_role] ?? `role${task.assigned_role}`}
          </span>
        )}
      </div>
    </article>
  );
}
