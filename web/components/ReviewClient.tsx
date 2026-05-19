"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  resolveFlag,
  type AuditFlagWithProject,
  type ProjectRead,
  type TaskRead,
} from "@/lib/api";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";
import { ConnectionStateBadge } from "./ConnectionStateBadge";
import { MassActionBar } from "./MassActionBar";
import { ProjectFlagCard } from "./ProjectFlagCard";
import { ThemePicker } from "./ThemePicker";
import {
  TerminateFlagModal,
  type TerminateTarget,
} from "./TerminateFlagModal";

// Kanban #1212 AA4 — interactive shell for the /review page.
//
// Owns:
//   - Per-flag selection state (Set<flag_id>).
//   - Per-project collapsed/expanded section state.
//   - The shared TerminateFlagModal (single + mass modes route through one
//     instance — page-level ownership avoids two competing modal stacks).
//   - The post-resolve refresh (router.refresh() to re-fetch the SSR data).
//
// SSE wiring uses the existing useRowChangedEvents hook with no projectId
// (wildcard subscription) so resolves from other operators / other surfaces
// also revalidate this page.

type Props = {
  initialFlags: AuditFlagWithProject[];
};

type ProjectGroup = {
  project: ProjectRead;
  flags: TaskRead[];
};

// Group flags by project_id while preserving the project metadata. Insertion
// order matches the source iteration (listProjects sorted by id ASC), so
// projects render in a stable order across reloads.
function groupByProject(flags: AuditFlagWithProject[]): ProjectGroup[] {
  const groups = new Map<number, ProjectGroup>();
  for (const { flag, project } of flags) {
    const existing = groups.get(project.id);
    if (existing) {
      existing.flags.push(flag);
    } else {
      groups.set(project.id, { project, flags: [flag] });
    }
  }
  // Sort flags within a group by task id ASC for stable card order.
  for (const g of groups.values()) {
    g.flags.sort((a, b) => a.id - b.id);
  }
  return Array.from(groups.values());
}

export function ReviewClient({ initialFlags }: Props) {
  const router = useRouter();

  // SSR data is the source of truth. router.refresh() re-runs the server
  // component + re-passes initialFlags. Keep `flags` as a ref to the
  // currently-mounted props so cross-render selection logic can read from
  // the latest list without prop-drilling state through every map.
  const flags = initialFlags;
  const groups = useMemo(() => groupByProject(flags), [flags]);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());

  // Terminate modal state — single OR mass. The page owns the modal so
  // either path renders the same component instance.
  const [terminateTargets, setTerminateTargets] = useState<
    TerminateTarget[] | null
  >(null);

  // Auto-revalidate on SSE events. Same pattern as DashboardRefresher.
  const onChange = useCallback(() => router.refresh(), [router]);
  const { connectionState, lastEventAt } = useRowChangedEvents({
    onTaskChange: onChange,
    onProjectChange: onChange,
  });

  const setSelected = (flagId: number, next: boolean) => {
    setSelectedIds((prev) => {
      const out = new Set(prev);
      if (next) out.add(flagId);
      else out.delete(flagId);
      return out;
    });
  };

  const selectAll = (next: boolean) => {
    if (next) {
      setSelectedIds(new Set(flags.map((f) => f.flag.id)));
    } else {
      setSelectedIds(new Set());
    }
  };

  const toggleCollapse = (projectId: number) => {
    setCollapsed((prev) => {
      const out = new Set(prev);
      if (out.has(projectId)) out.delete(projectId);
      else out.add(projectId);
      return out;
    });
  };

  const onResolved = useCallback(
    (flagId: number) => {
      // Drop from selection if it was there; refresh server state to drop
      // the card from the page (SSR re-fetch).
      setSelectedIds((prev) => {
        if (!prev.has(flagId)) return prev;
        const out = new Set(prev);
        out.delete(flagId);
        return out;
      });
      router.refresh();
    },
    [router],
  );

  // Mass continue / keep_paused — loop resolveFlag for each selected.
  const doMassConfirm = useCallback(
    async (action: "continue" | "keep_paused") => {
      const selected = flags.filter((f) => selectedIds.has(f.flag.id));
      // Sequential rather than parallel — the BE writes to projects_audit
      // per-call and the audit ordering is easier to read when the calls
      // serialize. The volume is small (<10s of flags typically).
      for (const { flag, project } of selected) {
        await resolveFlag(flag.id, project.id, { action });
      }
      setSelectedIds(new Set());
      router.refresh();
    },
    [flags, selectedIds, router],
  );

  // Mass terminate — open the shared modal with N targets.
  const requestMassTerminate = () => {
    const selected = flags.filter((f) => selectedIds.has(f.flag.id));
    if (selected.length === 0) return;
    setTerminateTargets(
      selected.map(({ flag, project }) => ({
        projectId: project.id,
        projectName: project.name,
        flagId: flag.id,
      })),
    );
  };

  // Single terminate — open the shared modal with 1 target.
  const requestSingleTerminate = (flag: TaskRead, project: ProjectRead) => {
    setTerminateTargets([
      {
        projectId: project.id,
        projectName: project.name,
        flagId: flag.id,
      },
    ]);
  };

  const onTerminateSubmit = useCallback(
    async (targets: TerminateTarget[], _reason: string) => {
      // The BE auto-formats the kill_project reason from the flag id +
      // actor; the user-typed reason is captured locally for now via the
      // (future) X-Actor / annotation channels. We still gate on it as a
      // muscle-memory brake.
      for (const t of targets) {
        await resolveFlag(t.flagId, t.projectId, { action: "terminate" });
      }
      setSelectedIds(new Set());
      setTerminateTargets(null);
      router.refresh();
    },
    [router],
  );

  return (
    <main className="flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Review
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span
          className="text-zinc-500 dark:text-zinc-400 tabular-nums"
          data-review-summary
        >
          {flags.length} flag{flags.length === 1 ? "" : "s"} across{" "}
          {groups.length} project{groups.length === 1 ? "" : "s"}
        </span>
        <Link
          href="/dashboard"
          className="ml-2 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
        >
          Dashboard
        </Link>
        <span className="ml-auto flex items-center gap-2">
          <ConnectionStateBadge
            state={connectionState}
            lastEventAt={lastEventAt}
          />
          <ThemePicker />
        </span>
      </header>

      {flags.length === 0 ? (
        <div
          className="flex flex-col items-center gap-2 rounded-md border border-zinc-200 bg-zinc-50 p-8 text-center dark:border-zinc-800 dark:bg-zinc-900"
          data-review-empty
        >
          <span aria-hidden className="text-3xl text-emerald-500">
            ✓
          </span>
          <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
            No flags — all projects continuing.
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            New AA3 audit flags will appear here when the auditor escalates.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <MassActionBar
            allFlags={flags}
            selectedFlagIds={selectedIds}
            onSelectAll={selectAll}
            onMassConfirm={doMassConfirm}
            onMassTerminateRequest={requestMassTerminate}
          />
          <div className="flex flex-col gap-4">
            {groups.map(({ project, flags: projectFlags }) => {
              const isCollapsed = collapsed.has(project.id);
              return (
                <section
                  key={project.id}
                  data-review-project-section
                  data-project-id={project.id}
                  data-project-name={project.name}
                  className="flex flex-col gap-2"
                >
                  <header className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => toggleCollapse(project.id)}
                      className="inline-flex items-center gap-2 text-sm font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
                      aria-expanded={!isCollapsed}
                      data-project-section-toggle
                    >
                      <span aria-hidden>{isCollapsed ? "▸" : "▾"}</span>
                      {project.name}
                    </button>
                    <span className="inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                      {projectFlags.length} flag
                      {projectFlags.length === 1 ? "" : "s"}
                    </span>
                  </header>
                  {!isCollapsed && (
                    <div className="flex flex-col gap-2">
                      {projectFlags.map((flag) => (
                        <ProjectFlagCard
                          key={flag.id}
                          flag={flag}
                          project={project}
                          selected={selectedIds.has(flag.id)}
                          onSelectChange={(next) =>
                            setSelected(flag.id, next)
                          }
                          onResolved={(_response) => onResolved(flag.id)}
                          onTerminateRequest={() =>
                            requestSingleTerminate(flag, project)
                          }
                        />
                      ))}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </div>
      )}

      <TerminateFlagModal
        open={terminateTargets !== null}
        targets={terminateTargets ?? []}
        onClose={() => setTerminateTargets(null)}
        onSubmit={onTerminateSubmit}
      />
    </main>
  );
}
