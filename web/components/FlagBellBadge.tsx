"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listAuditFlags } from "@/lib/api";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";

// Kanban #1212 AA4 (D5) — header notification surface. Small bell icon with a
// red-dot + count badge when AA3 audit flags are open across any project.
// Click navigates to /review.
//
// Polling cadence: 60s baseline + SSE-driven invalidation. SSE catches the
// common case (resolve from another tab / mass action elsewhere) within a
// second; the 60s poll catches the API-restart / connection-lost edge.
// Single fixed-positioned element so it doesn't depend on a header
// component being present on every route — embed once in app/layout.tsx
// and it shows on every page.

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

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // Also revalidate on row_changed events so resolve actions in other tabs
  // update this badge immediately. Wildcard subscription (no projectId).
  useRowChangedEvents({
    onTaskChange: refresh,
    onProjectChange: refresh,
  });

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
      className="fixed right-3 top-3 z-40 inline-flex h-9 w-9 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-700 shadow-sm transition-colors hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
    >
      <span aria-hidden className="text-lg leading-none">
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
