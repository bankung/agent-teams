"use client";

import { useEffect, useMemo, useState } from "react";

import {
  getTaskBlocks,
  patchTask,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { computeBlockedByExclusionSet } from "@/lib/cycleExclusion";
import { PendingBadge } from "./PendingBadge";
import { RunModeBadge } from "./RunModeBadge";
import { TaskKindBadge } from "./TaskKindBadge";

type Props = {
  task: TaskRead;
  allTasks: TaskRead[];
  projectId: number;
  onClose: () => void;
  onPatch: (updated: TaskRead) => void;
  onError: (message: string) => void;
};

const STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
};

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// TaskDetail — right-side drawer panel (#771).
// Aesthetic mirrors ProjectConsentGrantModal (backdrop + Escape + click-outside).
// Drawer (not centered modal) keeps the Board visible — useful for cross-task
// blocker picking later.
export function TaskDetail({
  task,
  allTasks,
  projectId,
  onClose,
  onPatch,
  onError,
}: Props) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [alsoBlocks, setAlsoBlocks] = useState<TaskRead[] | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (submitting) return;
      if (pickerOpen) {
        setPickerOpen(false);
      } else {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pickerOpen, submitting, onClose]);

  // Reverse-lookup "Also blocks" — optional affordance. Errors swallowed.
  useEffect(() => {
    let cancelled = false;
    setAlsoBlocks(null);
    getTaskBlocks(projectId, task.id)
      .then((rows) => {
        if (!cancelled) setAlsoBlocks(rows);
      })
      .catch(() => {
        if (!cancelled) setAlsoBlocks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, task.id]);

  const blockerTask = useMemo(() => {
    if (task.blocked_by == null) return null;
    return allTasks.find((t) => t.id === task.blocked_by) ?? null;
  }, [task.blocked_by, allTasks]);

  const setBlocker = async (newBlockedBy: number | null) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const updated = await patchTask(projectId, task.id, {
        blocked_by: newBlockedBy,
      });
      onPatch(updated);
      setPickerOpen(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Update failed";
      onError(`Task #${task.id}: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="taskdetail-title"
      data-task-detail-modal
      data-task-id={task.id}
      className="fixed inset-0 z-40 bg-zinc-900/40 dark:bg-zinc-950/70"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <aside
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[480px] flex-col overflow-y-auto border-l border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                #{task.id}
              </span>
              <RunModeBadge mode={task.run_mode} />
              <TaskKindBadge kind={task.task_kind} />
              <PendingBadge task={task} />
            </div>
            <h2
              id="taskdetail-title"
              className="mt-1 text-base font-semibold leading-snug text-zinc-900 dark:text-zinc-100"
            >
              {task.title}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
            data-task-detail-close
            className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
          >
            Close
          </button>
        </header>

        {/* Body */}
        <div className="flex flex-col gap-4 px-4 py-4 text-sm">
          <Section label="Status">
            <span className="text-zinc-700 dark:text-zinc-300">
              {STATUS_LABEL[task.process_status] ?? `ps${task.process_status}`}
            </span>
            <span className="ml-2 text-zinc-500 dark:text-zinc-400">
              priority {task.priority}
            </span>
            {task.assigned_role !== null && (
              <span className="ml-2 text-zinc-500 dark:text-zinc-400">
                role {task.assigned_role}
              </span>
            )}
          </Section>

          {task.description && (
            <Section label="Description">
              <p className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200">
                {task.description}
              </p>
            </Section>
          )}

          {task.parent_task_id !== null && (
            <Section label="Parent">
              <span className="font-mono text-zinc-700 dark:text-zinc-300">
                #{task.parent_task_id}
              </span>
            </Section>
          )}

          {/* Blocked-by mutator */}
          <Section label="Blocked by">
            {!pickerOpen ? (
              <div
                className="flex items-center gap-2"
                data-blocked-by-display
              >
                {blockerTask ? (
                  <span
                    className="font-mono text-zinc-700 dark:text-zinc-300"
                    data-blocked-by-current={blockerTask.id}
                  >
                    #{blockerTask.id} — {truncate(blockerTask.title, 60)}
                  </span>
                ) : task.blocked_by !== null ? (
                  // Defensive: blocker not in allTasks (e.g., filtered/soft-deleted).
                  <span className="font-mono text-zinc-500 italic dark:text-zinc-400">
                    #{task.blocked_by} (not in current list)
                  </span>
                ) : (
                  <span className="text-zinc-500 italic dark:text-zinc-400">
                    none
                  </span>
                )}
                <span className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    disabled={submitting}
                    data-blocked-by-change
                    className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                  >
                    {task.blocked_by !== null ? "Change" : "Set blocker"}
                  </button>
                  {task.blocked_by !== null && (
                    <button
                      type="button"
                      onClick={() => setBlocker(null)}
                      disabled={submitting}
                      data-blocked-by-clear
                      className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                    >
                      Clear
                    </button>
                  )}
                </span>
              </div>
            ) : (
              <BlockerPicker
                task={task}
                allTasks={allTasks}
                disabled={submitting}
                onPick={(id) => setBlocker(id)}
                onCancel={() => setPickerOpen(false)}
              />
            )}
          </Section>

          {/* Also blocks — optional reverse-lookup */}
          <Section label="Also blocks">
            {alsoBlocks === null ? (
              <span className="text-zinc-400 italic dark:text-zinc-500">…</span>
            ) : alsoBlocks.length === 0 ? (
              <span className="text-zinc-500 italic dark:text-zinc-400">
                (none)
              </span>
            ) : (
              <ul
                className="flex flex-col gap-1"
                data-also-blocks
              >
                {alsoBlocks.map((t) => (
                  <li
                    key={t.id}
                    className="font-mono text-xs text-zinc-700 dark:text-zinc-300"
                  >
                    #{t.id} — {truncate(t.title, 60)}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* Timestamps */}
          <Section label="Timestamps">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs text-zinc-600 dark:text-zinc-400">
              <dt>created</dt>
              <dd>{task.created_at}</dd>
              <dt>updated</dt>
              <dd>{task.updated_at}</dd>
              {task.started_at && (
                <>
                  <dt>started</dt>
                  <dd>{task.started_at}</dd>
                </>
              )}
              {task.completed_at && (
                <>
                  <dt>completed</dt>
                  <dd>{task.completed_at}</dd>
                </>
              )}
            </dl>
          </Section>
        </div>
      </aside>
    </div>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-1">
      <h3 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </h3>
      <div>{children}</div>
    </section>
  );
}

// BlockerPicker — single-select filterable list.
function BlockerPicker({
  task,
  allTasks,
  disabled,
  onPick,
  onCancel,
}: {
  task: TaskRead;
  allTasks: TaskRead[];
  disabled: boolean;
  onPick: (id: number) => void;
  onCancel: () => void;
}) {
  const [query, setQuery] = useState("");
  const excluded = useMemo(
    () => computeBlockedByExclusionSet(allTasks, task.id),
    [allTasks, task.id],
  );

  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    return allTasks
      .filter((t) => !excluded.has(t.id))
      .filter((t) => {
        if (q.length === 0) return true;
        const idMatch = String(t.id).includes(q);
        const titleMatch = t.title.toLowerCase().includes(q);
        return idMatch || titleMatch;
      })
      .sort((a, b) => a.id - b.id);
  }, [allTasks, excluded, query]);

  return (
    <div
      data-blocked-by-picker
      className="flex flex-col gap-2 rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-950/40"
    >
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by id or title…"
          autoFocus
          disabled={disabled}
          data-blocked-by-search
          className="flex-1 rounded border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <button
          type="button"
          onClick={onCancel}
          disabled={disabled}
          className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700"
        >
          Cancel
        </button>
      </div>
      <ul
        role="listbox"
        className="flex max-h-64 flex-col gap-0.5 overflow-y-auto"
      >
        {candidates.length === 0 ? (
          <li className="px-2 py-3 text-center text-xs text-zinc-500 italic dark:text-zinc-400">
            no matches
          </li>
        ) : (
          candidates.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                onClick={() => onPick(t.id)}
                disabled={disabled}
                data-blocked-by-option={t.id}
                className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-white disabled:opacity-50 dark:hover:bg-zinc-900"
              >
                <span className="font-mono text-zinc-500 dark:text-zinc-400">
                  #{t.id}
                </span>
                <span className="flex-1 truncate text-zinc-800 dark:text-zinc-200">
                  {truncate(t.title, 60)}
                </span>
                <span className="font-mono text-[10px] uppercase text-zinc-500 dark:text-zinc-400">
                  {STATUS_LABEL[t.process_status] ?? `ps${t.process_status}`}
                </span>
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
