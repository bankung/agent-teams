"use client";

// TaskTemplatePicker — Kanban #1310 (2026-06-04).
//
// Native <select> at the top of the New Task modal. Sourced from the GLOBAL
// GET /api/task-templates?team=<team> (#1303). Picking a template pre-fills the
// modal's description + acceptance-criteria editor client-side ({{placeholder}}
// substitution happens in NewTaskModal). Native <select> is intentional over a
// custom combobox — most mobile-robust (AC#6).
//
// Presentational only: the parent owns `templates`, `selectedId`, and all form
// state. The picker just maps a chosen <option> back to its TaskTemplateRead.
//
// Empty state (AC#5): team has zero templates → render a friendly inline note
// instead of the dropdown. The modal stays fully usable for manual entry.

import type { TaskTemplateRead } from "@/lib/api";

type Props = {
  templates: TaskTemplateRead[];
  team: string;
  selectedId: number | null;
  onSelect: (t: TaskTemplateRead | null) => void;
  disabled?: boolean;
};

export function TaskTemplatePicker({
  templates,
  team,
  selectedId,
  onSelect,
  disabled,
}: Props) {
  // AC#5 — no catalog for this team: friendly note, no dropdown. Manual entry
  // below remains fully functional.
  if (templates.length === 0) {
    return (
      <p
        className="mt-3 rounded border border-zinc-200 bg-zinc-50 px-2 py-1.5 text-[11px] text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-400"
        data-new-task-template-empty
      >
        No templates yet for the {team || "this"} team — fill in the task
        manually below.
      </p>
    );
  }

  return (
    <label className="mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
      Start from a template{" "}
      <span className="font-normal text-zinc-400">(optional)</span>
      <select
        value={selectedId === null ? "" : String(selectedId)}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") {
            onSelect(null);
            return;
          }
          const id = Number(v);
          onSelect(templates.find((t) => t.id === id) ?? null);
        }}
        disabled={disabled}
        className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
        data-new-task-template
      >
        <option value="">Manual entry (no template)</option>
        <optgroup label={`${team} (${templates.length})`}>
          {templates.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {`${t.icon ? t.icon + " " : ""}${t.name}`}
            </option>
          ))}
        </optgroup>
      </select>
    </label>
  );
}
