"use client";

// ActionTemplatePicker — Kanban #1340 (2026-05-20).
//
// Horizontal chip row at the top of the task-create modals (NewTaskModal +
// AiTaskModal). Sourced from GET /api/templates/actions (#1006). Each chip
// represents an action template; clicking pre-fills task_kind / task_type /
// priority / acceptance_criteria from that template's defaults.
//
// Empty state: when GET returns [] (no templates loaded or all YAMLs failed
// to parse), the entire section is hidden. Operators that don't author
// templates see no chrome at all.
//
// State ownership: the picker owns `templates` (fetched on mount) and
// `selectedId` (the chip currently selected). It does NOT own form state —
// the parent passes `onSelect` and gets the chosen ActionTemplateRead. The
// parent is responsible for merging template defaults into its form fields
// + sending `action_template_id` on the POST body.

import { useEffect, useState } from "react";

import { templates, type ActionTemplateRead } from "@/lib/api";

type Props = {
  selectedId: string | null;
  onSelect: (template: ActionTemplateRead | null) => void;
  disabled?: boolean;
};

export function ActionTemplatePicker({
  selectedId,
  onSelect,
  disabled,
}: Props) {
  const [items, setItems] = useState<ActionTemplateRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    templates.actions
      .list()
      .then((list) => {
        if (!cancelled) setItems(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setItems([]);
          setLoadError(err instanceof Error ? err.message : "Failed to load");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Hide the entire section when (a) still loading OR (b) loaded empty.
  // Errors are folded into the empty branch as well — a missing template
  // surface is not a blocker for the manual task-create flow.
  if (items === null) return null;
  if (items.length === 0) {
    // Suppress loadError display in the modal body (kept terse). The chrome
    // collapses entirely so the modal stays minimal.
    if (loadError) {
      // Surface only as a console hint for the operator-debug case.
      // eslint-disable-next-line no-console
      console.debug("[ActionTemplatePicker] load error:", loadError);
    }
    return null;
  }

  const selected =
    selectedId === null ? null : items.find((t) => t.id === selectedId) ?? null;

  return (
    <div
      className="mt-3 flex flex-col gap-1.5"
      data-action-template-picker
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
          Start from a template{" "}
          <span className="font-normal text-zinc-400">(optional)</span>
        </span>
        {selectedId !== null && (
          <button
            type="button"
            onClick={() => onSelect(null)}
            disabled={disabled}
            data-action-template-clear
            className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-800 disabled:opacity-50 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            Clear
          </button>
        )}
      </div>
      <div
        className="flex flex-wrap gap-1.5"
        role="radiogroup"
        aria-label="Action templates"
      >
        {items.map((t) => {
          const active = selectedId === t.id;
          return (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled}
              onClick={() => onSelect(active ? null : t)}
              data-action-template-chip={t.id}
              data-action-template-selected={active ? "true" : "false"}
              title={t.description}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors min-h-[36px] sm:min-h-0 disabled:opacity-50 disabled:cursor-not-allowed ${
                active
                  ? "border-violet-500 bg-violet-100 text-violet-900 dark:border-violet-400 dark:bg-violet-900/40 dark:text-violet-100"
                  : "border-zinc-300 bg-white text-zinc-700 hover:border-violet-300 hover:bg-violet-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-violet-700 dark:hover:bg-violet-950/40"
              }`}
            >
              <span>{t.name}</span>
            </button>
          );
        })}
      </div>
      {selected && (
        <p
          className="rounded border border-violet-200 bg-violet-50/60 px-2 py-1 text-[11px] text-violet-800 dark:border-violet-800 dark:bg-violet-950/30 dark:text-violet-200"
          data-action-template-selected-summary
        >
          <span className="font-semibold">From template:</span> {selected.description}
        </p>
      )}
    </div>
  );
}
