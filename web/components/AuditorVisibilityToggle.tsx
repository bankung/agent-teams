"use client";

// Toggle button rendered in the dashboard header bar. Writes
// `dashboard.panels.auditor.visible` to localStorage and dispatches a
// synthetic StorageEvent so the AuditorActivityPanel on the same page picks
// up the change immediately (the native "storage" event only fires in OTHER
// browser tabs, not the originating tab).
//
// Default: visible (true) when the key is absent — preserves backward-compat
// for users who have never interacted with the toggle.

import { usePersistentState } from "@/lib/usePersistentState";

const LS_KEY = "dashboard.panels.auditor.visible";

// Eye icon — visible state
function EyeIcon() {
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
      <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

// Eye-off icon — hidden state
function EyeOffIcon() {
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
      <path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z" />
      <circle cx="8" cy="8" r="2" />
      <line x1="2" y1="2" x2="14" y2="14" />
    </svg>
  );
}

export function AuditorVisibilityToggle() {
  // Server snapshot = true so SSR + first paint render the button "visible"
  // (avoids hydration mismatch / layout flash); client reads localStorage.
  // Absent → true. usePersistentState dispatches the same-tab StorageEvent that
  // AuditorActivityPanel listens for (replaces the old writeVisible).
  const [visible, setVisible] = usePersistentState<boolean>(LS_KEY, true, {
    deserialize: (raw) => JSON.parse(raw) !== false,
  });

  function toggle() {
    setVisible(!visible);
  }

  return (
    <button
      type="button"
      aria-label={visible ? "Hide auditor activity panel" : "Show auditor activity panel"}
      aria-pressed={visible}
      title={visible ? "Hide auditor panel" : "Show auditor panel"}
      onClick={toggle}
      data-auditor-toggle
      data-auditor-visible={visible}
      className="inline-flex items-center justify-center rounded border border-zinc-200 bg-white px-1.5 py-1 text-zinc-600 hover:text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
    >
      {visible ? <EyeIcon /> : <EyeOffIcon />}
    </button>
  );
}
