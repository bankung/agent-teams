"use client";

// ModelTierSelect — shared <select> for the per-task model-tier override.
// Kanban #1677 added identical option sets in TaskDetail, AiTaskModal, and
// NewTaskModal; this component deduplicates the three copies.
//
// Callers own the outer <label> (text + mt-* spacing differs per site).
// The select's className, option set, and disabled behaviour are identical
// across all three; this component owns those.
//
// Any additional props (data-* test selectors, id, name, etc.) are forwarded
// to the underlying <select> via rest spread.

import type { ComponentPropsWithoutRef } from "react";

type Props = Omit<ComponentPropsWithoutRef<"select">, "className" | "children">;

export function ModelTierSelect(props: Props) {
  return (
    <select
      {...props}
      className="mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-500"
    >
      <option value="">Inherit (default)</option>
      <option value="haiku">Haiku</option>
      <option value="sonnet">Sonnet</option>
      <option value="opus">Opus</option>
    </select>
  );
}
