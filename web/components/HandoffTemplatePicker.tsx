"use client";

// HandoffTemplatePicker — Kanban #1343 (2026-05-20).
//
// Dropdown labeled "After this task" at the bottom of NewTaskModal +
// AiTaskModal. Sourced from GET /api/handoff-templates?project_id=<current>
// (#1004) — returns globals + this-project's rows. Hidden when no templates
// exist.
//
// Behavior: selection writes `handoff_template_id` to the parent's form
// state. The BE persists this on the task row; the DONE-flip hook
// (services/handoff_spawn.py) reads it and atomically spawns the child task
// from the named template. The CHILD's value is always NULL (loop guard).
//
// Tooltip on the dropdown shows the title_pattern preview + ac_outline
// summary + default_task_kind so the operator knows what will fire on DONE.

import { useEffect, useMemo, useState } from "react";

import {
  handoffTemplates,
  type HandoffTemplateRead,
} from "@/lib/api";

type Props = {
  projectId: number;
  selectedId: number | null;
  onSelect: (templateId: number | null) => void;
  disabled?: boolean;
};

export function HandoffTemplatePicker({
  projectId,
  selectedId,
  onSelect,
  disabled,
}: Props) {
  const [items, setItems] = useState<HandoffTemplateRead[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    handoffTemplates
      .list({ projectId })
      .then((list) => {
        if (!cancelled) setItems(list);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const selected = useMemo(
    () =>
      selectedId === null
        ? null
        : items?.find((t) => t.id === selectedId) ?? null,
    [items, selectedId],
  );

  // Hide section while loading + when empty.
  if (items === null) return null;
  if (items.length === 0) return null;

  return (
    <div
      className="mt-3 flex flex-col gap-1"
      data-handoff-template-picker
    >
      <label
        htmlFor="handoff-template-select"
        className="text-xs font-medium text-zinc-700 dark:text-zinc-300"
      >
        After this task{" "}
        <span className="font-normal text-zinc-400">
          (optional — auto-spawn a child on DONE)
        </span>
      </label>
      <div className="flex items-center gap-2">
        <select
          id="handoff-template-select"
          value={selectedId === null ? "" : String(selectedId)}
          onChange={(e) => {
            const v = e.target.value;
            onSelect(v === "" ? null : Number(v));
          }}
          disabled={disabled}
          data-handoff-template-select
          className="flex-1 rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
        >
          <option value="">— none —</option>
          {items.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
              {t.project_id === null ? " (global)" : ""}
            </option>
          ))}
        </select>
        {selectedId !== null && (
          <button
            type="button"
            onClick={() => onSelect(null)}
            disabled={disabled}
            data-handoff-template-clear
            className="rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700"
          >
            Clear
          </button>
        )}
      </div>
      {selected && (
        <div
          className="rounded border border-zinc-200 bg-zinc-50/60 px-2 py-1 dark:border-zinc-800 dark:bg-zinc-950/40"
          data-handoff-template-summary
        >
          <p className="text-[11px] text-zinc-700 dark:text-zinc-300">
            <span className="font-semibold">Title pattern:</span>{" "}
            <span className="font-mono">{selected.title_pattern}</span>
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
            <span className="font-semibold">Kind / type:</span>{" "}
            {selected.task_kind} · {selected.task_type}
          </p>
          {selected.ac_outline.length > 0 && (
            <p className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
              <span className="font-semibold">AC outline:</span>{" "}
              {selected.ac_outline.length} item
              {selected.ac_outline.length === 1 ? "" : "s"}
            </p>
          )}
          {selected.description && (
            <p className="mt-0.5 text-[11px] italic text-zinc-500 dark:text-zinc-400">
              {selected.description}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
