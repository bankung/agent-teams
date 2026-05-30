"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { grantConsent } from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

type Props = {
  project: { id: number; name: string };
};

// Trigger button + dialog. Server-rendered banner embeds this Client component as a
// sibling — composition pattern keeps the banner SSR while only the action is Client.
// Deliberate-action: typed-acknowledgment must match project.name exactly (backend
// validates case-sensitive); no optimistic update — wait for 200 then router.refresh().
export function ProjectConsentGrantModal({ project }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Focus the input on open.
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
  }, [open]);

  const closeModal = () => {
    if (submitting) return;
    setOpen(false);
    setTyped("");
    setError(null);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await grantConsent(project.id, typed);
      // Re-fetch the Server-rendered banner so it flips zinc → emerald.
      router.refresh();
      setOpen(false);
      setTyped("");
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Grant failed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="ml-2 inline-flex items-center rounded border border-zinc-300 bg-white px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-consent-grant-trigger
      >
        Enable headless auto-run
      </button>
      <ModalShell
        open={open}
        onClose={closeModal}
        labelledBy="consent-grant-title"
        maxWidth="sm"
        backdropProps={{ "data-consent-grant-modal": true }}
      >
          <form
            onSubmit={onSubmit}
          >
            <h2
              id="consent-grant-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              Enable headless auto-run
            </h2>
            <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
              Auto-headless tasks run without per-task confirmation. Type the
              project name{" "}
              <span className="font-mono text-zinc-900 dark:text-zinc-100">{project.name}</span> to
              confirm.
            </p>
            <input
              ref={inputRef}
              type="text"
              value={typed}
              onChange={(e) => {
                setTyped(e.target.value);
                if (error !== null) setError(null);
              }}
              placeholder={project.name}
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
              className="mt-3 w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
              data-consent-grant-input
            />
            {error !== null && (
              <p
                role="alert"
                className="mt-2 text-xs text-red-700 dark:text-red-300"
                data-consent-grant-error
              >
                {error}
              </p>
            )}
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || typed.length === 0}
                className="rounded border border-emerald-600 bg-emerald-600 px-2 py-1 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                data-consent-grant-submit
              >
                {submitting ? "Granting…" : "Grant"}
              </button>
            </div>
          </form>
      </ModalShell>
    </>
  );
}
