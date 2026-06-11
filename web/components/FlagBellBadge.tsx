"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listAuditFlags } from "@/lib/api";
import { useWildcardRowChanged } from "@/lib/WildcardSSEContext";

// Kanban #1212 (D5) / #1330 — header notification surface. Small bell icon
// with a red-dot + count badge when #1211 audit flags are open across any project.
// Click navigates to /review.
//
// Polling cadence: 60s baseline + SSE-driven invalidation. SSE catches the
// common case (resolve from another tab / mass action elsewhere) within a
// second; the 60s poll catches the API-restart / connection-lost edge.
// Embedded as a flex-child in each route's header right-cluster (#1330 —
// replaced the prior single fixed-positioned layout.tsx embed).

const POLL_MS = 60_000;

export function FlagBellBadge() {
  const [count, setCount] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const flags = await listAuditFlags();
      setCount(flags.length);
    } catch {
      // Don't blank the badge on a transient API error — keep the last known
      // value. The next poll will recover.
    }
  }, []);

  // Also revalidate on row_changed events so resolve actions in other tabs
  // update this badge immediately. Shared wildcard connection (Part 1 #2111).
  const { connectionState } = useWildcardRowChanged({
    onTaskChange: refresh,
    onProjectChange: refresh,
  });

  // Initial fetch on mount.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Kanban #2111 Part 2 — fallback-only polling: fire only when SSE is not
  // open. SSE already invalidates on events; polling when SSE is healthy
  // double-fires. connectionState from the shared WildcardSSEProvider.
  useEffect(() => {
    if (connectionState === "open") return;
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh, connectionState]);

  const hasFlags = count !== null && count > 0;
  const label = hasFlags
    ? `${count} open flag${count === 1 ? "" : "s"} — open Review`
    : "No open flags";

  return (
    <Link
      href="/review"
      title={label}
      aria-label={label}
      data-flag-bell-badge
      data-flag-count={count ?? 0}
      className="relative inline-flex items-center justify-center rounded border border-zinc-200 bg-white px-1.5 py-1 text-zinc-600 transition-colors hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
    >
      <span aria-hidden className="text-sm leading-none">
        🔔
      </span>
      {hasFlags && (
        <span
          aria-hidden
          className="absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white shadow"
          data-flag-bell-count
        >
          {count}
        </span>
      )}
    </Link>
  );
}
