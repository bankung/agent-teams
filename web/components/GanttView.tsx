"use client";

import { useMemo } from "react";
import Link from "next/link";

import type { MilestoneDetail } from "@/lib/api";
import {
  epochDay,
  epochDayToKey,
  monthTickLabel,
  startOfMonthEpochDay,
  nextMonthEpochDay,
  todayKey,
} from "@/lib/calendarDates";
import { MilestoneStatusBadge } from "./MilestoneStatusBadge";

// GanttView — milestone-level Gantt timeline (#1874 M3, v1).
//
// Server component (page.tsx) SSR-fetches every milestone WITH its rollup
// (mirrors the milestones page fan-out). This client view computes the time
// axis from min(start_date) → max(target_date) across all DATED milestones and
// positions one horizontal bar per milestone (start → target). A milestone with
// only a target_date renders a diamond at the deadline; a milestone with NO
// dates stays in the left rail with the bar area labeled "no dates".
//
// Tasks are NOT plotted (locked design — milestone-level only). The rail shows
// each milestone's task count (done/total) but no per-task bars.
//
// Geometry: CSS-positioned absolute divs over a min-width track; the track
// scrolls horizontally when the span is long. Bars use left% / width% so they
// reflow with the container — except the track has a px min-width floor (so a
// short single-day span doesn't collapse to a sliver).

// Layout constants.
const ROW_H = 44; // px per milestone row (rail + timeline aligned)
const AXIS_H = 28; // px axis header height
const PX_PER_DAY_MIN = 6; // min pixels per day → drives the track min-width
const TRACK_MIN_PX = 640; // absolute floor for the timeline track width

type Props = {
  projectName: string;
  milestones: MilestoneDetail[];
};

type DatedSpan = {
  milestone: MilestoneDetail;
  startDay: number | null; // epoch-day index; null = no start (diamond at target)
  endDay: number | null; // epoch-day index; null = undated
};

export function GanttView({ projectName, milestones }: Props) {
  const today = useMemo(() => todayKey(), []);
  const todayDay = useMemo(() => epochDay(today), [today]);

  // Resolve each milestone to its day-index span.
  const spans: DatedSpan[] = useMemo(
    () =>
      milestones.map((m) => ({
        milestone: m,
        startDay: epochDay(m.start_date),
        endDay: epochDay(m.target_date),
      })),
    [milestones],
  );

  // Axis domain: min start (or target when no start) → max target across all
  // dated milestones. Undated milestones contribute nothing to the domain.
  const domain = useMemo(() => {
    let min: number | null = null;
    let max: number | null = null;
    for (const s of spans) {
      const lo = s.startDay ?? s.endDay; // diamond-only uses its target as both ends
      const hi = s.endDay ?? s.startDay;
      if (lo != null) min = min == null ? lo : Math.min(min, lo);
      if (hi != null) max = max == null ? hi : Math.max(max, hi);
    }
    if (min == null || max == null) return null;
    // Pad the domain a touch so edge bars aren't flush against the frame, and
    // guard the zero-width case (single dated day) → 1-day span minimum.
    if (max <= min) max = min + 1;
    return { min: min - 1, max: max + 1 };
  }, [spans]);

  // Total day span → track width (px). Floor at TRACK_MIN_PX so short spans
  // still render a usable track.
  const totalDays = domain ? domain.max - domain.min : 0;
  const trackWidthPx = domain
    ? Math.max(TRACK_MIN_PX, totalDays * PX_PER_DAY_MIN)
    : TRACK_MIN_PX;

  // Day-index → percent across the domain (0..100).
  const pctOf = (day: number): number => {
    if (!domain || totalDays <= 0) return 0;
    return ((day - domain.min) / totalDays) * 100;
  };

  // Month tick marks across the domain (first-of-month boundaries).
  const monthTicks = useMemo(() => {
    if (!domain) return [];
    const ticks: { day: number; label: string }[] = [];
    let cursor = startOfMonthEpochDay(domain.min);
    // Guard against a runaway loop on absurd domains.
    let guard = 0;
    while (cursor <= domain.max && guard < 240) {
      if (cursor >= domain.min) {
        ticks.push({ day: cursor, label: monthTickLabel(epochDayToKey(cursor)) });
      }
      cursor = nextMonthEpochDay(cursor);
      guard++;
    }
    return ticks;
  }, [domain]);

  const milestonesHref = `/p/${encodeURIComponent(projectName)}/milestones`;

  if (milestones.length === 0) {
    return (
      <p
        className="rounded border border-dashed border-zinc-200 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
        data-gantt-empty
      >
        No milestones yet.{" "}
        <Link href={milestonesHref} className="underline hover:text-zinc-900 dark:hover:text-zinc-100">
          Create one
        </Link>{" "}
        to see it on the timeline.
      </p>
    );
  }

  return (
    <section data-gantt-view aria-label={`Gantt timeline for ${projectName}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {milestones.length} milestone{milestones.length === 1 ? "" : "s"}
        </h2>
        {!domain && (
          <span className="text-xs text-zinc-400 dark:text-zinc-500">
            No dated milestones — set start/target dates to populate the timeline.
          </span>
        )}
      </div>

      <div className="flex overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
        {/* ── Left rail — one row per milestone (fixed width). ───────────── */}
        <div className="w-56 shrink-0 border-r border-zinc-200 dark:border-zinc-800">
          {/* Rail header aligns with the timeline axis row. */}
          <div
            className="flex items-center border-b border-zinc-200 bg-zinc-50 px-3 text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
            style={{ height: AXIS_H }}
          >
            Milestone
          </div>
          {spans.map((s) => {
            const { rollup } = s.milestone;
            return (
              <div
                key={s.milestone.id}
                data-gantt-rail-row={s.milestone.id}
                className="flex flex-col justify-center gap-0.5 border-b border-zinc-100 px-3 last:border-b-0 dark:border-zinc-800/60"
                style={{ height: ROW_H }}
              >
                <div className="flex items-center gap-1.5">
                  <Link
                    href={milestonesHref}
                    className="truncate text-xs font-medium text-zinc-800 hover:underline dark:text-zinc-200"
                    title={s.milestone.title}
                  >
                    {s.milestone.title}
                  </Link>
                  <MilestoneStatusBadge status={s.milestone.milestone_status} />
                </div>
                <span className="text-[10px] text-zinc-500 tabular-nums dark:text-zinc-400">
                  {rollup.done}/{rollup.total} done · {rollup.progress_pct.toFixed(0)}%
                </span>
              </div>
            );
          })}
        </div>

        {/* ── Right timeline — horizontally scrollable track. ───────────── */}
        <div className="min-w-0 flex-1 overflow-x-auto">
          <div
            data-gantt-track
            className="relative"
            style={{ width: domain ? trackWidthPx : "100%", minWidth: TRACK_MIN_PX }}
          >
            {/* Axis header: month tick labels + boundary lines. */}
            <div
              className="relative border-b border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900"
              style={{ height: AXIS_H }}
            >
              {domain &&
                monthTicks.map((tk) => (
                  <div
                    key={tk.day}
                    className="absolute top-0 flex h-full items-center"
                    style={{ left: `${pctOf(tk.day)}%` }}
                  >
                    <span className="border-l border-zinc-200 pl-1 text-[10px] text-zinc-500 dark:border-zinc-700 dark:text-zinc-400 whitespace-nowrap">
                      {tk.label}
                    </span>
                  </div>
                ))}
              {!domain && (
                <span className="flex h-full items-center pl-3 text-[11px] text-zinc-400 dark:text-zinc-500">
                  No timeline (no dated milestones)
                </span>
              )}
            </div>

            {/* Body: month gridlines + today line + one bar/diamond per row. */}
            <div className="relative">
              {/* Month gridlines spanning the full body height. */}
              {domain &&
                monthTicks.map((tk) => (
                  <div
                    key={`grid-${tk.day}`}
                    aria-hidden
                    className="absolute top-0 bottom-0 w-px bg-zinc-100 dark:bg-zinc-800/60"
                    style={{ left: `${pctOf(tk.day)}%` }}
                  />
                ))}

              {/* Today line (only when in-domain). */}
              {domain &&
                todayDay != null &&
                todayDay >= domain.min &&
                todayDay <= domain.max && (
                  <div
                    aria-hidden
                    data-gantt-today-line
                    className="absolute top-0 bottom-0 z-10 w-px bg-sky-500 dark:bg-sky-400"
                    style={{ left: `${pctOf(todayDay)}%` }}
                  >
                    <span className="absolute -top-0 left-0.5 rounded-sm bg-sky-500 px-1 text-[9px] font-semibold text-white dark:bg-sky-400 dark:text-zinc-900">
                      today
                    </span>
                  </div>
                )}

              {spans.map((s) => (
                <div
                  key={s.milestone.id}
                  data-gantt-row={s.milestone.id}
                  className="relative border-b border-zinc-100 last:border-b-0 dark:border-zinc-800/60"
                  style={{ height: ROW_H }}
                >
                  {renderBar(s, domain, pctOf)}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// renderBar — the bar / diamond / "no dates" content for one timeline row.
function renderBar(
  s: DatedSpan,
  domain: { min: number; max: number } | null,
  pctOf: (day: number) => number,
): React.ReactNode {
  const { milestone } = s;

  // No dates at all → label in the bar lane (kept on the rail per the AC).
  if (s.startDay == null && s.endDay == null) {
    return (
      <span
        data-gantt-nodates={milestone.id}
        className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] italic text-zinc-400 dark:text-zinc-500"
      >
        no dates
      </span>
    );
  }

  // Domain should exist whenever at least one milestone is dated; guard anyway.
  if (!domain) return null;

  // Target-only (no start) → diamond at the deadline.
  if (s.startDay == null && s.endDay != null) {
    return (
      <span
        data-gantt-diamond={milestone.id}
        title={`${milestone.title} — target ${milestone.target_date}`}
        className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] bg-violet-500 dark:bg-violet-400"
        style={{ left: `${pctOf(s.endDay)}%` }}
      />
    );
  }

  // Start-only (no target) → diamond at start (degenerate but supported).
  if (s.startDay != null && s.endDay == null) {
    return (
      <span
        data-gantt-diamond={milestone.id}
        title={`${milestone.title} — start ${milestone.start_date} (no target)`}
        className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] bg-amber-500 dark:bg-amber-400"
        style={{ left: `${pctOf(s.startDay)}%` }}
      />
    );
  }

  // Full bar: start → target. Both non-null here.
  const left = pctOf(s.startDay as number);
  const right = pctOf(s.endDay as number);
  const width = Math.max(right - left, 0.6); // min visible width for 1-day spans
  const released = milestone.milestone_status === "released";
  const cancelled = milestone.milestone_status === "cancelled";
  const barColor = cancelled
    ? "bg-red-400/70 dark:bg-red-500/50"
    : released
      ? "bg-emerald-500 dark:bg-emerald-400"
      : "bg-sky-500 dark:bg-sky-400";

  return (
    <div
      data-gantt-bar={milestone.id}
      title={`${milestone.title}: ${milestone.start_date} → ${milestone.target_date}`}
      className={`absolute top-1/2 flex h-5 -translate-y-1/2 items-center overflow-hidden rounded px-1 ${barColor}`}
      style={{ left: `${left}%`, width: `${width}%` }}
    >
      <span className="truncate text-[10px] font-medium text-white">
        {milestone.title}
      </span>
    </div>
  );
}
