"use client";

// Shared Usage panel — portfolio-wide OR per-project token/cost roll-up.
//
// Render sites:
//   1. web/app/dashboard/page.tsx — portfolio view (stats = all active projects);
//      defaultCollapsed=false (expanded by default), no storageKey.
//   2. web/components/Board.tsx — per-project view (stats = 0 or 1 entry);
//      defaultCollapsed=true, storageKey="project.<id>.panels.usage.expanded".
//
// Collapse behaviour follows the same localStorage + same-tab StorageEvent
// pattern as AuditorVisibilityToggle (#1291). When defaultCollapsed=false
// (default), the panel is always-expanded (no toggle chrome).

import { type ProjectStatsEntry } from "@/lib/api";
import { usePersistentState } from "@/lib/usePersistentState";

// Token / cost formatters — duplicates kept intentional: this file is the
// canonical render home; dashboard/page.tsx private helpers remain for
// AggregateSummary + CompactProjectCard which are NOT extracted (out of scope).
function parseUsd(raw: string): number {
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

function formatUsd(n: number): string {
  return `$${n.toFixed(2)}`;
}

function formatInt(n: number): string {
  return n.toLocaleString("en-US");
}

// Chevron icons — expand / collapse affordance
function ChevronDownIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="4 6 8 10 12 6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="6 4 10 8 6 12" />
    </svg>
  );
}

type Props = {
  stats: ProjectStatsEntry[];
  // aria-label for the section. Dashboard uses "Portfolio-wide token and cost
  // usage"; project page passes "Usage for <name>".
  ariaLabel?: string;
  // When true, the panel starts collapsed; the user can expand it via a
  // chevron toggle. Default false = always-expanded (dashboard behaviour).
  defaultCollapsed?: boolean;
  // localStorage key for persisting per-project collapse state. Required when
  // defaultCollapsed=true so each project remembers its own preference.
  // Ignored when defaultCollapsed=false.
  storageKey?: string;
  // Wave A (#7) — layout override for the panel <section> wrapper. Defaults to
  // "mb-5" (the standalone / dashboard vertical-stack spacing). The board's
  // 3-up panels band passes "h-full" so all three panels stretch to equal
  // height inside the grid row (gap handled by the band's `gap-3`).
  className?: string;
};

export function CostSummary({
  stats,
  ariaLabel = "Portfolio-wide token and cost usage",
  defaultCollapsed = false,
  storageKey,
  className = "mb-5",
}: Props) {
  let totalCost = 0;
  let totalInput = 0;
  let totalOutput = 0;
  let totalRuns = 0;
  // #2735 — Mode A card now reads actual_interactive_cost (the real usage_events
  // hook-capture ledger), NOT the estimated_cost heuristic roll-up. estimated_cost
  // stays on the type — the P&L components still consume it.
  let totalInteractiveCost = 0;
  let totalInteractiveInput = 0;
  let totalInteractiveOutput = 0;
  let hasInteractive = false;
  for (const entry of stats) {
    totalCost += parseUsd(entry.cost_usage.total_cost_usd);
    totalInput += entry.cost_usage.total_input_tokens;
    totalOutput += entry.cost_usage.total_output_tokens;
    totalRuns += entry.cost_usage.session_run_count;
    if (entry.actual_interactive_cost != null) {
      hasInteractive = true;
      totalInteractiveCost += parseUsd(entry.actual_interactive_cost.total_cost_usd);
      totalInteractiveInput += entry.actual_interactive_cost.total_input_tokens;
      totalInteractiveOutput += entry.actual_interactive_cost.total_output_tokens;
    }
  }

  const noUsage = totalRuns === 0;
  const collapsible = defaultCollapsed && storageKey != null;

  // Collapse state persisted via usePersistentState (SSR snapshot = expanded
  // default → no hydration mismatch; client snapshot reads localStorage). Stored
  // value `false` means collapsed; absent → !defaultCollapsed (= expanded here).
  const [storedExpanded, setStoredExpanded] = usePersistentState<boolean>(
    storageKey ?? "cost-summary:__noop",
    !defaultCollapsed,
    { deserialize: (raw) => JSON.parse(raw) !== false },
  );
  // Non-collapsible panels are always expanded (no toggle chrome / no storage).
  const expanded = collapsible ? storedExpanded : !defaultCollapsed;

  function toggle() {
    if (!collapsible) return;
    setStoredExpanded(!expanded);
  }

  return (
    <section
      data-cost-summary
      aria-label={ariaLabel}
      className={`${className} rounded-lg border border-amber-200/60 bg-amber-50/40 p-3 dark:border-amber-900/40 dark:bg-amber-950/10`}
    >
      <div className="flex items-center gap-2 flex-wrap" style={{ marginBottom: expanded ? "0.75rem" : 0 }}>
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Usage & Spend
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Usage & Spend
          </h2>
        )}
        {/* Compact inline summary shown only when collapsible + collapsed */}
        {collapsible && !expanded && (
          <span className="text-xs text-zinc-600 dark:text-zinc-400 tabular-nums">
            {hasInteractive && (
              <span title="Mode A actual interactive cost">A {formatUsd(totalInteractiveCost)}</span>
            )}
            {hasInteractive && !noUsage && <span className="mx-1 text-zinc-400">·</span>}
            {!noUsage && (
              <span title="Mode B actual cost">B·actual {formatUsd(totalCost)}</span>
            )}
            {(hasInteractive || !noUsage) && <span className="mx-1 text-zinc-400">·</span>}
            <span>{totalRuns} run{totalRuns === 1 ? "" : "s"}</span>
          </span>
        )}
      </div>

      {expanded && (
        <>
          {noUsage && !hasInteractive ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No usage tracked yet.
            </p>
          ) : (
            <>
              {/* Mode A + Mode B side-by-side compact cards */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {/* Mode A · Actual (interactive) — #2735: real usage_events cost */}
                {hasInteractive && (
                  <div className="rounded-md border border-blue-100 bg-blue-50/40 px-3 py-3 dark:border-blue-900/30 dark:bg-blue-950/10">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[11px] font-medium uppercase tracking-wide text-blue-600 dark:text-blue-400">
                        Mode A · Actual (interactive)
                      </span>
                    </div>
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span
                        className="text-lg font-semibold tabular-nums leading-none text-blue-700 dark:text-blue-300"
                        title={`$${totalInteractiveCost.toFixed(4)} USD (real interactive cost captured from Claude Code hooks — usage_events ledger)`}
                      >
                        {formatUsd(totalInteractiveCost)}
                      </span>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
                        {formatInt(totalInteractiveInput)} in / {formatInt(totalInteractiveOutput)} out tokens
                      </span>
                    </div>
                  </div>
                )}

                {/* Mode B · Actual (headless) */}
                {!noUsage ? (
                  <div className="rounded-md border border-amber-100 bg-white/70 px-3 py-3 dark:border-amber-900/30 dark:bg-zinc-950/40">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                        Mode B · Actual (headless)
                      </span>
                    </div>
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span
                        className="text-lg font-semibold tabular-nums leading-none text-amber-700 dark:text-amber-300"
                        title={`$${totalCost.toFixed(4)} USD`}
                      >
                        {formatUsd(totalCost)}
                      </span>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
                        {formatInt(totalInput)} in / {formatInt(totalOutput)} out tokens
                      </span>
                    </div>
                    <p className="mt-1 text-[10px] text-zinc-400 dark:text-zinc-500 tabular-nums">
                      {totalRuns} session run{totalRuns === 1 ? "" : "s"} tracked
                    </p>
                  </div>
                ) : (
                  /* When no session runs yet, show a muted placeholder for Mode B */
                  <div className="rounded-md border border-amber-100 bg-white/70 px-3 py-3 dark:border-amber-900/30 dark:bg-zinc-950/40">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                        Mode B · Actual (headless)
                      </span>
                    </div>
                    <p className="text-xs text-zinc-400 dark:text-zinc-600">
                      $0.00 — no headless runs recorded yet
                    </p>
                  </div>
                )}
              </div>
            </>
          )}

        </>
      )}
    </section>
  );
}
