"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  updateProject,
  type ProjectRead,
  type ProjectUpdateBody,
  type Source,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { Icon } from "./Icon";

// Edit-project modal (Kanban #943 FE). Sibling to NewProjectModal — modal
// chrome (ESC / backdrop / focus first input / #954 mobile sheet pattern) is
// copy-verbatim from NewProjectModal; the edit-mode semantics (pre-fill +
// diff-on-save + dynamic sources list) are what diverge.
//
// Out of scope here (deliberate — NewProjectModal owns these):
//   - `name`         — rename is a separate UX; modal MUST NOT expose it.
//   - `team`         — same; team change has scaffold implications.
//   - is_active / agent_overrides / tools_config / budget_*_usd / paths_* /
//     auto_run_consent_at — separate flows (consent / budget config / tool
//     gate). Brief explicitly limits the edit surface to: description,
//     stack_{web,api,db}, config.standards.*, working_path, working_repo,
//     sources.
//
// Validation philosophy (mirrors NewProjectModal): BE is the gate of last
// resort — we surface `HttpError.message` inline and let Pydantic 422 catch
// anything the client-side guard misses. The one extra client-side check is
// `sources[].url` shape — that catch is purely a UX nicety (BE rejects the
// same shape with 422 detail).

// Mirror api/src/schemas/project.py:_SCHEME_RE plus the absolute-path branch
// (Unix `/...` or Windows `X:\` / `X:/`). Case-insensitive scheme match.
const URL_SCHEME_RE = /^(?:https?|ref|file):\/\//i;
function isUrlShapeValid(raw: string): boolean {
  const s = raw.trim();
  if (s.length === 0) return false;
  if (URL_SCHEME_RE.test(s)) return true;
  if (s.startsWith("/")) return true;
  // Windows X:\ or X:/
  if (s.length >= 3 && /^[A-Za-z]$/.test(s[0]) && (s.slice(1, 3) === ":\\" || s.slice(1, 3) === ":/")) {
    return true;
  }
  return false;
}

const SOURCE_KIND_OPTIONS = ["", "doc", "spec", "repo", "dashboard", "other"] as const;

// EditSource — local form state; differs from `Source` only in that `kind` is
// always a string (empty string = "no kind") to keep the <select> controlled.
// On save we drop the kind key when it's "" so the wire shape matches `Source`.
type EditSource = {
  url: string;
  label: string;
  kind: string;
};

function sourceToEdit(s: Source): EditSource {
  return {
    url: s.url ?? "",
    label: s.label ?? "",
    kind: s.kind ?? "",
  };
}

function editToSource(e: EditSource): Source {
  const out: Source = { url: e.url.trim() };
  const label = e.label.trim();
  if (label.length > 0) out.label = label;
  if (e.kind.length > 0) out.kind = e.kind;
  return out;
}

// arraysEqual — element-wise deep compare for `Source[]`. Order matters
// (preserves user re-ordering as a change).
function sourcesEqual(a: Source[], b: Source[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (x.url !== y.url) return false;
    if ((x.label ?? "") !== (y.label ?? "")) return false;
    if ((x.kind ?? "") !== (y.kind ?? "")) return false;
  }
  return true;
}

function arraysEqualString(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// parseStandards — split a comma-separated input into a trimmed, empty-filtered
// string array. Whitespace inside an entry is preserved; only leading/trailing
// whitespace per entry is trimmed.
function parseStandards(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

// readStandards — extract `config.standards.{web,api,db}` defensively. Legacy
// rows may have `config = {}` or `config.standards` shape drift; coerce to
// `string[]` and drop non-string entries.
function readStandards(
  config: Record<string, unknown> | undefined,
  lane: "web" | "api" | "db",
): string[] {
  if (!config) return [];
  const standards = config.standards as unknown;
  if (standards === null || typeof standards !== "object" || Array.isArray(standards)) return [];
  const lanes = standards as Record<string, unknown>;
  const v = lanes[lane];
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

const MAX_SOURCES = 20;

type Props = { project: ProjectRead };

export function EditProjectModal({ project }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const firstInputRef = useRef<HTMLTextAreaElement | null>(null);

  // Form state — initialized from `project` on open; reset on close.
  const [description, setDescription] = useState("");
  const [stackWeb, setStackWeb] = useState("");
  const [stackApi, setStackApi] = useState("");
  const [stackDb, setStackDb] = useState("");
  const [standardsWeb, setStandardsWeb] = useState("");
  const [standardsApi, setStandardsApi] = useState("");
  const [standardsDb, setStandardsDb] = useState("");
  const [workingPath, setWorkingPath] = useState("");
  const [workingRepo, setWorkingRepo] = useState("");
  const [sources, setSources] = useState<EditSource[]>([]);

  // Pre-fill on open. Re-runs only when `open` flips true OR the `project`
  // identity changes (parent re-fetch after SSE refresh). Wrapping reset
  // inside the open=true branch keeps closed-state stable.
  useEffect(() => {
    if (!open) return;
    setDescription(project.description ?? "");
    setStackWeb(project.stack_web ?? "");
    setStackApi(project.stack_api ?? "");
    setStackDb(project.stack_db ?? "");
    setStandardsWeb(readStandards(project.config, "web").join(", "));
    setStandardsApi(readStandards(project.config, "api").join(", "));
    setStandardsDb(readStandards(project.config, "db").join(", "));
    setWorkingPath(project.working_path ?? "");
    setWorkingRepo(project.working_repo ?? "");
    setSources((project.sources ?? []).map(sourceToEdit));
    setError(null);
    // Focus first editable field on open (parity with NewProjectModal which
    // focuses the name input).
    requestAnimationFrame(() => firstInputRef.current?.focus());
  }, [open, project]);

  // ESC closes (when not submitting). Mirrors NewProjectModal.
  useEffect(() => {
    if (!open) return;
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
    setError(null);
  }

  // Per-source URL validity (computed once per render). Used for inline
  // error display + submit gate.
  const sourceUrlInvalid = useMemo(
    () => sources.map((s) => s.url.trim().length > 0 && !isUrlShapeValid(s.url)),
    [sources],
  );
  // Required-field check: every source MUST have a non-blank, well-shaped URL.
  const allSourcesValid = sources.every(
    (s) => s.url.trim().length > 0 && isUrlShapeValid(s.url),
  );
  const tooManySources = sources.length > MAX_SOURCES;
  const canSubmit = !submitting && allSourcesValid && !tooManySources;

  // diff — build a ProjectUpdateBody containing ONLY changed keys. Text fields
  // use null-vs-empty-string semantics per the brief:
  //   - orig null + user "" → omit key (no change)
  //   - orig "x" + user "" → send null (clear column)
  //   - otherwise → send trimmed value if changed
  function buildDiff(): ProjectUpdateBody {
    const diff: ProjectUpdateBody = {};

    // Helper for nullable single-line text fields.
    const nullableTextDiff = (
      key: "stack_web" | "stack_api" | "stack_db" | "working_path" | "working_repo" | "description",
      origRaw: string | null | undefined,
      next: string,
    ) => {
      const orig = (origRaw ?? "") as string;
      const trimmed = next.trim();
      // Trim original too — whitespace-only round-trips don't count as edits.
      const origTrimmed = orig.trim();
      if (origTrimmed === trimmed) return;
      if (trimmed.length === 0) {
        // Was non-empty, now empty → explicit null clears the column. BE
        // accepts null on nullable text fields (description / stack_* /
        // working_*); router writes NULL.
        diff[key] = null;
        return;
      }
      diff[key] = trimmed;
    };

    nullableTextDiff("description", project.description, description);
    nullableTextDiff("stack_web", project.stack_web, stackWeb);
    nullableTextDiff("stack_api", project.stack_api, stackApi);
    nullableTextDiff("stack_db", project.stack_db, stackDb);
    nullableTextDiff("working_path", project.working_path, workingPath);
    nullableTextDiff("working_repo", project.working_repo, workingRepo);

    // config.standards — compare parsed arrays per lane. If ANY lane changed,
    // send merged `config` with `standards` updated; preserve other config
    // keys (e.g. legacy keys the modal doesn't surface).
    const nextStandards = {
      web: parseStandards(standardsWeb),
      api: parseStandards(standardsApi),
      db: parseStandards(standardsDb),
    };
    const origStandards = {
      web: readStandards(project.config, "web"),
      api: readStandards(project.config, "api"),
      db: readStandards(project.config, "db"),
    };
    const standardsChanged =
      !arraysEqualString(nextStandards.web, origStandards.web) ||
      !arraysEqualString(nextStandards.api, origStandards.api) ||
      !arraysEqualString(nextStandards.db, origStandards.db);
    if (standardsChanged) {
      // PATCH semantics on `config` are REPLACE (per schemas/project.py:273).
      // Preserve every other top-level config key so we don't drop a legacy
      // field the modal doesn't expose.
      const merged: Record<string, unknown> = { ...(project.config ?? {}) };
      merged.standards = nextStandards;
      diff.config = merged;
    }

    // sources — REPLACE on any change. Empty array means "clear all sources",
    // which BE accepts (sends [] on the wire; router normalizes null → [] too).
    const nextSources: Source[] = sources.map(editToSource);
    const origSources: Source[] = project.sources ?? [];
    if (!sourcesEqual(nextSources, origSources)) {
      diff.sources = nextSources;
    }

    return diff;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setError(null);

    const diff = buildDiff();
    // Empty diff → no PATCH; close silently. (User changed nothing — or
    // changed and then reverted. No reason to hit the BE.)
    if (Object.keys(diff).length === 0) {
      setOpen(false);
      return;
    }

    setSubmitting(true);
    try {
      await updateProject(project.id, diff);
      // router.refresh() is belt-and-suspenders — DashboardRefresher
      // already listens to SSE row_changed (per #930) and triggers refresh
      // independently.
      router.refresh();
      setOpen(false);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Update failed"));
    } finally {
      setSubmitting(false);
    }
  }

  // Sources list mutators.
  function addSource() {
    if (sources.length >= MAX_SOURCES) return;
    setSources((prev) => [...prev, { url: "", label: "", kind: "" }]);
  }
  function removeSource(idx: number) {
    setSources((prev) => prev.filter((_, i) => i !== idx));
  }
  function updateSource(idx: number, patch: Partial<EditSource>) {
    setSources((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  }

  const invalidUrlCount = sourceUrlInvalid.filter(Boolean).length;

  return (
    <>
      {/* Gear icon trigger — 44×44 min tap target on mobile (#954 / AC #8) */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Edit project"
        className="inline-flex items-center justify-center rounded border border-zinc-300 bg-white text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 px-2 py-1 min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 sm:px-1.5 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-edit-project-trigger
      >
        <Icon name="agent-config" size={14} />
      </button>
      {open && (
        // #954 — mobile full-screen sheet; sm restores centered max-w-lg card
        // (denser form than NewProjectModal — more fields incl. sources list).
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="edit-project-title"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
          data-edit-project-modal
          data-edit-project-name={project.name}
        >
          <form
            onSubmit={onSubmit}
            className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-w-lg sm:overflow-visible sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800"
          >
            <h2
              id="edit-project-title"
              className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
            >
              Edit project · <span className="font-mono normal-case">{project.name}</span>
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Name + team locked. Save sends only changed fields.
            </p>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Description
              <textarea
                ref={firstInputRef}
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  if (error !== null) setError(null);
                }}
                rows={3}
                placeholder="Short description shown on the dashboard card"
                disabled={submitting}
                className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                data-edit-project-description
              />
            </label>

            <fieldset className="mt-3 rounded border border-zinc-200 p-2 dark:border-zinc-800">
              <legend className="px-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Stack
              </legend>
              {(
                [
                  { label: "web", value: stackWeb, set: setStackWeb, key: "web" as const },
                  { label: "api", value: stackApi, set: setStackApi, key: "api" as const },
                  { label: "db", value: stackDb, set: setStackDb, key: "db" as const },
                ]
              ).map((row) => (
                <label
                  key={row.key}
                  className="mt-1 flex items-center gap-2 text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  <span className="w-10 shrink-0 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-500">
                    {row.label}
                  </span>
                  <input
                    type="text"
                    value={row.value}
                    onChange={(e) => {
                      row.set(e.target.value);
                      if (error !== null) setError(null);
                    }}
                    placeholder={
                      row.key === "web"
                        ? "Next.js + React + Tailwind"
                        : row.key === "api"
                        ? "FastAPI + SQLAlchemy"
                        : "PostgreSQL"
                    }
                    autoComplete="off"
                    spellCheck={false}
                    disabled={submitting}
                    className="block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                    data-edit-project-stack={row.key}
                  />
                </label>
              ))}
            </fieldset>

            <fieldset className="mt-3 rounded border border-zinc-200 p-2 dark:border-zinc-800">
              <legend className="px-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Standards (comma-separated)
              </legend>
              {(
                [
                  { label: "web", value: standardsWeb, set: setStandardsWeb, key: "web" as const },
                  { label: "api", value: standardsApi, set: setStandardsApi, key: "api" as const },
                  { label: "db", value: standardsDb, set: setStandardsDb, key: "db" as const },
                ]
              ).map((row) => {
                const parsed = parseStandards(row.value);
                return (
                  <label
                    key={row.key}
                    className="mt-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    <div className="flex items-center gap-2">
                      <span className="w-10 shrink-0 font-mono text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-500">
                        {row.label}
                      </span>
                      <input
                        type="text"
                        value={row.value}
                        onChange={(e) => {
                          row.set(e.target.value);
                          if (error !== null) setError(null);
                        }}
                        placeholder="general, nextjs, tailwind"
                        autoComplete="off"
                        spellCheck={false}
                        disabled={submitting}
                        className="block w-full rounded border border-zinc-300 bg-white px-2 py-1 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
                        data-edit-project-standards={row.key}
                      />
                    </div>
                    {parsed.length > 0 ? (
                      <span className="mt-0.5 block pl-12 text-[10px] font-normal text-zinc-500 dark:text-zinc-500">
                        Parsed: [{parsed.map((p) => `"${p}"`).join(", ")}]
                      </span>
                    ) : null}
                  </label>
                );
              })}
            </fieldset>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Working path
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
                data-edit-project-working-path
              />
            </label>

            <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Working repo URL
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
                data-edit-project-working-repo
              />
            </label>

            <fieldset className="mt-3 rounded border border-zinc-200 p-2 dark:border-zinc-800">
              <legend className="px-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Sources ({sources.length}/{MAX_SOURCES})
              </legend>
              {sources.length === 0 ? (
                <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-500">
                  No sources. Add reference URLs the team can open from the project card.
                </p>
              ) : (
                <ul className="mt-1 flex flex-col gap-2">
                  {sources.map((s, idx) => {
                    const invalid = sourceUrlInvalid[idx];
                    return (
                      <li
                        key={idx}
                        className="flex flex-col gap-1 rounded border border-zinc-200 bg-zinc-50/40 p-2 dark:border-zinc-800 dark:bg-zinc-900/40"
                        data-edit-project-source-row={idx}
                      >
                        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
                          <input
                            type="text"
                            value={s.url}
                            onChange={(e) => updateSource(idx, { url: e.target.value })}
                            placeholder="https://… or /abs/path or ref://"
                            autoComplete="off"
                            spellCheck={false}
                            aria-invalid={invalid}
                            disabled={submitting}
                            className={`block w-full rounded border bg-white px-2 py-1 font-mono text-xs placeholder:text-zinc-400 focus:outline-none disabled:opacity-50 dark:bg-zinc-950 dark:placeholder:text-zinc-500 ${
                              invalid
                                ? "border-red-400 text-red-700 focus:border-red-500 dark:border-red-700 dark:text-red-300"
                                : "border-zinc-300 text-zinc-900 focus:border-zinc-500 dark:border-zinc-700 dark:text-zinc-100 dark:focus:border-zinc-500"
                            }`}
                            data-edit-project-source-url={idx}
                          />
                          <input
                            type="text"
                            value={s.label}
                            onChange={(e) => updateSource(idx, { label: e.target.value })}
                            placeholder="label (optional)"
                            autoComplete="off"
                            disabled={submitting}
                            className="block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500 sm:w-32"
                            data-edit-project-source-label={idx}
                          />
                          <select
                            value={s.kind}
                            onChange={(e) => updateSource(idx, { kind: e.target.value })}
                            disabled={submitting}
                            className="block rounded border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
                            data-edit-project-source-kind={idx}
                          >
                            {SOURCE_KIND_OPTIONS.map((opt) => (
                              <option key={opt} value={opt}>
                                {opt === "" ? "(no kind)" : opt}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            onClick={() => removeSource(idx)}
                            disabled={submitting}
                            aria-label={`Remove source ${idx + 1}`}
                            className="rounded border border-zinc-200 bg-white px-3 py-2 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-red-400 hover:text-red-700 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-red-700 dark:hover:text-red-300"
                            data-edit-project-source-remove={idx}
                          >
                            Remove
                          </button>
                        </div>
                        {invalid ? (
                          <span
                            className="text-[10px] text-red-700 dark:text-red-300"
                            role="alert"
                            data-edit-project-source-error={idx}
                          >
                            URL must be http(s)://, ref://, file://, or an absolute path.
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
              <button
                type="button"
                onClick={addSource}
                disabled={submitting || sources.length >= MAX_SOURCES}
                className="mt-2 inline-flex items-center rounded border border-zinc-300 bg-white px-3 py-2 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
                data-edit-project-source-add
              >
                + Add source
              </button>
            </fieldset>

            {error !== null && (
              <p
                role="alert"
                className="mt-3 text-xs text-red-700 dark:text-red-300"
                data-edit-project-error
              >
                {error}
              </p>
            )}
            {invalidUrlCount > 0 && (
              <p
                role="alert"
                className="mt-2 text-xs text-red-700 dark:text-red-300"
                data-edit-project-source-summary
              >
                Fix {invalidUrlCount} source URL{invalidUrlCount === 1 ? "" : "s"} before saving.
              </p>
            )}

            {/* #954 — 44px min tap target on mobile for the action pair */}
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={submitting}
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-edit-project-cancel
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                data-edit-project-submit
              >
                {submitting ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
