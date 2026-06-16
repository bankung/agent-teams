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
import { Icon } from "./Icon";
import { TaskActivityStrip } from "./TaskActivityStrip";

type Props = {
  task: TaskRead;
  onOpenDetail?: (task: TaskRead) => void;
  // #1001 follow-up (2026-05-20) — `?task=<id>` deep-link highlight. When
  // true, the card paints with a 2-second ring-pulse keyframe (defined in
  // globals.css) so the operator's eye lands on the matched card.
  highlighted?: boolean;
  // Kanban #2334 — project id needed to fetch the activity rail for IN_PROGRESS cards.
  projectId?: number;
  // #2412 — set of non-terminal task ids. Chip is suppressed when the blocker
  // is absent (terminal) or explicitly DONE/CANCELLED. Optional for backwards
  // compat (renders chip when not provided, preserving old behaviour).
  blockingTaskIds?: Set<number>;
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
  [TaskRole.SECURITY_REVIEWER]: "security",
};

const ROLE_CLASS: Record<number, string> = {
  [TaskRole.FRONTEND]: "text-blue-700 bg-blue-50 dark:text-blue-300 dark:bg-blue-900/30",
  [TaskRole.BACKEND]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.DEVOPS]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.QA]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.REVIEWER]: "text-indigo-700 bg-indigo-50 dark:text-indigo-300 dark:bg-indigo-900/30",
  [TaskRole.SECURITY_REVIEWER]: "text-rose-700 bg-rose-50 dark:text-rose-300 dark:bg-rose-900/30",
};

export function TaskCard({ task, onOpenDetail, highlighted = false, projectId, blockingTaskIds }: Props) {
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
  // Wave B (#4) — bug task_type gets a red left-accent border so bugs are
  // visually distinct on the board. Overrides the default zinc border-l only;
  // the remaining three sides stay zinc-200.
  const bugAccent =
    task.task_type === "bug"
      ? "border-l-4 border-l-red-500 dark:border-l-red-400"
      : "";
  const baseCard = `rounded-md border border-zinc-200 dark:border-zinc-800 ${cardBg} ${bugAccent} p-2.5 transition-colors`;
  const cursor = draggable ? " cursor-grab active:cursor-grabbing" : " cursor-not-allowed";
  // #1001 follow-up — deep-link ring-pulse. Class defined in globals.css
  // (animation-deep-link-pulse — 2s, ring-violet-500). Append after base so
  // the keyframe ring overrides the static border-zinc.
  const highlightClass = highlighted ? " animate-deep-link-pulse" : "";
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
      data-disabled={isAi || undefined}
      aria-label={`Task ${task.id}: ${task.title}`}
      data-run-mode={task.run_mode}
      data-task-id={task.id}
      data-task-card-id={task.id}
      data-task-kind={task.task_kind}
      data-is-template={task.is_template}
      data-draggable={draggable}
      data-card-pending={isPending}
      data-deep-link-highlighted={highlighted ? "true" : undefined}
      data-blocked-by={task.blocked_by ?? undefined}
      className={baseCard + cursor + highlightClass}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[11px] text-zinc-400 dark:text-zinc-500">#{task.id}</span>
        <div className="flex flex-wrap items-center gap-1.5">
          {steps && <StepCounter done={steps.done} total={steps.total} />}
          {/* #2412 — suppress chip when blocker is terminal (DONE/CANCELLED or absent
              from the loaded set, which means it's beyond the first-50 DONE rows). */}
          {task.blocked_by !== null && (blockingTaskIds === undefined || blockingTaskIds.has(task.blocked_by)) && (
            <span
              title={`Blocked by #${task.blocked_by}`}
              data-blocked-by-chip
              className="inline-flex items-center gap-1 rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700 dark:bg-red-900/30 dark:text-red-300"
            >
              <Icon name="status-blocked" size={11} />
              #{task.blocked_by}
            </span>
          )}
          {(task.interaction_kind === "question" || task.interaction_kind === "decision") && (() => {
            // #988 — HITL badge. Amber + "awaiting answer" while the task is paused
            // with no accepted answer; muted violet once resolved (done/cancelled or
            // answer_history has any is_valid=true entry).
            const hasAcceptedAnswer = (task.question_payload?.answer_history ?? [])
              .some((entry) => entry.is_valid === true);
            const isTerminal =
              task.process_status === TaskStatus.DONE ||
              task.process_status === TaskStatus.CANCELLED;
            const awaiting = !hasAcceptedAnswer && !isTerminal;
            const tooltip = awaiting
              ? "Awaiting answer"
              : task.interaction_kind === "decision"
                ? "Decision needed"
                : "Question for user";
            const chipClass = awaiting
              ? "inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
              : "inline-flex items-center gap-1 rounded bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-700 dark:bg-violet-900/30 dark:text-violet-300";
            return (
              <span
                title={tooltip}
                data-interaction-kind={task.interaction_kind}
                data-hitl-badge={awaiting ? "awaiting" : "resolved"}
                className={chipClass}
              >
                {task.interaction_kind === "decision" ? <Icon name="alert" size={11} /> : <Icon name="tooltip" size={11} />}
              </span>
            );
          })()}
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
      {/* Kanban #2334 — activity strip + running/idle dot for IN_PROGRESS cards only. */}
      {inProgress && projectId !== undefined && (
        <TaskActivityStrip projectId={projectId} taskId={task.id} />
      )}
    </article>
  );
}
