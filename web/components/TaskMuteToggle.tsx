"use client";

// TaskMuteToggle — Kanban #1349 (2026-05-20).
//
// Small per-task toggle rendered inside TaskDetail. Reads task.nudge_disabled;
// on flip, PATCHes /api/tasks/{id} with {nudge_disabled: <new>}. Optimistic
// flip + revert on error (mirrors the Board's drag-end pattern).
//
// Why optimistic: the toggle is high-frequency-low-risk (boolean field, no
// side effects beyond the column write). The server confirmation roundtrip
// adds latency the operator notices when batching mutes across multiple
// tasks; a revert-on-error keeps the perceived snappiness without losing
// the failure signal.

import { useState } from "react";

import { HttpError, patchTask, type TaskRead } from "@/lib/api";

type Props = {
  task: TaskRead;
  projectId: number;
  onPatch: (updated: TaskRead) => void;
  onError: (message: string) => void;
};

export function TaskMuteToggle({ task, projectId, onPatch, onError }: Props) {
  const [submitting, setSubmitting] = useState(false);
  // Defensive read — the column is NOT NULL DEFAULT false on the BE, but
  // legacy serialized payloads may omit the field. Treat undefined as false.
  const muted = task.nudge_disabled === true;

  async function onToggle() {
    if (submitting) return;
    const next = !muted;
    setSubmitting(true);
    // Optimistic flip: synthesize an updated TaskRead so the parent's
    // muted-state reflects immediately. Revert on error.
    onPatch({ ...task, nudge_disabled: next });
    try {
      const server = await patchTask(projectId, task.id, {
        nudge_disabled: next,
      });
      onPatch(server);
    } catch (err: unknown) {
      // Revert to original state.
      onPatch({ ...task, nudge_disabled: muted });
      const msg =
        err instanceof HttpError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Update failed";
      onError(`Task #${task.id}: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={muted}
      aria-label={
        muted
          ? `Unmute HITL nudges for task #${task.id}`
          : `Mute HITL nudges for task #${task.id}`
      }
      onClick={onToggle}
      disabled={submitting}
      data-task-mute-toggle
      data-task-mute-state={muted ? "muted" : "active"}
      title={
        muted
          ? "Nudges silenced for this task — click to re-enable"
          : "Click to silence aging-nudges for this task"
      }
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors min-h-[36px] sm:min-h-0 disabled:opacity-50 disabled:cursor-not-allowed ${
        muted
          ? "border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-200"
          : "border-zinc-200 bg-transparent text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800"
      }`}
    >
      {/* Track pill — mirrors Switch.tsx visual to keep the badge family
          consistent. Muted = amber track on; active = zinc track off. */}
      <span
        aria-hidden
        className={`relative inline-flex h-3 w-5 shrink-0 rounded-full transition-colors ${
          muted
            ? "bg-amber-500 dark:bg-amber-400"
            : "bg-zinc-300 dark:bg-zinc-600"
        }`}
      >
        <span
          className={`absolute top-0.5 h-2 w-2 rounded-full bg-white shadow transition-transform ${
            muted ? "translate-x-2.5" : "translate-x-0.5"
          }`}
        />
      </span>
      <span>{muted ? "Nudges muted" : "Mute nudges"}</span>
    </button>
  );
}
