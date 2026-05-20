"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  HttpError,
  killProject,
  reviveProject,
  type ProjectRead,
} from "@/lib/api";

// Kanban #1209 AA1 (D5) — hard kill switch confirmation modal. Single
// component handles BOTH kill + revive flows via the `mode` prop:
//
//   mode="kill"   → red-themed; type-project-name + reason (>=10 chars)
//                   + optional force checkbox; submit is a 2-step click
//                   when force is checked (1st reveals red banner +
//                   relabels button "Confirm force-kill", 2nd actually
//                   POSTs with `?force=true`).
//   mode="revive" → green-themed; single confirm step (lower-risk inverse).
//
// Visual chrome mirrors NewProjectModal / ProjectConsentGrantModal exactly:
// fixed inset overlay, mobile full-screen sheet at <sm, centered card at sm+,
// ESC + backdrop close, focus first field on open, disabled submit while in-
// flight. Error display is inline `role="alert"` with HttpError.message; 409
// idempotency errors land here too (already-killed / not-killed).

const FORCE_NOTICE =
  "Force mode skips checkpoint grace — in-flight work may be lost.";

const REASON_MIN_CHARS = 10;

type Props = {
  project: ProjectRead;
  mode: "kill" | "revive";
  // Render-mode for the trigger. The Board renders the Terminate trigger
  // inline in the project header (Switch component); the KilledBanner renders
  // the Revive trigger as a banner-inline button. Both modes accept either
  // visual via a custom render function — when omitted, falls back to a
  // sensible default (red "Terminate project" / green "Revive project").
  triggerLabel?: string;
  triggerClassName?: string;
  // Kanban #1288 — optional external open control for Switch-driven triggers.
  // When provided, the component renders no internal trigger button and
  // delegates open state to the caller instead.
  externalOpen?: boolean;
  onExternalClose?: () => void;
};

export function KillProjectModal({
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

  // Kill-only state
  const [typedName, setTypedName] = useState("");
  const [reason, setReason] = useState("");
  const [forceMode, setForceMode] = useState(false);
  // Two-step force submit: 1st click flips this true (reveals notice +
  // relabels button); 2nd click actually submits. Reset on close / on
  // any force toggle change so the user can't bypass by toggling.
  const [forceConfirmStage, setForceConfirmStage] = useState(false);

  const firstInputRef = useRef<HTMLInputElement | null>(null);

  function openModal() {
    if (externalOpen !== undefined) return; // caller controls open
    setInternalOpen(true);
  }

  function closeModal() {
    if (submitting) return;
    setInternalOpen(false);
    onExternalClose?.();
    setTypedName("");
    setReason("");
    setForceMode(false);
    setForceConfirmStage(false);
    setError(null);
  }

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => firstInputRef.current?.focus());
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) closeModal();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, submitting]);

  // Reset force-confirm stage whenever the user toggles the checkbox so a
  // sneaky path of (check → click → uncheck → submit) can't skip the
  // double-tap gate.
  function onForceToggle(next: boolean) {
    setForceMode(next);
    setForceConfirmStage(false);
  }

  // Kill validation — name exact match + reason >= REASON_MIN_CHARS.
  const nameMatches = typedName === project.name;
  const reasonValid = reason.trim().length >= REASON_MIN_CHARS;
  const canSubmitKill = !submitting && nameMatches && reasonValid;
  const canSubmitRevive = !submitting;

  // Submit label adapts: revive = "Revive project"; terminate non-force =
  // "Terminate project"; terminate force pre-confirm = "Terminate project";
  // terminate force after first click = "Confirm force-terminate".
  const submitLabel = (() => {
    if (mode === "revive") return submitting ? "Reviving…" : "Revive project";
    if (forceMode && forceConfirmStage) {
      return submitting ? "Terminating…" : "Confirm force-terminate";
    }
    return submitting ? "Terminating…" : "Terminate project";
  })();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "kill" && !canSubmitKill) return;
    if (mode === "revive" && !canSubmitRevive) return;

    // Two-step force gate: when force is checked AND we haven't yet shown
    // the red banner, the first submit click only reveals the banner +
    // relabels the button. User must click again to actually POST.
    if (mode === "kill" && forceMode && !forceConfirmStage) {
      setForceConfirmStage(true);
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      if (mode === "kill") {
        await killProject(project.id, { reason: reason.trim() }, forceMode);
      } else {
        await reviveProject(project.id);
      }
      router.refresh();
      // Close after successful refresh; reset state in closeModal() too
      // but call directly since closeModal short-circuits when submitting.
      setInternalOpen(false);
      onExternalClose?.();
      setTypedName("");
      setReason("");
      setForceMode(false);
      setForceConfirmStage(false);
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

  // Trigger button visuals — terminate = red, revive = green. The caller can
  // override className / label for placement-specific tweaks (e.g.
  // KilledBanner renders revive inline in the banner with a lighter style).
  const defaultTriggerLabel =
    mode === "kill" ? "Terminate project" : "Revive project";
  const defaultTriggerClass =
    mode === "kill"
      ? "inline-flex items-center rounded border border-red-600 bg-red-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-red-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-500 dark:bg-red-500 dark:hover:bg-red-600"
      : "inline-flex items-center rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600";

  return (
    <>
      {externalOpen === undefined && (
        <button
          type="button"
          onClick={openModal}
          className={triggerClassName ?? defaultTriggerClass}
          data-kill-project-trigger={mode}
        >
          {triggerLabel ?? defaultTriggerLabel}
        </button>
      )}
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="kill-project-title"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-kill-project-modal={mode}
          data-kill-project-name={project.name}
        >
          <form
            onSubmit={onSubmit}
            className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-w-md sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800"
          >
            <h2
              id="kill-project-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              {mode === "kill" ? "Terminate project" : "Revive project"} ·{" "}
              <span className="font-mono normal-case">{project.name}</span>
              {mode === "kill" ? "?" : "?"}
            </h2>

            {mode === "kill" ? (
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                Drains recurring tasks, blocks creation of new tasks, and freezes
                open TODO / BLOCKED rows. In-flight langgraph runs receive a
                30-second checkpoint grace (unless force is on). External
                commitments (calendars, third-party APIs) are NOT auto-cancelled
                — those need manual cleanup.
              </p>
            ) : (
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                Resumes recurring tasks (recomputes <span className="font-mono">next_fire_at</span>)
                and unfreezes <span className="font-mono">kill_frozen</span>{" "}
                rows. Cancelled external commitments will NOT be auto-recreated.
                Historical <span className="font-mono">killed_at</span> +{" "}
                <span className="font-mono">killed_reason</span> are preserved
                for audit.
              </p>
            )}

            {mode === "kill" && (
              <>
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
                    placeholder={project.name}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={submitting}
                    aria-invalid={typedName.length > 0 && !nameMatches}
                    className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                    data-kill-project-name-input
                  />
                </label>

                <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  Reason{" "}
                  <span className="font-normal text-zinc-500">
                    (≥{REASON_MIN_CHARS} chars)
                  </span>{" "}
                  <span className="text-red-600 dark:text-red-400">*</span>
                  <textarea
                    value={reason}
                    onChange={(e) => {
                      setReason(e.target.value);
                      if (error !== null) setError(null);
                    }}
                    rows={3}
                    placeholder="Why are we terminating this project? Captured into the audit row."
                    disabled={submitting}
                    aria-invalid={reason.length > 0 && !reasonValid}
                    className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                    data-kill-project-reason
                  />
                  <span className="mt-0.5 block text-[10px] text-zinc-500 dark:text-zinc-500 tabular-nums">
                    {reason.trim().length}/{REASON_MIN_CHARS}
                  </span>
                </label>

                <label className="mt-3 flex items-center gap-2 text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  <input
                    type="checkbox"
                    checked={forceMode}
                    onChange={(e) => onForceToggle(e.target.checked)}
                    disabled={submitting}
                    className="h-4 w-4 rounded border-zinc-300 text-red-600 focus:ring-red-500 dark:border-zinc-700 dark:bg-zinc-950"
                    data-kill-project-force-toggle
                  />
                  <span>
                    Force terminate immediately (no 30s grace)
                  </span>
                </label>

                {forceMode && (
                  <p
                    role="alert"
                    className="mt-2 rounded border border-red-300 bg-red-50 px-2 py-1.5 text-[11px] text-red-800 dark:border-red-700 dark:bg-red-900/30 dark:text-red-300"
                    data-kill-project-force-notice
                  >
                    ⚠ {FORCE_NOTICE}
                    {forceConfirmStage && (
                      <span className="mt-1 block font-medium">
                        Click again to confirm force-terminate.
                      </span>
                    )}
                  </p>
                )}
              </>
            )}

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
                data-kill-project-error
              >
                {error}
              </p>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-kill-project-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  mode === "kill" ? !canSubmitKill : !canSubmitRevive
                }
                className={
                  mode === "kill"
                    ? "rounded border border-red-600 bg-red-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-red-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-red-500 dark:bg-red-500 dark:hover:bg-red-600"
                    : "rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                }
                data-kill-project-submit
              >
                {submitLabel}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
