"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  createTask,
  HttpError,
  parseTaskText,
  type ParsedTaskProposal,
  type TaskCreateBody,
} from "@/lib/api";
import {
  TaskPriority,
  TaskRole,
  TaskStatus,
  type TaskPriorityValue,
  type TaskRoleValue,
} from "@/lib/constants";
import { Icon } from "./Icon";

// Trigger button + dialog for the AI-task flow (Kanban #857).
//
// Two phases — input then editable preview:
//   1. Input — user types a free-text description; click Parse → POST
//      /api/tasks/ai-parse → fills in title/description/task_type/priority/
//      assigned_role/blocked_by.
//   2. Preview — the parsed fields render as an editable form (pre-filled).
//      Confirm → POST /api/tasks via the existing createTask helper. Cancel
//      discards; "Edit prompt" returns to phase 1 keeping the original text.
//
// Sibling of NewTaskModal — same modal chrome (zinc panel, ESC closes, backdrop
// click closes, focus first input). Manual path remains intact; this is the
// AI-first alternative, mounted BEFORE the manual trigger in Board.tsx.
//
// Backend contract (Kanban #856):
//   200 → { proposed: ParsedTaskProposal }
//   422 → validation (empty text / invalid LLM output)
//   502 → provider 5xx / network
//   503 → provider not configured (e.g. ollama unsupported in api service, or
//          ANTHROPIC_API_KEY empty)
//   504 → provider exceeded 10s wall

type Phase = "input" | "preview";

type TaskTypeOption = {
  value: "bug" | "feature" | "chore" | "docs" | "refactor";
  label: string;
};

const TASK_TYPE_OPTIONS: TaskTypeOption[] = [
  { value: "bug", label: "Bug" },
  { value: "feature", label: "Feature" },
  { value: "chore", label: "Chore" },
  { value: "docs", label: "Docs" },
  { value: "refactor", label: "Refactor" },
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
};

type ErrorKind =
  | "validation" // 422
  | "not_configured" // 503
  | "transient" // 502 / 504
  | "generic";

type ParseError = {
  kind: ErrorKind;
  message: string;
  detail?: string;
};

export function AiTaskModal({ projectId }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("input");
  const [text, setText] = useState("");
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<ParseError | null>(null);

  // Preview-phase fields (pre-filled from `proposed`).
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [taskType, setTaskType] = useState<TaskTypeOption["value"]>("feature");
  const [priority, setPriority] = useState<TaskPriorityValue>(
    TaskPriority.NORMAL,
  );
  const [role, setRole] = useState<"" | TaskRoleValue>("");
  const [blockedBy, setBlockedBy] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const textInputRef = useRef<HTMLTextAreaElement | null>(null);
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    // Focus the appropriate first field for the current phase.
    if (phase === "input") textInputRef.current?.focus();
    else titleInputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !parsing && !creating) closeModal();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phase, parsing, creating]);

  function resetAll() {
    setPhase("input");
    setText("");
    setParseError(null);
    setTitle("");
    setDescription("");
    setTaskType("feature");
    setPriority(TaskPriority.NORMAL);
    setRole("");
    setBlockedBy("");
    setCreateError(null);
  }

  function closeModal() {
    if (parsing || creating) return;
    setOpen(false);
    resetAll();
  }

  function applyProposed(p: ParsedTaskProposal) {
    setTitle(p.title);
    setDescription(p.description);
    setTaskType(p.task_type);
    setPriority(p.priority);
    setRole(p.assigned_role === null ? "" : p.assigned_role);
    setBlockedBy(p.blocked_by === null ? "" : String(p.blocked_by));
  }

  function classifyError(err: unknown): ParseError {
    if (err instanceof HttpError) {
      if (err.status === 422) {
        return {
          kind: "validation",
          message: "Couldn't parse — try rewording.",
          detail: err.message,
        };
      }
      if (err.status === 503) {
        return {
          kind: "not_configured",
          message:
            "AI not configured. Set LANGGRAPH_LLM_PROVIDER + key in .env (then restart api).",
          detail: err.message,
        };
      }
      if (err.status === 502 || err.status === 504) {
        return {
          kind: "transient",
          message: "AI service unavailable, try again.",
          detail: err.message,
        };
      }
      return {
        kind: "generic",
        message: "Something went wrong.",
        detail: err.message,
      };
    }
    return {
      kind: "generic",
      message: "Something went wrong.",
      detail: err instanceof Error ? err.message : String(err),
    };
  }

  const trimmedText = text.trim();
  const canParse = !parsing && trimmedText.length > 0 && trimmedText.length <= 2000;

  async function onParse(e: React.FormEvent) {
    e.preventDefault();
    if (!canParse) return;
    setParseError(null);
    setParsing(true);
    try {
      const proposed = await parseTaskText(projectId, trimmedText);
      applyProposed(proposed);
      setPhase("preview");
    } catch (err: unknown) {
      setParseError(classifyError(err));
    } finally {
      setParsing(false);
    }
  }

  // Preview-phase validation mirrors NewTaskModal — title required, blocked_by
  // (when present) must be a positive integer.
  const trimmedTitle = title.trim();
  const titleValid = trimmedTitle.length > 0;
  const blockedByNum = blockedBy.trim() === "" ? null : Number(blockedBy);
  const blockedByValid =
    blockedByNum === null ||
    (Number.isInteger(blockedByNum) && blockedByNum >= 1);
  const canConfirm = !creating && titleValid && blockedByValid;

  async function onConfirm(e: React.FormEvent) {
    e.preventDefault();
    if (!canConfirm) return;
    setCreateError(null);
    setCreating(true);

    // TaskCreateBody doesn't expose task_type today (manual flow always sends
    // backend defaults). For the AI flow the user-edited task_type matters, so
    // we pass it via a structural-typed cast — the backend TaskCreate schema
    // accepts task_type as an optional string.
    const body: TaskCreateBody & { task_type?: string } = {
      project_id: projectId,
      title: trimmedTitle,
      process_status: TaskStatus.TODO,
      priority,
      task_type: taskType,
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(role !== "" ? { assigned_role: role } : {}),
      ...(blockedByNum !== null ? { blocked_by: blockedByNum } : {}),
    };

    try {
      await createTask(projectId, body);
      router.refresh();
      setOpen(false);
      resetAll();
    } catch (err: unknown) {
      if (err instanceof HttpError) setCreateError(err.message);
      else
        setCreateError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  function onEditPrompt() {
    // Return to input phase keeping the original text so the user can refine.
    setPhase("input");
    setParseError(null);
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded border border-violet-300 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-violet-700 hover:border-violet-400 hover:text-violet-900 dark:border-violet-700 dark:bg-zinc-900 dark:text-violet-300 dark:hover:border-violet-500 dark:hover:text-violet-100"
        data-ai-task-trigger
      >
        <Icon name="ai-agent" size={14} />
        <span>AI task</span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="ai-task-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 px-4 dark:bg-zinc-950/70"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-ai-task-modal
        >
          {phase === "input" ? (
            <form
              onSubmit={onParse}
              className="w-full max-w-md rounded border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
              data-ai-task-phase="input"
            >
              <h2
                id="ai-task-title"
                className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
              >
                <Icon name="ai-agent" size={14} />
                Create task with AI
              </h2>
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                Describe a task in plain language. The AI proposes fields; you
                review + edit before confirming.
              </p>

              <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Prompt{" "}
                <span className="text-red-600 dark:text-red-400">*</span>
                <textarea
                  ref={textInputRef}
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value);
                    if (parseError !== null) setParseError(null);
                  }}
                  placeholder="Describe a task in plain language (e.g. 'high priority backend bug for the login crash')"
                  rows={5}
                  maxLength={2000}
                  disabled={parsing}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                  data-ai-task-text
                />
              </label>
              <p className="mt-1 text-right text-[10px] text-zinc-400 dark:text-zinc-500">
                {trimmedText.length} / 2000
              </p>

              {parseError !== null && (
                <div
                  role="alert"
                  className={`mt-3 rounded border px-2 py-1.5 text-xs ${
                    parseError.kind === "not_configured"
                      ? "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
                      : "border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
                  }`}
                  data-ai-task-parse-error
                  data-ai-task-error-kind={parseError.kind}
                >
                  <p className="font-medium">{parseError.message}</p>
                  {parseError.detail && (
                    <details className="mt-1">
                      <summary className="cursor-pointer text-[11px] opacity-80">
                        details
                      </summary>
                      <p className="mt-1 break-all font-mono text-[11px] opacity-80">
                        {parseError.detail}
                      </p>
                    </details>
                  )}
                </div>
              )}

              <div className="mt-4 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={closeModal}
                  disabled={parsing}
                  className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                  data-ai-task-cancel
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!canParse}
                  className="inline-flex items-center gap-1.5 rounded border border-violet-600 bg-violet-600 px-2 py-1 text-xs font-medium uppercase tracking-wide text-white hover:bg-violet-700 disabled:opacity-50 dark:border-violet-500 dark:bg-violet-500 dark:hover:bg-violet-600"
                  data-ai-task-parse
                >
                  {parsing && (
                    <svg
                      aria-hidden
                      className="h-3 w-3 animate-spin"
                      viewBox="0 0 24 24"
                      fill="none"
                    >
                      <circle
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="3"
                        opacity="0.25"
                      />
                      <path
                        d="M12 2a10 10 0 0 1 10 10"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeLinecap="round"
                      />
                    </svg>
                  )}
                  <span>{parsing ? "Parsing…" : "Parse"}</span>
                </button>
              </div>
            </form>
          ) : (
            <form
              onSubmit={onConfirm}
              className="w-full max-w-md rounded border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
              data-ai-task-phase="preview"
            >
              <h2
                id="ai-task-title"
                className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
              >
                Review task
              </h2>
              <p
                className="mt-1 inline-flex items-center gap-1 text-[11px] text-violet-700 dark:text-violet-300"
                data-ai-task-parsed-badge
              >
                <Icon name="ai-agent" size={12} />
                <span>Parsed by AI — edit as needed</span>
              </p>

              <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Title <span className="text-red-600 dark:text-red-400">*</span>
                <input
                  ref={titleInputRef}
                  type="text"
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value);
                    if (createError !== null) setCreateError(null);
                  }}
                  placeholder="Short imperative summary"
                  autoComplete="off"
                  disabled={creating}
                  aria-invalid={title.length > 0 && !titleValid}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                  data-ai-task-title
                />
              </label>

              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  Type{" "}
                  <span className="text-red-600 dark:text-red-400">*</span>
                  <select
                    value={taskType}
                    onChange={(e) => {
                      setTaskType(e.target.value as TaskTypeOption["value"]);
                      if (createError !== null) setCreateError(null);
                    }}
                    disabled={creating}
                    className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                    data-ai-task-type
                  >
                    {TASK_TYPE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  Priority{" "}
                  <span className="text-red-600 dark:text-red-400">*</span>
                  <select
                    value={priority}
                    onChange={(e) => {
                      setPriority(Number(e.target.value) as TaskPriorityValue);
                      if (createError !== null) setCreateError(null);
                    }}
                    disabled={creating}
                    className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                    data-ai-task-priority
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
                Role{" "}
                <span className="font-normal text-zinc-400">(optional)</span>
                <select
                  value={role === "" ? "" : String(role)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setRole(v === "" ? "" : (Number(v) as TaskRoleValue));
                    if (createError !== null) setCreateError(null);
                  }}
                  disabled={creating}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                  data-ai-task-role
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
                    if (createError !== null) setCreateError(null);
                  }}
                  placeholder="e.g. 123"
                  disabled={creating}
                  aria-invalid={blockedBy.length > 0 && !blockedByValid}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                  data-ai-task-blocked-by
                />
              </label>

              <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Description{" "}
                <span className="font-normal text-zinc-400">(optional)</span>
                <textarea
                  value={description}
                  onChange={(e) => {
                    setDescription(e.target.value);
                    if (createError !== null) setCreateError(null);
                  }}
                  placeholder="Markdown supported"
                  rows={4}
                  disabled={creating}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                  data-ai-task-description
                />
              </label>

              {createError !== null && (
                <p
                  role="alert"
                  className="mt-3 text-xs text-red-700 dark:text-red-300"
                  data-ai-task-create-error
                >
                  {createError}
                </p>
              )}

              <div className="mt-4 flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={onEditPrompt}
                  disabled={creating}
                  className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                  data-ai-task-edit-prompt
                >
                  Edit prompt
                </button>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={closeModal}
                    disabled={creating}
                    className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                    data-ai-task-cancel
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={!canConfirm}
                    className="rounded border border-emerald-600 bg-emerald-600 px-2 py-1 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                    data-ai-task-confirm
                  >
                    {creating ? "Creating…" : "Confirm"}
                  </button>
                </div>
              </div>
            </form>
          )}
        </div>
      )}
    </>
  );
}
