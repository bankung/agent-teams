"use client";

import { useEffect, useRef, useState } from "react";

import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

// Kanban #1212 GOV4 — extra-friction modal for the "Terminate" action on an
// GOV3 audit flag (single-flag mode) or a batch of flags (mass mode).
//
// UX strength mirrors KillProjectModal: 3 gates must all pass before submit.
//   1. type project name(s) exactly to confirm — single mode only; mass mode
//      shows the project-name list inline + skips this gate (the list itself
//      IS the warning).
//   2. reason >= 10 chars — same min-length as GOV1 kill_project (D5).
//   3. type literal word "TERMINATE" — final muscle-memory brake (matches
//      the locked spec brief).
//
// Calling `resolveFlag({action:'terminate'})` cascades to GOV1 kill_project on
// the backend (services/pause_switch.py:resolve_flag terminate branch). The
// modal is intentionally pessimistic — disable submit until ALL gates pass;
// no "force" toggle (the GOV3 path always uses the gentle drain).

const REASON_MIN_CHARS = 10;
const CONFIRM_WORD = "TERMINATE";

export type TerminateTarget = {
  projectId: number;
  projectName: string;
  flagId: number;
};

type Props = {
  open: boolean;
  // single mode: 1 target; mass mode: N targets (skips per-name typing gate).
  targets: TerminateTarget[];
  onClose: () => void;
  // Submit handler — caller owns the resolveFlag loop + error handling +
  // refresh. Receives the user-typed reason so the caller can stamp it into
  // the X-Actor header / future audit metadata if desired (BE auto-formats
  // the kill reason; the operator's typed reason is captured in the audit
  // row through the kill_project path).
  onSubmit: (targets: TerminateTarget[], reason: string) => Promise<void>;
};

export function TerminateFlagModal({
  open,
  targets,
  onClose,
  onSubmit,
}: Props) {
  const isMass = targets.length > 1;
  const single = !isMass ? targets[0] : null;

  const [typedName, setTypedName] = useState("");
  const [reason, setReason] = useState("");
  const [typedConfirm, setTypedConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const firstInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => firstInputRef.current?.focus());
  }, [open]);

  function close() {
    if (submitting) return;
    setTypedName("");
    setReason("");
    setTypedConfirm("");
    setError(null);
    onClose();
  }

  // Gate validation. Mass mode skips the per-name typing gate (impractical
  // for >2 targets) — the inline project-name list serves the same purpose.
  const nameOk = isMass ? true : typedName === (single?.projectName ?? "");
  const reasonOk = reason.trim().length >= REASON_MIN_CHARS;
  const confirmOk = typedConfirm === CONFIRM_WORD;
  const canSubmit = !submitting && nameOk && reasonOk && confirmOk;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(targets, reason.trim());
      // Caller closes the modal on success (it owns the page-level state
      // that drives `open`). We just clear local state for the next open.
      setTypedName("");
      setReason("");
      setTypedConfirm("");
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "terminate failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ModalShell
      open={open}
      onClose={close}
      labelledBy="terminate-flag-title"
      backdropProps={{
        "data-terminate-flag-modal": true,
        "data-terminate-flag-mode": isMass ? "mass" : "single",
      }}
    >
      <form onSubmit={handleSubmit}>
        <h2
          id="terminate-flag-title"
          className="text-sm font-semibold uppercase tracking-wide text-red-700 dark:text-red-400"
        >
          {isMass
            ? `Terminate ${targets.length} projects?`
            : `Terminate project ${single?.projectName}?`}
        </h2>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          Terminate cascades to GOV1 hard kill: drains recurring tasks, blocks
          new task creation, and freezes open TODO / BLOCKED rows. External
          commitments (calendars, third-party APIs) are NOT auto-cancelled.
        </p>

        {isMass && (
          <div className="mt-3 max-h-32 overflow-y-auto rounded border border-zinc-200 bg-zinc-50 px-2 py-1.5 text-xs dark:border-zinc-700 dark:bg-zinc-950">
            <p className="mb-1 font-medium text-zinc-700 dark:text-zinc-300">
              Affected projects:
            </p>
            <ul className="space-y-0.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
              {targets.map((t) => (
                <li key={t.flagId} data-terminate-target-name>
                  · {t.projectName}{" "}
                  <span className="text-zinc-400">
                    (flag #{t.flagId})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!isMass && single !== null && (
          <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Type the project name exactly to confirm{" "}
            <span className="text-red-600 dark:text-red-400">*</span>
            <input
              ref={firstInputRef}
              type="text"
              value={typedName}
              onChange={(e) => {
                setTypedName(e.target.value);
                if (error !== null) setError(null);
              }}
              placeholder={single.projectName}
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
              aria-invalid={typedName.length > 0 && !nameOk}
              className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
              data-terminate-flag-name-input
            />
          </label>
        )}

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Reason{" "}
          <span className="font-normal text-zinc-500">
            (≥{REASON_MIN_CHARS} chars)
          </span>{" "}
          <span className="text-red-600 dark:text-red-400">*</span>
          <textarea
            ref={isMass ? firstInputRef as unknown as React.RefObject<HTMLTextAreaElement> : undefined}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              if (error !== null) setError(null);
            }}
            rows={3}
            placeholder="Why terminate? Captured into the audit row."
            disabled={submitting}
            aria-invalid={reason.length > 0 && !reasonOk}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            data-terminate-flag-reason
          />
          <span className="mt-0.5 block text-[10px] text-zinc-500 dark:text-zinc-500 tabular-nums">
            {reason.trim().length}/{REASON_MIN_CHARS}
          </span>
        </label>

        <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Type <span className="font-mono text-red-700 dark:text-red-400">{CONFIRM_WORD}</span> to enable submit{" "}
          <span className="text-red-600 dark:text-red-400">*</span>
          <input
            type="text"
            value={typedConfirm}
            onChange={(e) => {
              setTypedConfirm(e.target.value);
              if (error !== null) setError(null);
            }}
            autoComplete="off"
            spellCheck={false}
            disabled={submitting}
            aria-invalid={typedConfirm.length > 0 && !confirmOk}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            data-terminate-flag-confirm-input
          />
        </label>

        {error !== null && (
          <p
            role="alert"
            className="mt-3 text-xs text-red-700 dark:text-red-300"
            data-terminate-flag-error
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
            data-terminate-flag-cancel
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded border border-red-600 bg-red-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-red-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-500 dark:bg-red-500 dark:hover:bg-red-600"
            data-terminate-flag-submit
          >
            {submitting
              ? "Terminating…"
              : isMass
                ? `Terminate ${targets.length} projects`
                : "Terminate project"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
