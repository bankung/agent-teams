import Link from "next/link";

import type { AuditFlagWithProject } from "@/lib/api";

// Kanban #1212 GOV4 (D5) — dashboard widget summarizing open GOV3 audit flags.
// Server Component (no `"use client"`); the parent dashboard page fetches
// the list once + passes it down so the widget shares the SSR round-trip
// instead of double-fetching.
//
// Empty state intentionally hidden — when there are zero flags, the widget
// renders null so the dashboard isn't crowded with a "nothing to do" tile.
// The bell badge in app/layout.tsx already covers the "no flags" UX.

type Props = {
  flags: AuditFlagWithProject[];
};

export function ReviewSummaryWidget({ flags }: Props) {
  if (flags.length === 0) return null;
  const projectIds = new Set(flags.map((f) => f.project.id));

  return (
    <section
      data-review-summary-widget
      aria-label="Open audit flags requiring operator review"
      className="mb-5 flex flex-wrap items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-700 dark:bg-amber-900/20"
    >
      <span aria-hidden className="text-2xl leading-none">
        ⚠
      </span>
      <div className="flex min-w-0 flex-col">
        <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">
          {flags.length} flag{flags.length === 1 ? "" : "s"} open across{" "}
          {projectIds.size} project{projectIds.size === 1 ? "" : "s"}
        </p>
        <p className="text-xs text-amber-800/80 dark:text-amber-300/80">
          Operator decisions pending: continue / adjust / keep paused /
          terminate.
        </p>
      </div>
      <Link
        href="/review"
        className="ml-auto rounded border border-amber-600 bg-amber-500 px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-white hover:bg-amber-600 dark:border-amber-500 dark:bg-amber-600 dark:hover:bg-amber-700"
        data-review-summary-link
      >
        Open Review →
      </Link>
    </section>
  );
}
