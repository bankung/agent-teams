"use client";

// ProgressChartsPanel — Kanban #1292 (FE). Per-project burndown + velocity
// charts.
//
// Render site: web/components/Board.tsx header, after the Usage / P&L panels
// grid, before the KilledBanner.
//
// Data: SSR-fetched in web/app/p/[name]/page.tsx (Promise.all) via
// getProjectProgressStats — passed in as the `data` prop. v1 uses the single
// SSR payload (default bucket=week, days=90); there is NO client-side bucket
// toggle / range refetch in this slice (deliberately out of scope — would add
// a client fetch).
//
// Charts are HAND-ROLLED inline SVG + Tailwind — the repo has no chart library
// and we are NOT adding one (avoids npm install + container rebuild + bundle
// bloat). currentColor + Tailwind `dark:` classes carry dark-mode theming.
//
// Two views per series:
//   - mini  (in the panel): compact, no axis labels, no tooltip, click-to-open.
//   - full  (in the modal):  axis labels (x = dates, y = counts) + a state-
//                            driven tooltip (works on mobile tap, not just an
//                            SVG <title>) per AC#3.
//
// Empty state (AC#5): the endpoint always returns zero-filled buckets. When
// EVERY remaining AND EVERY completed is 0 (no task activity) we render a
// clean "No activity yet" message instead of flat-zero axes.
//
// Scaling: y-scale is computed from the series max; max===0 is guarded (we
// never divide by zero — denominators fall back to 1) so the SVG path never
// emits NaN coordinates.

import { useId, useMemo, useState } from "react";

import type {
  BurndownPoint,
  ProgressStatsResponse,
  VelocityPoint,
} from "@/lib/api";
import { ModalShell } from "@/components/ModalShell";

// ---- geometry constants -----------------------------------------------------
//
// SVGs scale to their container via a fixed viewBox + width="100%". The mini
// charts target ~100px tall; the modal charts are taller with room for axis
// labels in the bottom + left gutters.

const MINI = {
  vbWidth: 320,
  vbHeight: 100,
  padX: 6,
  padTop: 8,
  padBottom: 8,
} as const;

const FULL = {
  vbWidth: 720,
  vbHeight: 320,
  padLeft: 40, // y-axis label gutter
  padRight: 12,
  padTop: 16,
  padBottom: 40, // x-axis label gutter
} as const;

type ChartKind = "burndown" | "velocity";

// ---- scale helpers ----------------------------------------------------------

// niceMax — y-axis top. Guards max===0 (returns 1 so the axis renders a single
// "0..1" range and no coordinate divides by zero).
function niceMax(values: number[]): number {
  const m = values.reduce((acc, v) => (v > acc ? v : acc), 0);
  return m > 0 ? m : 1;
}

// xAt — evenly-spaced x position for bucket index i across the plot width.
// Single-point series (n===1) is centered (avoid divide-by-zero on n-1).
function xAt(
  i: number,
  n: number,
  left: number,
  plotW: number,
): number {
  if (n <= 1) return left + plotW / 2;
  return left + (plotW * i) / (n - 1);
}

// yAt — value mapped to a y coordinate (SVG y grows downward, so invert).
function yAt(
  value: number,
  max: number,
  top: number,
  plotH: number,
): number {
  const frac = max > 0 ? value / max : 0;
  return top + plotH * (1 - frac);
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

// formatDate — "2026-03-02" → "Mar 2" for compact axis labels. Falls back to
// the raw string if it doesn't parse (defensive; the contract is "YYYY-MM-DD").
function formatDate(t: string): string {
  const parts = t.split("-");
  if (parts.length !== 3) return t;
  const [y, mo, d] = parts.map((p) => Number.parseInt(p, 10));
  if (!y || !mo || !d) return t;
  return `${MONTHS[mo - 1] ?? mo} ${d}`;
}

// ============================================================================
// Burndown — area + line of `remaining` over `t`.
// ============================================================================

function BurndownChart({
  points,
  mode,
  hoverIdx,
  onHover,
  miniHeight,
}: {
  points: BurndownPoint[];
  mode: "mini" | "full";
  hoverIdx?: number | null;
  onHover?: (i: number | null) => void;
  miniHeight?: number;
}) {
  const cfg = mode === "mini" ? MINI : FULL;
  const left = mode === "mini" ? MINI.padX : FULL.padLeft;
  const right = mode === "mini" ? MINI.padX : FULL.padRight;
  const plotW = cfg.vbWidth - left - right;
  const plotH = cfg.vbHeight - cfg.padTop - cfg.padBottom;
  const n = points.length;
  const max = niceMax(points.map((p) => p.remaining));

  const coords = points.map((p, i) => ({
    x: xAt(i, n, left, plotW),
    y: yAt(p.remaining, max, cfg.padTop, plotH),
  }));

  const linePath =
    coords.length > 0
      ? coords
          .map((c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`)
          .join(" ")
      : "";
  // Area = line + close down to the baseline.
  const baseline = cfg.padTop + plotH;
  const areaPath =
    coords.length > 0
      ? `${linePath} L ${coords[coords.length - 1].x.toFixed(2)} ${baseline.toFixed(2)} L ${coords[0].x.toFixed(2)} ${baseline.toFixed(2)} Z`
      : "";

  return (
    <SvgFrame
      cfg={cfg}
      mode={mode}
      max={max}
      points={points.map((p) => ({ t: p.t, value: p.remaining }))}
      onHover={onHover}
      left={left}
      plotW={plotW}
      miniHeight={miniHeight}
    >
      {/* sky-500 line; indigo for the burndown family */}
      <path
        d={areaPath}
        className="fill-sky-500/15 dark:fill-sky-400/15"
        stroke="none"
      />
      <path
        d={linePath}
        className="stroke-sky-600 dark:stroke-sky-400"
        strokeWidth={mode === "mini" ? 1.5 : 2}
        fill="none"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {/* hover dot (full mode only) */}
      {mode === "full" &&
        hoverIdx != null &&
        coords[hoverIdx] != null && (
          <circle
            cx={coords[hoverIdx].x}
            cy={coords[hoverIdx].y}
            r={4}
            className="fill-sky-600 dark:fill-sky-400"
          />
        )}
    </SvgFrame>
  );
}

// ============================================================================
// Velocity — vertical bars of `completed` per `t`.
// ============================================================================

function VelocityChart({
  points,
  mode,
  hoverIdx,
  onHover,
  miniHeight,
}: {
  points: VelocityPoint[];
  mode: "mini" | "full";
  hoverIdx?: number | null;
  onHover?: (i: number | null) => void;
  miniHeight?: number;
}) {
  const cfg = mode === "mini" ? MINI : FULL;
  const left = mode === "mini" ? MINI.padX : FULL.padLeft;
  const right = mode === "mini" ? MINI.padX : FULL.padRight;
  const plotW = cfg.vbWidth - left - right;
  const plotH = cfg.vbHeight - cfg.padTop - cfg.padBottom;
  const n = points.length;
  const max = niceMax(points.map((p) => p.completed));
  const baseline = cfg.padTop + plotH;

  // Bar slot width; gap is a fraction of the slot. Single-bar series gets a
  // sensible fixed width rather than the whole plot.
  const slot = n > 0 ? plotW / n : plotW;
  const barW = Math.max(1, slot * (mode === "mini" ? 0.6 : 0.55));

  return (
    <SvgFrame
      cfg={cfg}
      mode={mode}
      max={max}
      points={points.map((p) => ({ t: p.t, value: p.completed }))}
      onHover={onHover}
      left={left}
      plotW={plotW}
      miniHeight={miniHeight}
    >
      {points.map((p, i) => {
        const cx = left + slot * (i + 0.5);
        const y = yAt(p.completed, max, cfg.padTop, plotH);
        const h = Math.max(0, baseline - y);
        const active = hoverIdx === i;
        return (
          <rect
            key={p.t}
            x={cx - barW / 2}
            y={y}
            width={barW}
            height={h}
            rx={mode === "mini" ? 0.5 : 1.5}
            className={
              active
                ? "fill-emerald-700 dark:fill-emerald-300"
                : "fill-emerald-500/80 dark:fill-emerald-400/70"
            }
          />
        );
      })}
    </SvgFrame>
  );
}

// ============================================================================
// SvgFrame — shared <svg> chrome: viewBox, baseline, optional axis labels +
// gridlines (full mode), and an invisible hover-capture overlay (full mode,
// state-driven so the tooltip works on mobile tap per AC#3).
// ============================================================================

function SvgFrame({
  cfg,
  mode,
  max,
  points,
  onHover,
  left,
  plotW,
  miniHeight = 100,
  children,
}: {
  cfg: typeof MINI | typeof FULL;
  mode: "mini" | "full";
  max: number;
  points: { t: string; value: number }[];
  onHover?: (i: number | null) => void;
  left: number;
  plotW: number;
  // #1781 — compact strip renders the mini chart shorter than the standalone
  // panel's 100px. Ignored in full (modal) mode.
  miniHeight?: number;
  children: React.ReactNode;
}) {
  const n = points.length;
  const baseline = cfg.padTop + (cfg.vbHeight - cfg.padTop - cfg.padBottom);

  // x-axis tick subset for full mode — show ~6 labels max to avoid overlap.
  const tickEvery = mode === "full" && n > 6 ? Math.ceil(n / 6) : 1;

  return (
    <svg
      viewBox={`0 0 ${cfg.vbWidth} ${cfg.vbHeight}`}
      width="100%"
      height={mode === "mini" ? miniHeight : 320}
      preserveAspectRatio="none"
      aria-hidden={true}
      className="text-zinc-400 dark:text-zinc-500"
    >
      {/* baseline */}
      <line
        x1={left}
        y1={baseline}
        x2={cfg.vbWidth - (mode === "mini" ? MINI.padX : FULL.padRight)}
        y2={baseline}
        stroke="currentColor"
        strokeWidth={0.75}
        className="text-zinc-300 dark:text-zinc-700"
      />

      {/* full-mode axes: y gridlines + labels, x date labels */}
      {mode === "full" && (
        <FullAxes
          cfg={cfg as typeof FULL}
          max={max}
          points={points}
          left={left}
          plotW={plotW}
          tickEvery={tickEvery}
        />
      )}

      {children}

      {/* full-mode hover capture — invisible rect grid; pointer + click both
          set the state-driven hover index so it works on mobile tap (AC#3). */}
      {mode === "full" &&
        onHover &&
        points.map((p, i) => {
          const slot = n > 0 ? plotW / n : plotW;
          return (
            <rect
              key={`hit-${p.t}`}
              x={left + slot * i}
              y={cfg.padTop}
              width={slot}
              height={cfg.vbHeight - cfg.padTop - cfg.padBottom}
              fill="transparent"
              onMouseEnter={() => onHover(i)}
              onMouseLeave={() => onHover(null)}
              onClick={() => onHover(i)}
              style={{ cursor: "pointer" }}
            />
          );
        })}
    </svg>
  );
}

// FullAxes — y gridlines (0, max/2, max) + y count labels + x date labels.
function FullAxes({
  cfg,
  max,
  points,
  left,
  plotW,
  tickEvery,
}: {
  cfg: typeof FULL;
  max: number;
  points: { t: string; value: number }[];
  left: number;
  plotW: number;
  tickEvery: number;
}) {
  const plotH = cfg.vbHeight - cfg.padTop - cfg.padBottom;
  const baseline = cfg.padTop + plotH;
  const n = points.length;

  // y ticks at 0, 50%, 100% of max (rounded for the labels).
  const yTicks = [0, 0.5, 1].map((frac) => ({
    frac,
    value: Math.round(max * frac),
    y: cfg.padTop + plotH * (1 - frac),
  }));

  return (
    <g>
      {yTicks.map((tk) => (
        <g key={`y-${tk.frac}`}>
          <line
            x1={left}
            y1={tk.y}
            x2={cfg.vbWidth - cfg.padRight}
            y2={tk.y}
            stroke="currentColor"
            strokeWidth={0.5}
            strokeDasharray={tk.frac === 0 ? "0" : "3 3"}
            className="text-zinc-200 dark:text-zinc-800"
          />
          <text
            x={left - 6}
            y={tk.y + 3}
            textAnchor="end"
            fontSize={11}
            className="fill-zinc-500 dark:fill-zinc-400"
          >
            {tk.value}
          </text>
        </g>
      ))}
      {points.map((p, i) => {
        if (i % tickEvery !== 0 && i !== n - 1) return null;
        const x = n <= 1 ? left + plotW / 2 : left + (plotW * i) / (n - 1);
        return (
          <text
            key={`x-${p.t}`}
            x={x}
            y={baseline + 16}
            textAnchor="middle"
            fontSize={11}
            className="fill-zinc-500 dark:fill-zinc-400"
          >
            {formatDate(p.t)}
          </text>
        );
      })}
    </g>
  );
}

// ============================================================================
// MiniChartCard — clickable button wrapping a mini chart. Opens the modal.
// ============================================================================

function MiniChartCard({
  title,
  latest,
  latestLabel,
  onOpen,
  compact = false,
  children,
}: {
  title: string;
  latest: number;
  latestLabel: string;
  onOpen: () => void;
  // #1781 — compact strip: tighter padding, no "click to expand" hint line.
  compact?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group flex flex-col gap-1 rounded-md border border-zinc-200 bg-white/70 text-left transition hover:border-zinc-300 hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 dark:border-zinc-800 dark:bg-zinc-950/40 dark:hover:border-zinc-700 ${compact ? "p-2" : "p-3"}`}
      aria-label={`${title} — latest ${latest} ${latestLabel}; click to expand`}
    >
      <span className="flex items-baseline justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {title}
        </span>
        <span className="text-xs tabular-nums text-zinc-600 dark:text-zinc-300">
          <span className="font-semibold">{latest}</span>{" "}
          <span className="text-zinc-400 dark:text-zinc-500">{latestLabel}</span>
        </span>
      </span>
      <span className="block w-full">{children}</span>
      {!compact && (
        <span className="text-[10px] text-zinc-400 opacity-0 transition group-hover:opacity-100 dark:text-zinc-500">
          Click to expand ↗
        </span>
      )}
    </button>
  );
}

// ============================================================================
// ExpandedChart — full-size chart in the modal + state-driven tooltip readout.
// ============================================================================

function ExpandedChart({
  kind,
  data,
}: {
  kind: ChartKind;
  data: ProgressStatsResponse;
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const point =
    hoverIdx != null
      ? kind === "burndown"
        ? {
            t: data.burndown[hoverIdx]?.t,
            value: data.burndown[hoverIdx]?.remaining,
            label: "remaining",
          }
        : {
            t: data.velocity[hoverIdx]?.t,
            value: data.velocity[hoverIdx]?.completed,
            label: "completed",
          }
      : null;

  return (
    <div>
      {/* Tooltip readout — state-driven so it works on mobile tap (AC#3). */}
      <div
        className="mb-2 flex h-6 items-center text-sm"
        aria-live="polite"
        role="status"
      >
        {point && point.t ? (
          <span className="text-zinc-700 dark:text-zinc-200">
            <span className="font-medium">{point.t}</span>
            {" · "}
            <span className="font-semibold tabular-nums">{point.value}</span>{" "}
            <span className="text-zinc-500 dark:text-zinc-400">
              {point.label}
            </span>
          </span>
        ) : (
          <span className="text-zinc-400 dark:text-zinc-500">
            Hover or tap a {kind === "velocity" ? "bar" : "point"} for details
          </span>
        )}
      </div>
      <div className="w-full">
        {kind === "burndown" ? (
          <BurndownChart
            points={data.burndown}
            mode="full"
            hoverIdx={hoverIdx}
            onHover={setHoverIdx}
          />
        ) : (
          <VelocityChart
            points={data.velocity}
            mode="full"
            hoverIdx={hoverIdx}
            onHover={setHoverIdx}
          />
        )}
      </div>
    </div>
  );
}

// ============================================================================
// ProgressChartsPanel — the exported panel.
// ============================================================================

export function ProgressChartsPanel({
  data,
  projectId,
  compact = false,
}: {
  data: ProgressStatsResponse;
  projectId: number;
  // #1781 — compact strip variant for the header panels band. Mini charts are
  // shorter + stacked in this single grid cell; the click→full modal is
  // unchanged. Default false preserves the standalone tall-panel render.
  compact?: boolean;
}) {
  const headingId = useId();
  const modalHeadingId = useId();
  const [openKind, setOpenKind] = useState<ChartKind | null>(null);

  // Empty state (AC#5): every remaining AND every completed is 0.
  const isEmpty = useMemo(() => {
    const noRemaining = data.burndown.every((p) => p.remaining === 0);
    const noCompleted = data.velocity.every((p) => p.completed === 0);
    return noRemaining && noCompleted;
  }, [data]);

  const latestRemaining =
    data.burndown.length > 0
      ? data.burndown[data.burndown.length - 1].remaining
      : 0;
  const latestCompleted =
    data.velocity.length > 0
      ? data.velocity[data.velocity.length - 1].completed
      : 0;

  const bucketLabel = data.bucket === "day" ? "daily" : "weekly";

  // #1781 — compact strip: shorter mini charts, stacked in this single cell.
  const miniHeight = compact ? 44 : 100;

  return (
    <section
      data-progress-charts-panel
      data-progress-compact={compact ? "true" : "false"}
      data-project-id={projectId}
      aria-labelledby={headingId}
      className={`rounded-lg border border-zinc-200 bg-zinc-50/40 dark:border-zinc-800 dark:bg-zinc-950/20 ${
        compact ? "p-3" : "mb-5 p-5"
      }`}
    >
      <div
        className={`flex flex-wrap items-baseline gap-x-2 ${
          compact ? "mb-2" : "mb-3"
        }`}
      >
        <h2
          id={headingId}
          className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
        >
          Progress
        </h2>
        <span className="text-[11px] text-zinc-400 dark:text-zinc-500">
          {bucketLabel} · last {data.window_days} days
        </span>
      </div>

      {isEmpty ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No activity yet — burndown and velocity will appear once tasks start
          moving.
        </p>
      ) : (
        <div
          className={
            compact
              ? "grid grid-cols-2 gap-2"
              : "grid grid-cols-1 gap-3 md:grid-cols-2"
          }
        >
          <MiniChartCard
            title="Burndown"
            latest={latestRemaining}
            latestLabel="remaining"
            onOpen={() => setOpenKind("burndown")}
            compact={compact}
          >
            <BurndownChart
              points={data.burndown}
              mode="mini"
              miniHeight={miniHeight}
            />
          </MiniChartCard>
          <MiniChartCard
            title="Velocity"
            latest={latestCompleted}
            latestLabel="completed"
            onOpen={() => setOpenKind("velocity")}
            compact={compact}
          >
            <VelocityChart
              points={data.velocity}
              mode="mini"
              miniHeight={miniHeight}
            />
          </MiniChartCard>
        </div>
      )}

      <ModalShell
        open={openKind !== null}
        onClose={() => setOpenKind(null)}
        labelledBy={modalHeadingId}
        maxWidth="lg"
        scrollable
      >
        <div className="mb-3 flex items-center justify-between">
          <h3
            id={modalHeadingId}
            className="text-sm font-semibold text-zinc-900 dark:text-zinc-100"
          >
            {openKind === "burndown" ? "Burndown" : "Velocity"}
            <span className="ml-2 text-xs font-normal text-zinc-500 dark:text-zinc-400">
              {bucketLabel} · last {data.window_days} days
            </span>
          </h3>
          <button
            type="button"
            onClick={() => setOpenKind(null)}
            className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            aria-label="Close chart"
          >
            <svg
              viewBox="0 0 16 16"
              width="16"
              height="16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
        {openKind !== null && <ExpandedChart kind={openKind} data={data} />}
      </ModalShell>
    </section>
  );
}
