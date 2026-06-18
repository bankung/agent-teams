// plRangePresets.ts — shared range-preset utilities for P&L components.
//
// Single source of truth for: RangeKey, STORAGE_KEY, STORABLE_RANGE_KEYS,
// RANGE_OPTIONS, date-math helpers, buildRange. (Range persistence itself is
// done by the consumers via usePersistentState on STORAGE_KEY — #2491.)
//
// Used by: PnlSummaryCard (per-project) + PnlDashboardSection (cross-project).
// Pure module — no React imports.

import type { PLPeriodLiteral } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type RangeKey =
  | "last_30d"
  | "this_month"
  | "last_month"
  | "this_quarter"
  | "all_time"
  | "custom";

export type RangeBuild = {
  period: PLPeriodLiteral;
  since: string | null;
  until: string | null;
};

export type RangeOption = {
  key: RangeKey;
  label: string;
  build: (now: Date) => RangeBuild;
  disabled?: boolean;
};

// ---------------------------------------------------------------------------
// Date-math helpers (UTC-only)
// ---------------------------------------------------------------------------

export function startOfMonthUtc(d: Date): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1, 0, 0, 0, 0));
}

export function startOfQuarterUtc(d: Date): Date {
  const qStartMonth = Math.floor(d.getUTCMonth() / 3) * 3;
  return new Date(Date.UTC(d.getUTCFullYear(), qStartMonth, 1, 0, 0, 0, 0));
}

export function daysAgoUtc(d: Date, days: number): Date {
  const out = new Date(d.getTime());
  out.setUTCDate(out.getUTCDate() - days);
  return out;
}

// ---------------------------------------------------------------------------
// buildRange — given a RangeKey + reference Date, return API call params
// ---------------------------------------------------------------------------

export function buildRange(key: RangeKey, now: Date): RangeBuild {
  switch (key) {
    case "last_30d":
      return {
        period: "daily",
        since: daysAgoUtc(now, 30).toISOString(),
        until: null,
      };
    case "this_month":
      return {
        period: "monthly",
        since: startOfMonthUtc(now).toISOString(),
        until: null,
      };
    case "last_month": {
      const thisStart = startOfMonthUtc(now);
      const lastStart = new Date(
        Date.UTC(
          thisStart.getUTCFullYear(),
          thisStart.getUTCMonth() - 1,
          1,
          0,
          0,
          0,
          0,
        ),
      );
      return {
        period: "monthly",
        since: lastStart.toISOString(),
        until: thisStart.toISOString(),
      };
    }
    case "this_quarter":
      return {
        period: "quarterly",
        since: startOfQuarterUtc(now).toISOString(),
        until: null,
      };
    case "all_time":
      return { period: "yearly", since: null, until: null };
    case "custom":
      // Stub — selector option is disabled; this branch never fires a fetch.
      return { period: "monthly", since: null, until: null };
  }
}

// ---------------------------------------------------------------------------
// RANGE_OPTIONS — canonical ordered list (includes disabled "custom" stub)
// ---------------------------------------------------------------------------

export const RANGE_OPTIONS: readonly RangeOption[] = [
  {
    key: "last_30d",
    label: "Last 30 days",
    build: (now) => buildRange("last_30d", now),
  },
  {
    key: "this_month",
    label: "This month",
    build: (now) => buildRange("this_month", now),
  },
  {
    key: "last_month",
    label: "Last month",
    build: (now) => buildRange("last_month", now),
  },
  {
    key: "this_quarter",
    label: "This quarter",
    build: (now) => buildRange("this_quarter", now),
  },
  {
    key: "all_time",
    label: "All time",
    build: (now) => buildRange("all_time", now),
  },
  {
    key: "custom",
    label: "Custom range (coming soon)",
    build: (now) => buildRange("custom", now),
    disabled: true,
  },
];

// ---------------------------------------------------------------------------
// localStorage persistence — shared key ensures both components stay in sync
// ---------------------------------------------------------------------------

export const STORAGE_KEY = "pnl_period_default" as const;

// Valid storable keys — "custom" is intentionally excluded (disabled stub).
// Both P&L components persist the range via usePersistentState (#2491) using
// STORAGE_KEY + this set as the deserialize-time validation guard (replacing
// the prior readStoredRange/writeStoredRange helpers, now removed).
export const STORABLE_RANGE_KEYS = new Set<RangeKey>([
  "last_30d",
  "this_month",
  "last_month",
  "this_quarter",
  "all_time",
]);
