import Link from "next/link";

import { getProjectsStats, type ProjectStatsEntry } from "@/lib/api";
import { NewProjectModal } from "@/components/NewProjectModal";
import { ThemePicker } from "@/components/ThemePicker";

// Cross-project dashboard — aggregate-first layout (Kanban #869, 2026-05-13).
// Server Component; fetches batched stats at request time via getProjectsStats.
// Layout (top → bottom):
//   1. data-aggregate-summary  — lifecycle lane numbers + stat strip (#869)
//   2. data-cost-summary       — portfolio-wide token/cost usage (#871)
//   3. data-project-grid       — compact per-project navigation cards (#869)
// Backend ordering (projects.created_at ASC) preserved for the per-project
// grid. Per-card cost strip (data-cost-strip) lives inside each card BELOW
// the lane grid.

export const dynamic = "force-dynamic";

// Canonical lane labels — match the COLUMNS array in components/Board.tsx so
// the dashboard reads with the same vocabulary as the per-project board.
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
  // Clamp to >=0 — guards against server `MAX(updated_at)` being slightly in
  // the future relative to the browser clock (NTP / clock-skew). Without this,
  // a positive skew renders "-Nm ago"; with it, future timestamps fall through
  // to the `m < 1` branch → "just now". (Kanban #873, follow-up to #869.)
  const diffMs = Math.max(0, Date.now() - then);
  const m = Math.floor(diffMs / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 14) return `${d}d ago`;
  return iso.slice(0, 10);
}

// Token / cost formatters — Kanban #871. `cost_usage.total_cost_usd` ships as
// a JSON STRING from the backend (Pydantic Decimal serialization); parse before
// arithmetic. `formatTokens` collapses big counts to k/M for the per-card cell
// (the aggregate uses raw tabular-nums).
function parseUsd(raw: string): number {
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

function formatUsd(n: number): string {
  // Display precision (2dp). Storage precision (4dp) lives in the JSON string.
  return `$${n.toFixed(2)}`;
}

function formatInt(n: number): string {
  // Locale grouping for the aggregate big-number rows; matches the lane row's
  // tabular-nums readability.
  return n.toLocaleString("en-US");
}

function formatTokens(n: number): string {
  // Compact form for per-card cells (≥1k → "1.2k", ≥1M → "1.2M"). Aggregate
  // uses formatInt() for full precision.
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

// Lane accent — shared between aggregate big-numbers row and per-card mini
// cells. Zero counts dim; non-zero get the canonical status hue.
function laneColor(key: "1" | "2" | "3" | "4" | "5", count: number): string {
  if (count === 0) {
    return "text-zinc-400 dark:text-zinc-600";
  }
  switch (key) {
    case "1":
      return "text-zinc-900 dark:text-zinc-100";
    case "2":
      return "text-amber-700 dark:text-amber-300";
    case "3":
      return "text-violet-700 dark:text-violet-300";
    case "4":
      return "text-red-700 dark:text-red-300";
    case "5":
      return "text-emerald-700 dark:text-emerald-300";
  }
}

// AggregateSummary — PRIMARY view. Sums each lane across all entries and
// renders a row of 5 oversized numbers + a stat strip with project count,
// total tasks, and most-recent activity.
function AggregateSummary({ stats }: { stats: ProjectStatsEntry[] }) {
  // Per-lane totals across all active projects.
  const laneTotals: Record<"1" | "2" | "3" | "4" | "5", number> = {
    "1": 0,
    "2": 0,
    "3": 0,
    "4": 0,
    "5": 0,
  };
  for (const entry of stats) {
    for (const { key } of LANES) {
      laneTotals[key] += entry.counts[key];
    }
  }
  const totalTasks =
    laneTotals["1"] + laneTotals["2"] + laneTotals["3"] + laneTotals["4"] + laneTotals["5"];

  // Most-recent activity across all projects — max of last_activity_at over
  // non-null entries. Falls back to null when no project has activity yet.
  let mostRecent: string | null = null;
  for (const entry of stats) {
    if (entry.last_activity_at === null) continue;
    if (mostRecent === null || entry.last_activity_at > mostRecent) {
      mostRecent = entry.last_activity_at;
    }
  }

  return (
    <section
      data-aggregate-summary
      aria-label="Aggregate summary across all active projects"
      className="mb-5 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Across all projects
      </h2>

      <div
        className="grid grid-cols-5 gap-3"
        role="list"
        aria-label="Total tasks per status across all projects"
      >
        {LANES.map(({ key, label }) => {
          const count = laneTotals[key];
          return (
            <div
              key={key}
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-zinc-100 bg-zinc-50/60 px-3 py-3 dark:border-zinc-800 dark:bg-zinc-950/40"
              title={`${label}: ${count}`}
            >
              <span
                className={`text-3xl font-semibold tabular-nums leading-none ${laneColor(key, count)}`}
              >
                {count}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {label}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span>
          <span className="font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">
            {totalTasks}
          </span>{" "}
          task{totalTasks === 1 ? "" : "s"} total
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-700">
          ·
        </span>
        <span>
          <span className="font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">
            {stats.length}
          </span>{" "}
          active project{stats.length === 1 ? "" : "s"}
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-700">
          ·
        </span>
        <span title={mostRecent ?? undefined}>
          last activity:{" "}
          <span className="font-medium text-zinc-700 dark:text-zinc-300">
            {formatRelative(mostRecent)}
          </span>
        </span>
      </div>
    </section>
  );
}

// CostSummary — Kanban #871. Portfolio-wide token + cost roll-up. Slots
// BETWEEN the lifecycle aggregate (above) and the per-project grid (below).
// Visual weight matches AggregateSummary but uses a subtle amber/zinc tint
// shift + a different header label ("Usage") so the two read as separate
// concerns instead of one merged strip.
//
// Empty-state: when EVERY project has session_run_count === 0, render a quiet
// "no usage tracked yet" line instead of zeros (zeros imply work where none
// has occurred).
function CostSummary({ stats }: { stats: ProjectStatsEntry[] }) {
  let totalCost = 0;
  let totalInput = 0;
  let totalOutput = 0;
  let totalRuns = 0;
  let totalWarnings = 0;
  for (const entry of stats) {
    totalCost += parseUsd(entry.cost_usage.total_cost_usd);
    totalInput += entry.cost_usage.total_input_tokens;
    totalOutput += entry.cost_usage.total_output_tokens;
    totalRuns += entry.cost_usage.session_run_count;
    totalWarnings += entry.cost_usage.budget_warning_count;
  }

  const noUsage = totalRuns === 0;

  return (
    <section
      data-cost-summary
      aria-label="Portfolio-wide token and cost usage"
      className="mb-5 rounded-lg border border-amber-200/60 bg-amber-50/40 p-5 dark:border-amber-900/40 dark:bg-amber-950/10"
    >
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Usage
        </h2>
        {totalWarnings > 0 ? (
          <span
            className="inline-flex items-center rounded bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
            title={`${totalWarnings} session run${totalWarnings === 1 ? "" : "s"} flagged budget-warned`}
          >
            ⚠ {totalWarnings} run{totalWarnings === 1 ? "" : "s"} budget-warned
          </span>
        ) : null}
      </div>

      {noUsage ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No usage tracked yet.
        </p>
      ) : (
        <>
          <div
            className="grid grid-cols-3 gap-3"
            role="list"
            aria-label="Portfolio-wide cost and token totals"
          >
            <div
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-amber-100 bg-white/70 px-3 py-3 dark:border-amber-900/30 dark:bg-zinc-950/40"
              title={`$${totalCost.toFixed(4)} USD across all projects`}
            >
              <span className="text-3xl font-semibold tabular-nums leading-none text-amber-700 dark:text-amber-300">
                {formatUsd(totalCost)}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Total cost
              </span>
            </div>
            <div
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-amber-100 bg-white/70 px-3 py-3 dark:border-amber-900/30 dark:bg-zinc-950/40"
              title={`${totalInput.toLocaleString("en-US")} input tokens`}
            >
              <span className="text-3xl font-semibold tabular-nums leading-none text-zinc-900 dark:text-zinc-100">
                {formatInt(totalInput)}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Input tokens
              </span>
            </div>
            <div
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-amber-100 bg-white/70 px-3 py-3 dark:border-amber-900/30 dark:bg-zinc-950/40"
              title={`${totalOutput.toLocaleString("en-US")} output tokens`}
            >
              <span className="text-3xl font-semibold tabular-nums leading-none text-zinc-900 dark:text-zinc-100">
                {formatInt(totalOutput)}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Output tokens
              </span>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
            <span>
              <span className="font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">
                {totalRuns}
              </span>{" "}
              session run{totalRuns === 1 ? "" : "s"} tracked
            </span>
          </div>
        </>
      )}
    </section>
  );
}

// CompactProjectCard — SECONDARY view. Same data as the old card but visually
// subordinate: tighter padding, smaller fonts, denser grid (3-col at lg).
// Run-mode chips intentionally dropped (every project is manual-only today;
// noise without auto_* signal — see #869 brief). Project name remains a link
// to /p/<name>.
function CompactProjectCard({ entry }: { entry: ProjectStatsEntry }) {
  const total = LANES.reduce((sum, { key }) => sum + entry.counts[key], 0);
  // Kanban #871 — per-card cost strip. Empty state when no session_runs:
  // muted em-dash + "no usage" (avoid rendering $0.00 · 0 tokens which implies
  // action where none has occurred).
  const cu = entry.cost_usage;
  const hasUsage = cu.session_run_count > 0;
  const cost = parseUsd(cu.total_cost_usd);
  const hasBudgetWarning = cu.budget_warning_count > 0;
  return (
    <article
      data-project-card
      data-project-name={entry.name}
      className="flex flex-col gap-2 rounded-md border border-zinc-200 bg-white p-3 transition-colors hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <Link
            href={`/p/${entry.name}`}
            className="truncate text-sm font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
          >
            {entry.name}
          </Link>
          <div className="flex items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
            <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
              {entry.team}
            </span>
            <span className="tabular-nums">
              {total} task{total === 1 ? "" : "s"}
            </span>
          </div>
        </div>
        <span
          className="shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400"
          title={entry.last_activity_at ?? undefined}
        >
          {formatRelative(entry.last_activity_at)}
        </span>
      </header>

      <div
        className="grid grid-cols-5 gap-1"
        role="list"
        aria-label={`Status counts for ${entry.name}`}
      >
        {LANES.map(({ key, label }) => {
          const count = entry.counts[key];
          return (
            <div
              key={key}
              role="listitem"
              className="flex flex-col items-center gap-0.5 rounded border border-zinc-100 bg-zinc-50/40 px-1 py-1 dark:border-zinc-800 dark:bg-zinc-900/40"
              title={`${label}: ${count}`}
            >
              <span
                className={`text-sm font-semibold tabular-nums ${laneColor(key, count)}`}
              >
                {count}
              </span>
              <span className="text-[9px] uppercase tracking-wide text-zinc-500 dark:text-zinc-500">
                {label}
              </span>
            </div>
          );
        })}
      </div>

      <div
        data-cost-strip
        className="flex items-center gap-1.5 text-[11px] tabular-nums"
        aria-label={`Cost and token usage for ${entry.name}`}
      >
        {hasUsage ? (
          <>
            <span
              className={
                hasBudgetWarning
                  ? "font-semibold text-amber-700 dark:text-amber-300"
                  : "font-semibold text-zinc-700 dark:text-zinc-300"
              }
              title={`$${cost.toFixed(4)} USD across ${cu.session_run_count} session run${cu.session_run_count === 1 ? "" : "s"}`}
            >
              {formatUsd(cost)}
            </span>
            <span aria-hidden className="text-zinc-300 dark:text-zinc-700">
              ·
            </span>
            <span
              className="text-zinc-500 dark:text-zinc-400"
              title={`${cu.total_input_tokens.toLocaleString("en-US")} in / ${cu.total_output_tokens.toLocaleString("en-US")} out`}
            >
              {formatTokens(cu.total_input_tokens)} in /{" "}
              {formatTokens(cu.total_output_tokens)} out
            </span>
            {hasBudgetWarning ? (
              <span
                className="ml-auto inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                title={`${cu.budget_warning_count} run${cu.budget_warning_count === 1 ? "" : "s"} flagged budget-warned`}
              >
                ⚠ {cu.budget_warning_count}
              </span>
            ) : null}
          </>
        ) : (
          <span className="text-zinc-400 dark:text-zinc-600" title="No session runs recorded yet">
            — no usage
          </span>
        )}
      </div>
    </article>
  );
}

export default async function DashboardPage() {
  const stats = await getProjectsStats();

  return (
    <main className="flex min-h-screen flex-col overflow-y-auto bg-white px-6 py-5 dark:bg-zinc-950">
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
        <span className="ml-auto flex items-center gap-2">
          <NewProjectModal />
          <ThemePicker />
        </span>
      </header>

      {stats.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No active projects.
        </p>
      ) : (
        <>
          <AggregateSummary stats={stats} />

          {/* Cost/token strip (Kanban #871). Sibling section BETWEEN the
              lifecycle aggregate and the per-project grid — separate concern
              (usage) from the lifecycle counts above and the navigation
              index below. */}
          <CostSummary stats={stats} />

          {/* Per-project compact grid (SECONDARY). The aggregate section above
              is the primary view; cards here are a navigation index into the
              per-project boards. Denser grid (up to 4 cols at xl) keeps cards
              from dominating the first viewport. */}
          <section
            aria-label="Per-project breakdown"
            data-project-grid
            className="flex flex-col gap-2"
          >
            <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Projects
            </h2>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {stats.map((entry) => (
                <CompactProjectCard key={entry.id} entry={entry} />
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
