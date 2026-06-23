"use client";

// PnlSummaryCard — Kanban #1329 (M6 FE). Per-project P&L header card.
//
// Render site: web/components/Board.tsx (project page top, next to CostSummary).
//
// Sources /api/projects/{id}/pl via getProjectPl. Period selector exposes 5
// canned ranges + a disabled "Custom range" stub (real date picker is filed
// as a v2 follow-up — not in this slice).
//
// Default period:
//   The card's defaultPeriod prop is the first-mount fallback. After mount,
//   the value is read from localStorage at key `pnl_period_default` (single
//   global default per browser). Changing the selector writes the new key
//   back, so the next per-project card mount picks up the same default.
//   This intentionally avoids adding a projects.pnl_default_period DB column
//   (out of scope for v1).
//
// Empty state:
//   transaction_count === 0 → show a CTA pointing at /api/transactions +
//   future project-settings webhook config (currently no settings UI; link
//   targets the project settings route which already exists per the FS).
//
// Mixed-currency badge:
//   PLSummary.currency is the first observed currency. We compute the bucket
//   currency cardinality client-side; >1 unique currency → render "(mixed)".

import { useMemo, useRef } from "react";
import Link from "next/link";

import {
  getProjectPl,
  HttpError,
  PL_PERIODS,
  type PLSummary,
} from "@/lib/api";
import { formatMoney, formatSignedPercent, parseMoney } from "@/lib/money";
import {
  RANGE_OPTIONS,
  STORAGE_KEY as PNL_RANGE_KEY,
  STORABLE_RANGE_KEYS,
  type RangeKey,
} from "@/lib/plRangePresets";
import { useAsyncData } from "@/lib/useAsyncData";
import { usePersistentState } from "@/lib/usePersistentState";

// Collapse + range persistence now route through usePersistentState (#2491);
// the prior local readPnlExpanded/writePnlExpanded dups (a copy of
// collapseState.ts) and the readStoredRange/writeStoredRange calls are removed.

// Chevron icons (local copies mirror CostSummary; no shared icon lib in scope)
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

// ----- Component -------------------------------------------------------------

type Props = {
  projectId: number;
  projectName: string;
  defaultCurrency?: string;
  /** When true the card starts collapsed; user can expand via chevron. */
  defaultCollapsed?: boolean;
  /** localStorage key for per-project collapse state persistence. Required when defaultCollapsed=true. */
  storageKey?: string;
  /**
   * Wave A (#7) — layout override for the card <section> wrapper. Defaults to
   * "mb-5" (standalone spacing). The board's 3-up panels band passes "h-full"
   * so all three panels stretch to equal height inside the grid row.
   */
  className?: string;
};

export function PnlSummaryCard({
  projectId,
  projectName,
  defaultCurrency = "USD",
  defaultCollapsed = false,
  storageKey,
  className = "mb-5",
}: Props) {
  // Selected range, persisted to the shared pnl_period_default key via
  // usePersistentState (replaces readStoredRange/writeStoredRange + the
  // restore-on-mount effect). SSR snapshot = "last_30d"; client restores the
  // stored default. "custom" is a disabled option (never selectable/persisted);
  // the deserialize guard maps any non-storable raw back to "last_30d".
  const [rangeKey, setRangeKey] = usePersistentState<RangeKey>(
    PNL_RANGE_KEY,
    "last_30d",
    {
      serialize: (v) => v,
      deserialize: (raw) =>
        STORABLE_RANGE_KEYS.has(raw as RangeKey)
          ? (raw as RangeKey)
          : "last_30d",
    },
  );
  // Pin "now" once per mount so range builds don't drift mid-render.
  const nowRef = useRef<Date>(new Date());
  // RANGE_OPTIONS is a stable readonly array; memoize the reference only.
  const options = useMemo(() => RANGE_OPTIONS, []);

  // Collapse state — persisted via usePersistentState (mirrors CostSummary).
  // SSR snapshot = expanded default (no hydration mismatch); client reads
  // localStorage. The same-tab StorageEvent is dispatched by the hook's writer.
  const collapsible = defaultCollapsed && storageKey != null;
  const [storedExpanded, setStoredExpanded] = usePersistentState<boolean>(
    storageKey ?? "pnl-summary:__noop",
    !defaultCollapsed,
    { deserialize: (raw) => JSON.parse(raw) !== false },
  );
  const expanded = collapsible ? storedExpanded : !defaultCollapsed;

  function toggle() {
    if (!collapsible) return;
    setStoredExpanded(!expanded);
  }

  // #2492 — fetch on rangeKey change via useAsyncData (was a reqIdRef-guarded
  // effect; the hook's cancel flag provides the same stale-response discard).
  // "custom"/disabled options are stubs → the fetcher resolves null (no fetch).
  // The HttpError "<status>: <message>" formatting is preserved by rethrowing a
  // formatted Error so extractErrorMessage surfaces it verbatim.
  const {
    data: pl,
    loading: plLoading,
    error: plError,
  } = useAsyncData<PLSummary | null>(
    () => {
      const opt = options.find((o) => o.key === rangeKey);
      if (!opt || opt.disabled) return Promise.resolve(null);
      const { period, since, until } = opt.build(nowRef.current);
      return getProjectPl(projectId, {
        period,
        since: since ?? undefined,
        until: until ?? undefined,
      }).catch((err: unknown) => {
        // Preserve the prior "<status>: <message>" formatting for HttpError.
        if (err instanceof HttpError) throw new Error(`${err.status}: ${err.message}`);
        throw err;
      });
    },
    [projectId, rangeKey, options],
    { errorFallback: "Unknown error" },
  );
  // Local discriminated view so the render's existing kind-checks keep working
  // with minimal churn. idle/loading collapse to one "loading" branch (the
  // render already treats them identically). Memoized so its identity is stable
  // per fetch — several useMemos below depend on it (exhaustive-deps).
  const state = useMemo<
    { kind: "loading" } | { kind: "ok"; data: PLSummary } | { kind: "error"; message: string }
  >(
    () =>
      plError !== null
        ? { kind: "error", message: plError }
        : pl !== null
          ? { kind: "ok", data: pl }
          : { kind: "loading" },
    [pl, plError],
  );

  const currentLabel =
    options.find((o) => o.key === rangeKey)?.label ?? "Last 30 days";

  function onChangeRange(e: React.ChangeEvent<HTMLSelectElement>) {
    const next = e.target.value as RangeKey;
    // "custom" is a disabled option (never selectable); guard preserves the
    // prior "never persist custom" contract. setRangeKey persists to the shared
    // pnl_period_default key via usePersistentState (replaces writeStoredRange).
    if (next !== "custom") setRangeKey(next);
  }

  // Compute derived render values from the summary.
  const render = useMemo(() => {
    if (state.kind !== "ok") return null;
    const d = state.data;
    const revenue = parseMoney(d.revenue);
    // Expenses = cost + expense only. Refunds is a separate cell (#1383).
    const expenses = parseMoney(d.cost) + parseMoney(d.expense);
    const refunds = parseMoney(d.refund);
    const net = parseMoney(d.net);
    const marginPct = revenue > 0 ? (net / revenue) * 100 : null;
    const netColor =
      net > 0
        ? "text-emerald-700 dark:text-emerald-300"
        : net < 0
          ? "text-red-700 dark:text-red-300"
          : "text-zinc-500 dark:text-zinc-400";
    // Mixed-currency: derive from bucket currency cardinality. PLSummary
    // exposes `currency` (first observed) but not an explicit mixed flag.
    const uniqueCurrencies = new Set(
      d.buckets.map((b) => (b.currency ?? "").toUpperCase()).filter(Boolean),
    );
    const mixed = uniqueCurrencies.size > 1;
    return { revenue, expenses, refunds, net, marginPct, netColor, mixed };
  }, [state]);

  const headerCurrency =
    state.kind === "ok" ? state.data.currency : defaultCurrency;

  // Compact net summary for collapsed header (only when data is available).
  const collapsedSummary = useMemo(() => {
    if (state.kind !== "ok" || !render) return null;
    const netStr = formatMoney(state.data.net, state.data.currency);
    const pctStr =
      render.marginPct !== null && render.marginPct !== undefined
        ? ` (${formatSignedPercent(render.marginPct)})`
        : "";
    return `net ${netStr}${pctStr}`;
  }, [state, render]);

  return (
    <section
      data-pnl-summary-card
      data-project-id={projectId}
      aria-label={`P&L summary for ${projectName}`}
      className={`${className} rounded-lg border border-emerald-200/60 bg-emerald-50/40 p-5 dark:border-emerald-900/40 dark:bg-emerald-950/10`}
    >
      <div className="flex flex-wrap items-center gap-2" style={{ marginBottom: expanded ? "0.75rem" : 0 }}>
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            P&amp;L
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            P&amp;L — {currentLabel}
          </h2>
        )}
        {/* Compact inline net summary shown only when collapsible + collapsed */}
        {collapsible && !expanded && collapsedSummary && (
          <span className="text-xs text-zinc-600 dark:text-zinc-400 tabular-nums">
            P&amp;L · {collapsedSummary}
          </span>
        )}
        {collapsible && !expanded && !collapsedSummary && (
          <span className="text-xs text-zinc-500 dark:text-zinc-400">
            {currentLabel}
          </span>
        )}
        {expanded && collapsible && (
          <>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              — {currentLabel}
            </span>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              {headerCurrency.toUpperCase()}
              {render?.mixed ? (
                <span
                  className="ml-1 inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                  title="Multiple currencies observed in window — totals use first-observed currency only"
                >
                  mixed
                </span>
              ) : null}
            </span>
          </>
        )}
        {!collapsible && (
          <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
            {headerCurrency.toUpperCase()}
            {render?.mixed ? (
              <span
                className="ml-1 inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                title="Multiple currencies observed in window — totals use first-observed currency only"
              >
                mixed
              </span>
            ) : null}
          </span>
        )}
        {expanded && (
          <label className="ml-auto flex items-center gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
            <span className="sr-only">Period</span>
            <select
              value={rangeKey}
              onChange={onChangeRange}
              className="rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs px-2 py-1 text-zinc-700 dark:text-zinc-300 focus:outline-none focus:ring-1 focus:ring-zinc-400"
              aria-label="P&L period range"
            >
              {options.map((opt) => (
                <option key={opt.key} value={opt.key} disabled={opt.disabled}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {expanded && (
        <>
          {state.kind === "loading" ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading P&amp;L…</p>
          ) : state.kind === "error" ? (
            <p className="text-sm text-red-700 dark:text-red-300">
              P&amp;L unavailable — {state.message}
            </p>
          ) : state.data.transaction_count === 0 ? (
            <div className="text-sm text-zinc-600 dark:text-zinc-400">
              <p>No transactions yet in this window.</p>
              <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-500">
                POST a manual record via <code>/api/transactions</code> or configure
                a Stripe / PayPal webhook in{" "}
                <Link
                  href={`/settings?project=${encodeURIComponent(projectName)}`}
                  className="text-emerald-700 underline hover:text-emerald-800 dark:text-emerald-300 dark:hover:text-emerald-200"
                >
                  project settings
                </Link>
                .
              </p>
            </div>
          ) : (
            <>
              <div
                className="grid grid-cols-2 gap-3 sm:grid-cols-4"
                role="list"
                aria-label="P&L totals"
              >
                <div
                  role="listitem"
                  className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
                  title={`Revenue: ${state.data.revenue} ${state.data.currency}`}
                >
                  <span className={`${collapsible ? "text-lg" : "text-2xl"} font-semibold tabular-nums leading-none text-emerald-700 dark:text-emerald-300`}>
                    {formatMoney(state.data.revenue, state.data.currency)}
                  </span>
                  <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Revenue
                  </span>
                </div>
                <div
                  role="listitem"
                  className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
                  title="Expenses = cost + operating expense (excludes refunds)"
                >
                  <span className={`${collapsible ? "text-lg" : "text-2xl"} font-semibold tabular-nums leading-none text-zinc-900 dark:text-zinc-100`}>
                    {formatMoney(render?.expenses ?? 0, state.data.currency)}
                  </span>
                  <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Expenses
                  </span>
                </div>
                <div
                  role="listitem"
                  className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
                  title="Refunds issued on prior sales"
                >
                  <span className={`${collapsible ? "text-lg" : "text-2xl"} font-semibold tabular-nums leading-none text-zinc-900 dark:text-zinc-100`}>
                    {formatMoney(render?.refunds ?? 0, state.data.currency)}
                  </span>
                  <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Refunds
                  </span>
                </div>
                <div
                  role="listitem"
                  className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
                  title="Net = revenue − expenses − refunds. transfer excluded."
                >
                  <span
                    className={`${collapsible ? "text-lg" : "text-2xl"} font-semibold tabular-nums leading-none ${render?.netColor ?? "text-zinc-900 dark:text-zinc-100"}`}
                  >
                    {formatMoney(state.data.net, state.data.currency)}
                  </span>
                  <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Net{" "}
                    {render?.marginPct !== null && render?.marginPct !== undefined ? (
                      <span
                        className={`ml-1 normal-case tracking-normal ${render.netColor}`}
                      >
                        ({formatSignedPercent(render.marginPct)})
                      </span>
                    ) : null}
                  </span>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

// Re-export PL_PERIODS at the component module level for ergonomic imports
// from sibling components that need the literal-union (e.g. dashboard view).
export { PL_PERIODS };
