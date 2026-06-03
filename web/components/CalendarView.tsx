"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import type { MilestoneRead, TaskRead } from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import {
  buildMonthGrid,
  monthLabel,
  monthParamKey,
  addMonths,
  normalizeDateOnly,
  todayKey,
  currentYearMonth,
  WEEKDAY_LABELS,
  type YearMonth,
} from "@/lib/calendarDates";

// CalendarView — month-grid of task due_dates + milestone deadlines (#1873 M2).
//
// Server component (page.tsx) resolves the project + visible month from the
// `?month=YYYY-MM` URL param and SSR-fetches the month's due-dated tasks +
// every milestone. This client view owns:
//   - the Sun-started weeks × 7 grid (5 or 6 rows; pad days dimmed)
//   - Prev / Next / Today nav (rewrites the ?month param → server re-fetch)
//   - per-day task chips (colored by process_status) + milestone deadline
//     markers (🎯), with a "+N more" overflow cap per cell
//   - today highlight (operator-LOCAL civil date; see calendarDates.ts header)
//
// Click a task chip → /p/<name>?task=<id> (Board's existing highlight handler).
// Click a milestone marker → /p/<name>/milestones.
//
// No week/day modes, no drag-to-reschedule, no inline create — v1 month grid.

const MAX_ITEMS_PER_CELL = 3;

// Per-process_status chip color — mirrors the board lane vocabulary
// (TaskCard / MilestoneStatusBadge hues): TODO=zinc, IN_PROGRESS=amber,
// REVIEW=sky, BLOCKED=red, DONE=emerald, CANCELLED=zinc-strikethrough.
const STATUS_CHIP: Record<TaskStatusValue, string> = {
  [TaskStatus.TODO]:
    "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  [TaskStatus.IN_PROGRESS]:
    "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200",
  [TaskStatus.REVIEW]:
    "bg-sky-50 text-sky-800 dark:bg-sky-900/30 dark:text-sky-200",
  [TaskStatus.BLOCKED]:
    "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200",
  [TaskStatus.DONE]:
    "bg-emerald-50 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200",
  [TaskStatus.CANCELLED]:
    "bg-zinc-100 text-zinc-400 line-through dark:bg-zinc-800 dark:text-zinc-500",
};

const STATUS_LABEL: Record<TaskStatusValue, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
};

type Props = {
  projectName: string;
  year: number;
  month0: number; // 0..11
  tasks: TaskRead[];
  milestones: MilestoneRead[];
};

export function CalendarView({
  projectName,
  year,
  month0,
  tasks,
  milestones,
}: Props) {
  const router = useRouter();
  const ym: YearMonth = useMemo(() => ({ year, month0 }), [year, month0]);

  const grid = useMemo(() => buildMonthGrid(ym), [ym]);
  const today = useMemo(() => todayKey(), []);

  // Index tasks by their due_date key. Tasks without a due_date are dropped
  // (the BE range filter shouldn't return them, but guard anyway).
  const tasksByDay = useMemo(() => {
    const map = new Map<string, TaskRead[]>();
    for (const t of tasks) {
      const key = normalizeDateOnly(t.due_date);
      if (!key) continue;
      const bucket = map.get(key);
      if (bucket) bucket.push(t);
      else map.set(key, [t]);
    }
    // Stable per-cell order: process_status then id (mirrors MilestonesView).
    for (const bucket of map.values()) {
      bucket.sort((a, b) => a.process_status - b.process_status || a.id - b.id);
    }
    return map;
  }, [tasks]);

  // Index milestones by their target_date (deadline) key.
  const milestonesByDay = useMemo(() => {
    const map = new Map<string, MilestoneRead[]>();
    for (const m of milestones) {
      const key = normalizeDateOnly(m.target_date);
      if (!key) continue;
      const bucket = map.get(key);
      if (bucket) bucket.push(m);
      else map.set(key, [m]);
    }
    for (const bucket of map.values()) {
      bucket.sort((a, b) => a.id - b.id);
    }
    return map;
  }, [milestones]);

  const boardHref = `/p/${encodeURIComponent(projectName)}`;
  const milestonesHref = `/p/${encodeURIComponent(projectName)}/milestones`;
  const calendarHref = `/p/${encodeURIComponent(projectName)}/calendar`;

  // Absolute path push (codebase convention — every nav builds /p/<name>/...).
  // Rewrites the ?month param so the server component re-fetches the new
  // month's task range on navigation.
  const goToMonth = (target: YearMonth) => {
    router.push(`${calendarHref}?month=${monthParamKey(target)}`);
  };

  // Total visible items (for the empty-month message).
  const totalItems = tasks.length + milestonesByDay.size;

  return (
    <section data-calendar-view aria-label={`Calendar for ${projectName}`}>
      {/* Month label + Prev / Next / Today nav. */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
          data-calendar-month-label
        >
          {monthLabel(ym)}
        </h2>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => goToMonth(addMonths(ym, -1))}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            aria-label="Previous month"
            data-calendar-prev
          >
            ← Prev
          </button>
          <button
            type="button"
            onClick={() => goToMonth(currentYearMonth())}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-calendar-today
          >
            Today
          </button>
          <button
            type="button"
            onClick={() => goToMonth(addMonths(ym, 1))}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            aria-label="Next month"
            data-calendar-next
          >
            Next →
          </button>
        </div>
      </div>

      {totalItems === 0 && (
        <p
          className="mb-3 rounded border border-dashed border-zinc-200 px-4 py-3 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
          data-calendar-empty
        >
          No tasks with a due date and no milestone deadlines in {monthLabel(ym)}.
        </p>
      )}

      {/* Weekday header row. */}
      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-t-lg border border-b-0 border-zinc-200 bg-zinc-200 text-center text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-800 dark:text-zinc-400">
        {WEEKDAY_LABELS.map((d) => (
          <div key={d} className="bg-zinc-50 py-1.5 dark:bg-zinc-900">
            {d}
          </div>
        ))}
      </div>

      {/* Month grid — gap-px over a zinc background gives 1px cell borders. */}
      <div
        className="grid grid-cols-7 gap-px overflow-hidden rounded-b-lg border border-zinc-200 bg-zinc-200 dark:border-zinc-800 dark:bg-zinc-800"
        data-calendar-grid
      >
        {grid.flat().map((cell) => {
          const dayTasks = cell.inMonth
            ? tasksByDay.get(cell.key) ?? []
            : [];
          const dayMilestones = cell.inMonth
            ? milestonesByDay.get(cell.key) ?? []
            : [];
          const isToday = cell.key === today;

          // Milestone markers render first (higher salience), then tasks; the
          // overflow cap applies to the combined item list per cell.
          const items = [
            ...dayMilestones.map((m) => ({ kind: "milestone" as const, m })),
            ...dayTasks.map((t) => ({ kind: "task" as const, t })),
          ];
          const visible = items.slice(0, MAX_ITEMS_PER_CELL);
          const overflow = items.length - visible.length;

          return (
            <div
              key={cell.key}
              data-calendar-cell={cell.key}
              data-cell-in-month={cell.inMonth ? "true" : "false"}
              data-cell-today={isToday ? "true" : undefined}
              className={`flex min-h-[92px] flex-col gap-1 p-1.5 ${
                cell.inMonth
                  ? "bg-white dark:bg-zinc-950"
                  : "bg-zinc-50 dark:bg-zinc-900/40"
              } ${isToday ? "ring-2 ring-inset ring-sky-500 dark:ring-sky-400" : ""}`}
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-xs tabular-nums ${
                    isToday
                      ? "flex h-5 w-5 items-center justify-center rounded-full bg-sky-600 font-semibold text-white dark:bg-sky-500"
                      : cell.inMonth
                        ? "text-zinc-600 dark:text-zinc-300"
                        : "text-zinc-300 dark:text-zinc-600"
                  }`}
                >
                  {cell.day}
                </span>
              </div>

              {visible.map((item) =>
                item.kind === "milestone" ? (
                  <Link
                    key={`m-${item.m.id}`}
                    href={milestonesHref}
                    title={`Milestone deadline: ${item.m.title}`}
                    data-calendar-milestone={item.m.id}
                    className="flex items-center gap-1 truncate rounded bg-violet-50 px-1 py-0.5 text-[11px] font-medium text-violet-800 hover:bg-violet-100 dark:bg-violet-900/30 dark:text-violet-200 dark:hover:bg-violet-900/50"
                  >
                    <span aria-hidden>🎯</span>
                    <span className="truncate">{item.m.title}</span>
                  </Link>
                ) : (
                  <Link
                    key={`t-${item.t.id}`}
                    href={`${boardHref}?task=${item.t.id}`}
                    title={`#${item.t.id} ${item.t.title} (${
                      STATUS_LABEL[item.t.process_status] ?? "task"
                    })`}
                    data-calendar-task={item.t.id}
                    className={`truncate rounded px-1 py-0.5 text-[11px] hover:opacity-80 ${
                      STATUS_CHIP[item.t.process_status] ??
                      STATUS_CHIP[TaskStatus.TODO]
                    }`}
                  >
                    {item.t.title}
                  </Link>
                ),
              )}

              {overflow > 0 && (
                <span
                  className="px-1 text-[10px] font-medium text-zinc-400 dark:text-zinc-500"
                  data-calendar-overflow={overflow}
                >
                  +{overflow} more
                </span>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
