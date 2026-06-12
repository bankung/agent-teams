// Shared kind→chip-colour palette for Lead activity-rail events.
// Used by TaskToolCalls (detail drawer) and TaskActivityStrip (card strip).
// Extracted from TaskToolCalls.tsx (#2320) to avoid duplication — Kanban #2334.
import type { LeadEventKind } from "@/lib/api";

export const KIND_CLASS: Record<LeadEventKind, string> = {
  spawn:         "bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",
  tool_result:   "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  ac_verified:   "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  commit:        "bg-blue-50 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  status_change: "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  blocked:       "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  tool_gap:      "bg-orange-50 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
  skill_gap:     "bg-orange-50 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
  note:          "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};
