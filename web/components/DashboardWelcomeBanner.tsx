"use client";

// T3 (#1362) — welcome banner shown to first-time users who land on the
// dashboard before creating their own project.
//
// Visibility rules (both must hold for the banner to show):
//   1. localStorage key `agent-teams.dashboard.welcomeDismissed` is NOT set
//   2. The user has no own project — i.e. the active project list, after
//      removing the built-in "agent-teams" and "demo-tour" entries, is empty.
//
// Dismiss behaviour:
//   • Clicking the X button writes the localStorage key and unmounts the banner
//     (permanent client-side flag until the user clears storage).
//   • When the user creates their own project the banner auto-hides (condition 2
//     fails); the localStorage flag is NOT written so the banner re-appears if
//     they later remove all their own projects.
//
// SSR safety: localStorage is not available server-side. `show` defaults to
// false; the effect hydrates the real value on first mount. First paint skips
// the banner (same pattern as InstallPwaNudge).

import { useEffect, useState } from "react";
import Link from "next/link";

import type { ProjectRead } from "@/lib/api";

const LS_KEY = "agent-teams.dashboard.welcomeDismissed";

// Built-in project names excluded from the "user has own project" check.
// Matching by name is stable across installs; ids vary.
const BUILTIN_NAMES = new Set(["agent-teams", "demo-tour"]);

type Props = {
  projects: ProjectRead[];
};

export function DashboardWelcomeBanner({ projects }: Props) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const dismissed = localStorage.getItem(LS_KEY) === "true";
    if (dismissed) return;

    const hasOwnProject = projects.some((p) => !BUILTIN_NAMES.has(p.name));
    if (!hasOwnProject) {
      setShow(true);
    }
  }, [projects]);

  if (!show) return null;

  function handleDismiss() {
    localStorage.setItem(LS_KEY, "true");
    setShow(false);
  }

  return (
    <aside
      role="note"
      aria-label="Welcome to agent-teams"
      data-welcome-banner
      className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-100"
    >
      <div className="min-w-0 flex-1 space-y-1">
        <p className="font-semibold">
          👋 Welcome to agent-teams
        </p>
        <p className="text-blue-800 dark:text-blue-200">
          AI agents do work for you. You approve key decisions.
        </p>
        <p className="text-blue-800 dark:text-blue-200">
          <Link
            href="/p/demo-tour"
            className="font-medium underline hover:no-underline"
          >
            Try the demo-tour project
          </Link>{" "}
          (3 sample tasks).
          <br />
          Or create your own — pick a domain, add a task, click Run.
        </p>
        <p className="text-blue-700 dark:text-blue-300 text-[12px]">
          Need a 5-min intro? Read{" "}
          <span className="font-mono">QUICKSTART.md</span> at the repo root.
        </p>
      </div>
      <button
        type="button"
        aria-label="Dismiss welcome banner"
        onClick={handleDismiss}
        className="shrink-0 rounded p-1 text-blue-600 hover:bg-blue-100 hover:text-blue-800 dark:text-blue-400 dark:hover:bg-blue-900/40 dark:hover:text-blue-200"
      >
        <svg
          aria-hidden
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 16 16"
          fill="currentColor"
          className="h-4 w-4"
        >
          <path d="M2.22 2.22a.75.75 0 0 1 1.06 0L8 6.94l4.72-4.72a.75.75 0 1 1 1.06 1.06L9.06 8l4.72 4.72a.75.75 0 1 1-1.06 1.06L8 9.06l-4.72 4.72a.75.75 0 0 1-1.06-1.06L6.94 8 2.22 3.28a.75.75 0 0 1 0-1.06Z" />
        </svg>
      </button>
    </aside>
  );
}
