"use client";

// #1019 (FE) — dismissible banner telling the operator to restart their
// Claude Code session when the backend detects a change under .claude/agents/.
// Agents load only at session start (see MEMORY.md
// feedback_agents_load_at_start.md); a running session's roster is stale
// until restart, so this is a nudge, not an auto-fix.
//
// Signal: shares the ONE existing wildcard SSE connection (WildcardSSEContext,
// Kanban #2111) rather than opening a second EventSource. The backend
// broadcasts { table: "agents", op: "changed", ts }; onAgentsChange fires on
// every such row_changed event regardless of payload shape (no id/project_id
// to key off — it's a filesystem tick, not a DB row).
//
// Visibility: no localStorage persistence (unlike DashboardWelcomeBanner) —
// each fresh onAgentsChange re-arms the banner even after a prior dismiss in
// the same session, per spec ("Re-appears on a fresh onAgentsChange after
// dismiss"). Dismissing only hides the CURRENT notification.
//
// Style: glassmorphism house style (context/standards/web/design-language.md)
// via the existing .glass-surface tier-1 class — no new CSS, and it inherits
// the @supports fallback + blur-tier cap already defined in globals.css.
// Non-blocking top banner (not a modal); role="status" + aria-live="polite"
// matches Toast.tsx / PausedBanner.tsx's announcement pattern.

import { useState } from "react";

import { useWildcardRowChanged } from "@/lib/WildcardSSEContext";

export function AgentsChangedBanner() {
  const [visible, setVisible] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useWildcardRowChanged({
    onAgentsChange: () => {
      setVisible(true);
      setExpanded(false);
    },
  });

  if (!visible) return null;

  function handleDismiss() {
    setVisible(false);
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-agents-changed-banner
      className="glass-surface relative z-20 mx-3 mt-3 flex flex-col gap-2 rounded-lg border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-900 shadow-sm dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-100"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-semibold">New or changed agent detected</p>
          <p className="text-violet-800 dark:text-violet-200">
            Restart your Claude Code session to activate it (agents load at
            session start).
          </p>
        </div>
        <button
          type="button"
          aria-label="Dismiss agent-changed notice"
          onClick={handleDismiss}
          className="shrink-0 rounded p-1 text-violet-600 hover:bg-violet-100 hover:text-violet-800 dark:text-violet-400 dark:hover:bg-violet-900/40 dark:hover:text-violet-200"
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
      </div>

      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((e) => !e)}
        className="self-start text-xs font-medium text-violet-700 underline hover:no-underline dark:text-violet-300"
      >
        {expanded ? "Hide" : "How to restart"}
      </button>
      {expanded && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-violet-800 dark:text-violet-200">
          <li>Close this Claude Code session, then start a new one (run `claude` again), or</li>
          <li>If you&apos;re inside an existing session, exit and reopen it.</li>
        </ul>
      )}
    </div>
  );
}
