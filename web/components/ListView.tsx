"use client";

import { useMemo, useState } from "react";

import type { TaskRead } from "@/lib/api";
import { TaskPriority, TaskRole, TaskStatus } from "@/lib/constants";
import { RunModeBadge } from "@/components/RunModeBadge";
import { TaskKindBadge } from "@/components/TaskKindBadge";

type Props = {
  tasks: TaskRead[];
  onOpenDetail: (task: TaskRead) => void;
  // #1001 follow-up (2026-05-20) — `?task=<id>` deep-link highlight.
  // ListView paints the matching row with the same ring-pulse keyframe as
  // the board's TaskCard so the operator's eye lands on the right row in
  // both view modes.
  highlightedTaskId?: number | null;
};

const STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "TODO",
  [TaskStatus.IN_PROGRESS]: "In progress",
  [TaskStatus.REVIEW]: "Review",
  [TaskStatus.BLOCKED]: "Blocked",
  [TaskStatus.DONE]: "Done",
};

const STATUS_CLASS: Record<number, string> = {
  [TaskStatus.TODO]: "text-zinc-600 bg-zinc-100 dark:text-zinc-400 dark:bg-zinc-800",
  [TaskStatus.IN_PROGRESS]: "text-blue-700 bg-blue-50 dark:text-blue-300 dark:bg-blue-900/30",
  [TaskStatus.REVIEW]: "text-yellow-700 bg-yellow-50 dark:text-yellow-300 dark:bg-yellow-900/30",
  [TaskStatus.BLOCKED]: "text-red-700 bg-red-50 dark:text-red-300 dark:bg-red-900/30",
  [TaskStatus.DONE]: "text-green-700 bg-green-50 dark:text-green-300 dark:bg-green-900/30",
};

const PRIORITY_LABEL: Record<number, string> = {
  [TaskPriority.LOW]: "P1 low",
  [TaskPriority.NORMAL]: "P2 normal",
  [TaskPriority.HIGH]: "P3 high",
  [TaskPriority.URGENT]: "P4 urgent",
};

const ROLE_SHORT: Record<number, string> = {
  [TaskRole.FRONTEND]: "FE",
  [TaskRole.BACKEND]: "BE",
  [TaskRole.DEVOPS]: "DevOps",
  [TaskRole.QA]: "QA",
  [TaskRole.REVIEWER]: "Reviewer",
  [TaskRole.SECURITY_REVIEWER]: "Security",
};

type SortKey = "id" | "title" | "process_status" | "priority" | "task_kind" | "run_mode" | "assigned_role" | "updated_at";
type SortDir = "asc" | "desc";

const COLUMN_DEFAULT_DIR: Record<SortKey, SortDir> = {
  id: "desc",
  title: "asc",
  process_status: "asc",
  priority: "asc",
  task_kind: "asc",
  run_mode: "asc",
  assigned_role: "asc",
  updated_at: "desc",
};

// #954 — `hideOnMobile` columns collapse to `hidden md:table-cell` so the iPhone
// width shows id / title / status / priority / updated only; Run Mode + Role +
// Kind appear at md+ where horizontal room exists.
const COLUMNS: { key: SortKey; label: string; hideOnMobile?: boolean }[] = [
  { key: "id", label: "#" },
  { key: "title", label: "Title" },
  { key: "process_status", label: "Status" },
  { key: "priority", label: "Priority", hideOnMobile: true },
  { key: "task_kind", label: "Kind", hideOnMobile: true },
  { key: "run_mode", label: "Run Mode", hideOnMobile: true },
  { key: "assigned_role", label: "Role", hideOnMobile: true },
  { key: "updated_at", label: "Updated" },
];

const STATUS_OPTIONS = [
  { value: TaskStatus.TODO, label: "TODO" },
  { value: TaskStatus.IN_PROGRESS, label: "In progress" },
  { value: TaskStatus.REVIEW, label: "Review" },
  { value: TaskStatus.BLOCKED, label: "Blocked" },
  { value: TaskStatus.DONE, label: "Done" },
];

const PRIORITY_OPTIONS = [
  { value: 0, label: "All" },
  { value: TaskPriority.LOW, label: "P1 low" },
  { value: TaskPriority.NORMAL, label: "P2 normal" },
  { value: TaskPriority.HIGH, label: "P3 high" },
  { value: TaskPriority.URGENT, label: "P4 urgent" },
];

const KIND_OPTIONS = [
  { value: "", label: "All" },
  { value: "ai", label: "AI" },
  { value: "human", label: "Human" },
];

const ROLE_OPTIONS = [
  { value: -1, label: "All" },
  { value: 0, label: "Unassigned" },
  { value: TaskRole.FRONTEND, label: "FE" },
  { value: TaskRole.BACKEND, label: "BE" },
  { value: TaskRole.DEVOPS, label: "DevOps" },
  { value: TaskRole.QA, label: "QA" },
  { value: TaskRole.REVIEWER, label: "Reviewer" },
  { value: TaskRole.SECURITY_REVIEWER, label: "Security" },
];

function compareTasks(a: TaskRead, b: TaskRead, key: SortKey, dir: SortDir): number {
  let va: string | number | null;
  let vb: string | number | null;

  switch (key) {
    case "id":
      va = a.id;
      vb = b.id;
      break;
    case "title":
      va = a.title.toLowerCase();
      vb = b.title.toLowerCase();
      break;
    case "process_status":
      va = a.process_status;
      vb = b.process_status;
      break;
    case "priority":
      va = a.priority;
      vb = b.priority;
      break;
    case "task_kind":
      va = a.task_kind;
      vb = b.task_kind;
      break;
    case "run_mode":
      va = a.run_mode;
      vb = b.run_mode;
      break;
    case "assigned_role":
      va = a.assigned_role ?? -1;
      vb = b.assigned_role ?? -1;
      break;
    case "updated_at":
      va = a.updated_at;
      vb = b.updated_at;
      break;
  }

  if (va === vb) return 0;
  const cmp = va < vb ? -1 : 1;
  return dir === "asc" ? cmp : -cmp;
}

export function ListView({ tasks, onOpenDetail, highlightedTaskId = null }: Props) {
  const [selectedStatuses, setSelectedStatuses] = useState<Set<number>>(new Set());
  const [selectedPriority, setSelectedPriority] = useState(0);
  const [selectedKind, setSelectedKind] = useState("");
  const [selectedRole, setSelectedRole] = useState(-1);

  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleHeaderClick(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(COLUMN_DEFAULT_DIR[key]);
    }
  }

  function toggleStatus(value: number) {
    setSelectedStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  const filtered = useMemo(() => {
    return tasks.filter((t) => {
      if (selectedStatuses.size > 0 && !selectedStatuses.has(t.process_status)) return false;
      if (selectedPriority !== 0 && t.priority !== selectedPriority) return false;
      if (selectedKind !== "" && t.task_kind !== selectedKind) return false;
      if (selectedRole === 0 && t.assigned_role !== null) return false;
      if (selectedRole > 0 && t.assigned_role !== selectedRole) return false;
      return true;
    });
  }, [tasks, selectedStatuses, selectedPriority, selectedKind, selectedRole]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => compareTasks(a, b, sortKey, sortDir));
  }, [filtered, sortKey, sortDir]);

  // #954 — selects + chips bump tap target to 44px on mobile; desktop restores
  // the dense xs sizing for layout parity with the rest of the board chrome.
  const selectClass = "rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm px-3 py-2 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 text-zinc-700 dark:text-zinc-300 focus:outline-none focus:ring-1 focus:ring-zinc-400";
  const chipBase = "inline-flex items-center rounded-full px-3 py-2 text-xs font-medium cursor-pointer select-none border transition-colors min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-0.5";

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1 overflow-hidden">
      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Status multi-select chips */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">Status</span>
          <div className="flex flex-wrap gap-1">
            {STATUS_OPTIONS.map((opt) => {
              const active = selectedStatuses.has(opt.value);
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggleStatus(opt.value)}
                  className={`${chipBase} ${
                    active
                      ? `${STATUS_CLASS[opt.value]} border-transparent`
                      : "border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 bg-transparent hover:bg-zinc-100 dark:hover:bg-zinc-800"
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Priority single-select */}
        <label className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">Priority</span>
          <select
            value={selectedPriority}
            onChange={(e) => setSelectedPriority(Number(e.target.value))}
            className={selectClass}
          >
            {PRIORITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>

        {/* Kind single-select */}
        <label className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">Kind</span>
          <select
            value={selectedKind}
            onChange={(e) => setSelectedKind(e.target.value)}
            className={selectClass}
          >
            {KIND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>

        {/* Role single-select */}
        <label className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">Role</span>
          <select
            value={selectedRole}
            onChange={(e) => setSelectedRole(Number(e.target.value))}
            className={selectClass}
          >
            {ROLE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>

        <span className="ml-auto text-xs text-zinc-400 dark:text-zinc-500 tabular-nums">
          {sorted.length} / {tasks.length}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto overflow-y-auto flex-1 rounded border border-zinc-200 dark:border-zinc-800">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-zinc-50 dark:bg-zinc-900 z-10">
            <tr>
              {COLUMNS.map((col) => {
                const isActive = sortKey === col.key;
                // #954 — low-value columns collapse on mobile via `hidden md:table-cell`
                const hideClass = col.hideOnMobile ? "hidden md:table-cell" : "";
                return (
                  <th
                    key={col.key}
                    onClick={() => handleHeaderClick(col.key)}
                    aria-sort={isActive ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
                    className={`text-xs font-semibold uppercase tracking-wide py-2 px-3 text-left whitespace-nowrap cursor-pointer select-none border-b border-zinc-200 dark:border-zinc-800 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors ${hideClass} ${
                      isActive
                        ? "text-zinc-900 dark:text-zinc-100"
                        : "text-zinc-500 dark:text-zinc-400"
                    }`}
                  >
                    {col.label}
                    {isActive && (
                      <span aria-hidden className="ml-1 inline-block">
                        {sortDir === "asc" ? "▲" : "▼"}
                      </span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((task) => {
              const isHighlighted = highlightedTaskId === task.id;
              return (
              <tr
                key={task.id}
                onClick={() => onOpenDetail(task)}
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenDetail(task); } }}
                data-task-card-id={task.id}
                data-deep-link-highlighted={isHighlighted ? "true" : undefined}
                className={`cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800 border-b border-zinc-100 dark:border-zinc-800/60 last:border-b-0 transition-colors${isHighlighted ? " animate-deep-link-pulse" : ""}`}
              >
                {/* #id */}
                <td className="py-2 px-3 align-middle text-right font-mono text-xs text-zinc-400 dark:text-zinc-500 whitespace-nowrap">
                  #{task.id}
                </td>
                {/* Title */}
                <td className="py-2 px-3 align-middle">
                  <span className="block truncate max-w-[320px] text-zinc-900 dark:text-zinc-100 font-medium">
                    {task.title}
                  </span>
                </td>
                {/* Status */}
                <td className="py-2 px-3 align-middle whitespace-nowrap">
                  <span
                    className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ${
                      STATUS_CLASS[task.process_status] ?? "text-zinc-600 bg-zinc-100 dark:text-zinc-300 dark:bg-zinc-800"
                    }`}
                  >
                    {STATUS_LABEL[task.process_status] ?? String(task.process_status)}
                  </span>
                </td>
                {/* Priority — #954 hidden on mobile */}
                <td className="hidden md:table-cell py-2 px-3 align-middle whitespace-nowrap text-xs text-zinc-600 dark:text-zinc-400">
                  {PRIORITY_LABEL[task.priority] ?? `P${task.priority}`}
                </td>
                {/* Kind — #954 hidden on mobile */}
                <td className="hidden md:table-cell py-2 px-3 align-middle">
                  <TaskKindBadge kind={task.task_kind} />
                </td>
                {/* Run Mode — #954 hidden on mobile */}
                <td className="hidden md:table-cell py-2 px-3 align-middle">
                  <RunModeBadge mode={task.run_mode} />
                </td>
                {/* Role — #954 hidden on mobile */}
                <td className="hidden md:table-cell py-2 px-3 align-middle whitespace-nowrap text-xs text-zinc-500 dark:text-zinc-400">
                  {task.assigned_role !== null
                    ? (ROLE_SHORT[task.assigned_role] ?? `role${task.assigned_role}`)
                    : <span className="text-zinc-300 dark:text-zinc-600">—</span>}
                </td>
                {/* Updated */}
                <td className="py-2 px-3 align-middle whitespace-nowrap text-xs text-zinc-400 dark:text-zinc-500 font-mono">
                  {task.updated_at.slice(0, 10)}
                </td>
              </tr>
              );
            })}
            {sorted.length === 0 && (
              <tr>
                <td
                  colSpan={COLUMNS.length}
                  className="py-8 text-center text-sm text-zinc-400 dark:text-zinc-600"
                >
                  No tasks match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
