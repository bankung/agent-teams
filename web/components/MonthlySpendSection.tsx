"use client";

// MonthlySpendSection — portfolio-wide billing-cycle spend card.
// Kanban #2356 (AC3). Prop-driven (no internal fetch) for RTL determinism.
//
// Collapse behaviour mirrors CostSummary / AuditorActivityPanel: localStorage
// + same-tab StorageEvent, readExpanded / writeExpanded from @/lib/collapseState.

import { useEffect, useState } from "react";

import { type MonthlyUsageResponse, type UsageMonthlyCycle } from "@/lib/api";
import { readExpanded, writeExpanded } from "@/lib/collapseState";

// ── helpers ──────────────────────────────────────────────────────────────────

function parseUsd(raw: string): number {
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}

function formatUsd(n: number): string {
  return `$${n.toFixed(2)}`;
}

// 4-dp tooltip value keeps precision visible without cluttering the row label.
function formatUsd4(n: number): string {
  return `$${n.toFixed(4)}`;
}

function fmtDate(iso: string): string {
  // "2026-06-01" → "Jun 1, 2026"
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

// ── icons ─────────────────────────────────────────────────────────────────────

function ChevronDownIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="4 6 8 10 12 6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="6 4 10 8 6 12" />
    </svg>
  );
}

// ── per-cycle drilldown ───────────────────────────────────────────────────────

function CycleRow({ cycle }: { cycle: UsageMonthlyCycle }) {
  const [open, setOpen] = useState(false);

  const modeA = parseUsd(cycle.mode_a_cost_usd);
  const modeB = parseUsd(cycle.mode_b_cost_usd);
  const total = parseUsd(cycle.total_cost_usd);

  return (
    <div className="rounded-md border border-zinc-200/70 bg-white/60 dark:border-zinc-700/50 dark:bg-zinc-900/30">
      {/* cycle header row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        {/* date range */}
        <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
          {fmtDate(cycle.cycle_start)} – {fmtDate(cycle.cycle_end)}
        </span>

        {/* Mode A */}
        <span
          className="text-xs font-medium text-blue-700 dark:text-blue-300 tabular-nums"
          title={`Mode A (estimated, interactive) ${formatUsd4(modeA)} USD`}
        >
          A ≈ {formatUsd(modeA)}
        </span>

        {/* Mode B */}
        <span
          className="text-xs font-medium text-amber-700 dark:text-amber-300 tabular-nums"
          title={`Mode B (actual, headless) ${formatUsd4(modeB)} USD`}
        >
          B {formatUsd(modeB)}
        </span>

        {/* Total */}
        <span
          className="text-xs font-semibold text-zinc-800 dark:text-zinc-100 tabular-nums"
          title={`Total ${formatUsd4(total)} USD`}
        >
          Total {formatUsd(total)}
        </span>

        {/* drilldown toggle */}
        {cycle.tasks.length > 0 && (
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} task breakdown for ${fmtDate(cycle.cycle_start)} – ${fmtDate(cycle.cycle_end)}`}
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-700 dark:text-zinc-500 dark:hover:text-zinc-200"
          >
            {open ? <ChevronDownIcon /> : <ChevronRightIcon />}
            <span>{open ? "Hide" : "Tasks"}</span>
          </button>
        )}
      </div>

      {/* per-task drilldown */}
      {open && (
        <ul className="border-t border-zinc-100 dark:border-zinc-800 divide-y divide-zinc-100 dark:divide-zinc-800">
          {cycle.tasks.map((t, i) => {
            const tA = parseUsd(t.mode_a_cost_usd);
            const tB = parseUsd(t.mode_b_cost_usd);
            const tTotal = parseUsd(t.total_cost_usd);
            const label = t.task_title ?? "Unattributed";
            return (
              <li
                key={t.task_id ?? `unattributed-${i}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5"
                data-task-row
              >
                <span className="flex-1 min-w-0 truncate text-xs text-zinc-600 dark:text-zinc-300">
                  {t.task_id == null ? (
                    <em className="not-italic text-zinc-400 dark:text-zinc-500">
                      {label}
                    </em>
                  ) : (
                    label
                  )}
                </span>
                <span
                  className="text-xs text-blue-600 dark:text-blue-400 tabular-nums"
                  title={`Mode A estimated ${formatUsd4(tA)}`}
                >
                  A ≈ {formatUsd(tA)}
                </span>
                <span
                  className="text-xs text-amber-600 dark:text-amber-400 tabular-nums"
                  title={`Mode B actual ${formatUsd4(tB)}`}
                >
                  B {formatUsd(tB)}
                </span>
                <span className="text-xs font-medium text-zinc-700 dark:text-zinc-200 tabular-nums">
                  {formatUsd(tTotal)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

type Props = {
  data: MonthlyUsageResponse;
  defaultCollapsed?: boolean;
  storageKey?: string;
  className?: string;
};

export function MonthlySpendSection({
  data,
  defaultCollapsed = false,
  storageKey,
  className = "mb-5",
}: Props) {
  const collapsible = defaultCollapsed && storageKey != null;

  // Mirror CostSummary: default expanded=true to avoid hydration mismatch;
  // collapible panels correct from localStorage in useEffect after hydration.
  const [expanded, setExpanded] = useState(!defaultCollapsed);

  useEffect(() => {
    if (!collapsible || !storageKey) return;
    setExpanded(readExpanded(storageKey, defaultCollapsed));

    function onStorage(e: StorageEvent) {
      if (e.key !== storageKey) return;
      setExpanded(
        e.newValue !== null ? JSON.parse(e.newValue) !== false : !defaultCollapsed,
      );
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [collapsible, storageKey, defaultCollapsed]);

  function toggle() {
    if (!collapsible || !storageKey) return;
    const next = !expanded;
    setExpanded(next);
    writeExpanded(storageKey, next);
  }

  const isEmpty = data.cycles.length === 0;

  return (
    <section
      data-monthly-spend
      aria-label="Monthly spend by billing cycle"
      className={`${className} rounded-lg border border-zinc-200/60 bg-zinc-50/40 p-3 dark:border-zinc-800/60 dark:bg-zinc-900/20`}
    >
      <div
        className="flex items-center gap-2 flex-wrap"
        style={{ marginBottom: expanded ? "0.75rem" : 0 }}
      >
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Monthly Spend (billing cycle)
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Monthly Spend (billing cycle)
          </h2>
        )}

        {/* Compact inline summary when collapsible + collapsed */}
        {collapsible && !expanded && (
          <span className="text-xs text-zinc-600 dark:text-zinc-400 tabular-nums">
            Total {formatUsd(parseUsd(data.total_cost_usd))}
            {data.months > 0 && (
              <span className="ml-1 text-zinc-400">{data.months}mo</span>
            )}
          </span>
        )}
      </div>

      {expanded && (
        <>
          {isEmpty ? (
            <p className="text-sm text-zinc-400 dark:text-zinc-600">
              No spend recorded yet.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {data.cycles.map((cycle) => (
                <CycleRow
                  key={`${cycle.cycle_start}__${cycle.cycle_end}`}
                  cycle={cycle}
                />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
