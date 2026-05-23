import Link from "next/link";

import {
  getAuditDailyRollup,
  getCrossProjectActiveTasks,
  getProjectsStats,
  listAuditFlags,
  listProjects,
  type DashboardActiveTasks,
  type ProjectRead,
  type ProjectStatsEntry,
} from "@/lib/api";
import { formatRelative } from "@/lib/time";
import { AuditorActivityPanel } from "@/components/AuditorActivityPanel";
import { AuditorVisibilityToggle } from "@/components/AuditorVisibilityToggle";
import { CrossProjectActiveTasksList } from "@/components/CrossProjectActiveTasksList";
import { DashboardWelcomeBanner } from "@/components/DashboardWelcomeBanner";
import { BudgetBar, pickBudgetDisplay } from "@/components/BudgetBar";
import { CostSummary } from "@/components/CostSummary";
import { DashboardRefresher } from "@/components/DashboardRefresher";
import { EditProjectModal } from "@/components/EditProjectModal";
import { FlagBellBadge } from "@/components/FlagBellBadge";
import { InboxBadge } from "@/components/InboxBadge";
import { NewProjectModal } from "@/components/NewProjectModal";
import { PnlDashboardSection } from "@/components/PnlDashboardSection";
import { ReviewSummaryWidget } from "@/components/ReviewSummaryWidget";
import { FINANCE_PANELS_ENABLED } from "@/lib/featureFlags";
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
              className="flex flex-col items-start gap-1 rounded-md border border-zinc-100 bg-zinc-50/60 px-2 py-2 sm:px-3 sm:py-3 dark:border-zinc-800 dark:bg-zinc-950/40"
              title={`${label}: ${count}`}
            >
              {/* #954 — shrink big numbers on mobile so 5-up grid fits a 375px viewport */}
              <span
                className={`text-2xl font-semibold tabular-nums leading-none sm:text-3xl ${laneColor(key, count)}`}
              >
                {count}
              </span>
              <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400 sm:text-[11px]">
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

// CompactProjectCard — SECONDARY view. Same data as the old card but visually
// subordinate: tighter padding, smaller fonts, denser grid (3-col at lg).
// Run-mode chips intentionally dropped (every project is manual-only today;
// noise without auto_* signal — see #869 brief). Project name remains a link
// to /p/<name>.
//
// `project` is the matching ProjectRead row from /api/projects merged by id
// (Kanban #951 AC #5). May be undefined when the stats endpoint and the
// project list disagree (e.g. race between fetches) — we degrade gracefully
// by hiding the budget bar.
function CompactProjectCard({
  entry,
  project,
}: {
  entry: ProjectStatsEntry;
  project: ProjectRead | undefined;
}) {
  const total = LANES.reduce((sum, { key }) => sum + entry.counts[key], 0);
  // Kanban #871 — per-card cost strip. Empty state when no session_runs:
  // muted em-dash + "no usage" (avoid rendering $0.00 · 0 tokens which implies
  // action where none has occurred).
  const cu = entry.cost_usage;
  const hasUsage = cu.session_run_count > 0;
  const cost = parseUsd(cu.total_cost_usd);
  const hasBudgetWarning = cu.budget_warning_count > 0;

  // Kanban #951 AC #5 — pick the most-constraining cap from the project's
  // 3 nullable budget columns. Returns null when all three are null, in which
  // case the budget bar is omitted entirely (legacy projects with no budget
  // configured). Until the BE migration lands, `project` will lack the budget
  // fields entirely → pickBudgetDisplay returns null and nothing renders.
  const budgetDisplay = project ? pickBudgetDisplay(project) : null;
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
        <div className="flex shrink-0 items-center gap-1.5">
          <span
            className="shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400"
            title={entry.last_activity_at ?? undefined}
          >
            {formatRelative(entry.last_activity_at)}
          </span>
          {/* #943 — gear-icon edit trigger; renders only when the stats row
              has a matching ProjectRead (race-safe per #951 AC #5 merge). */}
          {project ? <EditProjectModal project={project} /> : null}
        </div>
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

      {/* Budget bar (Kanban #951 AC #5). Sibling to the cost strip; rendered
          only when at least one of budget_daily_usd / budget_monthly_usd /
          budget_total_usd is non-null. Spend = lifetime project cost from
          cost_usage.total_cost_usd. Cap precedence: total > monthly > daily. */}
      {budgetDisplay ? (
        <BudgetBar
          spendUsd={cost}
          capUsd={budgetDisplay.capUsd}
          period={budgetDisplay.period}
        />
      ) : null}
    </article>
  );
}

export default async function DashboardPage() {
  // Kanban #951 AC #5 — parallel fetch: stats endpoint (lane counts +
  // cost_usage; primary data source for the cards) AND projects list (carries
  // the 3 budget cap columns added by the BE spawn). Merged by id below.
  // Promise.all keeps the request-time wall the same as the prior single-call
  // version (both calls hit the same FastAPI service over localhost; the LAN
  // round-trip dominates each).
  // #1082 — fetch the auditor daily rollup in parallel with the stats + projects
  // calls. BE defaults the window to today-7..today inclusive (UTC); we omit
  // the query params and let the server decide. Empty array is the typical
  // state today; AuditorActivity hides the entire section when so.
  // Kanban #1212 GOV4 (D5) — listAuditFlags joins listProjects + per-project
  // tasks; runs in parallel with the existing aggregate fetches. Failure
  // degrades to [] (the helper swallows per-project errors), so a single
  // backend hiccup doesn't blank the dashboard.
  // Kanban #945 — cross-project active-tasks list. Server-component fetch
  // alongside the other dashboard aggregates so SSE-driven `router.refresh()`
  // (DashboardRefresher) refreshes this section automatically when any task
  // row changes. Failure degrades to a zero-row placeholder so a single API
  // hiccup doesn't blank the rest of the dashboard.
  const [stats, projects, auditRollup, openFlags, activeTasks] = await Promise.all([
    getProjectsStats(),
    listProjects({ status: 1 }),
    getAuditDailyRollup(),
    listAuditFlags().catch(() => []),
    getCrossProjectActiveTasks().catch(
      (): DashboardActiveTasks => ({ rows: [], total_count: 0 }),
    ),
  ]);
  const projectsById = new Map<number, ProjectRead>();
  for (const p of projects) projectsById.set(p.id, p);

  return (
    <main className="flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      {/* T3 (#1362) — welcome banner; self-controls visibility (client-side
          localStorage flag + own-project check). Pass the already-fetched
          project list so the banner can detect whether the user has any own
          projects without a separate fetch. */}
      <DashboardWelcomeBanner projects={projects} />

      {/* #954 — header wraps on mobile so the right-aligned controls drop to a second row */}
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Dashboard
        </span>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-zinc-500 dark:text-zinc-400 tabular-nums">
          {stats.length} project{stats.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <DashboardRefresher />
          <AuditorVisibilityToggle />
          <NewProjectModal />
          <InboxBadge />
          <FlagBellBadge />
          <ThemePicker />
        </span>
      </header>

      {stats.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No active projects.
        </p>
      ) : (
        <>
          {/* GOV4 — operator review surface. Hidden when no flags are open. */}
          <ReviewSummaryWidget flags={openFlags} />

          <AggregateSummary stats={stats} />

          {/* Cost/token strip (Kanban #871). Sibling section BETWEEN the
              lifecycle aggregate and the per-project grid — separate concern
              (usage) from the lifecycle counts above and the navigation
              index below. */}
          <CostSummary
            stats={stats}
            ariaLabel="Portfolio-wide token and cost usage"
          />

          {/* Kanban #1329 (M6 FE) — cross-project P&L rollup. Operator-level
              view of revenue / expenses / net per project in the chosen
              window. Sits BELOW CostSummary (cost side first, P&L side after)
              and ABOVE the per-project navigation grid.
              Gated by NEXT_PUBLIC_FINANCE_PANELS_ENABLED (Kanban #1392). */}
          {FINANCE_PANELS_ENABLED && (
            <PnlDashboardSection
              defaultCollapsed={false}
              storageKey="dashboard.panels.pnl.expanded"
            />
          )}

          {/* Kanban #945 — cross-project active-tasks list. Operator-level
              view of tasks in {in-progress, review, blocked} across every
              active project. Refreshes via DashboardRefresher's SSE-driven
              router.refresh() (server-component fetch above). Sits BELOW
              PnlDashboardSection and ABOVE the per-project nav grid. */}
          <CrossProjectActiveTasksList
            data={activeTasks}
            defaultCollapsed={false}
            storageKey="dashboard.panels.active-tasks.expanded"
          />

          {/* Auditor activity (Kanban #1082 + #1291). Cross-project 7-day verdict
              rollup; hidden entirely when the API returns [] OR when the user
              toggles the panel off (AuditorVisibilityToggle in the header).
              Client visibility managed by AuditorActivityPanel (localStorage). */}
          <AuditorActivityPanel
            rollup={auditRollup}
            defaultCollapsed={false}
            storageKey="dashboard.panels.auditor.expanded"
          />

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
                <CompactProjectCard
                  key={entry.id}
                  entry={entry}
                  project={projectsById.get(entry.id)}
                />
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
