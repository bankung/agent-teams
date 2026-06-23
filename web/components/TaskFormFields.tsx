"use client";

// Shared task-creation form fields (#2373 R3).
//
// NewTaskModal + AiTaskModal rendered ~400 LOC of near-identical field JSX. This
// presentational component owns the common editable fields so both modals render
// one <TaskFormFields/> instead of duplicating the markup. It does NO fetching
// and OWNS no state — every value + setter is passed in by the parent, so
// template seeding (NewTaskModal) and AI-parse pre-fill (AiTaskModal) keep
// driving the same fields.
//
// data-* prefix parametrization — the two modals are queried by DIFFERENT
// data-* prefixes (data-new-task-* vs data-ai-task-*). The `prefix` prop selects
// which set this instance emits so EVERY original data-attr is preserved
// byte-identical per consumer (the #1310 test suite depends on this).
//
// Layout: 7 always-visible "common" sections (title, type, priority, role,
// milestone, due_date, description) + an "Advanced details" disclosure
// (collapsed by default) wrapping the RARE fields: blocked_by, model tier
// override, and the handoff template picker.

import type { MilestoneRead } from "@/lib/api";
import {
  PRIORITY_OPTIONS,
  type RoleOption,
  type TaskPriorityValue,
  type TaskRoleValue,
} from "@/lib/constants";
import { DatePicker } from "./DatePicker";
import { HandoffTemplatePicker } from "./HandoffTemplatePicker";
import { MilestoneCombobox } from "./MilestoneCombobox";
import { ModelTierSelect } from "./ModelTierSelect";

// task_type options — identical set both modals already render.
export const TASK_TYPE_FIELD_OPTIONS = [
  { value: "feature", label: "Feature" },
  { value: "bug", label: "Bug" },
  { value: "chore", label: "Chore" },
  { value: "docs", label: "Docs" },
  { value: "refactor", label: "Refactor" },
] as const;

export type TaskFormType =
  | "bug"
  | "feature"
  | "chore"
  | "docs"
  | "refactor";

type Props = {
  // data-* namespace selector. "new-task" → data-new-task-*; "ai-task" →
  // data-ai-task-*. Drives every data-attr below.
  prefix: "new-task" | "ai-task";

  // Title (required). `titleRef` lets the parent focus it on open.
  title: string;
  onTitleChange: (v: string) => void;
  titleValid: boolean;
  titleRef?: React.Ref<HTMLInputElement>;

  // Type / priority / role.
  taskType: TaskFormType;
  onTaskTypeChange: (v: TaskFormType) => void;
  priority: TaskPriorityValue;
  onPriorityChange: (v: TaskPriorityValue) => void;
  role: "" | TaskRoleValue;
  onRoleChange: (v: "" | TaskRoleValue) => void;
  roleOptions: RoleOption[];

  // Milestone + due date.
  milestoneId: "" | number;
  onMilestoneChange: (v: "" | number) => void;
  milestones: MilestoneRead[];
  dueDate: string;
  onDueDateChange: (v: string) => void;

  // Description.
  description: string;
  onDescriptionChange: (v: string) => void;

  // Advanced — blocked_by.
  blockedBy: string;
  onBlockedByChange: (v: string) => void;
  blockedByValid: boolean;

  // Advanced — model tier override.
  modelOverride: "haiku" | "sonnet" | "opus" | null;
  onModelOverrideChange: (v: "haiku" | "sonnet" | "opus" | null) => void;

  // Advanced — handoff template picker (fetches its own data via projectId).
  projectId: number;
  handoffTemplateId: number | null;
  onHandoffTemplateChange: (id: number | null) => void;

  disabled?: boolean;
};

const FIELD_CLS =
  "mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500";
const TEXT_FIELD_CLS =
  "mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500";
const LABEL_CLS =
  "mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300";

export function TaskFormFields({
  prefix,
  title,
  onTitleChange,
  titleValid,
  titleRef,
  taskType,
  onTaskTypeChange,
  priority,
  onPriorityChange,
  role,
  onRoleChange,
  roleOptions,
  milestoneId,
  onMilestoneChange,
  milestones,
  dueDate,
  onDueDateChange,
  description,
  onDescriptionChange,
  blockedBy,
  onBlockedByChange,
  blockedByValid,
  modelOverride,
  onModelOverrideChange,
  projectId,
  handoffTemplateId,
  onHandoffTemplateChange,
  disabled,
}: Props) {
  // data-* attr helpers keyed off the prefix so every consumer keeps its exact
  // selector (e.g. data-new-task-title / data-ai-task-title).
  const d = (suffix: string) => ({ [`data-${prefix}-${suffix}`]: true });

  return (
    <>
      <label className={LABEL_CLS}>
        Title <span className="text-red-600 dark:text-red-400">*</span>
        <input
          ref={titleRef}
          type="text"
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder="Short imperative summary"
          autoComplete="off"
          disabled={disabled}
          aria-invalid={title.length > 0 && !titleValid}
          className={TEXT_FIELD_CLS}
          {...d("title")}
        />
      </label>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Type <span className="text-red-600 dark:text-red-400">*</span>
          <select
            value={taskType}
            onChange={(e) => onTaskTypeChange(e.target.value as TaskFormType)}
            disabled={disabled}
            className={FIELD_CLS}
            {...d("type")}
          >
            {TASK_TYPE_FIELD_OPTIONS.map((o) => (
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
            onChange={(e) =>
              onPriorityChange(Number(e.target.value) as TaskPriorityValue)
            }
            disabled={disabled}
            className={FIELD_CLS}
            {...d("priority")}
          >
            {PRIORITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className={LABEL_CLS}>
        Role <span className="font-normal text-zinc-400">(optional)</span>
        <select
          value={role === "" ? "" : String(role)}
          onChange={(e) => {
            const v = e.target.value;
            onRoleChange(v === "" ? "" : (Number(v) as TaskRoleValue));
          }}
          disabled={disabled}
          className={FIELD_CLS}
          {...d("role")}
        >
          {roleOptions.map((o) => (
            <option key={String(o.value)} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Milestone <span className="font-normal text-zinc-400">(optional)</span>
          <MilestoneCombobox
            value={milestoneId === "" ? null : milestoneId}
            onChange={(id) => onMilestoneChange(id === null ? "" : id)}
            milestones={milestones}
            disabled={disabled}
            inputProps={d("milestone")}
          />
        </div>
        <div className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Due date <span className="font-normal text-zinc-400">(optional)</span>
          <DatePicker
            value={dueDate}
            onChange={(v) => onDueDateChange(v ?? "")}
            disabled={disabled}
            inputProps={d("due-date")}
          />
        </div>
      </div>

      <label className={LABEL_CLS}>
        Description{" "}
        <span className="font-normal text-zinc-400">(optional)</span>
        <textarea
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder="Markdown supported"
          rows={4}
          disabled={disabled}
          className={TEXT_FIELD_CLS}
          {...d("description")}
        />
      </label>

      {/* Advanced details — RARE fields, collapsed by default so the typical
          create flow shows ~7 sections. blocked_by / model tier / handoff. */}
      <details className="mt-3 group" {...d("advanced")}>
        <summary
          className="cursor-pointer select-none text-xs font-medium text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
          {...d("advanced-summary")}
        >
          Advanced details
        </summary>

        <label className={LABEL_CLS}>
          Blocked by{" "}
          <span className="font-normal text-zinc-400">(optional task id)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={blockedBy}
            onChange={(e) => onBlockedByChange(e.target.value)}
            placeholder="e.g. 123"
            disabled={disabled}
            aria-invalid={blockedBy.length > 0 && !blockedByValid}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
            {...d("blocked-by")}
          />
        </label>

        <label className={LABEL_CLS}>
          Model tier{" "}
          <span className="font-normal text-zinc-400">(optional)</span>
          <ModelTierSelect
            value={modelOverride ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              onModelOverrideChange(
                v === "" ? null : (v as "haiku" | "sonnet" | "opus"),
              );
            }}
            disabled={disabled}
            {...d("model-override")}
          />
        </label>

        {/* Self-hides when no handoff templates exist (empty GET response). */}
        <HandoffTemplatePicker
          projectId={projectId}
          selectedId={handoffTemplateId}
          onSelect={onHandoffTemplateChange}
          disabled={disabled}
        />
      </details>
    </>
  );
}
