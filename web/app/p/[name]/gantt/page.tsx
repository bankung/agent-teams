// Per-project Gantt page — Kanban #1874 (M3, 2026-06-03). MILESTONE-LEVEL v1.
//
// One horizontal bar per milestone (start_date → target_date) on a shared time
// axis. Tasks are NOT plotted (locked design — milestone-level only). Lives
// under /p/[name]/ alongside /milestones + /calendar; resolves the project by
// name the same way (Server Component; 404 → notFound, else re-throw).
//
// Data (SSR, mirrors the milestones page EXACTLY): list milestone rows (no
// rollup), then fetch each rollup in parallel via getMilestone. A single
// rollup fetch failing degrades that one row to a zeroed rollup rather than
// blanking the page. The client view (GanttView) computes the time axis +
// positions the bars.

import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getMilestone,
  getProjectByName,
  listMilestones,
  HttpError,
  type MilestoneDetail,
} from "@/lib/api";
import { GanttView } from "@/components/GanttView";
import { ViewSwitcher } from "@/components/ViewSwitcher";

type Props = { params: { name: string } };

export const dynamic = "force-dynamic";

export default async function ProjectGanttPage({ params }: Props) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }

  const rows = await listMilestones(project.id, { limit: 500 });
  const milestonesRaw: MilestoneDetail[] = await Promise.all(
    rows.map(async (row) => {
      try {
        return await getMilestone(project.id, row.id);
      } catch {
        return {
          ...row,
          rollup: { total: 0, by_process_status: {}, done: 0, progress_pct: 0 },
        };
      }
    }),
  );

  // Sort by status rank: active(0) → released(1) → planned(2) → cancelled(3).
  // Unknown/missing statuses rank last (4). Stable: index tiebreak preserves
  // the API's relative order within each rank.
  const STATUS_RANK: Record<string, number> = {
    active: 0,
    released: 1,
    planned: 2,
    cancelled: 3,
  };
  const milestones = milestonesRaw
    .map((m, i) => ({ m, i }))
    .sort(
      (a, b) =>
        (STATUS_RANK[a.m.milestone_status] ?? 4) -
          (STATUS_RANK[b.m.milestone_status] ?? 4) ||
        a.i - b.i,
    )
    .map(({ m }) => m);

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
            Gantt
          </span>
          <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
            ({project.name})
          </span>
        </span>
        {/* CENTER zone — Wave A (#1): shared view switcher (Gantt active). Off-board: all four items navigate (no onSelect). */}
        <span className="shrink-0">
          <ViewSwitcher projectName={project.name} active="gantt" />
        </span>
        {/* RIGHT zone — placeholder to balance the flex-1 left zone so the center stays centered. */}
        <span className="flex-1" />
      </header>

      <div className="mx-auto w-full max-w-6xl">
        <GanttView
          projectId={project.id}
          projectName={project.name}
          milestones={milestones}
        />
      </div>
    </main>
  );
}
