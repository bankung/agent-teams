"use client";

// TaskHaltModal — Kanban #1001 AC5. Confirmation gate for the Halt quick-action.
// Mirrors TaskRejectModal but the reason field is REQUIRED (halt_reason has BE
// min_length=1; empty string → 422 per shared/api-contracts.md #785). The
// submit button stays disabled until the user enters non-whitespace text.
//
// Halt flips ps=4 (BLOCKED) and stamps halt_reason. The action is reversible
// (clear halt_reason via a future Unhalt), so the gate is structurally lighter
// than KillProjectModal (no type-project-name confirmation), but still a
// deliberate-action mutation per
// context/standards/react/deliberate-action-mutations.md.

import { useEffect, useRef, useState } from "react";

import { ModalShell } from "./ModalShell";

type Props = {
  open: boolean;
  submitting: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onConfirm: (haltReason: string) => void;
};

export function TaskHaltModal({
  open,
  submitting,
  errorMessage,
  onCancel,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState("");
  const fieldRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => fieldRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open) setReason("");
  }, [open]);

  const reasonValid = reason.trim().length >= 1;
  const canSubmit = !submitting && reasonValid;

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    onConfirm(reason.trim());
  }

  return (
    <ModalShell
      open={open}
      onClose={() => { if (!submitting) onCancel(); }}
      labelledBy="task-halt-title"
      backdropProps={{ "data-task-halt-modal": true }}
    >
      <form onSubmit={onSubmit}>
        <h2
          id="task-halt-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          Halt this task?
        </h2>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          Flips the task to <span className="font-mono">BLOCKED</span> and stamps
          <span className="font-mono"> halt_reason</span>. Auto-pickup loops will
          skip the task until <span className="font-mono">halt_reason</span> is
          cleared. A reason is required.
        </p>

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Halt reason <span className="text-red-600 dark:text-red-400">*</span>
          <input
            ref={fieldRef}
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g., need clarification on AC#3; waiting on review"
            disabled={submitting}
            aria-invalid={reason.length > 0 && !reasonValid}
            data-task-halt-reason
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
          />
        </label>

        {errorMessage !== null && (
          <p
            role="alert"
            data-task-halt-error
            className="mt-3 text-xs text-red-700 dark:text-red-300"
          >
            {errorMessage}
          </p>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            data-task-halt-cancel
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            data-task-halt-confirm
            className="rounded border border-amber-500 bg-amber-500 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-amber-600 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-amber-400 dark:bg-amber-500 dark:hover:bg-amber-600"
          >
            {submitting ? "Halting…" : "Confirm halt"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
