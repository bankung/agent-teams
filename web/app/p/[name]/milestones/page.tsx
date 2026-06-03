// Per-project Milestones page — Kanban #1868 (2026-06-03), Phase 2 (FE).
//
// Milestones are X-Project-Id scoped (one project owns its milestones), so the
// page lives under /p/[name]/ alongside /p/[name]/settings — it resolves the
// active project by name the same way (Server Component; 404 → notFound, else
// re-throw into app/error.tsx). A top-level /milestones route has no project
// context (no project selector at that level), so the per-project segment is
// the only correct home for an X-Project-Id-scoped surface.
//
// Data: list the project's milestones, then fetch each milestone's rollup in
// parallel (the list endpoint returns rows WITHOUT the rollup; the detail
// endpoint carries it). Milestone counts per project are small, so the N+1
// parallel fan-out is acceptable for v1. The client view (MilestonesView)
// owns the New/Edit/Delete flows and the lazy per-milestone task expand.

import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getMilestone,
  getProjectByName,
  listMilestones,
  HttpError,
  type MilestoneDetail,
} from "@/lib/api";
import { MilestonesView } from "@/components/MilestonesView";
import { ThemePicker } from "@/components/ThemePicker";

type Props = { params: { name: string } };

export const dynamic = "force-dynamic";

export default async function ProjectMilestonesPage({ params }: Props) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }

  // List rows (no rollup), then fetch each rollup in parallel. A single
  // milestone's detail failing degrades that one card to zeroed rollup rather
  // than blanking the whole page.
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
          Milestones
        </span>
        <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
          ({project.name})
        </span>
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <ThemePicker />
        </span>
      </header>

      <div className="mx-auto w-full max-w-3xl">
        <MilestonesView
          projectId={project.id}
          projectName={project.name}
          milestones={milestones}
        />
      </div>
    </main>
  );
}
