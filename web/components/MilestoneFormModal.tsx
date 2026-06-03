"use client";

import { useEffect, useRef, useState } from "react";

import {
  createMilestone,
  updateMilestone,
  MILESTONE_STATUSES,
  type MilestoneCreate,
  type MilestoneRead,
  type MilestoneStatusValue,
  type MilestoneUpdate,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

// MilestoneFormModal — create/edit milestone dialog (Kanban #1868 FE).
//
// One component drives both create (no `milestone` prop) and edit (with one).
// Visual chrome mirrors NewTaskModal verbatim (zinc-bordered inputs, emerald
// submit, ESC/backdrop close via ModalShell). The parent owns open state +
// the success callback (so it can re-fetch / merge the updated row).
//
// Client-side date guard mirrors the BE rule (start_date <= target_date); the
// BE re-enforces it (422) so this is a UX nicety, not the source of truth.

const STATUS_LABEL: Record<MilestoneStatusValue, string> = {
  planned: "Planned",
  active: "Active",
  released: "Released",
  cancelled: "Cancelled",
};

type Props = {
  projectId: number;
  open: boolean;
  onClose: () => void;
  // Success callback — receives the freshly created/updated row so the parent
  // can merge it into local state without a full re-fetch.
  onSaved: (milestone: MilestoneRead) => void;
  // Edit mode when present; create mode when absent.
  milestone?: MilestoneRead;
};

export function MilestoneFormModal({
  projectId,
  open,
  onClose,
  onSaved,
  milestone,
}: Props) {
  const isEdit = milestone !== undefined;

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [milestoneStatus, setMilestoneStatus] =
    useState<MilestoneStatusValue>("planned");
  const [startDate, setStartDate] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  // Seed fields from the milestone (edit) or reset to defaults (create) every
  // time the modal opens. Keying on `open` + `milestone?.id` re-seeds when the
  // modal is reused for a different milestone.
  useEffect(() => {
    if (!open) return;
    if (milestone) {
      setTitle(milestone.title);
      setDescription(milestone.description ?? "");
      setMilestoneStatus(milestone.milestone_status);
      setStartDate(milestone.start_date ?? "");
      setTargetDate(milestone.target_date ?? "");
    } else {
      setTitle("");
      setDescription("");
      setMilestoneStatus("planned");
      setStartDate("");
      setTargetDate("");
    }
    setError(null);
    // Focus title after the panel mounts.
    requestAnimationFrame(() => titleInputRef.current?.focus());
  }, [open, milestone]);

  const trimmedTitle = title.trim();
  const titleValid = trimmedTitle.length > 0;
  // Client-side mirror of the BE start<=target rule. Only blocks when BOTH are
  // set (a half-specified window is legal, matching the BE).
  const datesValid =
    startDate === "" || targetDate === "" || startDate <= targetDate;
  const canSubmit = !submitting && titleValid && datesValid;

  function close() {
    if (submitting) return;
    onClose();
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);

    try {
      let saved: MilestoneRead;
      if (isEdit && milestone) {
        // PATCH the editable surface. Send explicit values for every field the
        // form owns; empty date / description string maps to null (clear).
        const body: MilestoneUpdate = {
          title: trimmedTitle,
          description: description.trim() === "" ? null : description.trim(),
          milestone_status: milestoneStatus,
          start_date: startDate === "" ? null : startDate,
          target_date: targetDate === "" ? null : targetDate,
        };
        saved = await updateMilestone(projectId, milestone.id, body);
      } else {
        const body: MilestoneCreate = {
          project_id: projectId,
          title: trimmedTitle,
          milestone_status: milestoneStatus,
          ...(description.trim() !== ""
            ? { description: description.trim() }
            : {}),
          ...(startDate !== "" ? { start_date: startDate } : {}),
          ...(targetDate !== "" ? { target_date: targetDate } : {}),
        };
        saved = await createMilestone(projectId, body);
      }
      onSaved(saved);
      onClose();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, isEdit ? "Update failed" : "Create failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell
      open={open}
      onClose={close}
      labelledBy="milestone-form-title"
      backdropProps={{ "data-milestone-form-modal": true }}
    >
      <form onSubmit={onSubmit}>
        <h2
          id="milestone-form-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          {isEdit ? "Edit milestone" : "New milestone"}
        </h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          {isEdit
            ? "Update this milestone's plan."
            : "Group tasks under a release milestone."}
        </p>

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Title <span className="text-red-600 dark:text-red-400">*</span>
          <input
            ref={titleInputRef}
            type="text"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (error !== null) setError(null);
            }}
            placeholder="e.g. v1.0 launch"
            autoComplete="off"
            disabled={submitting}
            aria-invalid={title.length > 0 && !titleValid}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
            data-milestone-title
          />
        </label>

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Status <span className="text-red-600 dark:text-red-400">*</span>
          <select
            value={milestoneStatus}
            onChange={(e) => {
              setMilestoneStatus(e.target.value as MilestoneStatusValue);
              if (error !== null) setError(null);
            }}
            disabled={submitting}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
            data-milestone-status-select
          >
            {MILESTONE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>

        <div className="mt-3 grid grid-cols-2 gap-3">
          <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Start date{" "}
            <span className="font-normal text-zinc-400">(optional)</span>
            <input
              type="date"
              value={startDate}
              onChange={(e) => {
                setStartDate(e.target.value);
                if (error !== null) setError(null);
              }}
              disabled={submitting}
              aria-invalid={!datesValid}
              className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
              data-milestone-start-date
            />
          </label>
          <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Target date{" "}
            <span className="font-normal text-zinc-400">(optional)</span>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => {
                setTargetDate(e.target.value);
                if (error !== null) setError(null);
              }}
              disabled={submitting}
              aria-invalid={!datesValid}
              className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
              data-milestone-target-date
            />
          </label>
        </div>
        {!datesValid && (
          <p className="mt-1 text-xs text-red-700 dark:text-red-300">
            Start date must be on or before target date.
          </p>
        )}

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Description{" "}
          <span className="font-normal text-zinc-400">(optional)</span>
          <textarea
            value={description}
            onChange={(e) => {
              setDescription(e.target.value);
              if (error !== null) setError(null);
            }}
            placeholder="Markdown supported"
            rows={3}
            disabled={submitting}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
            data-milestone-description
          />
        </label>

        {error !== null && (
          <p
            role="alert"
            className="mt-3 text-xs text-red-700 dark:text-red-300"
            data-milestone-form-error
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
            data-milestone-form-cancel
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
            data-milestone-form-submit
          >
            {submitting
              ? isEdit
                ? "Saving…"
                : "Creating…"
              : isEdit
                ? "Save"
                : "Create"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
