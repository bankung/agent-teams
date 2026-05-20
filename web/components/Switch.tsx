"use client";

// Kanban #1288 — icon-button-style toggle switch for binary project state
// (pause / terminate). The Switch is purely a trigger: clicking it opens
// the relevant confirmation modal rather than flipping state directly.
// Visual state reflects the CURRENT project status (on = active, off =
// paused / terminated). No new npm dep — Tailwind + aria-pressed pattern.
//
// Matches the audit-task chip styling at Board.tsx:320 for visual continuity:
// rounded-full border, px-2.5 py-1, text-[11px] uppercase tracking-wide.

type SwitchProps = {
  label: string;
  checked: boolean;
  onClick: () => void;
  // Color theme for the checked (ON) state.
  // unchecked state always renders muted zinc.
  colorOn?: "red" | "amber";
  disabled?: boolean;
  "aria-label"?: string;
};

export function Switch({
  label,
  checked,
  onClick,
  colorOn = "red",
  disabled,
  "aria-label": ariaLabel,
}: SwitchProps) {
  const onClasses =
    colorOn === "amber"
      ? "border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-200"
      : "border-red-400 bg-red-100 text-red-900 dark:border-red-700 dark:bg-red-950/40 dark:text-red-300";

  const offClasses =
    "border-zinc-200 bg-transparent text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel ?? label}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors min-h-[36px] sm:min-h-0 disabled:opacity-50 disabled:cursor-not-allowed ${checked ? onClasses : offClasses}`}
    >
      {/* Track pill */}
      <span
        aria-hidden
        className={`relative inline-flex h-3 w-5 shrink-0 rounded-full transition-colors ${
          checked
            ? colorOn === "amber"
              ? "bg-amber-500 dark:bg-amber-400"
              : "bg-red-500 dark:bg-red-400"
            : "bg-zinc-300 dark:bg-zinc-600"
        }`}
      >
        {/* Thumb */}
        <span
          className={`absolute top-0.5 h-2 w-2 rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-2.5" : "translate-x-0.5"
          }`}
        />
      </span>
      <span>{label}</span>
    </button>
  );
}
