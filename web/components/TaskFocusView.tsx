"use client";

// TaskFocusView — Kanban #1001 client orchestrator for /tasks/[id].
//
// Responsibilities:
//   1. Render a single-task focus card (title + #id + top-3 ACs + interaction
//      Q/A) sized to land within a 390x844 viewport without scroll.
//   2. Wire the action buttons (Approve / Reject / Halt / Open full) to the
//      API calls per the AC4 source-locked status_change_reason strings.
//   3. Surface inline errors below the button row on non-2xx; success path
//      shows a toast + redirects to /inbox.
//
// AC4 — push-attribution audit strings (locked verbatim — do not edit
// without coordinating with future audit-trail consumers):
//   - Approve work / question task:
//       status_change_reason = "Approved via push quick-action"
//   - Approve single-option decision (via /decide):
//       rationale            = "Approved via push quick-action"
//   - Reject:
//       status_change_reason = "Rejected via push quick-action: <user reason>"
//       (when the operator types nothing, the suffix is " (no reason)")
//   - Halt:
//       status_change_reason = "Halted via push quick-action: <halt_reason>"
//
// State ownership: this component holds `task` (current row), `submitting`
// flags per-action, the two modal-open flags, and the lightweight toast
// queue. The /inbox redirect is performed via router.push after a 600ms
// breather so the toast registers in the user's awareness before the route
// change. On error the redirect is skipped and the error renders inline.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  decideTask,
  patchTask,
  resolveHitlTask,
  type AcceptanceCriterion,
  type ProjectRead,
  type TaskRead,
} from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { TaskActionButtons } from "./TaskActionButtons";
import { TaskHaltModal } from "./TaskHaltModal";
import { TaskRejectModal } from "./TaskRejectModal";
import { ToastStack, type ToastMessage } from "./Toast";

type Props = {
  task: TaskRead;
  project: ProjectRead;
  actionHint: "approve" | "reject" | null;
};

// AC4 locked source strings — referenced multiple times below.
const REASON_APPROVE = "Approved via push quick-action";
const REASON_REJECT_PREFIX = "Rejected via push quick-action";
const REASON_HALT_PREFIX = "Halted via push quick-action";
const REJECT_NO_REASON_SUFFIX = "(no reason)";

const TERMINAL_STATUSES: ReadonlyArray<number> = [
  TaskStatus.DONE,
  TaskStatus.CANCELLED,
  // #2423: ps=8 (HALTED_PENDING_USER) intentionally excluded — it is an actionable pending-user state, not terminal.
];

const STATUS_LABEL: Record<number, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
  [TaskStatus.HALTED_PENDING_USER]: "halted",
};

// `options` is typed as string[] on the wire (legacy free-form question
// shape). Decision tasks carry OptionItem dicts inside the same JSONB.
// Narrow at runtime per opt — defensive against the type widening.
type OptItem = { id: string; label: string; description?: string | null };
function narrowOption(opt: unknown): OptItem | string {
  if (typeof opt === "string") return opt;
  if (
    opt !== null &&
    typeof opt === "object" &&
    "id" in opt &&
    "label" in opt &&
    typeof (opt as OptItem).id === "string" &&
    typeof (opt as OptItem).label === "string"
  ) {
    return opt as OptItem;
  }
  // Fallback — render whatever we have as a JSON string. Should not happen
  // against a well-formed BE payload.
  return JSON.stringify(opt);
}

// HITL-resume option-id resolver (Kanban #1451). Returns the id string for the
// Nth option in question_payload.options, supporting both legacy plain-string
// shape and new {id,label} dict shape. Returns null when the index is out of
// range (caller decides — Approve falls back to `action:'approve'` with no
// option; Reject hides the button entirely).
function resolveHitlOptionId(
  task: TaskRead,
  index: number,
): string | null {
  const raw = task.question_payload?.options ?? [];
  if (index < 0 || index >= raw.length) return null;
  const opt = narrowOption(raw[index]);
  if (typeof opt === "string") return opt;
  return opt.id;
}

// HITL-resume branch predicate (Kanban #1451). True when this task is a
// question/decision with is_pending=true — the resolveHitlTask path applies.
// Work tasks + non-pending tasks fall through to legacy PATCH ps=DONE / etc.
function isHitlResumeTask(task: TaskRead): boolean {
  return (
    (task.interaction_kind === "question" ||
      task.interaction_kind === "decision") &&
    task.is_pending === true
  );
}

export function TaskFocusView({ task: initialTask, project, actionHint }: Props) {
  const router = useRouter();
  const [task, setTask] = useState(initialTask);
  const [submitting, setSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [haltOpen, setHaltOpen] = useState(false);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const isTerminal = TERMINAL_STATUSES.includes(task.process_status);

  // Resolve Approve semantics based on interaction kind + payload shape.
  // Returns:
  //   - mode='disabled' — no meaningful Approve (terminal task / multi-option
  //     decision / orphan free-text question with no answer to accept)
  //   - mode='patch_done' — Approve = PATCH ps=5 + status_change_reason
  //   - mode='decide_single' — Approve = POST /decide with the single option's id
  const approveResolution = useMemo(() => {
    if (isTerminal) {
      return {
        mode: "disabled" as const,
        label: "Approve",
        reason: `Task already ${STATUS_LABEL[task.process_status]}`,
      };
    }
    if (task.interaction_kind === "decision") {
      const rawOptions = task.question_payload?.options ?? [];
      const narrowed = rawOptions.map(narrowOption);
      const structured = narrowed.filter(
        (o): o is OptItem => typeof o !== "string",
      );
      // Single structured option → /decide path. Multi-option → disable the
      // top Approve button; the inline option-chooser below handles it.
      if (structured.length === 1) {
        return {
          mode: "decide_single" as const,
          label: `Approve: ${structured[0].label}`,
          singleOption: structured[0],
        };
      }
      return {
        mode: "disabled" as const,
        label: "Approve",
        reason:
          structured.length > 1
            ? "Choose an option below"
            : "Decision task has no structured options",
      };
    }
    // 'work' or 'question' — Approve = PATCH done.
    return { mode: "patch_done" as const, label: "Approve" };
  }, [task, isTerminal]);

  function pushToast(text: string) {
    setToasts((prev) => [...prev, { id: Date.now() + Math.random(), text }]);
  }
  function dismissToast(id: number) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  // After a successful action, show a toast + redirect to /inbox. The
  // 600ms delay lets the user read the toast before the route change.
  function onSuccessRedirect(toastText: string) {
    pushToast(toastText);
    setTimeout(() => {
      router.push("/inbox");
    }, 600);
  }

  async function onApprove() {
    if (submitting || approveResolution.mode === "disabled") return;
    setInlineError(null);
    setSubmitting(true);
    try {
      // Kanban #1451 — HITL RESUME path. question/decision tasks with
      // is_pending=true call POST /api/tasks/{id}/decide with the new body
      // shape; the BE writes resume_context + flips is_pending=false WITHOUT
      // changing process_status (Lead resumes via SSE). All other tasks fall
      // through to the legacy PATCH ps=DONE / decideTask terminate paths.
      if (isHitlResumeTask(task)) {
        const selectedOption =
          resolveHitlOptionId(task, 0) ?? undefined;
        await resolveHitlTask(project.id, task.id, {
          action: "approve",
          ...(selectedOption !== undefined && { selected_option: selectedOption }),
        });
        // Resume path — process_status is unchanged on the row. We don't
        // re-fetch; the SSE broker will notify Lead. Just toast + redirect.
      } else if (approveResolution.mode === "decide_single") {
        const updated = await decideTask(project.id, task.id, {
          chosen_id: approveResolution.singleOption.id,
          rationale: REASON_APPROVE,
        });
        setTask(updated);
      } else {
        const updated = await patchTask(project.id, task.id, {
          process_status: TaskStatus.DONE,
          status_change_reason: REASON_APPROVE,
        });
        setTask(updated);
      }
      onSuccessRedirect("Task approved");
    } catch (err: unknown) {
      const msg = extractErrorMessage(err, "Approve failed");
      setInlineError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  async function onPickDecisionOption(option: OptItem) {
    if (submitting) return;
    setInlineError(null);
    setSubmitting(true);
    try {
      // Kanban #1451 — HITL RESUME path for multi-option decision picks.
      // is_pending=true → resolveHitlTask (resume, ps unchanged). Otherwise
      // legacy decideTask (DONE-flip).
      if (isHitlResumeTask(task)) {
        await resolveHitlTask(project.id, task.id, {
          action: "approve",
          selected_option: option.id,
        });
      } else {
        const updated = await decideTask(project.id, task.id, {
          chosen_id: option.id,
          rationale: `${REASON_APPROVE} (chose ${option.label})`,
        });
        setTask(updated);
      }
      onSuccessRedirect(`Chose: ${option.label}`);
    } catch (err: unknown) {
      const msg = extractErrorMessage(err, "Decision failed");
      setInlineError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  async function onConfirmReject(reason: string) {
    if (submitting) return;
    setInlineError(null);
    setSubmitting(true);
    try {
      // Kanban #1451 — HITL RESUME path. Reject for question/decision tasks
      // with is_pending=true maps to the second option (or first if only one
      // exists — but in that case the Reject button is hidden per
      // `hideRejectForHitl` below). The free-form reason is preserved via
      // custom_text so the resume_context carries the operator's rationale
      // even on the option-pick path.
      if (isHitlResumeTask(task)) {
        const selectedOption =
          resolveHitlOptionId(task, 1) ?? resolveHitlOptionId(task, 0) ?? undefined;
        await resolveHitlTask(project.id, task.id, {
          action: "reject",
          ...(selectedOption !== undefined && { selected_option: selectedOption }),
          ...(reason.length > 0 && { custom_text: reason }),
        });
      } else {
        const reasonSuffix = reason.length > 0 ? reason : REJECT_NO_REASON_SUFFIX;
        const updated = await patchTask(project.id, task.id, {
          process_status: TaskStatus.CANCELLED,
          status_change_reason: `${REASON_REJECT_PREFIX}: ${reasonSuffix}`,
        });
        setTask(updated);
      }
      setRejectOpen(false);
      onSuccessRedirect("Task rejected");
    } catch (err: unknown) {
      const msg = extractErrorMessage(err, "Reject failed");
      setInlineError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  async function onConfirmHalt(haltReason: string) {
    if (submitting) return;
    setInlineError(null);
    setSubmitting(true);
    try {
      const updated = await patchTask(project.id, task.id, {
        process_status: TaskStatus.BLOCKED,
        halt_reason: haltReason,
        status_change_reason: `${REASON_HALT_PREFIX}: ${haltReason}`,
      });
      setTask(updated);
      setHaltOpen(false);
      onSuccessRedirect("Task halted");
    } catch (err: unknown) {
      const msg = extractErrorMessage(err, "Halt failed");
      setInlineError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  const acList: AcceptanceCriterion[] = task.acceptance_criteria ?? [];
  const acTotal = acList.length;
  const acPassed = acList.filter((c) => c.status === "passed").length;
  const acTop3 = acList.slice(0, 3);
  const acRemainder = Math.max(0, acTotal - 3);

  // Decision-task inline option chooser — rendered when the task is a
  // multi-option decision (the top Approve button is disabled in that case).
  const decisionOptions = useMemo(() => {
    if (task.interaction_kind !== "decision") return null;
    if (isTerminal) return null;
    const raw = task.question_payload?.options ?? [];
    const structured = raw
      .map(narrowOption)
      .filter((o): o is OptItem => typeof o !== "string");
    if (structured.length < 2) return null;
    return structured;
  }, [task, isTerminal]);

  // #1001 follow-up — once the ?task=<id> deep-link landed on Board (#1349
  // batch), the "Open full" button targets the matching task card directly.
  const openFullHref = `/p/${encodeURIComponent(project.name)}?task=${task.id}`;

  // Kanban #1451 — hide Reject when HITL has 0/1 options (no meaningful 2nd
  // choice to map Reject to). Non-HITL tasks keep Reject (CANCELLED terminate).
  const hitlOptionCount = task.question_payload?.options?.length ?? 0;
  const hideRejectForHitl = isHitlResumeTask(task) && hitlOptionCount < 2;

  return (
    <>
      <section
        data-task-focus-view
        data-interaction-kind={task.interaction_kind}
        data-task-status={task.process_status}
        className="flex flex-col gap-4"
      >
        {/* Header — id, status badge, title. */}
        <header className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono text-zinc-500 dark:text-zinc-400">
              #{task.id}
            </span>
            <span
              aria-label={STATUS_LABEL[task.process_status]}
              data-task-process-status={task.process_status}
              className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
            >
              {STATUS_LABEL[task.process_status]}
            </span>
            <span
              data-task-interaction-kind={task.interaction_kind}
              className="rounded bg-violet-50 px-1.5 py-0.5 font-mono uppercase tracking-wide text-violet-700 dark:bg-violet-900/30 dark:text-violet-200"
            >
              {task.interaction_kind}
            </span>
            <span className="text-zinc-500 dark:text-zinc-400">
              in {project.name}
            </span>
          </div>
          <h1
            id="task-focus-title"
            className="text-lg font-semibold leading-snug text-zinc-900 dark:text-zinc-100"
          >
            {task.title}
          </h1>
        </header>

        {/* Question prompt (read-only) for question/decision tasks. */}
        {task.interaction_kind !== "work" && task.question_payload?.question && (
          <div
            data-task-question-prompt
            className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm text-zinc-800 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-200"
          >
            <p className="whitespace-pre-wrap">
              {task.question_payload.question}
            </p>
          </div>
        )}

        {/* Decision option chooser (multi-option only — single option is
            handled by the top Approve button). Clicking a chip fires /decide
            immediately (deliberate-action — server-confirm-then-flip). */}
        {decisionOptions && (
          <div
            data-task-decision-options
            className="flex flex-col gap-2"
          >
            <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Choose an option
            </h2>
            <div className="flex flex-col gap-1.5">
              {decisionOptions.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  disabled={submitting}
                  onClick={() => onPickDecisionOption(opt)}
                  data-task-decision-option={opt.id}
                  className="flex flex-col gap-0.5 rounded border border-violet-200 bg-violet-50 px-3 py-2 text-left text-sm text-violet-800 hover:bg-violet-100 disabled:opacity-50 dark:border-violet-800 dark:bg-violet-900/30 dark:text-violet-200 dark:hover:bg-violet-900/50 min-h-[44px]"
                >
                  <span className="font-medium">{opt.label}</span>
                  {opt.description && (
                    <span className="text-xs text-violet-700 dark:text-violet-300">
                      {opt.description}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Top 3 acceptance criteria (compact). */}
        <div data-task-ac-summary className="flex flex-col gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Acceptance criteria{" "}
            {acTotal > 0 && (
              <span className="font-normal text-zinc-400 dark:text-zinc-500">
                ({acPassed}/{acTotal})
              </span>
            )}
          </h2>
          {acTotal === 0 ? (
            <p className="text-sm italic text-zinc-500 dark:text-zinc-400">
              (none defined)
            </p>
          ) : (
            <ol className="flex flex-col gap-1">
              {acTop3.map((c, idx) => (
                <li
                  key={idx}
                  data-ac-item
                  data-ac-status={c.status}
                  className="flex gap-2 text-sm"
                >
                  <span
                    aria-label={c.status}
                    className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-semibold ${
                      c.status === "passed"
                        ? "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                        : c.status === "failed"
                          ? "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                          : c.status === "na"
                            ? "bg-zinc-50 text-zinc-400 dark:bg-zinc-900 dark:text-zinc-500"
                            : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
                    }`}
                  >
                    {c.status === "passed"
                      ? "✓"
                      : c.status === "failed"
                        ? "✗"
                        : c.status === "na"
                          ? "—"
                          : "·"}
                  </span>
                  <span className="text-zinc-900 dark:text-zinc-100">
                    {c.text}
                  </span>
                </li>
              ))}
              {acRemainder > 0 && (
                <li className="text-xs italic text-zinc-500 dark:text-zinc-400">
                  +{acRemainder} more — open full to see all
                </li>
              )}
            </ol>
          )}
        </div>

        {/* Inline error (non-2xx) — sticks below the AC, above the button
            row (mobile: rendered before the sticky toolbar). */}
        {inlineError !== null && (
          <p
            role="alert"
            data-task-focus-error
            className="rounded border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
          >
            {inlineError}
          </p>
        )}

        {/* Action button row — sticky-bottom on mobile, inline on desktop. */}
        <TaskActionButtons
          approveLabel={approveResolution.label}
          approveDisabled={approveResolution.mode === "disabled"}
          approveDisabledReason={
            approveResolution.mode === "disabled"
              ? approveResolution.reason
              : undefined
          }
          submitting={submitting}
          actionHint={actionHint}
          hideReject={hideRejectForHitl}
          openFullHref={openFullHref}
          onApprove={onApprove}
          onRejectClick={() => setRejectOpen(true)}
          onHaltClick={() => setHaltOpen(true)}
        />
      </section>

      <TaskRejectModal
        open={rejectOpen}
        submitting={submitting}
        errorMessage={rejectOpen ? inlineError : null}
        onCancel={() => {
          if (!submitting) {
            setRejectOpen(false);
            setInlineError(null);
          }
        }}
        onConfirm={onConfirmReject}
      />
      <TaskHaltModal
        open={haltOpen}
        submitting={submitting}
        errorMessage={haltOpen ? inlineError : null}
        onCancel={() => {
          if (!submitting) {
            setHaltOpen(false);
            setInlineError(null);
          }
        }}
        onConfirm={onConfirmHalt}
      />

      <ToastStack messages={toasts} onDismiss={dismissToast} />
    </>
  );
}

