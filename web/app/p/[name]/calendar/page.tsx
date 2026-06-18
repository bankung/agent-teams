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
import { currentYearMonth, parseMonthParam } from "@/lib/calendarDates";
import { CalendarView } from "@/components/CalendarView";
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

  // Wave E (#14) — placement is by due_date (unfinished) OR completed_at
  // (finished). The BE only has a due_date range filter (no completed_at range),
  // so we fetch the project's task set and let CalendarView resolve placement +
  // filter to the visible cells client-side. This also lets the week view (#13)
  // and drag-to-reschedule (#12) cover days adjacent to the month boundary
  // without a re-fetch. CANCELLED is excluded by the BE default (and dropped
  // from placement anyway). A milestone fetch failing degrades to an empty list
  // rather than blanking the page (mirrors the milestones page's fan-out).
  // FOLLOW-UP: a BE completed_from/completed_to filter would let us window this
  // fetch again if a single project ever exceeds the 500-row cap.
  const [tasks, milestones] = await Promise.all([
    listTasks(project.id, { limit: 500 }),
    listMilestones(project.id, { limit: 500 }).catch(() => []),
  ]);

  const boardHref = `/p/${encodeURIComponent(project.name)}`;

  return (
    <main className="glass-board flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      {/* #2404 — 3-zone header: left (flex-1) · centered ViewSwitcher (shrink-0) · right placeholder (flex-1). */}
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        {/* LEFT zone */}
        <span className="flex flex-1 flex-wrap items-center gap-2">
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
        </span>
        {/* CENTER zone — Wave A (#1): shared view switcher (Calendar active). Off-board: all four items navigate (no onSelect). */}
        <span className="shrink-0">
          <ViewSwitcher projectName={project.name} active="calendar" />
        </span>
        {/* RIGHT zone — placeholder to balance the flex-1 left zone so the center stays centered. */}
        <span className="flex-1" />
      </header>

      <div className="mx-auto w-full max-w-5xl">
        <CalendarView
          projectId={project.id}
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
