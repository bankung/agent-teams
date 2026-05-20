"use client";

// OptionCard — single decision-option render for the task-detail surface
// (Kanban #1335). The focus-page chip variant (TaskFocusView) renders a
// compact button optimized for a 390x844 viewport; this component is the
// full-fidelity card variant for the dashboard drawer where vertical space
// is not a constraint.
//
// Renders label + optional description + optional hints. Radio-style
// "selected" state for the parent DecisionInteractionView. Click toggles
// selection; the parent owns the chosen state and submits via /decide.

import type { OptionItem } from "@/lib/api";

type Props = {
  option: OptionItem;
  selected: boolean;
  disabled?: boolean;
  onSelect: (option: OptionItem) => void;
};

export function OptionCard({ option, selected, disabled, onSelect }: Props) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      onClick={() => onSelect(option)}
      data-option-card={option.id}
      data-option-selected={selected ? "true" : "false"}
      className={`group flex w-full flex-col gap-1 rounded border p-3 text-left transition-colors min-h-[44px] disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? "border-violet-500 bg-violet-50 ring-2 ring-violet-300 dark:border-violet-400 dark:bg-violet-900/30 dark:ring-violet-700"
          : "border-zinc-200 bg-white hover:border-violet-300 hover:bg-violet-50/40 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-violet-700 dark:hover:bg-violet-950/30"
      }`}
    >
      <div className="flex items-start gap-2">
        {/* Radio indicator */}
        <span
          aria-hidden
          className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 ${
            selected
              ? "border-violet-500 bg-white dark:border-violet-400 dark:bg-zinc-900"
              : "border-zinc-300 bg-white dark:border-zinc-600 dark:bg-zinc-900"
          }`}
        >
          {selected && (
            <span className="h-2 w-2 rounded-full bg-violet-500 dark:bg-violet-400" />
          )}
        </span>
        <div className="flex-1 min-w-0">
          <p
            className={`text-sm font-medium ${
              selected
                ? "text-violet-900 dark:text-violet-100"
                : "text-zinc-900 dark:text-zinc-100"
            }`}
          >
            {option.label}
          </p>
          {option.description && (
            <p
              className={`mt-0.5 whitespace-pre-wrap text-xs ${
                selected
                  ? "text-violet-700 dark:text-violet-300"
                  : "text-zinc-600 dark:text-zinc-400"
              }`}
            >
              {option.description}
            </p>
          )}
          {option.hints && option.hints.length > 0 && (
            <ul
              className="mt-1.5 flex flex-col gap-0.5"
              data-option-hints
            >
              {option.hints.map((hint, idx) => (
                <li
                  key={idx}
                  className="text-xs italic text-zinc-500 dark:text-zinc-400"
                >
                  · {hint}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </button>
  );
}
