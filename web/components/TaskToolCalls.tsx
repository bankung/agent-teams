"use client";

// TaskToolCalls — Kanban #980 (#949d sub-task).
// Collapsible section inside TaskDetail. Lazy-fetches
// GET /api/tasks/{id}/tool-calls only when the user expands the section,
// so closed-by-default tasks pay zero dashboard-load tax. When the endpoint
// returns an empty array the entire section is hidden (no "0 tool calls"
// noise). Row-level expand reveals input_json / output_summary / error /
// permission decision / absolute timestamp.

import { useEffect, useState } from "react";

import {
  getTaskToolCalls,
  type ToolCallPermissionDecision,
  type ToolCallRead,
  type ToolCallTier,
} from "@/lib/api";
import { formatRelative } from "@/lib/time";
import { Icon } from "./Icon";

type Props = {
  projectId: number;
  taskId: number;
};

// Tier → chip palette. Matches the existing zinc/amber/blue/red palette used
// elsewhere on the board (TaskKindBadge, BudgetBar, PendingBadge).
const TIER_CLASS: Record<ToolCallTier, string> = {
  read: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  write: "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  network: "bg-blue-50 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  destructive: "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-300",
};

const PERMISSION_CLASS: Record<ToolCallPermissionDecision, string> = {
  auto_allow:
    "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  halt: "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  reject: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300",
};

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m}m${rem}s`;
}

export function TaskToolCalls({ projectId, taskId }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [rows, setRows] = useState<ToolCallRead[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probedEmpty, setProbedEmpty] = useState(false);

  // First-load probe — fetch once on mount (cheap; just a count) so the
  // section can hide itself when the task has zero rows. We could defer this
  // until expand, but the brief explicitly says "hide when empty" → we have
  // to know the count before deciding whether to render the header at all.
  // The probe response IS the data, so we cache it as `rows` to avoid a
  // second fetch on expand.
  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);
    setProbedEmpty(false);
    getTaskToolCalls(projectId, taskId)
      .then((data) => {
        if (cancelled) return;
        if (data.length === 0) {
          setProbedEmpty(true);
        } else {
          setRows(data);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 404 (endpoint not deployed yet / no tool_calls row) → treat as empty.
        // Anything else surfaces as an inline message inside the expanded section.
        const msg = err instanceof Error ? err.message : String(err);
        if (/404/.test(msg)) {
          setProbedEmpty(true);
        } else {
          setError(msg);
          setProbedEmpty(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, taskId]);

  // Hide the entire section when probe returned 0 rows (no tool calls
  // recorded → nothing to show, no "0 tool calls" chrome).
  if (probedEmpty) return null;

  // Loading state — probe still in flight; render a tiny placeholder so
  // the section has stable layout. Hidden once we know the count.
  if (rows === null && error === null) {
    return (
      <section
        className="flex flex-col gap-2"
        data-tool-calls
        data-tool-calls-state="loading"
      >
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Tool calls
        </h3>
        <p className="text-xs italic text-zinc-400 dark:text-zinc-500">…</p>
      </section>
    );
  }

  const count = rows?.length ?? 0;

  const handleToggle = async () => {
    setExpanded((v) => !v);
    // Defensive re-fetch on expand if we have an error from the initial probe
    // (e.g. transient 500). For the happy path the probe data is already
    // cached in `rows`, so no extra request fires.
    if (!expanded && error !== null) {
      setLoading(true);
      setError(null);
      try {
        const data = await getTaskToolCalls(projectId, taskId);
        if (data.length === 0) {
          setProbedEmpty(true);
        } else {
          setRows(data);
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <section
      className="flex flex-col gap-2"
      data-tool-calls
      data-tool-calls-count={count}
    >
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={expanded}
        data-tool-calls-toggle
        className="flex w-full items-center gap-2 text-left"
      >
        <span
          aria-hidden
          className={`inline-block text-zinc-400 transition-transform dark:text-zinc-500 ${
            expanded ? "rotate-90" : ""
          }`}
        >
          {/* Tiny inline chevron — sprite has no chevron token; matches
              tabular weight of the surrounding small text. */}
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 1.5 L7 5 L3 8.5" />
          </svg>
        </span>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Tool calls{count > 0 ? ` (${count})` : ""}
        </h3>
      </button>

      {expanded && (
        <div className="flex flex-col gap-1.5" data-tool-calls-panel>
          {loading && (
            <p className="text-xs italic text-zinc-400 dark:text-zinc-500">
              Loading…
            </p>
          )}
          {error !== null && (
            <p className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700 dark:border-red-900 dark:bg-red-900/30 dark:text-red-300">
              Failed to load tool calls: {error}
            </p>
          )}
          {rows !== null && rows.length > 0 && (
            <ol className="flex flex-col gap-1">
              {rows.map((row) => (
                <ToolCallRow key={row.id} row={row} />
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

function ToolCallRow({ row }: { row: ToolCallRead }) {
  const [open, setOpen] = useState(false);
  const [outputExpanded, setOutputExpanded] = useState(false);

  const tierClass = TIER_CLASS[row.tier] ?? TIER_CLASS.read;
  const permClass = PERMISSION_CLASS[row.permission_decision];
  const statusGlyph = row.success ? (
    <Icon name="status-done" size={12} aria-label="success" />
  ) : (
    <Icon name="alert" size={12} aria-label="failure" />
  );

  return (
    <li
      className="rounded border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/40"
      data-tool-call-row
      data-tool-call-id={row.id}
      data-tool-call-tier={row.tier}
      data-tool-call-success={row.success ? "true" : "false"}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-tool-call-toggle
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-white dark:hover:bg-zinc-900"
      >
        <span
          aria-hidden
          className={`inline-block text-zinc-400 transition-transform dark:text-zinc-500 ${
            open ? "rotate-90" : ""
          }`}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 1.5 L7 5 L3 8.5" />
          </svg>
        </span>
        <span
          className="font-mono text-xs text-zinc-900 dark:text-zinc-100"
          data-tool-call-name
        >
          {row.tool_name}
        </span>
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tierClass}`}
          data-tool-call-tier-chip
        >
          {row.tier}
        </span>
        <span className="font-mono text-[11px] text-zinc-500 tabular-nums dark:text-zinc-400">
          {formatDuration(row.duration_ms)}
        </span>
        <span
          className="ml-auto inline-flex items-center gap-1 text-[11px]"
          data-tool-call-status
        >
          {statusGlyph}
          {!row.success && row.error_code && (
            <span className="font-mono text-red-700 dark:text-red-300">
              {row.error_code}
            </span>
          )}
        </span>
      </button>

      {open && (
        <div
          className="flex flex-col gap-2 border-t border-zinc-200 px-2 py-2 dark:border-zinc-800"
          data-tool-call-detail
        >
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span
              className={`inline-flex items-center rounded px-1.5 py-0.5 font-medium uppercase tracking-wide ${permClass}`}
              data-tool-call-permission
            >
              {row.permission_decision.replace("_", " ")}
            </span>
            <span
              className="font-mono text-zinc-500 dark:text-zinc-400"
              title={row.invoked_at}
              data-tool-call-invoked
            >
              {formatRelative(row.invoked_at)}
            </span>
          </div>

          {!row.success && row.error_msg && (
            <div className="rounded border border-red-200 bg-red-50 px-2 py-1 dark:border-red-900 dark:bg-red-900/30">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-red-700 dark:text-red-300">
                Error
              </p>
              <p className="whitespace-pre-wrap font-mono text-xs text-red-900 dark:text-red-200">
                {row.error_msg}
              </p>
            </div>
          )}

          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Input
            </p>
            <pre
              data-tool-call-input
              className="mt-0.5 max-h-48 overflow-auto rounded bg-zinc-100 px-2 py-1 font-mono text-[11px] text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200"
            >
              {JSON.stringify(row.input_json, null, 2)}
            </pre>
          </div>

          {row.output_summary && (
            <div>
              <div className="flex items-center justify-between">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Output (first 256 chars)
                </p>
                {row.output_summary.length > 120 && (
                  <button
                    type="button"
                    onClick={() => setOutputExpanded((v) => !v)}
                    data-tool-call-output-toggle
                    className="rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
                  >
                    {outputExpanded ? "Collapse" : "Expand"}
                  </button>
                )}
              </div>
              <p
                data-tool-call-output
                className={`mt-0.5 whitespace-pre-wrap rounded bg-zinc-100 px-2 py-1 font-mono text-[11px] text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 ${
                  outputExpanded ? "" : "line-clamp-3"
                }`}
              >
                {row.output_summary}
              </p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

