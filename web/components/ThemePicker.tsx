"use client";

import { useTheme, type Theme } from "./ThemeProvider";

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "light", label: "light" },
  { value: "dark", label: "dark" },
  { value: "system", label: "system" },
];

function Icon({ name }: { name: Theme }) {
  if (name === "light") {
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
        <circle cx="8" cy="8" r="3" />
        <path d="M8 1.5v2" />
        <path d="M8 12.5v2" />
        <path d="M1.5 8h2" />
        <path d="M12.5 8h2" />
        <path d="M3.4 3.4l1.4 1.4" />
        <path d="M11.2 11.2l1.4 1.4" />
        <path d="M3.4 12.6l1.4-1.4" />
        <path d="M11.2 4.8l1.4-1.4" />
      </svg>
    );
  }
  if (name === "dark") {
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
        <path d="M13.5 9.5A5.5 5.5 0 1 1 6.5 2.5a4.5 4.5 0 0 0 7 7z" />
      </svg>
    );
  }
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
      <rect x="2" y="3" width="12" height="8" rx="1" />
      <path d="M5.5 13.5h5" />
      <path d="M8 11.5v2" />
    </svg>
  );
}

export function ThemePicker() {
  const { theme, setTheme } = useTheme();

  return (
    <div
      role="group"
      aria-label="theme"
      data-theme-selected={theme}
      data-theme-picker
      className="inline-flex items-center rounded border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
    >
      {OPTIONS.map((opt) => {
        const selected = theme === opt.value;
        const base =
          "inline-flex items-center justify-center px-1.5 py-1 text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100";
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
            onClick={() => setTheme(opt.value)}
            className={base + sel}
            data-theme-option={opt.value}
          >
            <Icon name={opt.value} />
          </button>
        );
      })}
    </div>
  );
}
