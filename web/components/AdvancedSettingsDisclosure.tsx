"use client";

// AdvancedSettingsDisclosure — collapsible "Advanced" section on the settings
// page. Wraps Approval policies + Audit history (rarely-touched controls).
// Kanban #2482.
//
// Uses the same readExpanded/writeExpanded/storageKey pattern as CostSummary
// and AuditorActivityPanel. Default: collapsed.

import { useEffect, useState } from "react";
import { readExpanded, writeExpanded } from "@/lib/collapseState";

const STORAGE_KEY = "settings.advanced.expanded";

function ChevronDownIcon() {
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
      <polyline points="4 6 8 10 12 6" />
    </svg>
  );
}

function ChevronRightIcon() {
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
      <polyline points="6 4 10 8 6 12" />
    </svg>
  );
}

type Props = {
  children: React.ReactNode;
};

export function AdvancedSettingsDisclosure({ children }: Props) {
  // SSR-safe: default collapsed so SSR + first paint agree; hydrate from
  // localStorage in effect (same pattern as CostSummary).
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setExpanded(readExpanded(STORAGE_KEY, /* defaultCollapsed= */ true));

    function onStorage(e: StorageEvent) {
      if (e.key !== STORAGE_KEY) return;
      setExpanded(
        e.newValue !== null ? JSON.parse(e.newValue) !== false : false,
      );
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    writeExpanded(STORAGE_KEY, next);
  }

  return (
    <section
      data-settings-advanced
      aria-labelledby="settings-advanced-heading"
      className="flex flex-col gap-3"
    >
      <button
        type="button"
        id="settings-advanced-heading"
        onClick={toggle}
        aria-expanded={expanded}
        className="flex items-center gap-2 text-left text-base font-semibold text-zinc-900 dark:text-zinc-100"
      >
        <span className="text-zinc-500 dark:text-zinc-400">
          {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
        </span>
        Advanced
      </button>

      {expanded && (
        <div className="flex flex-col gap-8">{children}</div>
      )}
    </section>
  );
}
