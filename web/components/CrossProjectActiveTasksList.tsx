"use client";

// CrossProjectActiveTasksList — Kanban #945. Cross-project list of tasks
// with process_status in {IN_PROGRESS (2), REVIEW (3), BLOCKED (4)} across
// every active project. Powers the "what's actively going on" section on
// the operator dashboard.
//
// Converted to "use client" (Kanban #1408) to support the collapse/expand
// chevron toggle. Data still comes in as a prop — no fetch logic changes.
// SSE-driven live refresh still works via DashboardRefresher router.refresh().
//
// Render shape — rows pre-sorted by (project_name ASC, updated_at DESC) on
// the server; the component groups adjacent same-project rows under a
// project header, then renders a dense one-line-per-task row beneath. No
// pagination in v1 (small operator-visible set).
//
// Render site: web/app/dashboard/page.tsx — sits BELOW PnlDashboardSection
// and ABOVE the per-project compact grid.
//
// Out of scope (v1): status filter chips, collapse-by-project toggle,
// per-task drawer. Tracked separately if/when needed.

import Link from "next/link";

import {
  type DashboardActiveTaskRow,
  type DashboardActiveTasks,
} from "@/lib/api";
import { formatRelative } from "@/lib/time";
import { usePersistentState } from "@/lib/usePersistentState";

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

// ----- Visual labels --------------------------------------------------------

// Lifecycle code → chip label + color. Mirrors the per-project Board.tsx
// COLUMNS array and the dashboard `LANES` accent palette for visual
// consistency (in-progress=amber, review=violet, blocked=red).
const STATUS_LABEL: Record<number, string> = {
  2: "in-progress",
  3: "review",
  4: "blocked",
};

const STATUS_CHIP_CLASS: Record<number, string> = {
  2: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  3: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-200",
  4: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
};

// Role code → short label. Matches the Kanban schema codes table in
// .claude/teams/dev.md (1..6 for the dev team). Unknown codes (other teams,
// future codes) fall back to the raw int — keeps the surface forward-compat
// without coupling to every team's roster.
const ROLE_LABEL: Record<number, string> = {
  1: "frontend",
  2: "backend",
  3: "devops",
  4: "tester",
  5: "reviewer",
  6: "sec-rev",
  // novel-team codes intentionally minimal — typically not in this surface
  11: "writer",
  12: "editor",
};

// run_mode chip — manual is the dominant case; auto_* gets a stronger color
// so the eye picks them out fast in a long list. Identical accent palette
// to the per-project board header chips.
const RUN_MODE_CHIP_CLASS: Record<string, string> = {
  manual:
    "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  auto_pickup:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
  auto_headless:
    "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-200",
};

// ----- Helpers --------------------------------------------------------------

// Group adjacent same-project rows. Server pre-sorts (project_name ASC,
// updated_at DESC) so we can group with a single forward scan — no Map
// re-sort needed.
function groupByProject(
  rows: DashboardActiveTaskRow[],
): Array<{
  project_id: number;
  project_name: string;
  team: string;
  rows: DashboardActiveTaskRow[];
}> {
  const groups: Array<{
    project_id: number;
    project_name: string;
    team: string;
    rows: DashboardActiveTaskRow[];
  }> = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.project_id === row.project_id) {
      last.rows.push(row);
    } else {
      groups.push({
        project_id: row.project_id,
        project_name: row.project_name,
        team: row.team,
        rows: [row],
      });
    }
  }
  return groups;
}

// ----- Row sub-components ---------------------------------------------------

function StatusChip({ status }: { status: number }) {
  const cls =
    STATUS_CHIP_CLASS[status] ??
    "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}
    >
      {STATUS_LABEL[status] ?? `ps${status}`}
    </span>
  );
}

function RunModeChip({ runMode }: { runMode: string }) {
  // Default (unknown code) → neutral zinc; covers forward-compat for any
  // future run_mode value that lands in the schema before this map updates.
  const cls =
    RUN_MODE_CHIP_CLASS[runMode] ??
    "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400";
  // `manual` is the noise floor — render but dim it to keep the eye on the
  // auto_* rows. The chip is always rendered so the column stays alignment-
  // stable for the operator scanning down.
  const isManual = runMode === "manual";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium tracking-wide ${cls} ${isManual ? "opacity-70" : ""}`}
      title={`run_mode=${runMode}`}
    >
      {runMode}
    </span>
  );
}

function RoleChip({ role }: { role: number | null }) {
  if (role === null) {
    // Reserve a column slot but emit an em-dash so the row width stays
    // stable across rows with and without a role.
    return (
      <span className="inline-flex shrink-0 items-center text-[10px] text-zinc-400 dark:text-zinc-600">
        —
      </span>
    );
  }
  const label = ROLE_LABEL[role] ?? `r${role}`;
  return (
    <span
      className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
      title={`assigned_role=${role}`}
    >
      {label}
    </span>
  );
}

function BlockedByChip({ blockedBy }: { blockedBy: number }) {
  return (
    <Link
      href={`/tasks/${blockedBy}`}
      className="inline-flex shrink-0 items-center gap-0.5 rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-700 hover:bg-red-100 dark:bg-red-950/40 dark:text-red-300 dark:hover:bg-red-950/60"
      title={`Blocked by task #${blockedBy}`}
    >
      <span aria-hidden>⛔</span>
      <span className="tabular-nums">#{blockedBy}</span>
    </Link>
  );
}

function TaskRow({ row }: { row: DashboardActiveTaskRow }) {
  return (
    <li
      data-active-task-row
      data-task-id={row.task_id}
      data-process-status={row.process_status}
      className="flex flex-wrap items-center gap-1.5 border-t border-zinc-100 px-3 py-1.5 text-xs first:border-t-0 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900/60"
    >
      <Link
        href={`/tasks/${row.task_id}`}
        className="shrink-0 font-mono text-[11px] tabular-nums text-zinc-500 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
        title={`Open task #${row.task_id}`}
      >
        #{row.task_id}
      </Link>
      <StatusChip status={row.process_status} />
      <Link
        href={`/tasks/${row.task_id}`}
        className="min-w-0 flex-1 truncate text-zinc-900 hover:underline dark:text-zinc-100"
        title={row.title}
      >
        {row.title}
      </Link>
      <RunModeChip runMode={row.run_mode} />
      <RoleChip role={row.assigned_role} />
      <span
        className="shrink-0 text-[11px] text-zinc-500 tabular-nums dark:text-zinc-400"
        title={row.updated_at}
      >
        {formatRelative(row.updated_at)}
      </span>
      {/* #2419: hide chip when blocker is terminal (server-computed) */}
      {row.blocked_by !== null && !row.blocked_by_terminal ? (
        <BlockedByChip blockedBy={row.blocked_by} />
      ) : null}
    </li>
  );
}

// ----- Component ------------------------------------------------------------

type Props = {
  data: DashboardActiveTasks;
  defaultCollapsed?: boolean;
  storageKey?: string;
};

export function CrossProjectActiveTasksList({
  data,
  defaultCollapsed = false,
  storageKey,
}: Props) {
  const groups = groupByProject(data.rows);

  const collapsible = storageKey != null;

  // Persisted collapse state via usePersistentState. SSR snapshot = expanded
  // default (no hydration mismatch); client reads localStorage after hydration.
  const [storedExpanded, setStoredExpanded] = usePersistentState<boolean>(
    storageKey ?? "active-tasks-list:__noop",
    !defaultCollapsed,
    { deserialize: (raw) => JSON.parse(raw) !== false },
  );
  const expanded = collapsible ? storedExpanded : !defaultCollapsed;

  function toggle() {
    if (!collapsible) return;
    setStoredExpanded(!expanded);
  }

  return (
    <section
      data-active-tasks-list
      aria-label="Cross-project active tasks"
      className="mb-5 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex flex-wrap items-baseline gap-2" style={{ marginBottom: expanded ? "0.75rem" : 0 }}>
        {collapsible ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
            Active tasks across all projects
          </button>
        ) : (
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Active tasks across all projects
          </h2>
        )}
        <span
          className="text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums"
          aria-label={`${data.total_count} task${data.total_count === 1 ? "" : "s"}`}
        >
          n={data.total_count}
        </span>
        <span
          className="text-[11px] text-zinc-400 dark:text-zinc-500"
          title="in-progress + review + blocked"
        >
          · in-progress / review / blocked
        </span>
      </div>

      {expanded && (
        <>
          {data.total_count === 0 ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              No tasks in progress, review, or blocked across active projects.
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {groups.map((group) => (
                <div
                  key={group.project_id}
                  data-active-tasks-group
                  data-project-id={group.project_id}
                  className="flex flex-col rounded border border-zinc-100 dark:border-zinc-800"
                >
                  <header className="flex items-center gap-2 border-b border-zinc-100 bg-zinc-50/60 px-3 py-1.5 dark:border-zinc-800 dark:bg-zinc-950/40">
                    <Link
                      href={`/p/${group.project_name}`}
                      className="truncate text-xs font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
                    >
                      {group.project_name}
                    </Link>
                    <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                      {group.team}
                    </span>
                    <span
                      className="ml-auto text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums"
                      title={`${group.rows.length} active task${group.rows.length === 1 ? "" : "s"} on this project`}
                    >
                      {group.rows.length}
                    </span>
                  </header>
                  <ul role="list">
                    {group.rows.map((row) => (
                      <TaskRow key={row.task_id} row={row} />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
