"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { grantConsent, setProjectToolsConfig } from "@/lib/api";
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
  const [operatorToken, setOperatorToken] = useState("");
  // Default Q&A — consent with no tools write. Standard enables read-auto + write/net/destructive halt.
  // Future follow-up: a "Custom" tier editor for fine-grained tier assignment (out of scope for #2732).
  const [posture, setPosture] = useState<"qa" | "standard">("qa");
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
    setOperatorToken("");
    setPosture("qa");
    setError(null);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await grantConsent(project.id, typed, operatorToken);
      // Re-fetch the Server-rendered banner so it flips zinc → emerald.
      if (posture === "standard") {
        try {
          await setProjectToolsConfig(
            project.id,
            {
              tools_enabled: true,
              auto_allow_tiers: ["read"],
              halt_tiers: ["write", "network", "destructive"],
            },
            operatorToken,
          );
        } catch (toolsErr: unknown) {
          // Consent succeeded; only the tools posture write failed.
          // Keep modal open to show the distinct error; refresh so the
          // banner flips (consent took effect).
          setError(
            `Consent granted, but enabling Standard tools failed — the project is active in Q&A-only mode. Re-open to enable tools. (${extractErrorMessage(toolsErr, "unknown error")})`,
          );
          router.refresh();
          return;
        }
      }
      router.refresh();
      setOpen(false);
      setTyped("");
      setOperatorToken("");
      setPosture("qa");
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
        Enable autonomous execution
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
              Enable autonomous execution
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
            {/* Tools posture — sets tools_config in one deliberate action (#2732 Option C). */}
            <fieldset className="mt-3" disabled={submitting}>
              <legend className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Tools posture
              </legend>
              <div className="mt-1.5 space-y-2">
                <label className="flex cursor-pointer items-start gap-2">
                  <input
                    type="radio"
                    name="consent-posture"
                    value="qa"
                    checked={posture === "qa"}
                    onChange={() => setPosture("qa")}
                    className="mt-0.5 accent-zinc-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-zinc-500 dark:accent-zinc-400"
                    data-consent-posture-qa
                  />
                  <span className="text-xs text-zinc-700 dark:text-zinc-300">
                    <span className="font-medium">Q&amp;A only</span>
                    {" — "}
                    <span className="text-zinc-500 dark:text-zinc-400">
                      The agent answers questions and plans, but every tool call is blocked. Safest; you can enable tools later.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-2">
                  <input
                    type="radio"
                    name="consent-posture"
                    value="standard"
                    checked={posture === "standard"}
                    onChange={() => setPosture("standard")}
                    className="mt-0.5 accent-zinc-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-zinc-500 dark:accent-zinc-400"
                    data-consent-posture-standard
                  />
                  <span className="text-xs text-zinc-700 dark:text-zinc-300">
                    <span className="font-medium">Standard tools</span>
                    {" — "}
                    <span className="text-zinc-500 dark:text-zinc-400">
                      Read-only tools run automatically; write, network, and destructive actions pause for your approval.
                    </span>
                  </span>
                </label>
              </div>
            </fieldset>
            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Operator token{" "}
              <span className="font-normal text-zinc-500">(optional)</span>
              <input
                type="password"
                value={operatorToken}
                onChange={(e) => setOperatorToken(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-consent-grant-operator-token
              />
              <span className="mt-0.5 block text-[10px] text-zinc-500 dark:text-zinc-500">
                Required only if operator gating is enabled.
              </span>
            </label>
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
