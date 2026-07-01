// Settings — Kanban #955.C / #2375 (R5) / #2380 (R-merge) / #2716 (two-pane).
// Top-level surface for operator preferences, now ALSO project-aware AND
// restructured into a Windows-Settings-style two-pane layout (left category
// nav + right content pane that renders ONLY the active category).
//
// Server Component; mounts the client children (ThemePicker, IntegrationsPanel,
// PushNotificationsPanel, …) for the active section only.
//
// State model — #2716: the active category lives in the ?section= query param
// (deep-linkable, back/forward works, renders only the active section → lighter
// payload). Default section = Appearance. Pre-existing /settings and
// /settings?project=<x> links still resolve (section defaults to Appearance when
// absent).
//
// #2380 — per-project settings consolidated here. When ?project= is present the
// page resolves that project and the project-scoped categories (Project,
// Advanced) appear in the nav. Unknown/missing project → those categories are
// hidden and any project-scoped ?section= falls back to Appearance (never 500).
//
// The category list + active-section resolution come from a single source
// (@/lib/settingsCategories) so a future service-toggled category appends in one
// place without restructuring this page.

import Link from "next/link";

import {
  getProjectByName,
  listAllTasks,
  HttpError,
  type ProjectRead,
  type TaskRead,
} from "@/lib/api";
import {
  getSettingsCategories,
  resolveSettingsSection,
  sectionNeedsAudit,
  type SettingsSectionId,
} from "@/lib/settingsCategories";
import { AuditHistorySection } from "@/components/AuditHistorySection";
import { IntegrationsPanel } from "@/components/IntegrationsPanel";
import { ResourcesPanel } from "@/components/ResourcesPanel";
import { ProjectSettingsPanel } from "@/components/ProjectSettingsPanel";
import { ApprovalPoliciesEditor } from "@/components/ApprovalPoliciesEditor";
import { PushNotificationsPanel } from "@/components/PushNotificationsPanel";
import { SettingsNav } from "@/components/SettingsNav";
import { ThemePicker } from "@/components/ThemePicker";
import { GlassPicker } from "@/components/GlassPicker";
import { TourReplayButton } from "@/components/TourReplayButton";

type Props = {
  searchParams: Promise<{ project?: string; section?: string }>;
};

export const dynamic = "force-dynamic";

export default async function SettingsPage(props: Props) {
  const searchParams = await props.searchParams;

  // #2380 — resolve the project when ?project= is present. On 404 (or any
  // resolution failure that's a 404) the project is treated as absent: the
  // project-scoped categories are hidden and the page renders global-only
  // (never crash). Non-404 errors re-throw so genuine server errors surface.
  let project: ProjectRead | null = null;
  const projectName = searchParams?.project;
  if (projectName) {
    try {
      project = await getProjectByName(projectName);
    } catch (e) {
      if (!(e instanceof HttpError && e.status === 404)) throw e;
      project = null;
    }
  }

  const hasProject = project !== null;
  const categories = getSettingsCategories(hasProject);
  const section = resolveSettingsSection(searchParams?.section, hasProject);

  // #2716 — audit fetch is gated on the active section: only Advanced renders
  // AuditHistorySection, so every other section skips listAllTasks for a lighter
  // payload. The project object is still resolved above (the nav needs to know
  // project scope exists) — only the task fetch is deferred.
  let auditTasks: TaskRead[] = [];
  if (project && sectionNeedsAudit(section)) {
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

      {/* Two-pane: left category nav + right content pane (active section only). */}
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 sm:flex-row sm:gap-8">
        <SettingsNav
          categories={categories}
          active={section}
          projectName={project?.name}
        />

        <div className="min-w-0 flex-1">
          <SectionContent
            section={section}
            project={project}
            auditTasks={auditTasks}
          />
        </div>
      </div>
    </main>
  );
}

// Right pane — renders ONLY the active category's content. The resolver
// guarantees `section` is a visible category for the current scope, so a
// project-scoped branch always has a non-null project here; the explicit guard
// is a belt-and-braces fallback to Appearance (never an empty pane / crash).
function SectionContent({
  section,
  project,
  auditTasks,
}: {
  section: SettingsSectionId;
  project: ProjectRead | null;
  auditTasks: TaskRead[];
}) {
  switch (section) {
    case "notifications":
      // Push notifications — original #955.C panel.
      return <PushNotificationsPanel />;

    case "integrations":
      // Integrations — #2375 R5: relocated from PlatformSettingsModal.
      return <IntegrationsPanel />;

    case "tour":
      return (
        <section
          data-settings-tour
          aria-labelledby="settings-tour-heading"
          className="flex flex-col gap-3"
        >
          <header className="flex flex-col gap-1">
            <div className="flex items-start justify-between gap-2">
              <h2
                id="settings-tour-heading"
                className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
              >
                Tour &amp; help
              </h2>
              {/* How-to link — relocated from the page corner into this category. */}
              <Link
                href="/help"
                className="text-[12px] text-zinc-400 hover:text-zinc-700 dark:text-zinc-500 dark:hover:text-zinc-300"
              >
                How to ↗
              </Link>
            </div>
            <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
              Walk through the key features of agent-teams again.
            </p>
          </header>
          <TourReplayButton />
        </section>
      );

    case "project":
      if (!project) break;
      return (
        <section
          data-settings-project
          aria-labelledby="settings-project-heading"
          className="flex flex-col gap-3"
        >
          <header className="flex flex-col gap-1">
            <h2
              id="settings-project-heading"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              This project · <span className="font-mono">{project.name}</span>
            </h2>
            <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
              Settings that apply only to this project.
            </p>
          </header>
          {/* #1018 M1 — key={project.id} forces a full remount on a
              project switch. Without it, App Router reuses the same
              instance ("same position, new props") across a client-side
              ?project= change, which leaked AgentOverridesPanel's local
              rowState (stale optimistic edits could mis-write to the new
              project) and ProjectSettingsPanel's own nudgeRaw/effortValue
              useState (stale seed from the prior project's initial value). */}
          <ProjectSettingsPanel key={project.id} project={project} hideApprovalPolicies />
          {/* #2358 — ResourcesPanel moved here from Board.tsx. */}
          <ResourcesPanel projectId={project.id} />
        </section>
      );

    case "advanced":
      if (!project) break;
      return (
        // #2716 — Advanced is now its own left-nav category (no nested collapse;
        // the former AdvancedSettingsDisclosure is retired).
        <section
          data-settings-advanced
          aria-labelledby="settings-advanced-heading"
          className="flex flex-col gap-8"
        >
          <h2
            id="settings-advanced-heading"
            className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
          >
            Advanced
          </h2>
          <ApprovalPoliciesEditor project={project} />
          <AuditHistorySection auditTasks={auditTasks} />
        </section>
      );
  }

  // Default — Appearance (#2375 R5: ThemePicker relocated into this labelled
  // section; #2453: GlassPicker surface axis). Also the fallback for an unknown
  // / project-scoped-without-project section.
  return (
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
          Appearance
        </h2>
        <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
          Light, dark, or follow the system preference. Applies to this browser.
        </p>
      </header>
      <div className="glass-surface flex flex-col gap-3 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] text-zinc-700 dark:text-zinc-300">
            Mode
          </span>
          <ThemePicker />
        </div>
        {/* #2453 — glass surface axis (orthogonal to light/dark). Flat = current
            theme; glass = frosted cards on a soft blob backdrop. */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 pt-3 dark:border-zinc-800">
          <span className="text-[13px] text-zinc-700 dark:text-zinc-300">
            Surface
          </span>
          <GlassPicker />
        </div>
      </div>
    </section>
  );
}
