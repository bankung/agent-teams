// Per-project settings page — Kanban #1349 (2026-05-20).
//
// Houses project-scoped operator preferences that DON'T belong in the
// global /settings surface (which is cross-project). v1 surfaces only the
// HITL nudge threshold (`hitl_nudge_threshold_hours`); future per-project
// surfaces (push subscription per-project filter, recurrence default tz,
// etc.) can slot in as sibling sections.
//
// Server Component — resolves the project by name (mirrors
// /p/[name]/page.tsx 404 + throw semantics), then mounts the client panel.

import Link from "next/link";
import { notFound } from "next/navigation";

import { getProjectByName, HttpError } from "@/lib/api";
import { ProjectSettingsPanel } from "@/components/ProjectSettingsPanel";
import { ThemePicker } from "@/components/ThemePicker";

type Props = { params: { name: string } };

export const dynamic = "force-dynamic";

export default async function ProjectSettingsPage({ params }: Props) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }
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
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">·</span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Project settings
        </span>
        <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono">
          ({project.name})
        </span>
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <ThemePicker />
        </span>
      </header>

      <div className="mx-auto w-full max-w-2xl">
        <ProjectSettingsPanel project={project} />
      </div>
    </main>
  );
}
