"use client";

// TaskActivityStrip — Kanban #2334.
// Renders inside IN_PROGRESS TaskCards only.  Three behaviours:
//   1. Activity strip — newest ≤3 rail rows; hidden when zero rows.
//   2. Running/idle dot — animated pulse when newest row ≤5 min old; muted otherwise.
//   3. Polling — refetches every ~10s; pauses while document.hidden.

import { useEffect, useRef, useState } from "react";

import { getTaskToolCalls, type ToolCallRead } from "@/lib/api";
import { KIND_CLASS } from "@/lib/leadEventKinds";
import { formatRelative } from "@/lib/time";

// Threshold for "running" vs "idle" state: 5 minutes in ms.
const RUNNING_THRESHOLD_MS = 5 * 60 * 1000;
// Polling interval: ~10 seconds.
const POLL_INTERVAL_MS = 10_000;

type ActivityState = "running" | "idle";

function ageMs(iso: string): number {
  return Math.max(0, Date.now() - Date.parse(iso));
}

function deriveState(rows: ToolCallRead[] | null): ActivityState {
  if (!rows || rows.length === 0) return "idle";
  const newest = rows[0];
  return ageMs(newest.invoked_at) <= RUNNING_THRESHOLD_MS ? "running" : "idle";
}

type Props = {
  projectId: number;
  taskId: number;
};

export function TaskActivityStrip({ projectId, taskId }: Props) {
  // `null` = first fetch not yet resolved (SSR/initial default → idle).
  const [rows, setRows] = useState<ToolCallRead[] | null>(null);
  const [activityState, setActivityState] = useState<ActivityState>("idle");
  const cancelledRef = useRef(false);

  // Fetch helper — updates rows + derived state.
  function fetchRows() {
    if (cancelledRef.current) return;
    getTaskToolCalls(projectId, taskId, 3)
      .then((data) => {
        if (cancelledRef.current) return;
        setRows(data);
        setActivityState(deriveState(data));
      })
      .catch(() => {
        // Silently ignore errors in the card strip — the detail drawer
        // already surfaces full error state if the operator opens it.
      });
  }

  useEffect(() => {
    cancelledRef.current = false;

    // Initial fetch.
    fetchRows();

    // Polling: tick every POLL_INTERVAL_MS; skip while document is hidden.
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      fetchRows();
    }, POLL_INTERVAL_MS);

    // Pause/resume on visibility change.
    function onVisibility() {
      if (!document.hidden) fetchRows();
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      cancelledRef.current = true;
      clearInterval(id);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchRows closes over current props; [projectId, taskId] effect dep is the real guard. #2334
  }, [projectId, taskId]);

  const isRunning = activityState === "running";

  // Dot classes: animate-pulse when running; static muted when idle.
  // motion-reduce:animate-none respects prefers-reduced-motion.
  const dotClass = isRunning
    ? "h-2 w-2 rounded-full bg-emerald-500 animate-pulse motion-reduce:animate-none dark:bg-emerald-400"
    : "h-2 w-2 rounded-full bg-zinc-300 dark:bg-zinc-600";

  // Only show strip rows when we have data and it's non-empty.
  const hasRows = rows !== null && rows.length > 0;

  return (
    <div data-activity-strip className="mt-2">
      {/* Running/idle dot — always rendered from first paint (idle default). */}
      <span
        data-activity-state={activityState}
        aria-label={activityState}
        role="img"
        className="inline-block"
      >
        <span className={dotClass} aria-hidden />
      </span>

      {/* Activity rows — only when non-empty. */}
      {hasRows && (
        <ol className="mt-1 flex flex-col gap-0.5" data-activity-rows>
          {rows!.map((row) => (
            <ActivityRow key={row.id} row={row} />
          ))}
        </ol>
      )}
    </div>
  );
}

// Compact one-liner per rail row.
function ActivityRow({ row }: { row: ToolCallRead }) {
  const isLead = row.source === "lead";

  // Chip label + colour.
  const chipLabel = isLead
    ? (row.kind ?? "note")
    : (row.tool_name.length > 18 ? row.tool_name.slice(0, 16) + "…" : row.tool_name);

  const chipClass = isLead
    ? `inline-flex items-center rounded px-1 py-0.5 font-mono text-[9px] font-medium uppercase tracking-wide ${
        row.kind ? (KIND_CLASS[row.kind] ?? KIND_CLASS.note) : KIND_CLASS.note
      }`
    : "inline-flex items-center rounded px-1 py-0.5 font-mono text-[9px] font-medium uppercase tracking-wide bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";

  // Summary text (lead) or tool name (engine).
  const summaryText = isLead
    ? (row.summary ?? row.tool_name)
    : row.tool_name;

  return (
    <li className="flex items-center gap-1.5 overflow-hidden" data-activity-row>
      <span className={chipClass} aria-hidden>{chipLabel}</span>
      <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-600 dark:text-zinc-400">
        {summaryText}
      </span>
      <span
        className="shrink-0 font-mono text-[10px] tabular-nums text-zinc-400 dark:text-zinc-500"
        title={row.invoked_at}
      >
        {formatRelative(row.invoked_at)}
      </span>
    </li>
  );
}
