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

import { useEffect, useState } from "react";

import type { ProjectStatsEntry } from "@/lib/api";

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
};

function readExpanded(key: string, defaultCollapsed: boolean): boolean {
  // expanded = !defaultCollapsed when no stored pref exists.
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return !defaultCollapsed;
    return JSON.parse(raw) !== false;
  } catch {
    return !defaultCollapsed;
  }
}

function writeExpanded(key: string, next: boolean): void {
  try {
    localStorage.setItem(key, JSON.stringify(next));
    window.dispatchEvent(
      new StorageEvent("storage", {
        key,
        newValue: JSON.stringify(next),
        storageArea: localStorage,
      }),
    );
  } catch {
    // localStorage blocked — silently ignore.
  }
}

export function CostSummary({
  stats,
  ariaLabel = "Portfolio-wide token and cost usage",
  defaultCollapsed = false,
  storageKey,
}: Props) {
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
  const collapsible = defaultCollapsed && storageKey != null;

  // Default expanded=true so SSR + first paint avoid hydration mismatch.
  // For collapsible panels the actual value is read from localStorage after
  // hydration (useEffect), mirroring AuditorVisibilityToggle's pattern.
  const [expanded, setExpanded] = useState(!defaultCollapsed);

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

  return (
    <section
      data-cost-summary
      aria-label={ariaLabel}
      className="mb-5 rounded-lg border border-amber-200/60 bg-amber-50/40 p-5 dark:border-amber-900/40 dark:bg-amber-950/10"
    >
      <div className="mb-3 flex items-center gap-2">
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Usage
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Usage
          </h2>
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
          {noUsage ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No usage tracked yet.
            </p>
          ) : (
            <>
              {/* #954 — single column on mobile (375px iPhone); 3-col tile row restored at sm */}
              <div
                className="grid grid-cols-1 gap-3 sm:grid-cols-3"
                role="list"
                aria-label="Cost and token totals"
              >
                <div
                  role="listitem"
                  className="flex flex-col items-start gap-1 rounded-md border border-amber-100 bg-white/70 px-3 py-3 dark:border-amber-900/30 dark:bg-zinc-950/40"
                  title={`$${totalCost.toFixed(4)} USD`}
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
        </>
      )}
    </section>
  );
}
