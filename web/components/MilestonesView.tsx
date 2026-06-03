"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import {
  listTasks,
  type MilestoneDetail,
  type MilestoneRead,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { MilestoneStatusBadge } from "./MilestoneStatusBadge";
import { MilestoneFormModal } from "./MilestoneFormModal";
import { MilestoneDeleteModal } from "./MilestoneDeleteModal";

// MilestonesView — per-project milestones list (Kanban #1868 FE, v1).
//
// Server component (page.tsx) fetches every milestone WITH its rollup
// (MilestoneDetail[]) and the project name; this client view owns:
//   - the milestone cards (status badge, date window, progress bar, task count)
//   - the New / Edit / Delete modal flows (router.refresh() on success so the
//     server re-fetches the authoritative list + fresh rollups)
//   - inline expand → lazy GET /api/tasks?milestone_id= to surface a
//     milestone's task list (one fetch per expand, cached in local state)
//
// No board group-by, no calendar, no gantt — those are separate future slices.

const STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
};

function formatDateRange(start: string | null, target: string | null): string {
  if (start && target) return `${start} → ${target}`;
  if (start) return `${start} → —`;
  if (target) return `— → ${target}`;
  return "no dates set";
}

type Props = {
  projectId: number;
  projectName: string;
  milestones: MilestoneDetail[];
};

export function MilestonesView({ projectId, projectName, milestones }: Props) {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<MilestoneRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MilestoneRead | null>(null);

  // router.refresh() re-runs the server component, which re-fetches the
  // milestone list + rollups. We don't merge optimistically — the rollup is
  // server-computed, so a refresh is the simplest correct path.
  const onMutated = useCallback(() => {
    router.refresh();
  }, [router]);

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {milestones.length} milestone{milestones.length === 1 ? "" : "s"}
        </h2>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-1.5 rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
          data-new-milestone-trigger
        >
          New milestone
        </button>
      </div>

      {milestones.length === 0 ? (
        <p
          className="rounded border border-dashed border-zinc-200 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
          data-milestones-empty
        >
          No milestones yet. Create one to group tasks for release planning.
        </p>
      ) : (
        <ul className="flex flex-col gap-3" data-milestones-list>
          {milestones.map((m) => (
            <MilestoneCard
              key={m.id}
              milestone={m}
              projectId={projectId}
              projectName={projectName}
              onEdit={() => setEditTarget(m)}
              onDelete={() => setDeleteTarget(m)}
            />
          ))}
        </ul>
      )}

      {/* Create */}
      <MilestoneFormModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={onMutated}
      />
      {/* Edit — keyed on the target id so the form re-seeds per milestone */}
      <MilestoneFormModal
        key={editTarget?.id ?? "edit-closed"}
        projectId={projectId}
        open={editTarget !== null}
        onClose={() => setEditTarget(null)}
        onSaved={onMutated}
        milestone={editTarget ?? undefined}
      />
      {/* Delete confirm */}
      <MilestoneDeleteModal
        projectId={projectId}
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onDeleted={onMutated}
        milestone={deleteTarget}
      />
    </>
  );
}

function MilestoneCard({
  milestone,
  projectId,
  projectName,
  onEdit,
  onDelete,
}: {
  milestone: MilestoneDetail;
  projectId: number;
  projectName: string;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { rollup } = milestone;
  const pct = Math.max(0, Math.min(100, rollup.progress_pct));

  const [expanded, setExpanded] = useState(false);
  // Lazy task list: null = not yet fetched; loaded once on first expand.
  const [tasks, setTasks] = useState<TaskRead[] | null>(null);
  const [loadingTasks, setLoadingTasks] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);

  const toggleExpand = useCallback(() => {
    const next = !expanded;
    setExpanded(next);
    if (next && tasks === null && !loadingTasks) {
      setLoadingTasks(true);
      setTaskError(null);
      listTasks(projectId, { milestone_id: milestone.id, limit: 500 })
        .then((rows) => setTasks(rows))
        .catch((err: unknown) => {
          setTaskError(extractErrorMessage(err, "Failed to load tasks"));
          setTasks([]);
        })
        .finally(() => setLoadingTasks(false));
    }
  }, [expanded, tasks, loadingTasks, projectId, milestone.id]);

  // Sort the expanded task list by process_status then id for stable display.
  const sortedTasks = useMemo(
    () =>
      tasks === null
        ? null
        : [...tasks].sort(
            (a, b) => a.process_status - b.process_status || a.id - b.id,
          ),
    [tasks],
  );

  return (
    <li
      data-milestone-card
      data-milestone-id={milestone.id}
      className="flex flex-col gap-2 rounded-lg border border-zinc-200 bg-white p-4 transition-colors hover:border-zinc-300 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {milestone.title}
            </h3>
            <MilestoneStatusBadge status={milestone.milestone_status} />
          </div>
          <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
            {formatDateRange(milestone.start_date, milestone.target_date)}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onEdit}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-milestone-edit
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-600 hover:border-red-300 hover:text-red-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-red-800 dark:hover:text-red-300"
            data-milestone-delete
          >
            Delete
          </button>
        </div>
      </div>

      {milestone.description && (
        <p className="whitespace-pre-wrap text-sm text-zinc-700 dark:text-zinc-300">
          {milestone.description}
        </p>
      )}

      {/* Progress bar — done / total (excluding cancelled) + progress_pct. */}
      <div className="flex flex-col gap-1" data-milestone-progress>
        <div className="flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
          <span>
            {rollup.done}/{rollup.total} done
          </span>
          <span>{pct.toFixed(1)}%</span>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${milestone.title} progress`}
        >
          <div
            className="h-full rounded-full bg-emerald-500 transition-[width] dark:bg-emerald-400"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Expand → lazy task list */}
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={toggleExpand}
          aria-expanded={expanded}
          className="self-start text-xs font-medium text-zinc-600 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
          data-milestone-expand
        >
          {expanded ? "Hide tasks" : `View tasks (${rollup.total})`}
        </button>
        {/* Convenience deep-link to the board (full task context). */}
        <Link
          href={`/p/${encodeURIComponent(projectName)}`}
          className="text-xs text-zinc-500 hover:text-zinc-900 hover:underline dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          Open board →
        </Link>
      </div>

      {expanded && (
        <div className="mt-1 border-t border-zinc-100 pt-2 dark:border-zinc-800" data-milestone-tasks>
          {loadingTasks ? (
            <p className="text-xs text-zinc-400 italic dark:text-zinc-500" role="status">
              Loading tasks…
            </p>
          ) : taskError !== null ? (
            <p className="text-xs text-red-700 dark:text-red-300" role="alert">
              {taskError}
            </p>
          ) : sortedTasks === null || sortedTasks.length === 0 ? (
            <p className="text-xs text-zinc-500 italic dark:text-zinc-400">
              No tasks assigned to this milestone.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {sortedTasks.map((t) => (
                <li key={t.id} className="flex items-center gap-2 text-xs">
                  <Link
                    href={`/p/${encodeURIComponent(projectName)}?task=${t.id}`}
                    className="flex min-w-0 flex-1 items-center gap-2 hover:underline"
                  >
                    <span className="font-mono text-zinc-500 dark:text-zinc-400">
                      #{t.id}
                    </span>
                    <span className="flex-1 truncate text-zinc-800 dark:text-zinc-200">
                      {t.title}
                    </span>
                  </Link>
                  <span className="shrink-0 font-mono text-[10px] uppercase text-zinc-500 dark:text-zinc-400">
                    {STATUS_LABEL[t.process_status] ?? `ps${t.process_status}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}
