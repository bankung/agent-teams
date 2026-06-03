"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  createTask,
  listMilestones,
  HttpError,
  type ActionTemplateRead,
  type MilestoneRead,
  type ProjectRead,
  type TaskCreateBody,
} from "@/lib/api";
import {
  PRIORITY_OPTIONS,
  REASON_MIN_CHARS,
  ROLE_OPTIONS,
  TaskPriority,
  TaskStatus,
  type TaskPriorityValue,
  type TaskRoleValue,
  type TaskStatusValue,
} from "@/lib/constants";
import { filterRoleOptions } from "@/lib/enabledRoles";
import { extractErrorMessage } from "@/lib/errors";
import { ActionTemplatePicker } from "./ActionTemplatePicker";
import { PauseOverrideBlock } from "./PauseOverrideBlock";
import { HandoffTemplatePicker } from "./HandoffTemplatePicker";
import { Icon } from "./Icon";
import { ModalShell } from "./ModalShell";
import { ModelTierSelect } from "./ModelTierSelect";

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
  // #1781 — optional external open control (mirrors Pause/KillProjectModal).
  // When provided, the component renders no internal trigger button and the
  // +New dropdown owns the open state.
  externalOpen?: boolean;
  onExternalClose?: () => void;
};


export function NewTaskModal({
  projectId,
  enabledRoles,
  project,
  onPushToast,
  externalOpen,
  onExternalClose,
}: Props) {
  const isProjectPaused = project?.is_paused === true;
  // #7 §A AC#3 — narrow role dropdown to project.config.enabled_roles when set.
  // Unassigned sentinel is always retained.
  const visibleRoleOptions = useMemo(
    () => filterRoleOptions(ROLE_OPTIONS, enabledRoles),
    [enabledRoles],
  );
  const router = useRouter();
  // #1781 — external open wins when provided; otherwise self-managed.
  const [internalOpen, setInternalOpen] = useState(false);
  const open = externalOpen !== undefined ? externalOpen : internalOpen;
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
  // #1677 — per-task model-tier override. null = Inherit (default).
  const [modelOverride, setModelOverride] = useState<"haiku" | "sonnet" | "opus" | null>(null);
  // #1868 — optional milestone grouping ("" = none) + display/planning date.
  const [milestoneId, setMilestoneId] = useState<"" | number>("");
  const [dueDate, setDueDate] = useState("");
  const [milestones, setMilestones] = useState<MilestoneRead[]>([]);
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    titleInputRef.current?.focus();
  }, [open]);

  // #1868 — load the project's active milestones when the modal opens so the
  // picker is populated. Failure degrades to an empty list (the picker just
  // shows "None"); a milestone-list outage shouldn't block task creation.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    listMilestones(projectId, { limit: 500 })
      .then((rows) => {
        if (!cancelled) setMilestones(rows);
      })
      .catch(() => {
        if (!cancelled) setMilestones([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  function closeModal() {
    if (submitting) return;
    setInternalOpen(false);
    onExternalClose?.();
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
    setModelOverride(null);
    setMilestoneId("");
    setDueDate("");
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
    trimmedOverrideReason.length >= REASON_MIN_CHARS;
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
      // #1677 — only include when a tier is explicitly chosen; null/omit = inherit.
      ...(modelOverride !== null ? { model_override: modelOverride } : {}),
      // #1868 — optional milestone grouping + due date. Omitting them sends
      // nothing (BE defaults to NULL = unassigned / unset).
      ...(milestoneId !== "" ? { milestone_id: milestoneId } : {}),
      ...(dueDate !== "" ? { due_date: dueDate } : {}),
    };

    try {
      await createTask(projectId, body);
      router.refresh();
      setInternalOpen(false);
      onExternalClose?.();
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
        setError(extractErrorMessage(err, "Create failed"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      {/* #954 — 44px min tap target on mobile.
          #1781 — when the +New dropdown drives this modal (externalOpen set),
          render no internal trigger; the dropdown owns the open state. */}
      {externalOpen === undefined && (
        <button
          type="button"
          onClick={() => setInternalOpen(true)}
          className="inline-flex items-center gap-1.5 rounded border border-zinc-300 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
          data-new-task-trigger
        >
          <Icon name="add-task" size={14} aria-hidden />
          <span>New task</span>
        </button>
      )}
      {/* #954 — mobile: full-screen sheet (no padding, edge-to-edge); desktop restores centered max-w-md card */}
      <ModalShell
        open={open}
        onClose={() => { if (!submitting) closeModal(); }}
        labelledBy="new-task-title"
        backdropProps={{ "data-new-task-modal": true }}
      >
          <form
            onSubmit={onSubmit}
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

            {/* #1677 — per-task model-tier override dropdown */}
            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Model tier{" "}
              <span className="font-normal text-zinc-400">(optional)</span>
              <ModelTierSelect
                value={modelOverride ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  setModelOverride(
                    v === "" ? null : (v as "haiku" | "sonnet" | "opus"),
                  );
                  if (error !== null) setError(null);
                }}
                disabled={submitting}
                data-new-task-model-override
              />
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

            {/* #1868 — optional milestone picker + due date. */}
            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Milestone{" "}
                <span className="font-normal text-zinc-400">(optional)</span>
                <select
                  value={milestoneId === "" ? "" : String(milestoneId)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setMilestoneId(v === "" ? "" : Number(v));
                    if (error !== null) setError(null);
                  }}
                  disabled={submitting}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                  data-new-task-milestone
                >
                  <option value="">None</option>
                  {milestones.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.title}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Due date{" "}
                <span className="font-normal text-zinc-400">(optional)</span>
                <input
                  type="date"
                  value={dueDate}
                  onChange={(e) => {
                    setDueDate(e.target.value);
                    if (error !== null) setError(null);
                  }}
                  disabled={submitting}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                  data-new-task-due-date
                />
              </label>
            </div>

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

            {/* #1238 GOV3 — paused-project override (E3: extracted to PauseOverrideBlock). */}
            {isProjectPaused && (
              <PauseOverrideBlock
                allowDuringPause={allowDuringPause}
                setAllowDuringPause={setAllowDuringPause}
                allowDuringPauseReason={allowDuringPauseReason}
                setAllowDuringPauseReason={setAllowDuringPauseReason}
                disabled={submitting}
                onClearError={() => { if (error !== null) setError(null); }}
                trimmedOverrideReason={trimmedOverrideReason}
                overrideReasonValid={overrideReasonValid}
                dataPrefix="new-task"
              />
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
      </ModalShell>
    </>
  );
}
