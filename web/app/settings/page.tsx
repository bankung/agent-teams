// Settings — Kanban #955.C / #2375 (R5) / #2380 (R-merge). Top-level surface
// for operator preferences, now ALSO project-aware.
//
// Server Component; mounts the client children (ThemePicker, IntegrationsPanel,
// PushNotificationsPanel).
//
// #2380 (R-merge) — per-project settings consolidated here. When `?project=`
// is present, the page resolves that project and renders a project-scoped block
// FIRST (ProjectSettingsPanel + AuditHistorySection), then the global sections
// below. Unknown/missing project → skip the project block (never 500). When the
// param is absent the page is global-only (original behavior).
//
// Layout mirrors the dashboard header pattern (compact header + main panel
// body). Body holds labelled <section>s: Theme (relocated out of the header —
// #2375 R5), Integrations (relocated from the former PlatformSettingsModal),
// and Push notifications.

import Link from "next/link";

import {
  getProjectByName,
  listAllTasks,
  HttpError,
  type ProjectRead,
  type TaskRead,
} from "@/lib/api";
import { AuditHistorySection } from "@/components/AuditHistorySection";
import { IntegrationsPanel } from "@/components/IntegrationsPanel";
import { ResourcesPanel } from "@/components/ResourcesPanel";
import { ProjectSettingsPanel } from "@/components/ProjectSettingsPanel";
import { PushNotificationsPanel } from "@/components/PushNotificationsPanel";
import { ThemePicker } from "@/components/ThemePicker";
import { GlassPicker } from "@/components/GlassPicker";
import { TourReplayButton } from "@/components/TourReplayButton";

type Props = { searchParams: { project?: string } };

export const dynamic = "force-dynamic";

export default async function SettingsPage({ searchParams }: Props) {
  // #2380 — project-scoped block when ?project= is present. On 404 (or any
  // resolution failure) the project section is skipped; the global sections
  // still render (never crash the whole page).
  let project: ProjectRead | null = null;
  let auditTasks: TaskRead[] = [];
  const projectName = searchParams?.project;
  if (projectName) {
    try {
      project = await getProjectByName(projectName);
      const allTasks = await listAllTasks(project.id);
      // Mirror the auditTasks sort from /p/[name]/settings (completed_at desc,
      // then id desc).
      auditTasks = [...allTasks.filter((t) => t.task_type === "audit")].sort(
        (a, b) => {
          const aDone = a.completed_at ?? "";
          const bDone = b.completed_at ?? "";
          if (aDone === bDone) return b.id - a.id;
          return aDone < bDone ? 1 : -1;
        },
      );
    } catch (e) {
      // 404 (unknown name) → skip the project section, render global only.
      // Re-throw anything else so genuine server errors aren't masked.
      if (!(e instanceof HttpError && e.status === 404)) throw e;
      project = null;
    }
  }

  return (
    <main className="glass-board flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href={project ? `/p/${encodeURIComponent(project.name)}` : "/dashboard"}
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← {project ? `${project.name} board` : "Dashboard"}
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Settings
        </span>
      </header>

      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
        {/* #2380 — project-scoped block (first), clearly labelled vs the global
            sections below. Rendered only when ?project= resolves. */}
        {project && (
          <section
            data-settings-project
            aria-labelledby="settings-project-heading"
            className="glass-surface flex flex-col gap-3 rounded-md border border-zinc-200 bg-zinc-50/60 p-4 dark:border-zinc-800 dark:bg-zinc-900/40"
          >
            <header className="flex flex-col gap-1">
              <h2
                id="settings-project-heading"
                className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
              >
                This project ·{" "}
                <span className="font-mono">{project.name}</span>
              </h2>
              <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
                Settings that apply only to this project. Global preferences are
                below.
              </p>
            </header>
            <ProjectSettingsPanel project={project} />
            <AuditHistorySection auditTasks={auditTasks} />
            {/* #2358 — ResourcesPanel moved here from Board.tsx. */}
            <ResourcesPanel projectId={project.id} />
          </section>
        )}

        {/* Theme — #2375 R5: ThemePicker relocated from every route header into
            this labelled body section. ThemeProvider/useTheme unchanged. */}
        <section
          data-settings-theme
          aria-labelledby="settings-theme-heading"
          className="flex flex-col gap-3"
        >
          <header className="flex flex-col gap-1">
            <h2
              id="settings-theme-heading"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              Theme
            </h2>
            <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
              Light, dark, or follow the system preference. Applies to this
              browser.
            </p>
          </header>
          <div className="glass-surface flex flex-col gap-3 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[13px] text-zinc-700 dark:text-zinc-300">
                Mode
              </span>
              <ThemePicker />
            </div>
            {/* #2453 — glass surface axis (orthogonal to light/dark). Flat =
                current theme; glass = frosted cards on a soft blob backdrop. */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 pt-3 dark:border-zinc-800">
              <span className="text-[13px] text-zinc-700 dark:text-zinc-300">
                Surface
              </span>
              <GlassPicker />
            </div>
          </div>
        </section>

        {/* Integrations — #2375 R5: relocated from PlatformSettingsModal. */}
        <IntegrationsPanel />

        {/* Push notifications — original #955.C panel. */}
        <PushNotificationsPanel />

        {/* Product tour — #2376 R7: replay from settings. */}
        <section
          data-settings-tour
          aria-labelledby="settings-tour-heading"
          className="flex flex-col gap-3"
        >
          <header className="flex flex-col gap-1">
            <h2
              id="settings-tour-heading"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              Product tour
            </h2>
            <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
              Walk through the key features of agent-teams again.
            </p>
          </header>
          <TourReplayButton />
        </section>
      </div>
    </main>
  );
}
