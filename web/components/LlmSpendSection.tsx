"use client";

// LlmSpendSection — Kanban #2135. Cross-project LLM API spend summary for the
// dashboard. Sources GET /api/usage/daily (operator-level; no X-Project-Id).
//
// Render site: web/app/dashboard/page.tsx — sibling of CostSummary, outside
// the FINANCE_PANELS_ENABLED gate.
//
// Content:
//   • "Today: $X.XXXX · This month: $X.XXXX"
//   • Compact per-provider breakdown for today (nonzero providers only).
//   • Zero-data state renders "$0.0000" gracefully — no crash, no skeleton-forever.
//   • Error state renders a quiet one-liner; no crash.

import { useEffect, useRef, useState } from "react";

import { getDailyUsage, HttpError, type DailyUsageResponse } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: DailyUsageResponse }
  | { kind: "error"; message: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseUsd(raw: string): number {
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

function fmt4dp(n: number): string {
  return `$${n.toFixed(4)}`;
}

// todayProviderTotals — sum cost_usd per provider for rows dated today.
// Prefers the server's UTC date (data.today) to avoid timezone-edge mismatch
// where the client clock is on a different UTC date than the DB server.
function todayProviderTotals(
  rows: DailyUsageResponse["rows"],
  today: string,
): Map<string, number> {
  const map = new Map<string, number>();
  for (const row of rows) {
    if (row.date !== today) continue;
    const prev = map.get(row.provider) ?? 0;
    map.set(row.provider, prev + parseUsd(row.cost_usd));
  }
  return map;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function LlmSpendSection() {
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const reqIdRef = useRef(0);

  useEffect(() => {
    const myReq = ++reqIdRef.current;
    setState({ kind: "loading" });
    getDailyUsage({ days: 31 })
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
  }, []);

  return (
    <section
      data-llm-spend
      aria-label="LLM API spend"
      className="mb-5 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        LLM spend
      </h2>

      {state.kind === "idle" || state.kind === "loading" ? (
        <p className="text-sm text-zinc-400 dark:text-zinc-600" aria-live="polite">
          Loading…
        </p>
      ) : state.kind === "error" ? (
        <p
          className="text-sm text-zinc-400 dark:text-zinc-600"
          aria-live="polite"
          title={state.message}
        >
          Spend unavailable
        </p>
      ) : (
        <SpendContent data={state.data} />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// SpendContent — renders once data is loaded
// ---------------------------------------------------------------------------

function SpendContent({ data }: { data: DailyUsageResponse }) {
  const todayUsd = parseUsd(data.total_today_usd);
  const monthUsd = parseUsd(data.total_month_usd);
  // Prefer server UTC date; fall back to client UTC date when field absent.
  const todayDate = data.today ?? new Date().toISOString().slice(0, 10);
  const providerMap = todayProviderTotals(data.rows, todayDate);

  // Only show providers with nonzero spend today.
  const providerEntries = [...providerMap.entries()].filter(
    ([, cost]) => cost > 0,
  );

  return (
    <div className="flex flex-col gap-1.5">
      {/* Summary line */}
      <p className="text-sm tabular-nums text-zinc-700 dark:text-zinc-300">
        <span aria-label={`Today: ${fmt4dp(todayUsd)}`}>
          Today:{" "}
          <span className="font-semibold text-zinc-900 dark:text-zinc-100">
            {fmt4dp(todayUsd)}
          </span>
        </span>
        <span aria-hidden className="mx-2 text-zinc-300 dark:text-zinc-700">
          ·
        </span>
        <span aria-label={`This month: ${fmt4dp(monthUsd)}`}>
          This month:{" "}
          <span className="font-semibold text-zinc-900 dark:text-zinc-100">
            {fmt4dp(monthUsd)}
          </span>
        </span>
      </p>

      {/* Per-provider breakdown (today, nonzero only) */}
      {providerEntries.length > 0 && (
        <ul
          className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] tabular-nums text-zinc-500 dark:text-zinc-400"
          aria-label="Today's spend by provider"
        >
          {providerEntries.map(([provider, cost]) => (
            <li key={provider}>
              <span className="font-medium text-zinc-700 dark:text-zinc-300">
                {provider}
              </span>{" "}
              <span aria-label={`${provider} cost ${fmt4dp(cost)}`}>
                {fmt4dp(cost)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
