// Per-project Artifacts page — Kanban #2558 (FE phase; BE aggregate listing
// landed first).
//
// Lists every task-result file across the whole project (cross-task
// aggregate), newest-first. Lives under /p/[name]/ alongside /calendar +
// /gantt — resolves the active project by name exactly like those pages
// (Server Component; 404 → notFound, else re-throw into app/error.tsx).
//
// Data: the client view (ArtifactsPanel) owns the fetch (GET
// /api/projects/{id}/outputs) + "Load more" pagination — no SSR data-fetch
// here, mirroring how ResourcesPanel self-fetches client-side rather than
// receiving an SSR-prefetched page (this page has no other data need that
// would justify a parallel SSR fan-out like calendar/gantt's tasks+milestones).

import Link from "next/link";
import { notFound } from "next/navigation";

import { getProjectByName, HttpError } from "@/lib/api";
import { ArtifactsPanel } from "@/components/ArtifactsPanel";
import { ViewSwitcher } from "@/components/ViewSwitcher";

type Props = {
  params: Promise<{ name: string }>;
};

export const dynamic = "force-dynamic";

export default async function ProjectArtifactsPage(props: Props) {
  const params = await props.params;
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }

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
            Artifacts
          </span>
          <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
            ({project.name})
          </span>
        </span>
        {/* CENTER zone — shared view switcher (Artifacts active). Off-board: all items navigate (no onSelect). */}
        <span className="shrink-0">
          <ViewSwitcher projectName={project.name} active="artifacts" />
        </span>
        {/* RIGHT zone — placeholder to balance the flex-1 left zone so the center stays centered. */}
        <span className="flex-1" />
      </header>

      <div className="mx-auto w-full max-w-5xl">
        <ArtifactsPanel projectId={project.id} projectName={project.name} />
      </div>
    </main>
  );
}
