"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { getUserPending, type UserPendingByProject } from "@/lib/api";
import { useWildcardRowChanged } from "@/lib/WildcardSSEContext";

// Kanban #1457 phase 2 — replace N+1 fan-out with single /api/user/pending call.
// Color thresholds:
//   green  count === 0
//   yellow count 1–INBOX_YELLOW_MAX
//   red    count > INBOX_YELLOW_MAX  OR  oldest_age_hours > INBOX_RED_AGE_HOURS
// Polling 60s + SSE row_changed invalidation (mirrors FlagBellBadge pattern).
// SSE debounce: 400ms (within 300–500ms spec range).

const POLL_MS = 60_000;
const SSE_DEBOUNCE_MS = 400;

export const INBOX_YELLOW_MAX = 5;
export const INBOX_RED_AGE_HOURS = 48;

type BadgeColor = "green" | "yellow" | "red";

function resolveBadgeColor(
  count: number,
  oldestAgeHours: number | null,
): BadgeColor {
  if (count === 0) return "green";
  if (count > INBOX_YELLOW_MAX || (oldestAgeHours ?? 0) > INBOX_RED_AGE_HOURS)
    return "red";
  return "yellow";
}

const COLOR_CLASSES: Record<BadgeColor, string> = {
  green:
    "border-green-300 bg-green-50 text-green-700 hover:border-green-400 hover:text-green-900 dark:border-green-800 dark:bg-green-950/40 dark:text-green-400 dark:hover:border-green-700 dark:hover:text-green-200",
  yellow:
    "border-yellow-300 bg-yellow-50 text-yellow-700 hover:border-yellow-400 hover:text-yellow-900 dark:border-yellow-700 dark:bg-yellow-950/40 dark:text-yellow-400 dark:hover:border-yellow-600 dark:hover:text-yellow-200",
  red:
    "border-red-300 bg-red-50 text-red-700 hover:border-red-400 hover:text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-400 dark:hover:border-red-700 dark:hover:text-red-200",
};

type InboxState = {
  count: number;
  oldest_age_hours: number | null;
  by_project: UserPendingByProject[];
};

export function InboxBadge() {
  const [data, setData] = useState<InboxState | null>(null);
  const [showBreakdown, setShowBreakdown] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await getUserPending();
      setData({
        count: res.count,
        oldest_age_hours: res.oldest_age_hours,
        by_project: res.by_project,
      });
    } catch {
      // Keep last known value on transient error; next poll will recover.
    }
  }, []);

  // SSE-driven refresh with 400ms debounce per spec (300–500ms range).
  const debouncedRefresh = useCallback(() => {
    if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => void refresh(), SSE_DEBOUNCE_MS);
  }, [refresh]);

  const { connectionState } = useWildcardRowChanged({
    onTaskChange: debouncedRefresh,
    onProjectChange: debouncedRefresh,
  });

  // Initial fetch on mount.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Kanban #2111 Part 2 — fallback-only polling: fire only when SSE is not
  // open (connection lost / reconnecting). SSE already invalidates on events;
  // polling when SSE is healthy just double-fires. connectionState from the
  // shared WildcardSSEProvider (Part 1).
  useEffect(() => {
    if (connectionState === "open") return;
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh, connectionState]);

  // Cleanup debounce timer on unmount.
  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    };
  }, []);

  const count = data?.count ?? 0;
  const hasItems = data !== null && count > 0;
  const color = data !== null
    ? resolveBadgeColor(count, data.oldest_age_hours)
    : "green";
  const label = hasItems
    ? `${count} pending interaction task${count === 1 ? "" : "s"} — open Inbox`
    : "Inbox — no pending tasks";

  return (
    <div className="relative">
      <Link
        href="/inbox"
        title={label}
        aria-label={label}
        data-inbox-badge
        data-inbox-count={count}
        className={`relative inline-flex items-center justify-center rounded border px-2 py-1 text-xs font-medium transition-colors ${COLOR_CLASSES[color]}`}
        onMouseEnter={() => setShowBreakdown(true)}
        onMouseLeave={() => setShowBreakdown(false)}
        onFocus={() => setShowBreakdown(true)}
        onBlur={() => setShowBreakdown(false)}
      >
        Inbox
        {data !== null && (
          <span
            aria-hidden
            data-inbox-count-bubble
            className="ml-1.5 tabular-nums"
          >
            ({count})
          </span>
        )}
      </Link>

      {/* Hover/tap breakdown — per-project counts */}
      {showBreakdown && data !== null && data.by_project.length > 0 && (
        <div
          role="tooltip"
          aria-live="polite"
          className="absolute left-0 top-full z-50 mt-1 min-w-[160px] rounded border border-zinc-200 bg-white py-1.5 shadow-md dark:border-zinc-700 dark:bg-zinc-900"
        >
          {data.by_project.map((entry) => (
            <div
              key={entry.project_id}
              className="flex items-center justify-between gap-4 px-3 py-0.5 text-xs"
            >
              <span className="truncate text-zinc-700 dark:text-zinc-300">
                {entry.project_name}
              </span>
              <span className="shrink-0 tabular-nums font-medium text-zinc-900 dark:text-zinc-100">
                {entry.count}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Mobile tile (sm:hidden) — per-project breakdown visible at 375px */}
      {data !== null && data.by_project.length > 0 && (
        <div
          aria-label="Inbox breakdown by project"
          className="mt-2 w-full rounded border border-zinc-200 bg-zinc-50 sm:hidden dark:border-zinc-800 dark:bg-zinc-900/40"
        >
          <p className="border-b border-zinc-200 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:text-zinc-500">
            Pending by project
          </p>
          {data.by_project.map((entry) => (
            <div
              key={entry.project_id}
              className="flex items-center justify-between px-3 py-1.5 text-xs"
            >
              <span className="text-zinc-700 dark:text-zinc-300">
                {entry.project_name}
              </span>
              <span className="tabular-nums font-medium text-zinc-900 dark:text-zinc-100">
                {entry.count}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
