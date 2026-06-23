"use client";

import { useGlass, type GlassMode } from "./GlassProvider";

// Kanban #2453 — on/off segmented control for the glass theme axis. Visual
// idiom matches ThemePicker (segmented buttons, aria-pressed, data-* hooks for
// tests). Lives next to ThemePicker in the Settings "Theme" section.

const OPTIONS: { value: GlassMode; label: string }[] = [
  { value: "off", label: "flat" },
  { value: "on", label: "glass" },
];

function Icon({ name }: { name: GlassMode }) {
  if (name === "on") {
    // Frosted-pane glyph: rounded rect with a diagonal sheen.
    return (
      <svg
        viewBox="0 0 16 16"
        width="14"
        height="14"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="2.5" y="2.5" width="11" height="11" rx="2.5" />
        <path d="M5 11l6-6" />
        <path d="M8 12l4-4" />
      </svg>
    );
  }
  // Flat glyph: plain rounded rect.
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="2.5" y="3.5" width="11" height="9" rx="1.5" />
    </svg>
  );
}

export function GlassPicker() {
  const { glass, setGlass } = useGlass();

  return (
    <div
      role="group"
      aria-label="surface style"
      data-glass-selected={glass}
      data-glass-picker
      className="inline-flex items-center rounded border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
    >
      {OPTIONS.map((opt) => {
        const selected = glass === opt.value;
        const base =
          "inline-flex items-center gap-1 px-2 py-1 text-xs text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100";
        const sel = selected
          ? " bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
          : "";
        return (
          <button
            key={opt.value}
            type="button"
            aria-label={opt.label}
            aria-pressed={selected}
            title={opt.label}
            onClick={() => setGlass(opt.value)}
            className={base + sel}
            data-glass-option={opt.value}
          >
            <Icon name={opt.value} />
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
