import Link from "next/link";

import { getProjectsStats, type ProjectStatsEntry } from "@/lib/api";
import { ThemePicker } from "@/components/ThemePicker";

// Cross-project dashboard (Kanban #769). Server Component — fetches batched
// stats at request time via getProjectsStats (no X-Project-Id; cross-project
// read). Ordering preserved from backend (projects.created_at ASC).
//
// Layout mirrors the per-project board shell: same bg, same header pattern
// (small text-sm row of meta + ThemePicker on ml-auto), so visual identity
// stays consistent when toggling between /dashboard and /p/<name>.

export const dynamic = "force-dynamic";

// Canonical lane labels — match the COLUMNS array in components/Board.tsx so
// the card grid reads with the same vocabulary as the per-project board.
const LANES: Array<{ key: "1" | "2" | "3" | "4" | "5"; label: string }> = [
  { key: "1", label: "New" },
  { key: "2", label: "In progress" },
  { key: "3", label: "Review" },
  { key: "4", label: "Blocked" },
  { key: "5", label: "Done" },
];

// Relative-time formatter — keeps the dashboard scannable. Falls back to an
// absolute YYYY-MM-DD slice for anything older than 14 days (matches the
// ListView "updated" column convention).
function formatRelative(iso: string | null): string {
  if (iso === null) return "no activity yet";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  const m = Math.floor(diffMs / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 14) return `${d}d ago`;
  return iso.slice(0, 10);
}

function laneColor(key: "1" | "2" | "3" | "4" | "5", count: number): string {
  // Zero-count cells: dim. Non-zero: subtle status accent so the eye lands on
  // the lanes that hold work (mirrors the Board lane headers' implicit hint
  // without re-introducing a full status palette here).
  if (count === 0) {
    return "text-zinc-400 dark:text-zinc-600";
  }
  switch (key) {
    case "1":
      return "text-zinc-900 dark:text-zinc-100"; // TODO — neutral, primary
    case "2":
      return "text-amber-700 dark:text-amber-300"; // IN_PROGRESS
    case "3":
      return "text-violet-700 dark:text-violet-300"; // REVIEW
    case "4":
      return "text-red-700 dark:text-red-300"; // BLOCKED
    case "5":
      return "text-emerald-700 dark:text-emerald-300"; // DONE
  }
}

function RunModeChips({
  breakdown,
}: {
  breakdown: ProjectStatsEntry["run_mode_breakdown"];
}) {
  // Compact "run mode" row. If only `manual` is non-zero (the common case
  // today), render a single neutral badge — three rows reads as noise. When
  // any auto_* mode is non-zero, surface all non-zero modes so the operator
  // sees the auto/manual split at a glance.
  const hasAuto =
    breakdown.auto_pickup > 0 || breakdown.auto_headless > 0;
  if (!hasAuto) {
    return (
      <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 tabular-nums">
        manual · {breakdown.manual}
      </span>
    );
  }
  return (
    <div className="flex flex-wrap gap-1">
      {breakdown.manual > 0 && (
        <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 tabular-nums">
          manual · {breakdown.manual}
        </span>
      )}
      {breakdown.auto_pickup > 0 && (
        <span className="inline-flex items-center rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 tabular-nums">
          auto pickup · {breakdown.auto_pickup}
        </span>
      )}
      {breakdown.auto_headless > 0 && (
        <span className="inline-flex items-center rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 tabular-nums">
          auto headless · {breakdown.auto_headless}
        </span>
      )}
    </div>
  );
}

function ProjectCard({ entry }: { entry: ProjectStatsEntry }) {
  const total = LANES.reduce((sum, { key }) => sum + entry.counts[key], 0);
  return (
    <article
      data-project-card
      data-project-name={entry.name}
      className="flex flex-col gap-3 rounded-lg border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1 min-w-0">
          <Link
            href={`/p/${entry.name}`}
            className="truncate text-base font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
          >
            {entry.name}
          </Link>
          <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
            <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
              {entry.team}
            </span>
            <span className="tabular-nums">{total} task{total === 1 ? "" : "s"}</span>
          </div>
        </div>
        <span
          className="shrink-0 text-xs text-zinc-500 dark:text-zinc-400"
          title={entry.last_activity_at ?? undefined}
        >
          {formatRelative(entry.last_activity_at)}
        </span>
      </header>

      <div
        className="grid grid-cols-5 gap-1.5"
        role="list"
        aria-label={`Status counts for ${entry.name}`}
      >
        {LANES.map(({ key, label }) => {
          const count = entry.counts[key];
          return (
            <div
              key={key}
              role="listitem"
              className="flex flex-col items-center gap-0.5 rounded border border-zinc-100 bg-zinc-50/40 px-1 py-1.5 dark:border-zinc-800 dark:bg-zinc-900/40"
              title={`${label}: ${count}`}
            >
              <span
                className={`text-base font-semibold tabular-nums ${laneColor(key, count)}`}
              >
                {count}
              </span>
              <span className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-500">
                {label}
              </span>
            </div>
          );
        })}
      </div>

      <RunModeChips breakdown={entry.run_mode_breakdown} />
    </article>
  );
}

export default async function DashboardPage() {
  const stats = await getProjectsStats();

  return (
    <main className="flex h-screen flex-col overflow-hidden bg-white dark:bg-zinc-950 px-6 py-5">
      <header className="mb-4 flex items-center gap-2 text-sm">
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Dashboard
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-zinc-500 dark:text-zinc-400 tabular-nums">
          {stats.length} project{stats.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto">
          <ThemePicker />
        </span>
      </header>

      {stats.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No active projects.
        </p>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto md:grid-cols-2 xl:grid-cols-3">
          {stats.map((entry) => (
            <ProjectCard key={entry.id} entry={entry} />
          ))}
        </div>
      )}
    </main>
  );
}
