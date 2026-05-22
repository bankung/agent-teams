"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listProjects, listTasks } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { useRowChangedEvents } from "@/lib/useRowChangedEvents";

// Kanban #1003 phase 1 — cross-project HITL-waiting count badge.
// HITL-waiting = interaction_kind in ('question','decision') AND
// process_status not in (DONE=5, CANCELLED=6) AND status=1 (active).
// Client-side aggregate: fetch active projects → per-project tasks → filter.
// Polling 60s + SSE row_changed invalidation (mirrors FlagBellBadge pattern).

const POLL_MS = 60_000;

async function fetchHitlCount(): Promise<number> {
  const projects = await listProjects({ status: 1 });
  const counts = await Promise.all(
    projects.map(async (project) => {
      try {
        const tasks = await listTasks(project.id, { pending: true, limit: 500 });
        return tasks.filter(
          (t) =>
            t.interaction_kind !== "work" &&
            t.process_status !== TaskStatus.DONE &&
            t.process_status !== TaskStatus.CANCELLED,
        ).length;
      } catch {
        return 0;
      }
    }),
  );
  return counts.reduce((sum, n) => sum + n, 0);
}

export function InboxBadge() {
  const [count, setCount] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const n = await fetchHitlCount();
      setCount(n);
    } catch {
      // Keep last known value on transient error; next poll will recover.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  useRowChangedEvents({
    onTaskChange: refresh,
    onProjectChange: refresh,
  });

  const hasItems = count !== null && count > 0;
  const label = hasItems
    ? `${count} pending interaction task${count === 1 ? "" : "s"} — open Inbox`
    : "Inbox — no pending tasks";

  return (
    <Link
      href="/inbox"
      title={label}
      aria-label={label}
      data-inbox-badge
      data-inbox-count={count ?? 0}
      className="relative inline-flex items-center justify-center rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium text-zinc-600 transition-colors hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
    >
      Inbox
      {count !== null && (
        <span
          aria-hidden
          data-inbox-count-bubble
          className={
            hasItems
              ? "ml-1.5 tabular-nums"
              : "ml-1.5 tabular-nums text-zinc-400 dark:text-zinc-600"
          }
        >
          ({count})
        </span>
      )}
    </Link>
  );
}
