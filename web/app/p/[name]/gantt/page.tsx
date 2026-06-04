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
import { ThemePicker } from "@/components/ThemePicker";
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
  const milestones: MilestoneDetail[] = await Promise.all(
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
          Gantt
        </span>
        <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
          ({project.name})
        </span>
        {/* Wave A (#1) — shared view switcher (Gantt active). Off-board: all
            four items navigate (no onSelect). */}
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <ViewSwitcher projectName={project.name} active="gantt" />
          <ThemePicker />
        </span>
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
