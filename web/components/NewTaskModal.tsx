"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  createTask,
  HttpError,
  type ActionTemplateRead,
  type ProjectRead,
  type TaskCreateBody,
} from "@/lib/api";
import {
  TaskPriority,
  TaskRole,
  TaskStatus,
  type TaskPriorityValue,
  type TaskRoleValue,
  type TaskStatusValue,
} from "@/lib/constants";
import { filterRoleOptions } from "@/lib/enabledRoles";
import { ActionTemplatePicker } from "./ActionTemplatePicker";
import { HandoffTemplatePicker } from "./HandoffTemplatePicker";
import { Icon } from "./Icon";

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
  { value: TaskRole.SECURITY_REVIEWER, label: "Security Reviewer" },
];

type Props = {
  projectId: number;
  // #7 §A AC#3 — per-project role whitelist (project.config.enabled_roles).
  // null / undefined / empty array → show all roles (current behaviour).
  enabledRoles?: number[] | null;
  // #1238 GOV3 — full ProjectRead so the modal can read `is_paused` + show
  // the override checkbox + render the 423 toast with paused_reason context.
  // Optional for forward-compat with callers that don't carry it yet.
  project?: ProjectRead;
  // #1238 GOV3 — Board exposes its toast push helper so 423 errors land in the
  // ToastStack rather than as inline-only red text. Optional for the same
  // forward-compat reason.
  onPushToast?: (text: string) => void;
};

// #1238 GOV3 — minimum length for the per-task pause-override reason. Mirrors
// the BE schema (api/src/schemas/task.py — Field(min_length=10)).
const ALLOW_DURING_PAUSE_REASON_MIN_CHARS = 10;

export function NewTaskModal({
  projectId,
  enabledRoles,
  project,
  onPushToast,
}: Props) {
  const isProjectPaused = project?.is_paused === true;
  // #7 §A AC#3 — narrow role dropdown to project.config.enabled_roles when set.
  // Unassigned sentinel is always retained.
  const visibleRoleOptions = useMemo(
    () => filterRoleOptions(ROLE_OPTIONS, enabledRoles),
    [enabledRoles],
  );
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
  // #1238 GOV3 — per-task pause override (only meaningful when isProjectPaused).
  const [allowDuringPause, setAllowDuringPause] = useState(false);
  const [allowDuringPauseReason, setAllowDuringPauseReason] = useState("");
  // #1340 — action template chip selection. When set, server pre-fills
  // task_kind / task_type / priority / acceptance_criteria from the template.
  // The chip-row picker also seeds local form state so the visible defaults
  // match what the BE will persist (the operator can still edit before submit).
  const [actionTemplateId, setActionTemplateId] = useState<string | null>(null);
  // #1343 — handoff template pointer. Persisted on the task row; BE spawns
  // the child on the DONE-flip (services/handoff_spawn.py).
  const [handoffTemplateId, setHandoffTemplateId] = useState<number | null>(null);
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
    setAllowDuringPause(false);
    setAllowDuringPauseReason("");
    setActionTemplateId(null);
    setHandoffTemplateId(null);
    setError(null);
  }

  // #1340 — when an action template is picked, seed local form fields from
  // its defaults so the visible form matches what the BE will persist. The
  // user can still edit any field before submit; caller-explicit values win
  // server-side too (the BE only applies template defaults to fields the
  // caller did not explicitly set in the same POST body).
  function onPickActionTemplate(template: ActionTemplateRead | null) {
    if (template === null) {
      setActionTemplateId(null);
      return;
    }
    setActionTemplateId(template.id);
    setPriority(template.default_priority);
    if (error !== null) setError(null);
  }

  // Title is required by the backend (min_length=1). The disabled-submit guard
  // mirrors that constraint so empty titles never reach the network.
  const trimmedTitle = title.trim();
  const titleValid = trimmedTitle.length > 0;
  const blockedByNum = blockedBy.trim() === "" ? null : Number(blockedBy);
  const blockedByValid =
    blockedByNum === null ||
    (Number.isInteger(blockedByNum) && blockedByNum >= 1);
  // #1238 GOV3 — when the override is checked on a paused project, the reason
  // textarea must satisfy the BE min_length=10 gate before submit is enabled.
  // We DO NOT block submit when the override is unchecked — the user is
  // allowed to attempt the POST without the override; the BE will return 423
  // and we surface a toast prompting them to check the box.
  const trimmedOverrideReason = allowDuringPauseReason.trim();
  const overrideReasonValid =
    !isProjectPaused ||
    !allowDuringPause ||
    trimmedOverrideReason.length >= ALLOW_DURING_PAUSE_REASON_MIN_CHARS;
  const canSubmit =
    !submitting && titleValid && blockedByValid && overrideReasonValid;

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
      // #1238 GOV3 — only attach the override pair when both (a) the project
      // is paused and (b) the operator checked the box. The BE schema
      // requires the reason to accompany allow_during_pause=true; the form
      // guards that above so a paired POST never lands with a missing reason.
      ...(isProjectPaused && allowDuringPause
        ? {
            allow_during_pause: true,
            allow_during_pause_reason: trimmedOverrideReason,
          }
        : {}),
      // #1340 — server pre-fills task_kind / task_type / acceptance_criteria
      // from the named template (caller-explicit values above still win).
      ...(actionTemplateId !== null
        ? { action_template_id: actionTemplateId }
        : {}),
      // #1343 — persisted on row; BE spawns child on DONE-flip.
      ...(handoffTemplateId !== null
        ? { handoff_template_id: handoffTemplateId }
        : {}),
    };

    try {
      await createTask(projectId, body);
      router.refresh();
      setOpen(false);
      resetFields();
    } catch (err: unknown) {
      if (err instanceof HttpError) {
        // #1238 GOV3 — 423 = paused-project gate. Render a toast with the
        // project's paused_reason + a hint about the override checkbox so
        // the operator can react without re-reading the BE detail blob.
        if (err.status === 423 && isProjectPaused) {
          const pausedReason =
            (project?.paused_reason && project.paused_reason.trim()) ||
            "(no reason recorded)";
          const toastMsg = `Project paused: ${pausedReason}. Check "Allow this task during pause" to override.`;
          if (onPushToast) onPushToast(toastMsg);
          // Keep the inline error too so the user sees something when there
          // is no toast handler wired (older callers / future variants).
          setError(toastMsg);
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : "Create failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      {/* #954 — 44px min tap target on mobile */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded border border-zinc-300 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-new-task-trigger
      >
        <Icon name="add-task" size={14} aria-hidden />
        <span>New task</span>
      </button>
      {open && (
        // #954 — mobile: full-screen sheet (no padding, edge-to-edge); desktop restores centered max-w-md card
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-task-title"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-new-task-modal
        >
          <form
            onSubmit={onSubmit}
            className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-w-md sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800"
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

            {/* #1340 — action template chip row. Self-hides when no templates
                exist (empty GET /api/templates/actions response). */}
            <ActionTemplatePicker
              selectedId={actionTemplateId}
              onSelect={onPickActionTemplate}
              disabled={submitting}
            />

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
                {visibleRoleOptions.map((o) => (
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

            {/* #1343 — handoff template picker. Self-hides when no templates
                exist (empty GET response). On DONE-flip BE atomically spawns
                the child task per the chosen template (#1004 spawn hook). */}
            <HandoffTemplatePicker
              projectId={projectId}
              selectedId={handoffTemplateId}
              onSelect={(id) => {
                setHandoffTemplateId(id);
                if (error !== null) setError(null);
              }}
              disabled={submitting}
            />

            {/* #1238 GOV3 — paused-project override. Only rendered when the
                operator is filing a task against a currently-paused project;
                hidden entirely otherwise so the form chrome stays minimal.
                When checked, reveals a reason textarea (>=10 chars) that the
                BE requires alongside allow_during_pause=true. */}
            {isProjectPaused && (
              <div
                className="mt-3 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 dark:border-amber-600 dark:bg-amber-950/40"
                data-new-task-pause-override
              >
                <label className="flex items-start gap-2 text-xs font-medium text-amber-900 dark:text-amber-200">
                  <input
                    type="checkbox"
                    checked={allowDuringPause}
                    onChange={(e) => {
                      setAllowDuringPause(e.target.checked);
                      if (error !== null) setError(null);
                    }}
                    disabled={submitting}
                    className="mt-0.5 h-4 w-4 rounded border-amber-400 text-amber-600 focus:ring-amber-500 dark:border-amber-600 dark:bg-zinc-950"
                    data-new-task-pause-override-toggle
                  />
                  <span className="flex-1">
                    Allow this task during pause{" "}
                    <span className="font-normal opacity-80">
                      (project is paused — POST will 423 without this)
                    </span>
                  </span>
                </label>
                {allowDuringPause && (
                  <label className="mt-2 block text-xs font-medium text-amber-900 dark:text-amber-200">
                    Reason{" "}
                    <span className="font-normal opacity-80">
                      (≥{ALLOW_DURING_PAUSE_REASON_MIN_CHARS} chars)
                    </span>{" "}
                    <span className="text-red-600 dark:text-red-400">*</span>
                    <textarea
                      value={allowDuringPauseReason}
                      onChange={(e) => {
                        setAllowDuringPauseReason(e.target.value);
                        if (error !== null) setError(null);
                      }}
                      rows={2}
                      placeholder="Why is this task required despite the pause? Captured into projects_audit (action='pause_override')."
                      disabled={submitting}
                      aria-invalid={
                        allowDuringPauseReason.length > 0 &&
                        !overrideReasonValid
                      }
                      className="mt-1 block w-full rounded border border-amber-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-amber-500 focus:outline-none disabled:opacity-50 dark:border-amber-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
                      data-new-task-pause-override-reason
                    />
                    <span className="mt-0.5 block text-[10px] tabular-nums opacity-80">
                      {trimmedOverrideReason.length}/
                      {ALLOW_DURING_PAUSE_REASON_MIN_CHARS}
                    </span>
                  </label>
                )}
              </div>
            )}

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
                data-new-task-error
              >
                {error}
              </p>
            )}

            {/* #954 — 44px min tap target on mobile for the modal action pair */}
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-new-task-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
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
