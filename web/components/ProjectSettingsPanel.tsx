"use client";

// ProjectSettingsPanel — Kanban #1349 (2026-05-20).
//
// Per-project operator preferences. Mounted on /p/[name]/settings/. v1
// surfaces one control: HITL nudge threshold (`hitl_nudge_threshold_hours`).
// Kanban #2300 (2026-06-11) adds a second: thinking effort (`effort_mode`).
// Future per-project settings drop in as sibling <section>s.
//
// State posture: form holds a local string for the input (so the operator
// can type "" without losing focus while the field is empty). Save button
// PATCHes /api/projects/{id} with the parsed int OR null. Success toast +
// router.refresh so the new value renders on the next read. No optimistic
// flip — this is a low-frequency mutation that runs at most once per
// project per operator session; a server-confirmed flip avoids subtle
// "looks saved but isn't" bugs.

import { useState } from "react";
import { useRouter } from "next/navigation";

import {
  updateProject,
  type ProjectRead,
  type ProjectUpdateBody,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ApprovalPoliciesEditor } from "./ApprovalPoliciesEditor";

// Effort mode select options. null encodes "use global default (= off)".
type EffortValue = "off" | "low" | "medium" | "high" | "extra" | "auto" | null;
const EFFORT_OPTIONS: { label: string; value: EffortValue }[] = [
  { label: "Default (off)", value: null },
  { label: "Off — no extended thinking", value: "off" },
  { label: "Low", value: "low" },
  { label: "Medium", value: "medium" },
  { label: "High", value: "high" },
  { label: "Extra (deepest auto-selectable)", value: "extra" },
  { label: "Auto — picks per task, caps at Extra", value: "auto" },
];
// Stable serialisation key so the <select> can round-trip through string.
function encodeEffort(v: EffortValue): string {
  return v === null ? "__null__" : v;
}
function decodeEffort(s: string): EffortValue {
  return s === "__null__" ? null : (s as EffortValue);
}

type Props = {
  project: ProjectRead;
  // When true the ApprovalPoliciesEditor is not rendered here; the caller
  // is responsible for rendering it (e.g. the settings page's Advanced section).
  hideApprovalPolicies?: boolean;
};

// Decode the wire value into a string for the input. NULL = empty
// (rendered as the "disabled" hint). Positive int = string form.
function decodeThreshold(value: number | null | undefined): string {
  if (value == null) return "";
  return String(value);
}

export function ProjectSettingsPanel({ project, hideApprovalPolicies = false }: Props) {
  const router = useRouter();
  const initialNudge = decodeThreshold(project.hitl_nudge_threshold_hours);
  const [nudgeRaw, setNudgeRaw] = useState(initialNudge);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  // Thinking effort state.
  const initialEffort: EffortValue =
    (project.effort_mode as EffortValue | undefined) ?? null;
  const [effortValue, setEffortValue] = useState<EffortValue>(initialEffort);
  const [effortSaving, setEffortSaving] = useState(false);
  const [effortError, setEffortError] = useState<string | null>(null);
  const [effortSavedNote, setEffortSavedNote] = useState<string | null>(null);
  const effortDirty = effortValue !== initialEffort;
  const canSaveEffort = !effortSaving && effortDirty;

  async function onSaveEffort(e: React.FormEvent) {
    e.preventDefault();
    if (!canSaveEffort) return;
    setEffortError(null);
    setEffortSavedNote(null);
    setEffortSaving(true);
    try {
      const body: ProjectUpdateBody = { effort_mode: effortValue };
      await updateProject(project.id, body);
      const label =
        effortValue === null ? "Saved. Using global default (off)." : `Saved. Effort: ${effortValue}.`;
      setEffortSavedNote(label);
      router.refresh();
    } catch (err: unknown) {
      setEffortError(extractErrorMessage(err, "Save failed"));
    } finally {
      setEffortSaving(false);
    }
  }

  // Validate: empty = clear to NULL (nudges disabled); "0" = disabled per BE
  // semantics; positive int ≥1 = threshold in hours. Negatives / non-ints
  // are rejected before the network call.
  const trimmed = nudgeRaw.trim();
  let parsed: number | null | "invalid" = null;
  if (trimmed === "") {
    parsed = null;
  } else {
    const n = Number(trimmed);
    if (!Number.isInteger(n) || n < 0) parsed = "invalid";
    else parsed = n;
  }
  const validationError =
    parsed === "invalid"
      ? "Enter a non-negative integer, or leave blank to disable."
      : null;

  const dirty = nudgeRaw !== initialNudge;
  const canSave = !saving && validationError === null && dirty;

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    if (!canSave || parsed === "invalid") return;
    setError(null);
    setSavedNote(null);
    setSaving(true);
    try {
      await updateProject(project.id, {
        hitl_nudge_threshold_hours: parsed,
      });
      const label =
        parsed === null || parsed === 0
          ? "Nudges disabled"
          : `Nudge threshold: ${parsed}h`;
      setSavedNote(label);
      // Refresh the server component so subsequent reads see the new value.
      router.refresh();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Save failed"));
    } finally {
      setSaving(false);
    }
  }

  // Derive a human-readable status line for the current value.
  const currentValueLabel = (() => {
    if (initialNudge === "" || initialNudge === "0")
      return "Currently: nudges disabled.";
    return `Currently: fire after ${initialNudge}h.`;
  })();

  return (
    <div
      data-project-settings-panel
      className="flex flex-col gap-8"
    >
    <section
      aria-labelledby="project-settings-heading"
      className="flex flex-col gap-4"
    >
      <header className="flex flex-col gap-1">
        <h2
          id="project-settings-heading"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          HITL nudge threshold
        </h2>
        <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
          When a HITL task waits longer than this threshold without operator
          attention, the backend sends a single aging-nudge. Empty or{" "}
          <span className="font-mono">0</span> = nudges disabled for this
          project. Per-task overrides via the &ldquo;Mute nudges&rdquo;
          toggle in the task drawer.
        </p>
      </header>

      <form
        onSubmit={onSave}
        className="flex flex-col gap-3 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
      >
        <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Hours before nudge fires{" "}
          <span className="font-normal text-zinc-400">
            (blank = disabled)
          </span>
          <input
            type="number"
            min={0}
            step={1}
            value={nudgeRaw}
            onChange={(e) => {
              setNudgeRaw(e.target.value);
              setError(null);
              setSavedNote(null);
            }}
            placeholder="e.g. 24"
            disabled={saving}
            data-project-nudge-threshold-input
            aria-invalid={validationError !== null}
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 font-mono text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />
        </label>
        <p className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono">
          {currentValueLabel}
        </p>

        {validationError !== null && (
          <p
            role="alert"
            className="text-[12px] text-red-700 dark:text-red-300"
          >
            {validationError}
          </p>
        )}
        {error !== null && (
          <p
            role="alert"
            className="text-[12px] text-red-700 dark:text-red-300"
          >
            {error}
          </p>
        )}
        {savedNote !== null && (
          <p
            role="status"
            className="text-[12px] text-green-700 dark:text-green-300"
          >
            Saved. {savedNote}
          </p>
        )}

        <div className="flex items-center justify-end">
          <button
            type="submit"
            disabled={!canSave}
            data-project-nudge-threshold-save
            className="min-h-[44px] rounded border border-emerald-600 bg-emerald-600 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </section>

    {!hideApprovalPolicies && <ApprovalPoliciesEditor project={project} />}

    {/* Kanban #2300 — Thinking effort per-project override */}
    <section
      aria-labelledby="project-effort-mode-heading"
      className="flex flex-col gap-4"
    >
      <header className="flex flex-col gap-1">
        <h2
          id="project-effort-mode-heading"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          Thinking effort
        </h2>
        <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
          Anthropic models on the headless engine only. Controls thinking depth
          and token spend per agent run. Auto lets the orchestrator pick a level
          per task — it never selects levels above Extra.
        </p>
      </header>

      <form
        onSubmit={onSaveEffort}
        className="flex flex-col gap-3 rounded-md border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
      >
        <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Effort level
          <select
            value={encodeEffort(effortValue)}
            onChange={(e) => {
              setEffortValue(decodeEffort(e.target.value));
              setEffortError(null);
              setEffortSavedNote(null);
            }}
            disabled={effortSaving}
            data-project-effort-mode-select
            className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
          >
            {EFFORT_OPTIONS.map((opt) => (
              <option key={encodeEffort(opt.value)} value={encodeEffort(opt.value)}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        {effortError !== null && (
          <p
            role="alert"
            className="text-[12px] text-red-700 dark:text-red-300"
          >
            {effortError}
          </p>
        )}
        {effortSavedNote !== null && (
          <p
            role="status"
            className="text-[12px] text-green-700 dark:text-green-300"
          >
            {effortSavedNote}
          </p>
        )}

        <div className="flex items-center justify-end">
          <button
            type="submit"
            disabled={!canSaveEffort}
            data-project-effort-mode-save
            className="min-h-[44px] rounded border border-emerald-600 bg-emerald-600 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 sm:min-h-0 sm:px-3 sm:py-1.5 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
          >
            {effortSaving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </section>
    </div>
  );
}
