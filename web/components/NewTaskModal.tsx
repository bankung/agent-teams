"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  createTask,
  listMilestones,
  listTaskTemplates,
  HttpError,
  type AcceptanceCriterion,
  type ActionTemplateRead,
  type MilestoneRead,
  type ProjectRead,
  type TaskCreateBody,
  type TaskTemplateRead,
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
import { TaskTemplatePicker } from "./TaskTemplatePicker";
import { PauseOverrideBlock } from "./PauseOverrideBlock";
import { Icon } from "./Icon";
import { ModalShell } from "./ModalShell";
import { TaskFormFields } from "./TaskFormFields";

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

// #1310 — task_type values the modal's <select> can represent. A template's
// default_task_type only seeds the form when it's one of these (e.g. 'audit'
// or unknown kinds are ignored, leaving the current selection).
const MODAL_TASK_TYPES = [
  "feature",
  "bug",
  "chore",
  "docs",
  "refactor",
] as const;
type ModalTaskType = (typeof MODAL_TASK_TYPES)[number];

// #1310 — replace every {{key}} with values[key] when that value is a non-empty
// string; otherwise leave the literal {{key}} in place so unfilled placeholders
// stay visible. Single regex pass; pure.
function substitutePlaceholders(
  text: string,
  values: Record<string, string>,
): string {
  return text.replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (match, key: string) => {
    const v = values[key];
    return typeof v === "string" && v.trim() !== "" ? v : match;
  });
}

// #1310 — derive the pre-filled description + AC rows from a template given the
// current placeholder values. Pure; substitution never throws.
// #1310 r4 null-safety: guard against partial/malformed template objects so a
// bad template degrades gracefully instead of white-screening the modal.
function deriveFromTemplate(
  template: TaskTemplateRead,
  values: Record<string, string>,
): { description: string; ac: { text: string }[] } {
  return {
    description: substitutePlaceholders(String(template.description_template ?? ""), values),
    ac: (Array.isArray(template.acceptance_criteria_template)
      ? template.acceptance_criteria_template
      : []
    )
      .filter((row) => row && typeof row.text === "string")
      .map((row) => ({
        text: substitutePlaceholders(row.text, values),
      })),
  };
}


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
  // Wave E (#11) — pre-fill the due_date ("YYYY-MM-DD") when the modal is opened
  // from a Calendar day cell's "New task on this date" action. Seeds the field
  // on open; the operator can still edit/clear it before submit. resetFields
  // restores this initial value rather than blanking it.
  initialDueDate?: string;
};


export function NewTaskModal({
  projectId,
  enabledRoles,
  project,
  onPushToast,
  externalOpen,
  onExternalClose,
  initialDueDate,
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
  // Wave B (#4) — task_type selector. Default 'feature' mirrors the BE default;
  // 'bug' triggers the red border on the board and in ListView.
  const [taskType, setTaskType] = useState<"bug" | "feature" | "chore" | "docs" | "refactor">("feature");
  // #1868 — optional milestone grouping ("" = none) + display/planning date.
  const [milestoneId, setMilestoneId] = useState<"" | number>("");
  // Wave E (#11) — seed from initialDueDate (Calendar "New task on this date").
  const [dueDate, setDueDate] = useState(initialDueDate ?? "");
  const [milestones, setMilestones] = useState<MilestoneRead[]>([]);
  // #1310 — Task Template picker. `templates` is fetched on open from the
  // GLOBAL /api/task-templates surface; `selectedTemplateId` tracks the chosen
  // row; `placeholderValues` holds the live {{key}} inputs; `acceptanceCriteria`
  // is the editable AC list seeded from the template (also sent on submit).
  const [templates, setTemplates] = useState<TaskTemplateRead[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(
    null,
  );
  const [placeholderValues, setPlaceholderValues] = useState<
    Record<string, string>
  >({});
  const [acceptanceCriteria, setAcceptanceCriteria] = useState<
    { text: string }[]
  >([]);
  // #1310 — independent "user has taken over" flags. Once the user edits the
  // description (descriptionDirty) or the AC list (acDirty — text edit OR
  // structural add/remove), placeholder changes stop re-deriving THAT field,
  // but the other keeps live-substituting. Both reset on a fresh template
  // baseline. Splitting avoids: (a) "+ Add criterion" freezing the description,
  // and (b) the re-derive wiping a user-added AC row.
  const [descriptionDirty, setDescriptionDirty] = useState(false);
  const [acDirty, setAcDirty] = useState(false);
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

  // #1310 — load the team's task templates when the modal opens. GLOBAL
  // endpoint (no X-Project-Id). Failure degrades to [] (manual entry only) —
  // a template-list outage must NOT block task creation. When the project /
  // team is unknown, skip the fetch and show manual-entry only.
  useEffect(() => {
    if (!open) return;
    const team = project?.team;
    if (!team) {
      setTemplates([]);
      return;
    }
    let cancelled = false;
    listTaskTemplates(team, { limit: 200 })
      .then((rows) => {
        if (!cancelled) setTemplates(rows);
      })
      .catch(() => {
        if (!cancelled) setTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, project?.team]);

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
    setTaskType("feature");
    setMilestoneId("");
    // #1310 — clear the template selection + derived fields. `templates` itself
    // is left untouched (it refetches on open).
    setSelectedTemplateId(null);
    setPlaceholderValues({});
    setAcceptanceCriteria([]);
    setDescriptionDirty(false);
    setAcDirty(false);
    // Wave E (#11) — restore the calendar-seeded due_date rather than blanking
    // it, so a "New task on this date" flow keeps the target day on re-open.
    setDueDate(initialDueDate ?? "");
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

  // #1310 — Task Template selection. null = manual entry: clear the selection
  // and the pre-filled fields cleanly (AC#4). A template seeds priority +
  // (when it maps) task_type, then derives description + AC from the template
  // with empty placeholder values (so unfilled {{key}} stay literal/visible).
  function onSelectTemplate(t: TaskTemplateRead | null) {
    // #1310 — re-selecting the already-active template is a no-op (don't wipe
    // the user's filled placeholder values / derived edits).
    if (t !== null && t.id === selectedTemplateId) return;
    if (t === null) {
      setSelectedTemplateId(null);
      setPlaceholderValues({});
      setDescription("");
      setAcceptanceCriteria([]);
      setDescriptionDirty(false);
      setAcDirty(false);
      if (error !== null) setError(null);
      return;
    }
    setSelectedTemplateId(t.id);
    const values: Record<string, string> = {};
    setPlaceholderValues(values);
    // #1310 r4 — mirror the MODAL_TASK_TYPES guard: only apply default_priority
    // when it maps to a real option; an out-of-range value (e.g. 99) is silently
    // ignored so the current valid priority is kept. (#1310 round-4)
    if (PRIORITY_OPTIONS.some((o) => o.value === t.default_priority)) setPriority(t.default_priority);
    // Map default_task_type onto the modal's union ONLY when it's a value the
    // <select> can show; 'audit' / unknowns are ignored (current type kept).
    if ((MODAL_TASK_TYPES as readonly string[]).includes(t.default_task_type)) {
      setTaskType(t.default_task_type as ModalTaskType);
    }
    const derived = deriveFromTemplate(t, values);
    setDescription(derived.description);
    setAcceptanceCriteria(derived.ac);
    setDescriptionDirty(false);
    setAcDirty(false);
    if (error !== null) setError(null);
  }

  // #1310 — live substitution (AC#2). On every placeholder edit we re-derive
  // description + AC from the SELECTED template with the new values, UNLESS the
  // user has taken over that specific field (descriptionDirty / acDirty). Each
  // field is guarded independently so editing one doesn't freeze the other.
  function onPlaceholderChange(key: string, val: string) {
    const next = { ...placeholderValues, [key]: val };
    setPlaceholderValues(next);
    if (selectedTemplateId === null) return;
    const t = templates.find((x) => x.id === selectedTemplateId);
    if (!t) return;
    const derived = deriveFromTemplate(t, next);
    if (!descriptionDirty) setDescription(derived.description);
    if (!acDirty) setAcceptanceCriteria(derived.ac);
    if (error !== null) setError(null);
  }

  // #1310 — the currently selected template row (for placeholder rendering).
  const selectedTemplate =
    selectedTemplateId === null
      ? null
      : templates.find((t) => t.id === selectedTemplateId) ?? null;

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

    // #1909 AC3 — client-side pre-flight: BE caps acceptance_criteria at 50.
    // Count non-empty rows before the POST and show an inline error so the
    // operator can trim the list without a round-trip 422.
    const nonEmptyAc = acceptanceCriteria.filter((r) => r.text.trim() !== "");
    if (nonEmptyAc.length > 50) {
      setError(`Too many acceptance criteria (${nonEmptyAc.length}/50 max). Remove ${nonEmptyAc.length - 50} before submitting.`);
      return;
    }

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
      // Wave B (#4) — only include when operator chose a non-default type.
      // Omitting 'feature' is equivalent (BE default = 'feature') — just keeps
      // the payload minimal for the common case.
      ...(taskType !== "feature" ? { task_type: taskType } : {}),
      // #1868 — optional milestone grouping + due date. Omitting them sends
      // nothing (BE defaults to NULL = unassigned / unset).
      ...(milestoneId !== "" ? { milestone_id: milestoneId } : {}),
      ...(dueDate !== "" ? { due_date: dueDate } : {}),
      // #1310 — template-derived (or hand-edited) acceptance criteria. Only
      // non-empty rows are sent; each becomes a fresh `pending` AC. No template
      // id is sent — the created task is a plain task (pure client-side
      // pre-fill). Omitted entirely when no AC rows have text.
      ...(nonEmptyAc.length > 0
        ? {
            acceptance_criteria: nonEmptyAc.map(
              (r): AcceptanceCriterion => ({
                text: r.text.trim(),
                status: "pending",
                verified_by: null,
                verified_at: null,
                notes: null,
              }),
            ),
          }
        : {}),
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

            {/* #1310 — Task Template picker (native <select>). Pre-fills
                description + AC client-side. Empty-state note when the team has
                no templates; manual entry below stays fully usable. */}
            <TaskTemplatePicker
              templates={templates}
              team={project?.team ?? ""}
              selectedId={selectedTemplateId}
              onSelect={onSelectTemplate}
              disabled={submitting}
            />

            {/* #1310 — one text input per placeholder of the chosen template.
                Live substitution: each edit re-derives description + AC. */}
            {selectedTemplate !== null &&
              (selectedTemplate.placeholders ?? []).length > 0 && (
                <div className="mt-3 flex flex-col gap-2" data-new-task-placeholders>
                  {(selectedTemplate.placeholders ?? []).map((key) => (
                    <label
                      key={key}
                      className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      <span className="font-mono">{`{{${key}}}`}</span>
                      <input
                        type="text"
                        value={placeholderValues[key] ?? ""}
                        onChange={(e) => onPlaceholderChange(key, e.target.value)}
                        placeholder={`Value for ${key}`}
                        autoComplete="off"
                        disabled={submitting}
                        className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                        data-new-task-placeholder={key}
                      />
                    </label>
                  ))}
                </div>
              )}

            {/* Lane — NewTaskModal-only (AiTaskModal always files to TODO). */}
            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Lane <span className="text-red-600 dark:text-red-400">*</span>
              <select
                value={processStatus}
                onChange={(e) => {
                  setProcessStatus(Number(e.target.value) as TaskStatusValue);
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

            {/* #2373 R3 — shared common fields + Advanced disclosure. */}
            <TaskFormFields
              prefix="new-task"
              title={title}
              onTitleChange={(v) => {
                setTitle(v);
                if (error !== null) setError(null);
              }}
              titleValid={titleValid}
              titleRef={titleInputRef}
              taskType={taskType}
              onTaskTypeChange={(v) => {
                setTaskType(v);
                if (error !== null) setError(null);
              }}
              priority={priority}
              onPriorityChange={(v) => {
                setPriority(v);
                if (error !== null) setError(null);
              }}
              role={role}
              onRoleChange={(v) => {
                setRole(v);
                if (error !== null) setError(null);
              }}
              roleOptions={visibleRoleOptions}
              milestoneId={milestoneId}
              onMilestoneChange={(v) => {
                setMilestoneId(v);
                if (error !== null) setError(null);
              }}
              milestones={milestones}
              dueDate={dueDate}
              onDueDateChange={(v) => {
                setDueDate(v);
                if (error !== null) setError(null);
              }}
              description={description}
              onDescriptionChange={(v) => {
                setDescription(v);
                setDescriptionDirty(true);
                if (error !== null) setError(null);
              }}
              blockedBy={blockedBy}
              onBlockedByChange={(v) => {
                setBlockedBy(v);
                if (error !== null) setError(null);
              }}
              blockedByValid={blockedByValid}
              modelOverride={modelOverride}
              onModelOverrideChange={(v) => {
                setModelOverride(v);
                if (error !== null) setError(null);
              }}
              projectId={projectId}
              handoffTemplateId={handoffTemplateId}
              onHandoffTemplateChange={(id) => {
                setHandoffTemplateId(id);
                if (error !== null) setError(null);
              }}
              disabled={submitting}
            />

            {/* #1310 — acceptance-criteria editor. Visible only when a template
                is selected; seeded from the template (substituted), then freely
                editable. Non-empty rows are sent on submit as `pending` ACs. */}
            {/* #1310 — AC editor shown only when a template is selected; standalone manual AC entry is out of scope for this task. */}
            {/* #1310 r4 — gate on resolved template (same source as placeholder editor) so a stale id can't render the AC editor without its template. */}
            {selectedTemplate !== null && (
              <div className="mt-3 flex flex-col gap-2" data-new-task-ac-editor>
                <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  Acceptance criteria{" "}
                  <span className="font-normal text-zinc-400">
                    (from template, editable)
                  </span>
                </span>
                {acceptanceCriteria.map((row, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={row.text}
                      onChange={(e) => {
                        const next = acceptanceCriteria.slice();
                        next[i] = { text: e.target.value };
                        setAcceptanceCriteria(next);
                        setAcDirty(true);
                        if (error !== null) setError(null);
                      }}
                      placeholder="Criterion"
                      autoComplete="off"
                      disabled={submitting}
                      className="block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                      data-new-task-ac-row
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setAcceptanceCriteria(
                          acceptanceCriteria.filter((_, j) => j !== i),
                        );
                        setAcDirty(true);
                        if (error !== null) setError(null);
                      }}
                      disabled={submitting}
                      className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500 hover:border-zinc-300 hover:text-zinc-800 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-700 dark:hover:text-zinc-200"
                      data-new-task-ac-remove
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => {
                    setAcceptanceCriteria([...acceptanceCriteria, { text: "" }]);
                    setAcDirty(true);
                    if (error !== null) setError(null);
                  }}
                  disabled={submitting}
                  className="self-start rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                  data-new-task-ac-add
                >
                  + Add criterion
                </button>
              </div>
            )}

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
