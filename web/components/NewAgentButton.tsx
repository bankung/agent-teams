"use client";

// NewAgentButton — Kanban #2481. Small client island for the /agents page
// header: a "New agent" trigger that owns the create-mode AgentFormModal open
// state. The gallery page is a Server Component, so the create entry point is
// this leaf island (mirrors how NewTaskModal renders its own trigger button).

import { useState } from "react";

import { Icon } from "./Icon";
import { AgentFormModal } from "./AgentFormModal";

export function NewAgentButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded border border-zinc-300 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-new-agent-trigger
      >
        <Icon name="add-task" size={14} aria-hidden />
        <span>New agent</span>
      </button>
      <AgentFormModal mode="create" open={open} onClose={() => setOpen(false)} />
    </>
  );
}
