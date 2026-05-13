"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { createProject, HttpError } from "@/lib/api";
import { ProjectTeam, type ProjectTeamValue } from "@/lib/constants";

// Trigger button + dialog for POST /api/projects (Kanban #843 FE).
// Visual pattern mirrors ProjectConsentGrantModal: zinc-bordered panel, focus
// on first input, ESC closes, backdrop click closes. Team options are derived
// from ProjectTeam constant (Object.values) — new teams added to the enum
// (e.g., #844's `general`) auto-appear without a code change here.
//
// Server-side validation owns name regex / dup-name / team enum — we lean on
// 422 detail strings rendered inline; the client-side disabled-submit guard
// is best-effort UX (required fields filled), not a contract enforcer.

const NAME_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;

const TEAM_OPTIONS: ProjectTeamValue[] = Object.values(ProjectTeam);

export function NewProjectModal() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [workingPath, setWorkingPath] = useState("");
  const [workingRepo, setWorkingRepo] = useState("");
  const [team, setTeam] = useState<ProjectTeamValue>(ProjectTeam.DEV);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    nameInputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) closeModal();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, submitting]);

  function closeModal() {
    if (submitting) return;
    setOpen(false);
    setName("");
    setWorkingPath("");
    setWorkingRepo("");
    setTeam(ProjectTeam.DEV);
    setError(null);
  }

  // Required-field guard for the submit button. Mirrors the backend's name
  // pattern so a clearly-invalid name disables submit; backend remains the
  // source of truth and will 422 anything that slips past.
  const nameValid = NAME_PATTERN.test(name);
  const canSubmit = !submitting && nameValid && team.length > 0;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);

    // paths.{web,api,db} are required by the backend. Derive from working_path
    // if set (per-lane sub-folders), otherwise default to "<name>/<lane>" so
    // the request always carries the 3 keys. The user never sees raw paths.
    const root = workingPath.trim() || name;
    const paths = {
      web: `${root}/web`,
      api: `${root}/api`,
      db: `${root}/db`,
    };
    const body = {
      name: name.trim(),
      paths,
      team,
      ...(workingPath.trim() ? { working_path: workingPath.trim() } : {}),
      ...(workingRepo.trim() ? { working_repo: workingRepo.trim() } : {}),
    };
    try {
      await createProject(body);
      router.refresh();
      setOpen(false);
      setName("");
      setWorkingPath("");
      setWorkingRepo("");
      setTeam(ProjectTeam.DEV);
    } catch (err: unknown) {
      if (err instanceof HttpError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Create failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center rounded border border-zinc-300 bg-white px-2 py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-new-project-trigger
      >
        + New project
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-project-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 px-4 dark:bg-zinc-950/70"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-new-project-modal
        >
          <form
            onSubmit={onSubmit}
            className="w-full max-w-md rounded border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <h2
              id="new-project-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              Create project
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Scaffolds <span className="font-mono">context/projects/&lt;name&gt;/</span> and
              the per-lane folders.
            </p>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Name <span className="text-red-600 dark:text-red-400">*</span>
              <input
                ref={nameInputRef}
                type="text"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="my-project"
                autoComplete="off"
                spellCheck={false}
                disabled={submitting}
                aria-invalid={name.length > 0 && !nameValid}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-new-project-name
              />
              <span className="mt-0.5 block text-[10px] text-zinc-500 dark:text-zinc-500">
                Letters, digits, <span className="font-mono">_</span> or <span className="font-mono">-</span> only (1–64 chars).
              </span>
            </label>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Working path <span className="font-normal text-zinc-400">(optional)</span>
              <input
                type="text"
                value={workingPath}
                onChange={(e) => {
                  setWorkingPath(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="C:\\Users\\me\\Projects\\my-project"
                autoComplete="off"
                spellCheck={false}
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-new-project-working-path
              />
            </label>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Working repo URL <span className="font-normal text-zinc-400">(optional)</span>
              <input
                type="text"
                value={workingRepo}
                onChange={(e) => {
                  setWorkingRepo(e.target.value);
                  if (error !== null) setError(null);
                }}
                placeholder="https://github.com/you/my-project"
                autoComplete="off"
                spellCheck={false}
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-new-project-working-repo
              />
            </label>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Team <span className="text-red-600 dark:text-red-400">*</span>
              <select
                value={team}
                onChange={(e) => {
                  setTeam(e.target.value as ProjectTeamValue);
                  if (error !== null) setError(null);
                }}
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                data-new-project-team
              >
                {TEAM_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
                data-new-project-error
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
                data-new-project-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded border border-emerald-600 bg-emerald-600 px-2 py-1 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                data-new-project-submit
              >
                {submitting ? "Creating…" : "Create"}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
