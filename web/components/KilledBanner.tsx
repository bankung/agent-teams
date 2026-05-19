"use client";

import { useEffect, useState } from "react";

import type { ProjectRead } from "@/lib/api";
import { KillProjectModal } from "@/components/KillProjectModal";

// Kanban #1209 AA1 (D5 step 5) — red strip rendered at the top of the board
// when `project.is_killed=true`. Shows when the project was killed + the
// reason captured in the audit row, and surfaces a Revive trigger inline.
//
// Client component because of the "X minutes ago" relative-time render —
// re-computes once per minute against `killed_at`. The Revive trigger
// (`KillProjectModal mode="revive"`) is already Client.

type Props = {
  project: ProjectRead;
};

// formatTimeAgo — coarse human-readable delta. We round to the nearest unit
// (seconds → minutes → hours → days) and prefer "just now" for sub-30s
// freshness. ISO timestamp parsing tolerates any TZ-aware string the BE
// emits (Pydantic mode='json' default: full ISO with offset).
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

export function KilledBanner({ project }: Props) {
  // Re-render once per minute to keep the relative time fresh. SSR-safe:
  // initial state is `Date.now()` on the server, the effect kicks in
  // post-hydration.
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);

  if (!project.is_killed) return null;

  const ago = project.killed_at ? formatTimeAgo(project.killed_at, now) : "unknown";
  const reason = project.killed_reason?.trim() || "(no reason recorded)";

  return (
    <div
      className="flex flex-col items-start gap-2 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-700 dark:bg-red-900/30 dark:text-red-300 sm:flex-row sm:items-center sm:justify-between"
      role="alert"
      data-killed-banner
    >
      <div className="min-w-0 flex-1">
        <span className="font-medium">
          ⚠ Project killed {ago}
          {project.killed_at && (
            <span className="ml-1 font-mono text-[10px] opacity-70">
              ({project.killed_at.slice(0, 19).replace("T", " ")})
            </span>
          )}
          :
        </span>{" "}
        <span className="break-words">{reason}</span>
      </div>
      <div className="shrink-0">
        <KillProjectModal
          project={project}
          mode="revive"
          triggerLabel="Revive project"
          triggerClassName="inline-flex items-center rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
        />
      </div>
    </div>
  );
}
