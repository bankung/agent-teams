"use client";

// PnlDashboardSection — Kanban #1329 (M6 FE). Cross-project P&L table for the
// dashboard. Sources /api/pnl via getCrossProjectPl. NO X-Project-Id header
// (operator-level endpoint by design).
//
// Render site: web/app/dashboard/page.tsx (between CostSummary and the
// per-project compact grid, per task brief).
//
// Period selector mirrors PnlSummaryCard's range options (same canned set +
// same localStorage key `pnl_period_default`) so the dashboard view and the
// project-page card share a single user-chosen default.
//
// Grand-total chip:
//   PLCrossProject.grand_total_net_first_currency_only is non-null only when
//   every row shares the same currency. When null, the chip is hidden and
//   the per-row table is the only source of truth.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import {
  getCrossProjectPl,
  HttpError,
  type PLCrossProject,
} from "@/lib/api";
import { formatMoney, parseMoney } from "@/lib/money";
import {
  RANGE_OPTIONS,
  buildRange,
  readStoredRange,
  writeStoredRange,
  type RangeKey,
} from "@/lib/plRangePresets";

// ----- Icons -----------------------------------------------------------------

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

// ----- Collapse helpers ------------------------------------------------------

function readExpanded(key: string, defaultCollapsed: boolean): boolean {
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

// ----- Component -------------------------------------------------------------

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: PLCrossProject }
  | { kind: "error"; message: string };

type Props = {
  defaultCollapsed?: boolean;
  storageKey?: string;
};

export function PnlDashboardSection({
  defaultCollapsed = false,
  storageKey,
}: Props) {
  const [rangeKey, setRangeKey] = useState<RangeKey>("last_30d");
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const nowRef = useRef<Date>(new Date());
  const reqIdRef = useRef(0);

  const collapsible = storageKey != null;

  // Default expanded=true so SSR + first paint avoid hydration mismatch.
  // useEffect corrects from localStorage after hydration.
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

  // Restore stored default on mount.
  useEffect(() => {
    const stored = readStoredRange();
    if (stored) setRangeKey(stored);
  }, []);

  // Fetch on rangeKey change.
  useEffect(() => {
    if (rangeKey === "custom") return;
    const { period, since, until } = buildRange(rangeKey, nowRef.current);
    const myReq = ++reqIdRef.current;
    setState({ kind: "loading" });
    getCrossProjectPl({
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
  }, [rangeKey]);

  function onChangeRange(e: React.ChangeEvent<HTMLSelectElement>) {
    const next = e.target.value as RangeKey;
    setRangeKey(next);
    if (next !== "custom") writeStoredRange(next);
  }

  // Sorted rows: net DESC so the most-profitable projects surface first.
  // Zero-txn rows (likely the bulk of the table today) slot to the bottom.
  const sortedRows = useMemo(() => {
    if (state.kind !== "ok") return [];
    return [...state.data.rows].sort((a, b) => {
      const aHas = a.transaction_count > 0 ? 1 : 0;
      const bHas = b.transaction_count > 0 ? 1 : 0;
      if (aHas !== bHas) return bHas - aHas;
      return parseMoney(b.net) - parseMoney(a.net);
    });
  }, [state]);

  return (
    <section
      data-pnl-cross-project
      aria-label="Cross-project P&L"
      className="mb-5 rounded-lg border border-emerald-200/60 bg-emerald-50/40 p-5 dark:border-emerald-900/40 dark:bg-emerald-950/10"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Cross-project P&amp;L —{" "}
            {RANGE_OPTIONS.find((o) => o.key === rangeKey)?.label ?? "Last 30 days"}
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Cross-project P&amp;L —{" "}
            {RANGE_OPTIONS.find((o) => o.key === rangeKey)?.label ?? "Last 30 days"}
          </h2>
        )}
        {state.kind === "ok" ? (
          <>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
              {state.data.total_projects} project
              {state.data.total_projects === 1 ? "" : "s"}
            </span>
            {state.data.grand_total_net_first_currency_only !== null &&
            sortedRows.length > 0 ? (
              <span
                className="inline-flex items-center rounded bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200 tabular-nums"
                title="Grand total net — only valid when every row shares one currency"
              >
                Grand net:{" "}
                {formatMoney(
                  state.data.grand_total_net_first_currency_only,
                  sortedRows[0]?.currency_default ?? "USD",
                )}
              </span>
            ) : null}
          </>
        ) : null}
        <label className="ml-auto flex items-center gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
          <span className="sr-only">Period</span>
          <select
            value={rangeKey}
            onChange={onChangeRange}
            className="rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs px-2 py-1 text-zinc-700 dark:text-zinc-300 focus:outline-none focus:ring-1 focus:ring-zinc-400"
            aria-label="Cross-project P&L period range"
          >
            {RANGE_OPTIONS.map((opt) => (
              <option key={opt.key} value={opt.key} disabled={opt.disabled}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {expanded && (
        <>
          {state.kind === "loading" || state.kind === "idle" ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Loading cross-project P&amp;L…
            </p>
          ) : state.kind === "error" ? (
            <p className="text-sm text-red-700 dark:text-red-300">
              P&amp;L unavailable — {state.message}
            </p>
          ) : sortedRows.length === 0 ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No projects in window.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b border-emerald-200/60 text-left text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:border-emerald-900/40 dark:text-zinc-400">
                    <th className="px-2 py-2">Project</th>
                    <th className="px-2 py-2">Team</th>
                    <th className="px-2 py-2">Currency</th>
                    <th className="px-2 py-2 text-right">Revenue</th>
                    <th className="px-2 py-2 text-right">Expenses</th>
                    <th className="px-2 py-2 text-right">Refunds</th>
                    <th className="px-2 py-2 text-right">Net</th>
                    <th className="px-2 py-2 text-right">Txns</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedRows.map((row) => {
                    const revenue = parseMoney(row.revenue);
                    // Expenses = cost + operating expense; refund is separate (#1383).
                    const expenses =
                      parseMoney(row.cost) + parseMoney(row.expense);
                    const refunds = parseMoney(row.refund);
                    const net = parseMoney(row.net);
                    const dimmed = row.transaction_count === 0;
                    const netClass =
                      net > 0
                        ? "text-emerald-700 dark:text-emerald-300"
                        : net < 0
                          ? "text-red-700 dark:text-red-300"
                          : "text-zinc-500 dark:text-zinc-400";
                    return (
                      <tr
                        key={row.project_id}
                        className={`border-b border-emerald-100/60 last:border-0 dark:border-emerald-900/20 ${dimmed ? "opacity-60" : ""}`}
                        title={
                          dimmed
                            ? "no transactions in window"
                            : `${row.bucket_count} bucket${row.bucket_count === 1 ? "" : "s"}`
                        }
                      >
                        <td className="px-2 py-2">
                          <Link
                            href={`/p/${row.project_name}`}
                            className="font-medium text-zinc-900 hover:underline dark:text-zinc-100"
                          >
                            {row.project_name}
                          </Link>
                        </td>
                        <td className="px-2 py-2 text-xs text-zinc-500 dark:text-zinc-400">
                          <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                            {row.team}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-xs text-zinc-600 dark:text-zinc-400 tabular-nums">
                          {row.currency_default.toUpperCase()}
                          {row.mixed_currency ? (
                            <span
                              className="ml-1 inline-flex items-center rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                              title="Multiple currencies observed for this project in window — totals shown are first-currency-only"
                            >
                              mixed
                            </span>
                          ) : null}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums text-emerald-700 dark:text-emerald-300">
                          {formatMoney(revenue, row.currency_default)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums text-zinc-700 dark:text-zinc-300">
                          {formatMoney(expenses, row.currency_default)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums text-zinc-700 dark:text-zinc-300">
                          {formatMoney(refunds, row.currency_default)}
                        </td>
                        <td
                          className={`px-2 py-2 text-right font-semibold tabular-nums ${netClass}`}
                        >
                          {formatMoney(net, row.currency_default)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums text-zinc-600 dark:text-zinc-400">
                          {row.transaction_count}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
