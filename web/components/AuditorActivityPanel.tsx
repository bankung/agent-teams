"use client";

// Client wrapper for AuditorActivity display. Reads `dashboard.panels.auditor.visible`
// from localStorage and conditionally renders the auditor verdict rollup section.
// Listens for same-tab StorageEvents dispatched by AuditorVisibilityToggle.
//
// The rollup data is fetched server-side (passed as a prop from the Server
// Component) — this wrapper only gates visibility on the client side.
//
// SSR / hydration: defaults to visible (true) so first paint matches the
// server-rendered output (server always passes the data through). The
// useEffect corrects to actual localStorage value after hydration, but since
// default = visible, there is no layout flash or hydration mismatch.

import Link from "next/link";
import { useEffect, useState } from "react";
import type { AuditDailyRollupEntry } from "@/lib/api";
import { readExpanded, writeExpanded } from "@/lib/collapseState";

const LS_KEY = "dashboard.panels.auditor.visible";
const LS_EXPANDED_KEY = "dashboard.panels.auditor.expanded";

// ----- Icons -----------------------------------------------------------------

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

function readVisible(): boolean {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw === null) return true;
    return JSON.parse(raw) !== false;
  } catch {
    return true;
  }
}

// Verdict configuration — mirrors the inline constants in page.tsx.
const VERDICTS: Array<{
  key: keyof AuditDailyRollupEntry["counts"];
  label: string;
}> = [
  { key: "pass", label: "Pass" },
  { key: "auto_resolved", label: "Auto" },
  { key: "escalated", label: "Escalated" },
  { key: "failed_giveup", label: "Failed" },
  { key: "pending_escalation", label: "Pending" },
];

function verdictColor(
  key: keyof AuditDailyRollupEntry["counts"],
  count: number,
): string {
  if (count === 0) {
    return "text-zinc-400 dark:text-zinc-600";
  }
  switch (key) {
    case "pass":
      return "text-emerald-700 dark:text-emerald-300";
    case "auto_resolved":
      return "text-blue-700 dark:text-blue-300";
    case "escalated":
      return "text-amber-700 dark:text-amber-300";
    case "failed_giveup":
      return "text-red-700 dark:text-red-300";
    case "pending_escalation":
      return "text-violet-700 dark:text-violet-300";
  }
}

type Props = {
  rollup: AuditDailyRollupEntry[];
  defaultCollapsed?: boolean;
  storageKey?: string;
};

export function AuditorActivityPanel({
  rollup,
  defaultCollapsed = false,
  storageKey,
}: Props) {
  const [visible, setVisible] = useState(true);

  // Expanded state — independent of visible. When visible=false the whole
  // section is hidden. When visible=true, expanded gates the body content.
  const collapsible = storageKey != null;
  const [expanded, setExpanded] = useState(!defaultCollapsed);

  useEffect(() => {
    setVisible(readVisible());

    function onStorage(e: StorageEvent) {
      if (e.key !== LS_KEY) return;
      setVisible(e.newValue !== null ? JSON.parse(e.newValue) !== false : true);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    const key = storageKey ?? LS_EXPANDED_KEY;
    setExpanded(readExpanded(key, defaultCollapsed));

    function onStorage(e: StorageEvent) {
      if (e.key !== key) return;
      setExpanded(
        e.newValue !== null ? JSON.parse(e.newValue) !== false : !defaultCollapsed,
      );
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [storageKey, defaultCollapsed]);

  function toggle() {
    const key = storageKey ?? LS_EXPANDED_KEY;
    const next = !expanded;
    setExpanded(next);
    writeExpanded(key, next);
  }

  // When the rollup is empty the section is hidden regardless of toggle state —
  // matches original AuditorActivity behavior (hides when API returns []).
  if (rollup.length === 0 || !visible) return null;

  // Group rows by project_id (BE already sorted; preserve order).
  const byProject = new Map<
    number,
    { name: string; rows: AuditDailyRollupEntry[] }
  >();
  for (const entry of rollup) {
    const existing = byProject.get(entry.project_id);
    if (existing) {
      existing.rows.push(entry);
    } else {
      byProject.set(entry.project_id, {
        name: entry.project_name,
        rows: [entry],
      });
    }
  }

  return (
    <section
      data-auditor-activity
      aria-label="Auditor verdict rollup across projects (last 7 days)"
      className="mb-5 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="mb-3 flex items-center gap-2">
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Auditor activity
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Auditor activity
          </h2>
        )}
      </div>

      {expanded && (
        <div className="flex flex-col gap-4">
          {Array.from(byProject.entries()).map(([projectId, { name, rows }]) => (
            <div
              key={projectId}
              data-auditor-project
              data-project-name={name}
              className="flex flex-col gap-2"
            >
              <Link
                href={`/p/${name}`}
                className="text-sm font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
              >
                {name}
              </Link>

              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
                {rows.map((row) => (
                  <div
                    key={`${projectId}-${row.day}`}
                    data-auditor-day={row.day}
                    className="flex flex-col gap-1.5 rounded-md border border-zinc-100 bg-zinc-50/60 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/40"
                    title={`${name} · ${row.day}`}
                  >
                    <span className="text-[11px] font-medium tabular-nums uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                      {row.day}
                    </span>
                    <div
                      className="flex flex-wrap items-baseline gap-x-3 gap-y-1"
                      role="list"
                      aria-label={`Verdict counts for ${name} on ${row.day}`}
                    >
                      {VERDICTS.map(({ key, label }) => {
                        const count = row.counts[key];
                        return (
                          <span
                            key={key}
                            role="listitem"
                            className="flex items-baseline gap-1"
                            title={`${label}: ${count}`}
                          >
                            <span
                              className={`text-sm font-semibold tabular-nums leading-none ${verdictColor(key, count)}`}
                            >
                              {count}
                            </span>
                            <span className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-500">
                              {label}
                            </span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
