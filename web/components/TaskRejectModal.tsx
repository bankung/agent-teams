"use client";

// TaskRejectModal — Kanban #1001 AC5. Confirmation gate for the Reject quick-
// action. Mirrors PauseProjectModal's chrome (fixed inset overlay, mobile full-
// screen sheet at <sm, centered card at sm+, ESC + backdrop close, disabled
// submit while in-flight, inline error rendering).
//
// Field rules:
//   - reason is OPTIONAL but encouraged (no min-length gate); empty submit
//     still works (the parent appends a default "no reason" suffix to the
//     status_change_reason).
//
// Submit is a deliberate-action mutation per
// context/standards/react/deliberate-action-mutations.md — Reject flips
// process_status=6 (CANCELLED), which is hard-to-reverse (the task lands
// outside the default list filter). No optimistic flip; the parent's success
// callback closes the modal after the PATCH lands.

import { useEffect, useRef, useState } from "react";

type Props = {
  open: boolean;
  submitting: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
};

export function TaskRejectModal({
  open,
  submitting,
  errorMessage,
  onCancel,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState("");
  const fieldRef = useRef<HTMLTextAreaElement | null>(null);

  // Focus the reason field on open; ESC closes (unless submitting).
  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => fieldRef.current?.focus());
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, submitting, onCancel]);

  // Clear the reason on close — open it again, blank slate (avoids stale
  // text leaking into a second attempt).
  useEffect(() => {
    if (!open) setReason("");
  }, [open]);

  if (!open) return null;

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    onConfirm(reason.trim());
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="task-reject-title"
      data-task-reject-modal
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !submitting) onCancel();
      }}
    >
      <form
        onSubmit={onSubmit}
        className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-w-md sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800"
      >
        <h2
          id="task-reject-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          Reject this task?
        </h2>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          The task is flipped to <span className="font-mono">CANCELLED</span>;
          a reason is captured in the audit trail. Optional but encouraged.
        </p>

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Reason{" "}
          <span className="font-normal text-zinc-500">(optional)</span>
          <textarea
            ref={fieldRef}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Why reject? Captured into status_change_reason."
            disabled={submitting}
            data-task-reject-reason
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
          />
        </label>

        {errorMessage !== null && (
          <p
            role="alert"
            data-task-reject-error
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
            data-task-reject-cancel
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            data-task-reject-confirm
            className="rounded border border-red-500 bg-red-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-red-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-700 dark:bg-red-600 dark:hover:bg-red-700"
          >
            {submitting ? "Rejecting…" : "Confirm reject"}
          </button>
        </div>
      </form>
    </div>
  );
}
