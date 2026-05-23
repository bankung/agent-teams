"use client";

import { useEffect, useState } from "react";

import type { ProjectRead } from "@/lib/api";
import { PauseProjectModal } from "@/components/PauseProjectModal";

// Kanban #1211 / #1238 GOV3 (FE) — amber strip rendered at the top of the
// board when `project.is_paused=true`. Mirror of KilledBanner (GOV1) but
// amber/yellow themed because soft-pause is a warning, not an error.
//
// Client component because of the relative-time render — re-computes once
// per minute against `paused_at` (same pattern as KilledBanner). The
// Unpause trigger (`PauseProjectModal mode="unpause"`) is already Client.

type Props = {
  project: ProjectRead;
};

// formatTimeAgo — coarse human-readable delta. Mirror of the helper in
// KilledBanner.tsx (same algorithm, same edge cases). Kept inline rather
// than promoted to a shared util because the delta formatter is one of
// two callers today and lifting it would balloon a new module for a 12-LOC
// function. If a third caller appears, extract to web/lib/time.ts.
function formatTimeAgo(iso: string, now: number): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const deltaSec = Math.max(0, Math.floor((now - then) / 1000));
  if (deltaSec < 30) return "just now";
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const mins = Math.floor(deltaSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function PausedBanner({ project }: Props) {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);

  if (!project.is_paused) return null;

  const ago = project.paused_at ? formatTimeAgo(project.paused_at, now) : "unknown";
  const reason = project.paused_reason?.trim() || "(no reason recorded)";

  return (
    <div
      className="flex flex-col items-start gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-200 sm:flex-row sm:items-center sm:justify-between"
      role="status"
      data-paused-banner
    >
      <div className="min-w-0 flex-1">
        <span className="font-medium">
          ⏸ Project paused {ago}
          {project.paused_at && (
            <span className="ml-1 font-mono text-[10px] opacity-70">
              ({project.paused_at.slice(0, 19).replace("T", " ")})
            </span>
          )}
          :
        </span>{" "}
        <span className="break-words">{reason}</span>
      </div>
      <div className="shrink-0">
        <PauseProjectModal
          project={project}
          mode="unpause"
          triggerLabel="Unpause project"
          triggerClassName="inline-flex items-center rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
        />
      </div>
    </div>
  );
}
