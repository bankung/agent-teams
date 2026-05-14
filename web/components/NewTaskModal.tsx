"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { createTask, HttpError, type TaskCreateBody } from "@/lib/api";
import {
  TaskPriority,
  TaskRole,
  TaskStatus,
  type TaskPriorityValue,
  type TaskRoleValue,
  type TaskStatusValue,
} from "@/lib/constants";

// Trigger button + dialog for POST /api/tasks (Kanban #855 FE).
// Visual pattern mirrors NewProjectModal: zinc-bordered panel, focus on first
// input, ESC closes, backdrop click closes. process_status/priority/role
// options derived from the same constants the backend mirrors.
//
// task_type/task_kind/run_mode are omitted — backend defaults
// ('feature'/'ai'/'manual') are correct for a manual user-filed task. If the
// user later wants to override, that's a separate Picker task.
//
// New task appears in the lane via router.refresh() — the server emits a
// row_changed SSE event on insert, which the existing useRowChangedEvents
// hook on Board.tsx routes to router.refresh(). The successful POST also
// triggers router.refresh() locally so the user sees the new card even on
// SSE reconnect / cold-start.

type LaneOption = { value: TaskStatusValue; label: string };

const LANE_OPTIONS: LaneOption[] = [
  { value: TaskStatus.TODO, label: "New tasks" },
  { value: TaskStatus.IN_PROGRESS, label: "In progress" },
  { value: TaskStatus.REVIEW, label: "Review" },
  { value: TaskStatus.BLOCKED, label: "Blocked" },
  { value: TaskStatus.DONE, label: "Done" },
];

type PriorityOption = { value: TaskPriorityValue; label: string };

const PRIORITY_OPTIONS: PriorityOption[] = [
  { value: TaskPriority.URGENT, label: "Urgent" },
  { value: TaskPriority.HIGH, label: "High" },
  { value: TaskPriority.NORMAL, label: "Normal" },
  { value: TaskPriority.LOW, label: "Low" },
];

type RoleOption = { value: "" | TaskRoleValue; label: string };

const ROLE_OPTIONS: RoleOption[] = [
  { value: "", label: "— unassigned —" },
  { value: TaskRole.FRONTEND, label: "Frontend" },
  { value: TaskRole.BACKEND, label: "Backend" },
  { value: TaskRole.DEVOPS, label: "DevOps" },
  { value: TaskRole.QA, label: "QA" },
  { value: TaskRole.REVIEWER, label: "Reviewer" },
];

type Props = {
  projectId: number;
};

export function NewTaskModal({ projectId }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [processStatus, setProcessStatus] = useState<TaskStatusValue>(
    TaskStatus.TODO,
  );
  const [priority, setPriority] = useState<TaskPriorityValue>(
    TaskPriority.NORMAL,
  );
  const [role, setRole] = useState<"" | TaskRoleValue>("");
  const [blockedBy, setBlockedBy] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    titleInputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) closeModal();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, submitting]);

  function closeModal() {
    if (submitting) return;
    setOpen(false);
    resetFields();
  }

  function resetFields() {
    setTitle("");
    setDescription("");
    setProcessStatus(TaskStatus.TODO);
    setPriority(TaskPriority.NORMAL);
    setRole("");
    setBlockedBy("");
    setError(null);
  }

  // Title is required by the backend (min_length=1). The disabled-submit guard
  // mirrors that constraint so empty titles never reach the network.
  const trimmedTitle = title.trim();
  const titleValid = trimmedTitle.length > 0;
  const blockedByNum = blockedBy.trim() === "" ? null : Number(blockedBy);
  const blockedByValid =
    blockedByNum === null ||
    (Number.isInteger(blockedByNum) && blockedByNum >= 1);
  const canSubmit = !submitting && titleValid && blockedByValid;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);

    const body: TaskCreateBody = {
      project_id: projectId,
      title: trimmedTitle,
      process_status: processStatus,
      priority,
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(role !== "" ? { assigned_role: role } : {}),
      ...(blockedByNum !== null ? { blocked_by: blockedByNum } : {}),
    };

    try {
      await createTask(projectId, body);
      router.refresh();
      setOpen(false);
      resetFields();
    } catch (err: unknown) {
      if (err instanceof HttpError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Create failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center rounded border border-zinc-300 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-new-task-trigger
      >
        + New task
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-task-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 px-4 dark:bg-zinc-950/70"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-new-task-modal
        >
          <form
            onSubmit={onSubmit}
            className="w-full max-w-md rounded border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <h2
              id="new-task-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              Create task
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Files a new row in <span className="font-mono">tasks</span>. New
              card appears in the chosen lane.
            </p>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Title <span className="text-red-600 dark:text-red-400">*</span>
              <input
                ref={titleInputRef}
                type="text"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="Short imperative summary"
                autoComplete="off"
                disabled={submitting}
                aria-invalid={title.length > 0 && !titleValid}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-new-task-title
              />
            </label>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Lane <span className="text-red-600 dark:text-red-400">*</span>
                <select
                  value={processStatus}
                  onChange={(e) => {
                    setProcessStatus(
                      Number(e.target.value) as TaskStatusValue,
                    );
                    if (error !== null) setError(null);
                  }}
                  disabled={submitting}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                  data-new-task-lane
                >
                  {LANE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Priority <span className="text-red-600 dark:text-red-400">*</span>
                <select
                  value={priority}
                  onChange={(e) => {
                    setPriority(Number(e.target.value) as TaskPriorityValue);
                    if (error !== null) setError(null);
                  }}
                  disabled={submitting}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                  data-new-task-priority
                >
                  {PRIORITY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Role <span className="font-normal text-zinc-400">(optional)</span>
              <select
                value={role === "" ? "" : String(role)}
                onChange={(e) => {
                  const v = e.target.value;
                  setRole(v === "" ? "" : (Number(v) as TaskRoleValue));
                  if (error !== null) setError(null);
                }}
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                data-new-task-role
              >
                {ROLE_OPTIONS.map((o) => (
                  <option key={String(o.value)} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Blocked by{" "}
              <span className="font-normal text-zinc-400">
                (optional task id)
              </span>
              <input
                type="number"
                min={1}
                step={1}
                value={blockedBy}
                onChange={(e) => {
                  setBlockedBy(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="e.g. 123"
                disabled={submitting}
                aria-invalid={blockedBy.length > 0 && !blockedByValid}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-new-task-blocked-by
              />
            </label>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Description{" "}
              <span className="font-normal text-zinc-400">(optional)</span>
              <textarea
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="Markdown supported"
                rows={4}
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-new-task-description
              />
            </label>

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
                data-new-task-error
              >
                {error}
              </p>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-new-task-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded border border-emerald-600 bg-emerald-600 px-2 py-1 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                data-new-task-submit
              >
                {submitting ? "Creating…" : "Create"}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
