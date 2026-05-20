"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  HttpError,
  pauseProject,
  unpauseProject,
  type ProjectRead,
} from "@/lib/api";

// Kanban #1211 / #1238 AA3 (FE) — soft-pause confirmation modal. Single
// component handles BOTH pause + unpause flows via the `mode` prop. Lighter
// than KillProjectModal (the AA1 hard-kill modal) on purpose: pause is a
// reversible governance action, not a destructive one.
//
//   mode="pause"   → amber-themed; ONLY a reason textarea (>=10 chars).
//                    NO type-project-name confirmation. NO force checkbox /
//                    2-step gate. Submit is a single click and POSTs
//                    immediately.
//   mode="unpause" → emerald-themed; NO fields. Single confirm step. The
//                    operator action itself is the implicit rationale (the
//                    BE accepts an empty body) — there's no audit cost to
//                    asking for one.
//
// Modal chrome mirrors KillProjectModal / NewProjectModal exactly: fixed
// inset overlay, mobile full-screen sheet at <sm, centered card at sm+,
// ESC + backdrop close, focus first field on open, disabled submit while in-
// flight. Error display is inline `role="alert"` with HttpError.message;
// 409 idempotency errors (already paused / not paused) land here too.

const REASON_MIN_CHARS = 10;

type Props = {
  project: ProjectRead;
  mode: "pause" | "unpause";
  triggerLabel?: string;
  triggerClassName?: string;
  // Kanban #1288 — optional external open control for Switch-driven triggers.
  externalOpen?: boolean;
  onExternalClose?: () => void;
};

export function PauseProjectModal({
  project,
  mode,
  triggerLabel,
  triggerClassName,
  externalOpen,
  onExternalClose,
}: Props) {
  const router = useRouter();
  const [internalOpen, setInternalOpen] = useState(false);
  const open = externalOpen !== undefined ? externalOpen : internalOpen;
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // pause-only state
  const [reason, setReason] = useState("");
  const firstFieldRef = useRef<HTMLTextAreaElement | null>(null);
  const firstButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    // Focus the reason textarea for pause; the cancel button for unpause
    // (which has no input — focusing the confirm button would risk an
    // accidental Enter submit).
    requestAnimationFrame(() => {
      if (mode === "pause") firstFieldRef.current?.focus();
      else firstButtonRef.current?.focus();
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) closeModal();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, submitting, mode]);

  function openModal() {
    if (externalOpen !== undefined) return; // caller controls open
    setInternalOpen(true);
  }

  function closeModal() {
    if (submitting) return;
    setInternalOpen(false);
    onExternalClose?.();
    setReason("");
    setError(null);
  }

  const reasonValid = reason.trim().length >= REASON_MIN_CHARS;
  const canSubmitPause = !submitting && reasonValid;
  const canSubmitUnpause = !submitting;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "pause" && !canSubmitPause) return;
    if (mode === "unpause" && !canSubmitUnpause) return;

    setError(null);
    setSubmitting(true);
    try {
      if (mode === "pause") {
        await pauseProject(project.id, { reason: reason.trim() });
      } else {
        await unpauseProject(project.id);
      }
      router.refresh();
      setInternalOpen(false);
      onExternalClose?.();
      setReason("");
    } catch (err: unknown) {
      if (err instanceof HttpError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : `${mode} failed`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  // Trigger button visuals — pause = amber/yellow (warning, not error), unpause
  // = emerald (mirrors revive's resume-action semantic). Callers can override
  // className / label so a banner-inline button picks the right size class.
  const defaultTriggerLabel =
    mode === "pause" ? "Pause project" : "Unpause project";
  const defaultTriggerClass =
    mode === "pause"
      ? "inline-flex items-center rounded border border-amber-500 bg-amber-500 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-amber-600 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-amber-400 dark:bg-amber-500 dark:hover:bg-amber-600"
      : "inline-flex items-center rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600";

  return (
    <>
      {externalOpen === undefined && (
        <button
          type="button"
          onClick={openModal}
          className={triggerClassName ?? defaultTriggerClass}
          data-pause-project-trigger={mode}
        >
          {triggerLabel ?? defaultTriggerLabel}
        </button>
      )}
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="pause-project-title"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-pause-project-modal={mode}
          data-pause-project-name={project.name}
        >
          <form
            onSubmit={onSubmit}
            className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-w-md sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800"
          >
            <h2
              id="pause-project-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              {mode === "pause" ? "Pause project" : "Unpause project"} ·{" "}
              <span className="font-mono normal-case">{project.name}</span>?
            </h2>

            {mode === "pause" ? (
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                Soft-pause: blocks creation of new tasks (unless the per-task{" "}
                <span className="font-mono">allow_during_pause</span> override
                is set with a reason), drains recurring fires, and freezes
                template <span className="font-mono">next_fire_at</span>. In-
                flight runs complete naturally. Open TODOs are NOT auto-frozen
                — review them via the audit-flag resolution flow.
              </p>
            ) : (
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                Resumes recurring tasks (recomputes{" "}
                <span className="font-mono">next_fire_at</span>) and clears
                every <span className="font-mono">kill_frozen=true</span>{" "}
                marker. Historical <span className="font-mono">paused_at</span>{" "}
                + <span className="font-mono">paused_reason</span> are
                preserved for audit.
              </p>
            )}

            {mode === "pause" && (
              <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Reason{" "}
                <span className="font-normal text-zinc-500">
                  (≥{REASON_MIN_CHARS} chars)
                </span>{" "}
                <span className="text-red-600 dark:text-red-400">*</span>
                <textarea
                  ref={firstFieldRef}
                  value={reason}
                  onChange={(e) => {
                    setReason(e.target.value);
                    if (error !== null) setError(null);
                  }}
                  rows={3}
                  placeholder="Why are we pausing this project? Captured into the audit row."
                  disabled={submitting}
                  aria-invalid={reason.length > 0 && !reasonValid}
                  className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                  data-pause-project-reason
                />
                <span className="mt-0.5 block text-[10px] text-zinc-500 dark:text-zinc-500 tabular-nums">
                  {reason.trim().length}/{REASON_MIN_CHARS}
                </span>
              </label>
            )}

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
                data-pause-project-error
              >
                {error}
              </p>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                ref={firstButtonRef}
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-pause-project-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  mode === "pause" ? !canSubmitPause : !canSubmitUnpause
                }
                className={
                  mode === "pause"
                    ? "rounded border border-amber-500 bg-amber-500 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-amber-600 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-amber-400 dark:bg-amber-500 dark:hover:bg-amber-600"
                    : "rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                }
                data-pause-project-submit
              >
                {mode === "pause"
                  ? submitting
                    ? "Pausing…"
                    : "Pause project"
                  : submitting
                    ? "Unpausing…"
                    : "Unpause project"}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
