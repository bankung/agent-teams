// Per-project Calendar page — Kanban #1873 (M2, 2026-06-03).
//
// Month-grid of task due_dates + milestone deadlines, scoped to one project.
// Lives under /p/[name]/ alongside /milestones — resolves the active project by
// name exactly like that page (Server Component; 404 → notFound, else re-throw
// into app/error.tsx).
//
// Data (SSR, mirrors the milestones page's fan-out):
//   - The visible month comes from `?month=YYYY-MM` (default = operator-local
//     current month). The server computes [1st, last] of that month and fetches
//     tasks via listTasks({ due_from, due_to }) — only due-dated tasks in range.
//   - listMilestones(projectId) returns every milestone; the client view places
//     the ones with a target_date on the grid (deadline markers).
//
// The client view (CalendarView) owns the grid render, Prev/Next/Today nav
// (which rewrite the ?month param), and the per-cell "+N more" overflow.

import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getProjectByName,
  listMilestones,
  listTasks,
  HttpError,
} from "@/lib/api";
import {
  currentYearMonth,
  monthRangeKeys,
  parseMonthParam,
} from "@/lib/calendarDates";
import { CalendarView } from "@/components/CalendarView";
import { ThemePicker } from "@/components/ThemePicker";
import { ViewSwitcher } from "@/components/ViewSwitcher";

type Props = {
  params: { name: string };
  searchParams: { month?: string };
};

export const dynamic = "force-dynamic";

export default async function ProjectCalendarPage({
  params,
  searchParams,
}: Props) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }

  // Resolve the visible month from the URL param; malformed → current month.
  const ym = parseMonthParam(searchParams.month) ?? currentYearMonth();
  const { from, to } = monthRangeKeys(ym);

  // Tasks in the visible month's due_date range + every milestone (small N).
  // A milestone fetch failing degrades to an empty list rather than blanking
  // the whole page (mirrors the milestones page's defensive fan-out).
  const [tasks, milestones] = await Promise.all([
    listTasks(project.id, { due_from: from, due_to: to, limit: 500 }),
    listMilestones(project.id, { limit: 500 }).catch(() => []),
  ]);

  const boardHref = `/p/${encodeURIComponent(project.name)}`;

  return (
    <main className="flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href={boardHref}
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← {project.name} board
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Calendar
        </span>
        <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
          ({project.name})
        </span>
        {/* Wave A (#1) — shared view switcher (Calendar active). Off-board: all
            four items navigate (no onSelect). */}
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <ViewSwitcher projectName={project.name} active="calendar" />
          <ThemePicker />
        </span>
      </header>

      <div className="mx-auto w-full max-w-5xl">
        <CalendarView
          projectName={project.name}
          year={ym.year}
          month0={ym.month0}
          tasks={tasks}
          milestones={milestones}
        />
      </div>
    </main>
  );
}
