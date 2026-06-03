"use client";

import { useState } from "react";

import { deleteMilestone, type MilestoneRead } from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

// MilestoneDeleteModal — soft-delete confirm dialog (Kanban #1868 FE).
// Soft-delete detaches every child task (milestone_id → NULL) in the same
// transaction on the BE; the copy below warns the operator of that side effect.

type Props = {
  projectId: number;
  open: boolean;
  onClose: () => void;
  onDeleted: (id: number) => void;
  milestone: MilestoneRead | null;
};

export function MilestoneDeleteModal({
  projectId,
  open,
  onClose,
  onDeleted,
  milestone,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    if (submitting) return;
    setError(null);
    onClose();
  }

  async function onConfirm() {
    if (submitting || !milestone) return;
    setError(null);
    setSubmitting(true);
    try {
      await deleteMilestone(projectId, milestone.id);
      onDeleted(milestone.id);
      onClose();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Delete failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell
      open={open}
      onClose={close}
      labelledBy="milestone-delete-title"
      maxWidth="sm"
      backdropProps={{ "data-milestone-delete-modal": true }}
    >
      <div>
        <h2
          id="milestone-delete-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          Delete milestone
        </h2>
        <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">
          Delete{" "}
          <span className="font-semibold text-zinc-900 dark:text-zinc-100">
            {milestone?.title ?? "this milestone"}
          </span>
          ? Tasks assigned to it will be unassigned (their milestone is cleared);
          the tasks themselves are NOT deleted.
        </p>

        {error !== null && (
          <p
            role="alert"
            className="mt-3 text-xs text-red-700 dark:text-red-300"
            data-milestone-delete-error
          >
            {error}
          </p>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={close}
            disabled={submitting}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-milestone-delete-cancel
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={submitting}
            className="rounded border border-red-300 bg-red-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-red-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-700"
            data-milestone-delete-confirm
          >
            {submitting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
