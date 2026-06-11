"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { createProject, getTeams, type Team } from "@/lib/api";
import { ProjectTeam, type ProjectTeamValue } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

// Inline info-icon popover (click-toggle). No external library — uses
// Tailwind positioning + outside-click dismiss. Reused for team + working_path.
function InfoPopover({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function handleOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex">
      <button
        type="button"
        aria-label="More info"
        onClick={() => setOpen((v) => !v)}
        className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-blue-400 bg-blue-50 text-[11px] font-semibold text-blue-600 hover:border-blue-500 hover:bg-blue-100 hover:text-blue-700 dark:border-blue-500/70 dark:bg-blue-900/30 dark:text-blue-300 dark:hover:border-blue-400 dark:hover:bg-blue-900/50 dark:hover:text-blue-200"
      >
        ?
      </button>
      {open && (
        <div
          role="tooltip"
          className="absolute left-5 top-0 z-50 w-72 rounded border border-zinc-200 bg-white p-3 text-xs text-zinc-700 shadow-md dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
        >
          {children}
        </div>
      )}
    </span>
  );
}

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

// Compile-time fallback list — used while /api/teams is in flight or on fetch
// failure. Keeps the select populated even without a network round-trip.
const TEAM_OPTIONS_FALLBACK: ProjectTeamValue[] = Object.values(ProjectTeam);

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

  // Fetched teams from /api/teams. null = not yet loaded; [] = fetch failed.
  const [teams, setTeams] = useState<Team[] | null>(null);

  useEffect(() => {
    getTeams()
      .then(setTeams)
      .catch(() => setTeams([]));
  }, []);

  // The select options: runtime API list if available, compile-time fallback otherwise.
  const teamOptions: string[] =
    teams && teams.length > 0
      ? teams.map((t) => t.team)
      : TEAM_OPTIONS_FALLBACK;

  // Roster for the currently-selected team (from fetched data only).
  const selectedTeamRoster: string[] | null =
    teams && teams.length > 0
      ? (teams.find((t) => t.team === team)?.roster ?? null)
      : null;

  useEffect(() => {
    if (!open) return;
    nameInputRef.current?.focus();
  }, [open]);

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
      setError(extractErrorMessage(err, "Create failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      {/* #954 — 44px min tap target on mobile */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center rounded border border-zinc-300 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-new-project-trigger
      >
        + New project
      </button>
      {/* #954 — mobile: full-screen sheet; desktop restores centered max-w-md card */}
      <ModalShell
        open={open}
        onClose={closeModal}
        labelledBy="new-project-title"
        backdropProps={{ "data-new-project-modal": true }}
      >
          <form
            onSubmit={onSubmit}
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
              <span className="inline-flex items-center">
                Working path <span className="font-normal text-zinc-400 ml-1">(optional)</span>
                <InfoPopover>
                  <p className="font-semibold text-zinc-800 dark:text-zinc-200 mb-1">Working path</p>
                  <p className="mb-1">The folder where this project&apos;s files actually live. Agents read/write files under this path.</p>
                  <p className="mb-1">
                    <span className="font-medium">Leave blank</span> → uses{" "}
                    <span className="font-mono">context/projects/&lt;name&gt;/</span> inside the
                    agent-teams repo itself (good for novel, general, or one-off projects with no
                    separate repo).
                  </p>
                  <p>
                    <span className="font-medium">Set an absolute path</span> → agents work in that
                    folder (e.g.{" "}
                    <span className="font-mono">C:\Code\myapp</span>). Use this when the project has
                    its own repo. Agents will write to{" "}
                    <span className="font-mono">&lt;path&gt;/shared/</span> and{" "}
                    <span className="font-mono">&lt;path&gt;/&lt;role&gt;/</span>.
                  </p>
                </InfoPopover>
              </span>
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
              <span className="inline-flex items-center">
                Team <span className="text-red-600 dark:text-red-400 ml-0.5">*</span>
                <InfoPopover>
                  <p className="font-semibold text-zinc-800 dark:text-zinc-200 mb-1.5">Team — agent roster</p>
                  {selectedTeamRoster ? (
                    <p className="text-zinc-500 dark:text-zinc-400">
                      <span className="font-medium text-zinc-800 dark:text-zinc-200">{team}</span>
                      {" — "}
                      {selectedTeamRoster.join(" · ")}
                    </p>
                  ) : (
                    <p className="text-zinc-400 dark:text-zinc-500 italic">Select a team to see its roster.</p>
                  )}
                </InfoPopover>
              </span>
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
                {teamOptions.map((t) => (
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

            {/* #954 — 44px min tap target on mobile for the modal action pair */}
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-new-project-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                data-new-project-submit
              >
                {submitting ? "Creating…" : "Create"}
              </button>
            </div>
          </form>
      </ModalShell>
    </>
  );
}
