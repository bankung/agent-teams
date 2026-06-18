"use client";

// AgentDetailActions — Kanban #2481. Client island for the /agents/[name]
// header: an "Edit" trigger that owns the edit-mode AgentFormModal open state
// and pre-fills it from the AgentDetail the page already fetched (no extra
// round-trip). The detail page + body (AgentDetail) stay Server / presentational;
// this is the one interactive leaf on that route.

import { useState } from "react";

import type { AgentDetail } from "@/lib/api";
import { AgentFormModal } from "./AgentFormModal";

export function AgentDetailActions({ agent }: { agent: AgentDetail }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded border border-zinc-300 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-edit-agent-trigger
      >
        Edit
      </button>
      <AgentFormModal
        mode="edit"
        agent={agent}
        open={open}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
