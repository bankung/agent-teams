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

import { useEffect, useRef, useState } from "react";

import { getDailyUsage, HttpError, type DailyUsageResponse, type ProjectStatsEntry } from "@/lib/api";
import { readExpanded, writeExpanded } from "@/lib/collapseState";

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

function fmt4dp(n: number): string {
  return `$${n.toFixed(4)}`;
}

function parseUsdFlt(raw: string): number {
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

function todayProviderTotals(
  rows: DailyUsageResponse["rows"],
  today: string,
): Map<string, number> {
  const map = new Map<string, number>();
  for (const row of rows) {
    if (row.date !== today) continue;
    map.set(row.provider, (map.get(row.provider) ?? 0) + parseUsdFlt(row.cost_usd));
  }
  return map;
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
  let totalWarnings = 0;
  let totalEstimatedCost = 0;
  let totalEstimatedInput = 0;
  let totalEstimatedOutput = 0;
  let hasEstimated = false;
  for (const entry of stats) {
    totalCost += parseUsd(entry.cost_usage.total_cost_usd);
    totalInput += entry.cost_usage.total_input_tokens;
    totalOutput += entry.cost_usage.total_output_tokens;
    totalRuns += entry.cost_usage.session_run_count;
    totalWarnings += entry.cost_usage.budget_warning_count;
    if (entry.estimated_cost != null) {
      hasEstimated = true;
      totalEstimatedCost += parseUsd(entry.estimated_cost.total_cost_usd);
      totalEstimatedInput += entry.estimated_cost.total_input_tokens;
      totalEstimatedOutput += entry.estimated_cost.total_output_tokens;
    }
  }

  const noUsage = totalRuns === 0;
  const collapsible = defaultCollapsed && storageKey != null;

  // Default expanded=true so SSR + first paint avoid hydration mismatch.
  // For collapsible panels the actual value is read from localStorage after
  // hydration (useEffect), mirroring AuditorVisibilityToggle's pattern.
  const [expanded, setExpanded] = useState(!defaultCollapsed);

  // Provider breakdown — fetched once when the panel is first expanded.
  type SpendState =
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "ok"; providerEntries: [string, number][]; todayUsd: number; monthUsd: number }
    | { kind: "error" };
  const [spendState, setSpendState] = useState<SpendState>({ kind: "idle" });
  const spendFetchedRef = useRef(false);

  useEffect(() => {
    if (!collapsible || !storageKey) return;
    setExpanded(readExpanded(storageKey, defaultCollapsed));

    function onStorage(e: StorageEvent) {
      if (e.key !== storageKey) return;
      setExpanded(
        e.newValue !== null ? JSON.parse(e.newValue) !== false : !defaultCollapsed,
      );
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [collapsible, storageKey, defaultCollapsed]);

  function toggle() {
    if (!collapsible || !storageKey) return;
    const next = !expanded;
    setExpanded(next);
    writeExpanded(storageKey, next);
  }

  // Fetch provider breakdown once on first expand.
  useEffect(() => {
    if (!expanded || spendFetchedRef.current) return;
    spendFetchedRef.current = true;
    setSpendState({ kind: "loading" });
    getDailyUsage({ days: 31 })
      .then((data) => {
        const todayDate = data.today ?? new Date().toISOString().slice(0, 10);
        const map = todayProviderTotals(data.rows, todayDate);
        const providerEntries = [...map.entries()].filter(([, c]) => c > 0);
        setSpendState({
          kind: "ok",
          providerEntries,
          todayUsd: parseUsdFlt(data.total_today_usd),
          monthUsd: parseUsdFlt(data.total_month_usd),
        });
      })
      .catch((err: unknown) => {
        void err;
        setSpendState({ kind: "error" });
      });
  }, [expanded]);

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
            {hasEstimated && (
              <span title="Mode A estimated cost">A·est {formatUsd(totalEstimatedCost)}</span>
            )}
            {hasEstimated && !noUsage && <span className="mx-1 text-zinc-400">·</span>}
            {!noUsage && (
              <span title="Mode B actual cost">B·actual {formatUsd(totalCost)}</span>
            )}
            {(hasEstimated || !noUsage) && <span className="mx-1 text-zinc-400">·</span>}
            <span>{totalRuns} run{totalRuns === 1 ? "" : "s"}</span>
          </span>
        )}
        {totalWarnings > 0 ? (
          <span
            className="inline-flex items-center rounded bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
            title={`${totalWarnings} session run${totalWarnings === 1 ? "" : "s"} flagged budget-warned`}
          >
            ⚠ {totalWarnings} run{totalWarnings === 1 ? "" : "s"} budget-warned
          </span>
        ) : null}
      </div>

      {expanded && (
        <>
          {noUsage && !hasEstimated ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No usage tracked yet.
            </p>
          ) : (
            <>
              {/* Mode A + Mode B side-by-side compact cards */}
              <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {/* Mode A · Estimated */}
                {hasEstimated && (
                  <div className="rounded-md border border-blue-100 bg-blue-50/40 px-3 py-3 dark:border-blue-900/30 dark:bg-blue-950/10">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[11px] font-medium uppercase tracking-wide text-blue-600 dark:text-blue-400">
                        Mode A · Estimated
                      </span>
                    </div>
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span
                        className="text-lg font-semibold tabular-nums leading-none text-blue-700 dark:text-blue-300"
                        title={`$${totalEstimatedCost.toFixed(4)} USD (heuristic estimate at API token rates)`}
                      >
                        {formatUsd(totalEstimatedCost)}
                      </span>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
                        {formatInt(totalEstimatedInput)} in / {formatInt(totalEstimatedOutput)} out tokens
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

          {/* Provider breakdown — folded in from LlmSpendSection (v0.7.0) */}
          {spendState.kind === "ok" && (
            <div className="mt-3 border-t border-amber-100 pt-3 dark:border-amber-900/30">
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Provider breakdown (today · this month)
              </span>
              <p className="mt-1 text-xs tabular-nums text-zinc-700 dark:text-zinc-300">
                Today:{" "}
                <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                  {fmt4dp(spendState.todayUsd)}
                </span>
                <span aria-hidden className="mx-2 text-zinc-300 dark:text-zinc-700">·</span>
                This month:{" "}
                <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                  {fmt4dp(spendState.monthUsd)}
                </span>
              </p>
              {spendState.providerEntries.length > 0 && (
                <ul
                  className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] tabular-nums text-zinc-500 dark:text-zinc-400"
                  aria-label="Today's spend by provider"
                >
                  {spendState.providerEntries.map(([provider, cost]) => (
                    <li key={provider}>
                      <span className="font-medium text-zinc-700 dark:text-zinc-300">{provider}</span>{" "}
                      <span aria-label={`${provider} cost ${fmt4dp(cost)}`}>{fmt4dp(cost)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {spendState.kind === "error" && (
            <p className="mt-3 text-[11px] text-zinc-400 dark:text-zinc-600 border-t border-amber-100 pt-3 dark:border-amber-900/30">
              Provider breakdown unavailable
            </p>
          )}
        </>
      )}
    </section>
  );
}
