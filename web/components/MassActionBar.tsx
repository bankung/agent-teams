"use client";

import { useState } from "react";

import type { AuditFlagWithProject } from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";

// Kanban #1212 GOV4 (D4) — mass-action bar above the project-grouped flag
// list on /review. Multi-select N flags + apply one of 3 mass actions:
// Continue / Keep Paused / Terminate. Adjust is NOT batchable per the
// locked spec brief (per-project adjustments form makes batching ambiguous).
//
// Continue + KeepPaused use a single confirm modal with the flag count +
// affected project list. Terminate routes through the shared
// TerminateFlagModal owned by the page (extra-friction: project-name list
// + reason >=10 chars + type-TERMINATE).
//
// The bar's interactive state (which flags are selected) is owned by the
// page's ReviewClient — the bar receives the set + emits action requests.

type MassAction = "continue" | "keep_paused" | "terminate";

type Props = {
  // All flags currently rendered (the bar's "select all" toggles selection
  // across this universe — not across the entire DB).
  allFlags: AuditFlagWithProject[];
  selectedFlagIds: Set<number>;
  onSelectAll: (next: boolean) => void;
  // Single-confirm modal handler — page receives the action + targets and
  // loops resolveFlag for each. Terminate is special-cased (page opens the
  // extra-friction modal instead of a confirm dialog).
  onMassConfirm: (action: "continue" | "keep_paused") => Promise<void>;
  onMassTerminateRequest: () => void;
};

export function MassActionBar({
  allFlags,
  selectedFlagIds,
  onSelectAll,
  onMassConfirm,
  onMassTerminateRequest,
}: Props) {
  const [confirming, setConfirming] = useState<MassAction | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const count = selectedFlagIds.size;
  const total = allFlags.length;
  const allSelected = total > 0 && count === total;
  const someSelected = count > 0 && count < total;

  // Selected flags + their project metadata (for the confirm modal's list).
  const selected = allFlags.filter((f) => selectedFlagIds.has(f.flag.id));

  async function handleConfirm() {
    if (confirming === null || confirming === "terminate") return;
    setSubmitting(true);
    setError(null);
    try {
      await onMassConfirm(confirming);
      setConfirming(null);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, `mass ${confirming} failed`));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div
        className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-2 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
        data-mass-action-bar
      >
        <label className="flex items-center gap-2 text-xs font-medium text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = someSelected;
            }}
            onChange={(e) => onSelectAll(e.target.checked)}
            className="h-4 w-4 rounded border-zinc-300 text-zinc-700 focus:ring-zinc-500 dark:border-zinc-600 dark:bg-zinc-950"
            data-mass-select-all
            aria-label="Select all flags"
          />
          <span className="tabular-nums">
            {count} of {total} selected
          </span>
        </label>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setConfirming("continue")}
            disabled={count === 0}
            className="rounded border border-zinc-300 bg-zinc-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
            data-mass-action="continue"
          >
            Continue selected ({count})
          </button>
          <button
            type="button"
            onClick={() => setConfirming("keep_paused")}
            disabled={count === 0}
            className="rounded border border-orange-500 bg-orange-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-orange-800 hover:bg-orange-200 disabled:opacity-40 dark:border-orange-600 dark:bg-orange-900/30 dark:text-orange-300 dark:hover:bg-orange-900/50"
            data-mass-action="keep_paused"
          >
            Keep Paused selected ({count})
          </button>
          <button
            type="button"
            onClick={onMassTerminateRequest}
            disabled={count === 0}
            className="rounded border border-red-600 bg-red-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-red-700 hover:bg-red-200 disabled:opacity-40 dark:border-red-500 dark:bg-red-900/30 dark:text-red-300 dark:hover:bg-red-900/50"
            data-mass-action="terminate"
          >
            Terminate selected ⚠ ({count})
          </button>
        </div>
      </div>

      {/* Single confirm modal for continue + keep_paused (terminate routes
          through the page's TerminateFlagModal instead). */}
      {confirming !== null && confirming !== "terminate" && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="mass-confirm-title"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget && !submitting) setConfirming(null);
          }}
          data-mass-confirm-modal={confirming}
        >
          <div className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-w-md sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800">
            <h2
              id="mass-confirm-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              {confirming === "continue"
                ? `Continue ${count} flags?`
                : `Keep ${count} flags paused?`}
            </h2>
            <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
              {confirming === "continue"
                ? "Each flag will be marked DONE and its project will be unpaused. Audit re-fires next cycle if conditions persist."
                : "Each flag will be marked DONE; each project stays paused. Audit re-fires next cycle."}
            </p>
            <div className="mt-3 max-h-40 overflow-y-auto rounded border border-zinc-200 bg-zinc-50 px-2 py-1.5 text-xs dark:border-zinc-700 dark:bg-zinc-950">
              <ul className="space-y-0.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
                {selected.map(({ flag, project }) => (
                  <li key={flag.id} data-mass-confirm-target>
                    · {project.name}{" "}
                    <span className="text-zinc-400">
                      (flag #{flag.id})
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
              >
                {error}
              </p>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirming(null)}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={submitting}
                className={
                  confirming === "continue"
                    ? "rounded border border-zinc-400 bg-zinc-500 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-zinc-600 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-500 dark:bg-zinc-600 dark:hover:bg-zinc-700"
                    : "rounded border border-orange-600 bg-orange-500 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-orange-600 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-orange-500 dark:bg-orange-600 dark:hover:bg-orange-700"
                }
                data-mass-confirm-submit
              >
                {submitting
                  ? "Submitting…"
                  : confirming === "continue"
                    ? `Continue ${count} flags`
                    : `Keep ${count} paused`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
