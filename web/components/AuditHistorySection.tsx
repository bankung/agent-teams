"use client";

import { useMemo, useState } from "react";

import type { TaskRead } from "@/lib/api";

// Kanban #1211 / #1238 AA3 (FE) — Audit History section for the project
// detail page. Renders below the main Kanban board.
//
// Source: tasks where task_type='audit', sorted by completed_at DESC. The
// list comes pre-filtered + pre-sorted from listProjectAuditTasks() so the
// component is purely presentational; the parent component fetches once on
// mount.
//
// UX:
// - Section is COLLAPSIBLE (closed by default — operators usually want the
//   active board, not the historical audit trail).
// - Empty state: a single line "No audit history yet." so it doesn't take
//   the whole section if the project has never had an audit fire.
// - Each row: completed_at + recommendation badge + ▸ expand. Expanding
//   reveals the raw audit_report JSON pretty-printed; cheaper than building
//   a structured detail view for what's effectively a debug surface today
//   (the operator-facing summary lives on the /review page once a flag has
//   been raised; this section is the audit-history archive for context).

type Props = {
  auditTasks: TaskRead[];
};

// Recommendation values: 'continue' | 'review' | 'pause' (services/audit_flag.py
// _VALID_RECOMMENDATIONS). Unknown / missing renders as a neutral muted badge
// so a malformed audit_report doesn't break the section.
function recommendationBadge(rec: unknown): {
  label: string;
  classes: string;
} {
  if (rec === "continue") {
    return {
      label: "continue",
      classes:
        "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
    };
  }
  if (rec === "review") {
    return {
      label: "review",
      classes:
        "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-200",
    };
  }
  if (rec === "pause") {
    return {
      label: "pause",
      classes:
        "border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300",
    };
  }
  return {
    label: typeof rec === "string" && rec.length > 0 ? rec : "—",
    classes:
      "border-zinc-300 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400",
  };
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  // Trim to the seconds boundary + space-separate for readability. ISO 8601
  // with TZ offset stays in the wire format; we just lop off sub-seconds.
  return iso.slice(0, 19).replace("T", " ");
}

export function AuditHistorySection({ auditTasks }: Props) {
  const [open, setOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const count = auditTasks.length;

  // Memo-ize the rendered audit_report JSON keys so toggling expand doesn't
  // re-stringify every row. Keyed by task id; null when the row has no
  // audit_report yet.
  const reportPreviews = useMemo(() => {
    const map = new Map<number, string>();
    for (const t of auditTasks) {
      if (t.audit_report && typeof t.audit_report === "object") {
        try {
          map.set(t.id, JSON.stringify(t.audit_report, null, 2));
        } catch {
          // Circular ref or other JSON serializer failure — fall back to a
          // sentinel string so the expand panel still renders.
          map.set(t.id, "(audit_report could not be serialized)");
        }
      }
    }
    return map;
  }, [auditTasks]);

  return (
    <section
      className="mt-3 rounded border border-zinc-200 bg-white text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
      data-audit-history-section
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-800"
        aria-expanded={open}
        data-audit-history-toggle
      >
        <span>Audit history</span>
        <span className="inline-flex items-center gap-2">
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] tabular-nums text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
            {count}
          </span>
          <span aria-hidden className="text-zinc-400 dark:text-zinc-500">
            {open ? "▾" : "▸"}
          </span>
        </span>
      </button>
      {open && (
        <div className="border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
          {count === 0 ? (
            <p
              className="py-2 text-center text-xs text-zinc-400 dark:text-zinc-500"
              data-audit-history-empty
            >
              No audit history yet.
            </p>
          ) : (
            <ul className="flex flex-col divide-y divide-zinc-100 dark:divide-zinc-800">
              {auditTasks.map((task) => {
                const rec = task.audit_report?.recommendation;
                const badge = recommendationBadge(rec);
                const isExpanded = expandedId === task.id;
                const preview = reportPreviews.get(task.id);
                return (
                  <li key={task.id} data-audit-history-row={task.id}>
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedId(isExpanded ? null : task.id)
                      }
                      className="flex w-full items-center gap-2 py-2 text-left text-xs hover:bg-zinc-50 dark:hover:bg-zinc-800"
                      aria-expanded={isExpanded}
                    >
                      <span className="font-mono text-[11px] tabular-nums text-zinc-500 dark:text-zinc-400">
                        {formatDateTime(task.completed_at)}
                      </span>
                      <span
                        className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${badge.classes}`}
                      >
                        {badge.label}
                      </span>
                      <span className="flex-1 truncate text-zinc-700 dark:text-zinc-300">
                        #{task.id} · {task.title}
                      </span>
                      <span
                        aria-hidden
                        className="shrink-0 text-zinc-400 dark:text-zinc-500"
                      >
                        {isExpanded ? "▾" : "▸"}
                      </span>
                    </button>
                    {isExpanded && (
                      <div
                        className="mb-2 ml-2 rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-950"
                        data-audit-history-detail={task.id}
                      >
                        {preview ? (
                          <pre className="whitespace-pre-wrap break-all font-mono text-[10px] leading-tight text-zinc-700 dark:text-zinc-300">
                            {preview}
                          </pre>
                        ) : (
                          <p className="text-[11px] italic text-zinc-500 dark:text-zinc-500">
                            (no audit_report recorded)
                          </p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
