"use client";

// DecisionInteractionView — Kanban #1335 (2026-05-20).
//
// Orchestrates the decision-task interaction inside the task-detail drawer
// (TaskDetail). Sibling to TaskFocusView's inline option chooser (#1001);
// THIS variant uses full OptionCards (label + description + hints) tuned for
// the dashboard drawer where vertical space is generous.
//
// Two states:
//   1. Pending — render OptionCards (radio select) + optional rationale
//      textarea + Submit button. Calls POST /api/tasks/{id}/decide on submit.
//   2. Decided — read-only summary: chosen option label + rationale +
//      chosen_at + chosen_by. The parent TaskDetail decides which to render
//      based on `task.question_payload.chosen_id` being non-null.

import { useMemo, useState } from "react";

import {
  decideTask,
  type OptionItem,
  type TaskRead,
} from "@/lib/api";
import { OptionCard } from "./OptionCard";

type Props = {
  task: TaskRead;
  projectId: number;
  onPatch: (updated: TaskRead) => void;
  onError: (message: string) => void;
};

// Narrow a heterogeneous option (string | OptionItem) to a typed object.
// Strings are legacy free-form question options and should not appear on
// `interaction_kind='decision'` tasks; if they do, we filter them out and
// let the empty-state branch render the diagnostic message.
function narrowOption(opt: unknown): OptionItem | null {
  if (
    opt !== null &&
    typeof opt === "object" &&
    "id" in opt &&
    "label" in opt &&
    typeof (opt as OptionItem).id === "string" &&
    typeof (opt as OptionItem).label === "string"
  ) {
    return opt as OptionItem;
  }
  return null;
}

export function DecisionInteractionView({
  task,
  projectId,
  onPatch,
  onError,
}: Props) {
  const payload = task.question_payload;
  const isDecided =
    payload?.chosen_id !== undefined &&
    payload?.chosen_id !== null &&
    payload.chosen_id.length > 0;

  // Memoized typed-option list — strips out any non-structured entries so we
  // never feed a bare string into OptionCard (which expects {id, label}).
  const options: OptionItem[] = useMemo(() => {
    const raw = payload?.options ?? [];
    const out: OptionItem[] = [];
    for (const opt of raw) {
      const narrowed = narrowOption(opt);
      if (narrowed !== null) out.push(narrowed);
    }
    return out;
  }, [payload?.options]);

  // ------------------------- Decided branch (read-only) ----------------------
  if (isDecided) {
    const chosen = options.find((o) => o.id === payload?.chosen_id) ?? null;
    return (
      <section
        className="flex flex-col gap-2"
        data-decision-interaction
        data-decision-state="decided"
      >
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Decision
        </h3>
        {payload?.question && (
          <p className="whitespace-pre-wrap text-sm text-zinc-800 dark:text-zinc-200">
            {payload.question}
          </p>
        )}
        <div className="flex flex-col gap-1 rounded border border-green-200 bg-green-50 px-3 py-2 dark:border-green-800 dark:bg-green-900/30">
          <p className="text-xs font-semibold uppercase tracking-wide text-green-800 dark:text-green-200">
            Chosen
          </p>
          <p
            className="text-sm font-medium text-green-900 dark:text-green-100"
            data-decision-chosen-id={payload?.chosen_id ?? ""}
          >
            {chosen ? chosen.label : `(option id: ${payload?.chosen_id})`}
          </p>
          {chosen?.description && (
            <p className="whitespace-pre-wrap text-xs text-green-800 dark:text-green-200">
              {chosen.description}
            </p>
          )}
          {payload?.rationale && (
            <div className="mt-1.5 border-t border-green-200 pt-1.5 dark:border-green-800">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-green-700 dark:text-green-300">
                Rationale
              </p>
              <p className="mt-0.5 whitespace-pre-wrap text-xs text-green-900 dark:text-green-100">
                {payload.rationale}
              </p>
            </div>
          )}
          <p className="mt-1 font-mono text-[10px] text-green-700 dark:text-green-300">
            by {payload?.chosen_by ?? "—"}
            {payload?.chosen_at && ` · ${payload.chosen_at}`}
          </p>
        </div>
      </section>
    );
  }

  // ------------------------- Pending branch (interactive) --------------------
  return (
    <DecisionPendingForm
      task={task}
      projectId={projectId}
      options={options}
      onPatch={onPatch}
      onError={onError}
    />
  );
}

function DecisionPendingForm({
  task,
  projectId,
  options,
  onPatch,
  onError,
}: {
  task: TaskRead;
  projectId: number;
  options: OptionItem[];
  onPatch: (updated: TaskRead) => void;
  onError: (message: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rationale, setRationale] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const payload = task.question_payload;

  async function onSubmit() {
    if (submitting || selectedId === null) return;
    setSubmitting(true);
    try {
      const trimmedRationale = rationale.trim();
      const updated = await decideTask(projectId, task.id, {
        chosen_id: selectedId,
        rationale: trimmedRationale.length > 0 ? trimmedRationale : null,
      });
      // Reset form state then propagate the updated row to the parent.
      setSelectedId(null);
      setRationale("");
      onPatch(updated);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Decide failed";
      onError(`Task #${task.id}: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      className="flex flex-col gap-2"
      data-decision-interaction
      data-decision-state="pending"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Decision
      </h3>
      {payload?.question && (
        <p className="whitespace-pre-wrap text-sm text-zinc-800 dark:text-zinc-200">
          {payload.question}
        </p>
      )}

      {options.length === 0 ? (
        <p
          className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs italic text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
          data-decision-no-options
        >
          No structured options on this decision task. Operator: file a bug —
          /decide expects question_payload.options[] as OptionItem[].
        </p>
      ) : (
        <>
          <div
            role="radiogroup"
            aria-label="Decision options"
            className="flex flex-col gap-2"
            data-decision-options
          >
            {options.map((opt) => (
              <OptionCard
                key={opt.id}
                option={opt}
                selected={selectedId === opt.id}
                disabled={submitting}
                onSelect={(o) => setSelectedId(o.id)}
              />
            ))}
          </div>

          <label className="mt-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Rationale{" "}
            <span className="font-normal text-zinc-400">(optional)</span>
            <textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              disabled={submitting}
              placeholder="Why this option? Captured into question_payload.rationale."
              rows={2}
              data-decision-rationale
              className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            />
          </label>

          <div className="flex items-center justify-end">
            <button
              type="button"
              disabled={submitting || selectedId === null}
              onClick={onSubmit}
              data-decision-submit
              className="min-h-[44px] rounded border border-violet-600 bg-violet-600 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white hover:bg-violet-700 disabled:opacity-50 sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-violet-500 dark:bg-violet-500 dark:hover:bg-violet-600"
            >
              {submitting ? "Submitting…" : "Submit decision"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
