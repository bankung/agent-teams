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

import { useEffect, useMemo, useRef, useState } from "react";
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
  readStoredRange,
  writeStoredRange,
  type RangeKey,
} from "@/lib/plRangePresets";

// ----- Component -------------------------------------------------------------

type Props = {
  projectId: number;
  projectName: string;
  defaultCurrency?: string;
};

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: PLSummary }
  | { kind: "error"; message: string };

export function PnlSummaryCard({
  projectId,
  projectName,
  defaultCurrency = "USD",
}: Props) {
  // SSR / first-paint fallback is the brief's default "last_30d" so the
  // initial render is stable. Hydration effect upgrades to the stored value
  // (if any) — same pattern as CostSummary's expand-state persistence.
  const [rangeKey, setRangeKey] = useState<RangeKey>("last_30d");
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  // Pin "now" once per mount so range builds don't drift mid-render.
  const nowRef = useRef<Date>(new Date());
  // RANGE_OPTIONS is a stable readonly array; memoize the reference only.
  const options = useMemo(() => RANGE_OPTIONS, []);
  // Promotes "last fetched in-flight" → renderable; lets us ignore stale
  // responses if the user flips the dropdown faster than the BE responds.
  const reqIdRef = useRef(0);

  // Restore stored default on mount.
  useEffect(() => {
    const stored = readStoredRange();
    if (stored) setRangeKey(stored);
  }, []);

  // Fetch on rangeKey change. Custom is a stub — no fetch fires.
  useEffect(() => {
    const opt = options.find((o) => o.key === rangeKey);
    if (!opt || opt.disabled) return;
    const { period, since, until } = opt.build(nowRef.current);
    const myReq = ++reqIdRef.current;
    setState({ kind: "loading" });
    getProjectPl(projectId, {
      period,
      since: since ?? undefined,
      until: until ?? undefined,
    })
      .then((data) => {
        if (myReq !== reqIdRef.current) return;
        setState({ kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (myReq !== reqIdRef.current) return;
        const message =
          err instanceof HttpError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : "Unknown error";
        setState({ kind: "error", message });
      });
  }, [projectId, rangeKey, options]);

  const currentLabel =
    options.find((o) => o.key === rangeKey)?.label ?? "Last 30 days";

  function onChangeRange(e: React.ChangeEvent<HTMLSelectElement>) {
    const next = e.target.value as RangeKey;
    setRangeKey(next);
    if (next !== "custom") writeStoredRange(next);
  }

  // Compute derived render values from the summary.
  const render = useMemo(() => {
    if (state.kind !== "ok") return null;
    const d = state.data;
    const revenue = parseMoney(d.revenue);
    // "Expenses" = cost + expense + refund (per #953 ledger semantics).
    const expenses =
      parseMoney(d.cost) + parseMoney(d.expense) + parseMoney(d.refund);
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
    return { revenue, expenses, net, marginPct, netColor, mixed };
  }, [state]);

  const headerCurrency =
    state.kind === "ok" ? state.data.currency : defaultCurrency;

  return (
    <section
      data-pnl-summary-card
      data-project-id={projectId}
      aria-label={`P&L summary for ${projectName}`}
      className="mb-5 rounded-lg border border-emerald-200/60 bg-emerald-50/40 p-5 dark:border-emerald-900/40 dark:bg-emerald-950/10"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          P&amp;L — {currentLabel}
        </h2>
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
      </div>

      {state.kind === "loading" || state.kind === "idle" ? (
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
              href={`/p/${projectName}/settings`}
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
            className="grid grid-cols-1 gap-3 sm:grid-cols-3"
            role="list"
            aria-label="P&L totals"
          >
            <div
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
              title={`Revenue: ${state.data.revenue} ${state.data.currency}`}
            >
              <span className="text-2xl font-semibold tabular-nums leading-none text-emerald-700 dark:text-emerald-300">
                {formatMoney(state.data.revenue, state.data.currency)}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Revenue
              </span>
            </div>
            <div
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
              title="cost + expense + refund (per #953 ledger semantics)"
            >
              <span className="text-2xl font-semibold tabular-nums leading-none text-zinc-900 dark:text-zinc-100">
                {formatMoney(render?.expenses ?? 0, state.data.currency)}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Expenses
              </span>
            </div>
            <div
              role="listitem"
              className="flex flex-col items-start gap-1 rounded-md border border-emerald-100 bg-white/70 px-3 py-3 dark:border-emerald-900/30 dark:bg-zinc-950/40"
              title={`Net = revenue − (cost + expense + refund). transfer is excluded.`}
            >
              <span
                className={`text-2xl font-semibold tabular-nums leading-none ${render?.netColor ?? "text-zinc-900 dark:text-zinc-100"}`}
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
          <div className="mt-4 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
            <span>
              <span className="font-semibold text-zinc-900 dark:text-zinc-100 tabular-nums">
                {state.data.transaction_count}
              </span>{" "}
              transaction{state.data.transaction_count === 1 ? "" : "s"}
            </span>
          </div>
        </>
      )}
    </section>
  );
}

// Re-export PL_PERIODS at the component module level for ergonomic imports
// from sibling components that need the literal-union (e.g. dashboard view).
export { PL_PERIODS };
