"use client";

import { useEffect, useMemo, useState } from "react";

import {
  cancelTask,
  getTaskBlocks,
  invalidateAnswer,
  patchTask,
  submitAnswer,
  type AcceptanceCriterion,
  type AnswerHistoryEntry,
  type TaskRead,
} from "@/lib/api";
import { TaskKind, TaskRunMode, TaskStatus } from "@/lib/constants";
import { computeBlockedByExclusionSet } from "@/lib/cycleExclusion";
import { DecisionInteractionView } from "./DecisionInteractionView";
import { PendingBadge } from "./PendingBadge";
import { RunModeBadge } from "./RunModeBadge";
import { TaskKindBadge } from "./TaskKindBadge";
import { TaskMuteToggle } from "./TaskMuteToggle";
import { TaskToolCalls } from "./TaskToolCalls";

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
  [TaskStatus.CANCELLED]: "cancelled",
};

// Terminal states — Cancel/Done hide the Cancel button
const TERMINAL_STATUSES: ReadonlyArray<number> = [
  TaskStatus.DONE,
  TaskStatus.CANCELLED,
];

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// #944 — compact tokens (847 / 12k / 1.2M). Matches BE estimator output;
// 4-decimal "$X.XXXX" cost kept verbatim from the wire string.
function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${Math.round(n / 100) / 10}k`.replace(/\.0k$/, "k");
  return `${Math.round(n / 100_000) / 10}M`.replace(/\.0M$/, "M");
}

// TaskDetail — right-side drawer (#771); backdrop + Escape + click-outside, #818
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
  // #854 — inline cancel; state: cancelOpen / cancelReason
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (submitting) return;
      // Nested ESC precedence: inner UIs absorb ESC before drawer (#854)
      if (cancelOpen) {
        setCancelOpen(false);
        setCancelReason("");
      } else if (pickerOpen) {
        setPickerOpen(false);
      } else {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [cancelOpen, pickerOpen, submitting, onClose]);

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

  // #854 — PATCH ps=6 + reason; close drawer on success
  const handleCancelTask = async () => {
    if (submitting) return;
    const reason = cancelReason.trim();
    if (reason === "") return;
    setSubmitting(true);
    try {
      await cancelTask(projectId, task.id, reason);
      setCancelOpen(false);
      setCancelReason("");
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Cancel failed";
      onError(`Task #${task.id}: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  };

  const isTerminal = TERMINAL_STATUSES.includes(task.process_status);

  // #860 — show Run for TODO+ai+manual; auto_pickup over auto_headless skips consent gate. Details: shared/decisions.md 2026-05-14
  const canRun =
    task.process_status === TaskStatus.TODO &&
    task.task_kind === TaskKind.AI &&
    task.run_mode === TaskRunMode.MANUAL;

  const handleRun = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const updated = await patchTask(projectId, task.id, {
        run_mode: TaskRunMode.AUTO_PICKUP,
      });
      onPatch(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Run failed";
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
      aria-describedby="taskdetail-desc"
      data-task-detail-modal
      data-task-id={task.id}
      className="fixed inset-0 z-40 bg-zinc-900/40 dark:bg-zinc-950/70"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <aside
        // #818 — responsive width; tier breakpoints 480→640→720; details in shared/decisions.md
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[90vw] flex-col overflow-y-auto border-l border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 sm:max-w-[480px] md:max-w-[640px] lg:max-w-[720px]"
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
            <CostStrip task={task} />
          </div>
          {/* #954 — 44px min tap target on mobile; desktop restores px-2 py-1 chip size */}
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
            data-task-detail-close
            className="shrink-0 rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
          >
            Close
          </button>
        </header>

        {/* Body */}
        {/* #859 — gap-6 between sections; see Section/QuestionInteractionSection */}
        <div className="flex flex-col gap-6 px-4 py-4 text-sm">
          {/* #818 — fixed 120px label column */}
          <Section label="Status">
            <dl className="grid grid-cols-[120px_1fr] gap-y-1 text-sm">
              <dt className="text-zinc-500 dark:text-zinc-400">Status</dt>
              <dd className="text-zinc-900 dark:text-zinc-100">
                {STATUS_LABEL[task.process_status] ?? `ps${task.process_status}`}
              </dd>
              <dt className="text-zinc-500 dark:text-zinc-400">Priority</dt>
              <dd className="text-zinc-900 dark:text-zinc-100">
                {task.priority}
              </dd>
              {task.assigned_role !== null && (
                <>
                  <dt className="text-zinc-500 dark:text-zinc-400">Role</dt>
                  <dd className="text-zinc-900 dark:text-zinc-100">
                    {task.assigned_role}
                  </dd>
                </>
              )}
            </dl>
            {/* #854 — reason display; truncated, italic */}
            {task.status_change_reason && (
              <p
                data-status-change-reason
                title={task.status_change_reason}
                className="mt-1 text-xs italic text-zinc-500 dark:text-zinc-400"
              >
                {task.process_status === TaskStatus.CANCELLED
                  ? "Cancelled: "
                  : "Reason: "}
                {truncate(task.status_change_reason, 120)}
              </p>
            )}
            {/* #860 — Run: flips run_mode manual→auto_pickup; see canRun guard above */}
            {canRun && (
              <div className="mt-2" data-run-task-control>
                {/* #954 — 44px min tap target on mobile */}
                <button
                  type="button"
                  onClick={handleRun}
                  disabled={submitting}
                  data-run-task-trigger
                  className="rounded border border-violet-300 bg-violet-600 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white hover:bg-violet-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-3 sm:py-1 dark:border-violet-700 dark:bg-violet-700 dark:hover:bg-violet-600"
                >
                  {submitting ? "Queuing…" : "Run"}
                </button>
              </div>
            )}
            {/* #1349 — per-task HITL nudge toggle. Visible on non-terminal
                tasks (terminal tasks no longer fire nudges anyway, so the
                toggle would be cosmetic). Optimistic flip + revert on error. */}
            {!isTerminal && (
              <div className="mt-2" data-task-mute-control>
                <TaskMuteToggle
                  task={task}
                  projectId={projectId}
                  onPatch={onPatch}
                  onError={onError}
                />
              </div>
            )}
            {/* #854 — Cancel: hidden on terminal states */}
            {!isTerminal && (
              <div className="mt-2" data-cancel-task-control>
                {!cancelOpen ? (
                  // #954 — 44px min tap target on mobile
                  <button
                    type="button"
                    onClick={() => {
                      setCancelOpen(true);
                      setCancelReason("");
                    }}
                    disabled={submitting}
                    data-cancel-task-trigger
                    className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-600 hover:border-red-300 hover:text-red-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-0.5 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-red-800 dark:hover:text-red-300"
                  >
                    Cancel task
                  </button>
                ) : (
                  <div className="flex flex-col gap-1.5 rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-950/40">
                    <label
                      htmlFor="cancel-task-reason"
                      className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
                    >
                      Reason for cancellation
                    </label>
                    <input
                      id="cancel-task-reason"
                      type="text"
                      value={cancelReason}
                      onChange={(e) => setCancelReason(e.target.value)}
                      placeholder="e.g., superseded by #872; not needed for v2"
                      disabled={submitting}
                      autoFocus
                      data-cancel-task-reason-input
                      className="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                    />
                    {/* #954 — 44px min tap target on mobile for the cancel confirm pair */}
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={submitting || cancelReason.trim() === ""}
                        onClick={handleCancelTask}
                        data-cancel-task-confirm
                        className="rounded border border-red-300 bg-red-600 px-3 py-2 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-700"
                      >
                        Confirm cancel
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setCancelOpen(false);
                          setCancelReason("");
                        }}
                        disabled={submitting}
                        data-cancel-task-dismiss
                        className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
                      >
                        Back
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </Section>

          {task.description && (
            <Section label="Description">
              <p id="taskdetail-desc" className="whitespace-pre-wrap text-zinc-800 dark:text-zinc-200">
                {task.description}
              </p>
            </Section>
          )}

          {/* #834 / #1335 — question/decision section; hidden for work tasks.
              Decision tasks (interaction_kind='decision') render the
              full OptionCard variant from DecisionInteractionView; question
              tasks (free-text answers / legacy string-options) render the
              original QuestionInteractionSection chip variant. */}
          {task.interaction_kind === "decision" && (
            <DecisionInteractionView
              task={task}
              projectId={projectId}
              onPatch={onPatch}
              onError={onError}
            />
          )}
          {task.interaction_kind === "question" && (
            <QuestionInteractionSection
              task={task}
              projectId={projectId}
              onPatch={onPatch}
              onError={onError}
            />
          )}

          {/* #827 — AC section always rendered (discipline gate) */}
          <AcceptanceCriteriaSection criteria={task.acceptance_criteria} />

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
                {/* #954 — 44px min tap target on mobile for the blocker mutator buttons */}
                <span className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    disabled={submitting}
                    data-blocked-by-change
                    className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-0.5 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                  >
                    {task.blocked_by !== null ? "Change" : "Set blocker"}
                  </button>
                  {task.blocked_by !== null && (
                    <button
                      type="button"
                      onClick={() => setBlocker(null)}
                      disabled={submitting}
                      data-blocked-by-clear
                      className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-0.5 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
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
              <span role="status" className="text-zinc-400 italic dark:text-zinc-500">…</span>
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

          {/* #980 — per-task tool-call audit; self-hides when 0 rows */}
          <TaskToolCalls projectId={projectId} taskId={task.id} />
        </div>
      </aside>
    </div>
  );
}

// #944 — compact cost strip rendered under the task title in the header.
// Hidden entirely when all 3 estimate fields are null (legacy / never-closed tasks).
// Format: "~$0.0001 · 12k in / 4k out" — primary cost left, token breakdown right.
function CostStrip({ task }: { task: TaskRead }) {
  const cost = task.estimated_cost_usd;
  const inTok = task.estimated_input_tokens;
  const outTok = task.estimated_output_tokens;
  if (cost === null && inTok === null && outTok === null) return null;

  const parts: string[] = [];
  if (cost !== null) parts.push(`~$${cost}`);
  if (inTok !== null || outTok !== null) {
    const inStr = inTok !== null ? `${formatTokens(inTok)} in` : "— in";
    const outStr = outTok !== null ? `${formatTokens(outTok)} out` : "— out";
    parts.push(`${inStr} / ${outStr}`);
  }

  return (
    <p
      data-cost-strip
      className="mt-1 text-xs text-zinc-500 dark:text-zinc-400"
    >
      {parts.join(" · ")}
    </p>
  );
}

// #818 #859 — heavier header + gap-2 for scan-ability; mirrored on Question/AC sections
function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </h3>
      <div>{children}</div>
    </section>
  );
}

// #834 — answer UI for question/decision tasks
function QuestionInteractionSection({
  task,
  projectId,
  onPatch,
  onError,
}: {
  task: TaskRead;
  projectId: number;
  onPatch: (updated: TaskRead) => void;
  onError: (message: string) => void;
}) {
  const [answerValue, setAnswerValue] = useState("");
  const [submittingAnswer, setSubmittingAnswer] = useState(false);
  const [invalidateReasonFor, setInvalidateReasonFor] = useState<number | null>(null);
  const [invalidateReason, setInvalidateReason] = useState("");

  const payload = task.question_payload;
  const history: AnswerHistoryEntry[] = payload?.answer_history ?? [];
  const isDone = task.process_status === TaskStatus.DONE;

  // Find the index of the last valid answer (at most one should exist at a time).
  const lastValidIdx = (() => {
    for (let i = history.length - 1; i >= 0; i--) {
      if (history[i].is_valid) return i;
    }
    return null;
  })();

  const handleSubmitAnswer = async (value: string) => {
    if (submittingAnswer || value.trim() === "") return;
    setSubmittingAnswer(true);
    try {
      const updated = await submitAnswer(projectId, task.id, value);
      setAnswerValue("");
      onPatch(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Submit failed";
      onError(`Task #${task.id}: ${msg}`);
    } finally {
      setSubmittingAnswer(false);
    }
  };

  const handleInvalidate = async (idx: number) => {
    if (submittingAnswer || invalidateReason.trim() === "") return;
    setSubmittingAnswer(true);
    try {
      const updated = await invalidateAnswer(projectId, task.id, invalidateReason);
      setInvalidateReasonFor(null);
      setInvalidateReason("");
      onPatch(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Invalidate failed";
      onError(`Task #${task.id} (entry ${idx}): ${msg}`);
    } finally {
      setSubmittingAnswer(false);
    }
  };

  const sectionLabel =
    task.interaction_kind === "decision" ? "Decision" : "Question";

  return (
    <section
      className="flex flex-col gap-2"
      data-question-interaction
      data-interaction-kind={task.interaction_kind}
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {sectionLabel}
      </h3>
      <div className="flex flex-col gap-3">
        {payload?.question && (
          <p className="whitespace-pre-wrap text-sm text-zinc-800 dark:text-zinc-200">
            {payload.question}
          </p>
        )}

        {isDone ? (
          <p className="rounded bg-green-50 px-3 py-2 text-sm font-medium text-green-700 dark:bg-green-900/30 dark:text-green-300">
            {sectionLabel} resolved
          </p>
        ) : payload?.options != null && payload.options.length > 0 ? (
          // Options mode — buttons; clicking one immediately submits.
          // #954 — 44px min tap target on mobile for option chips
          // #1335 — `options` is heterogeneous (string | OptionItem). For
          // question tasks (this code path) the legacy shape is `string[]`;
          // narrow defensively so a stray OptionItem dict renders its label.
          <div className="flex flex-wrap gap-2" data-question-options>
            {payload.options.map((opt, idx) => {
              const label = typeof opt === "string" ? opt : opt.label;
              return (
              <button
                key={`${label}-${idx}`}
                type="button"
                disabled={submittingAnswer}
                onClick={() => handleSubmitAnswer(label)}
                data-question-option={label}
                className="min-h-[44px] rounded border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-medium text-violet-800 hover:bg-violet-100 disabled:opacity-50 dark:border-violet-800 dark:bg-violet-900/30 dark:text-violet-200 dark:hover:bg-violet-900/50"
              >
                {label}
              </button>
              );
            })}
          </div>
        ) : (
          // Free-text mode — textarea + explicit submit button.
          <div className="flex flex-col gap-2" data-question-freetext>
            <textarea
              value={answerValue}
              onChange={(e) => setAnswerValue(e.target.value)}
              disabled={submittingAnswer}
              placeholder="Type your answer…"
              rows={3}
              data-question-textarea
              className="w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
            />
            {/* #988 — 44px tap target on all viewports for the answer submit (HITL workflow critical path) */}
            <button
              type="button"
              disabled={submittingAnswer || answerValue.trim() === ""}
              onClick={() => handleSubmitAnswer(answerValue)}
              data-question-submit
              className="min-h-[44px] self-start rounded border border-violet-300 bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50 dark:border-violet-700 dark:bg-violet-700 dark:hover:bg-violet-600"
            >
              Submit answer
            </button>
          </div>
        )}

        {/* Answer history */}
        {history.length > 0 && (
          <div className="flex flex-col gap-1.5" data-answer-history>
            <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
              Answer history
            </p>
            <ol className="flex flex-col gap-2">
              {history.map((entry, idx) => (
                <li
                  key={idx}
                  className="flex flex-col gap-1 rounded border border-zinc-100 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-950/40"
                  data-answer-entry={idx}
                  data-answer-valid={entry.is_valid}
                >
                  <div className="flex items-start gap-2">
                    <span
                      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-semibold ${
                        entry.is_valid
                          ? "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                          : "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                      }`}
                      aria-label={entry.is_valid ? "valid" : "invalid"}
                    >
                      {entry.is_valid ? "✓" : "✗"}
                    </span>
                    <div className="flex-1">
                      <p className="whitespace-pre-wrap text-sm text-zinc-900 dark:text-zinc-100">
                        {entry.value}
                      </p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400">
                        by {entry.answered_by}
                        {entry.answered_at && ` · ${entry.answered_at}`}
                      </p>
                      {!entry.is_valid && entry.invalidated_reason && (
                        <p className="mt-0.5 text-xs text-red-600 dark:text-red-400">
                          Reason: {entry.invalidated_reason}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Invalidate button — only on last valid answer, only when not done */}
                  {!isDone && idx === lastValidIdx && (
                    invalidateReasonFor === idx ? (
                      <div className="mt-1 flex flex-col gap-1.5 pl-7">
                        <input
                          type="text"
                          value={invalidateReason}
                          onChange={(e) => setInvalidateReason(e.target.value)}
                          placeholder="Reason for invalidation…"
                          disabled={submittingAnswer}
                          autoFocus
                          data-invalidate-reason-input
                          className="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                        />
                        <div className="flex gap-2">
                          <button
                            type="button"
                            disabled={submittingAnswer || invalidateReason.trim() === ""}
                            onClick={() => handleInvalidate(idx)}
                            data-invalidate-confirm
                            className="rounded border border-red-300 bg-red-600 px-2 py-0.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50 dark:border-red-700"
                          >
                            Confirm invalidate
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setInvalidateReasonFor(null);
                              setInvalidateReason("");
                            }}
                            disabled={submittingAnswer}
                            className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-300 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setInvalidateReasonFor(idx);
                          setInvalidateReason("");
                        }}
                        disabled={submittingAnswer}
                        data-invalidate-button={idx}
                        className="ml-7 self-start rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700"
                      >
                        Invalidate
                      </button>
                    )
                  )}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </section>
  );
}

// #827 — read-only AC display; always rendered (shows "(none defined)" cue when empty)
const AC_STATUS_BADGE: Record<AcceptanceCriterion["status"], {
  glyph: string;
  className: string;
  label: string;
}> = {
  passed: {
    glyph: "✓",
    className:
      "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300",
    label: "passed",
  },
  failed: {
    glyph: "✗",
    className: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300",
    label: "failed",
  },
  pending: {
    glyph: "·",
    className:
      "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    label: "pending",
  },
  na: {
    glyph: "—",
    className:
      "bg-zinc-50 text-zinc-400 dark:bg-zinc-900 dark:text-zinc-500",
    label: "n/a",
  },
};

function AcceptanceCriteriaSection({
  criteria,
}: {
  criteria: AcceptanceCriterion[] | null;
}) {
  const list = criteria ?? [];
  const total = list.length;
  const passed = list.filter((c) => c.status === "passed").length;
  const headerLabel =
    total > 0
      ? `Acceptance criteria (${passed}/${total})`
      : "Acceptance criteria";

  return (
    <section className="flex flex-col gap-2" data-acceptance-criteria>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {headerLabel}
      </h3>
      <div>
        {total === 0 ? (
          <p className="text-sm italic text-zinc-500 dark:text-zinc-400">
            (none defined)
          </p>
        ) : (
          <ol className="flex flex-col gap-1">
            {list.map((c, idx) => {
              const badge = AC_STATUS_BADGE[c.status];
              return (
                <li
                  key={idx}
                  className="flex gap-2 py-1.5"
                  data-ac-item
                  data-ac-status={c.status}
                >
                  <span
                    aria-label={badge.label}
                    className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-semibold ${badge.className}`}
                  >
                    {badge.glyph}
                  </span>
                  <div className="flex-1">
                    <p className="whitespace-pre-wrap text-sm text-zinc-900 dark:text-zinc-100">
                      {c.text}
                    </p>
                    {c.verified_by && (
                      <p className="text-xs text-zinc-500 dark:text-zinc-400">
                        by {c.verified_by}
                        {c.verified_at && ` · ${c.verified_at}`}
                      </p>
                    )}
                    {c.notes && (
                      <p className="mt-1 whitespace-pre-wrap text-xs text-zinc-600 dark:text-zinc-400">
                        {c.notes}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
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
