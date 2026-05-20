"use client";

// Toggle button rendered in the dashboard header bar. Writes
// `dashboard.panels.auditor.visible` to localStorage and dispatches a
// synthetic StorageEvent so the AuditorActivityPanel on the same page picks
// up the change immediately (the native "storage" event only fires in OTHER
// browser tabs, not the originating tab).
//
// Default: visible (true) when the key is absent — preserves backward-compat
// for users who have never interacted with the toggle.

import { useEffect, useState } from "react";

const LS_KEY = "dashboard.panels.auditor.visible";

function readVisible(): boolean {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw === null) return true;
    return JSON.parse(raw) !== false;
  } catch {
    return true;
  }
}

function writeVisible(next: boolean): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(next));
    // Notify same-tab listeners (native StorageEvent only goes to other tabs).
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: LS_KEY,
        newValue: JSON.stringify(next),
        storageArea: localStorage,
      }),
    );
  } catch {
    // localStorage blocked (private-mode quota exceeded, etc.) — silently ignore.
  }
}

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
  // Default true so SSR and first paint both render the button in "visible"
  // state — avoids hydration mismatch and layout flash.
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    // Sync to actual localStorage value after hydration.
    setVisible(readVisible());

    function onStorage(e: StorageEvent) {
      if (e.key !== LS_KEY) return;
      setVisible(e.newValue !== null ? JSON.parse(e.newValue) !== false : true);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function toggle() {
    const next = !visible;
    setVisible(next);
    writeVisible(next);
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
