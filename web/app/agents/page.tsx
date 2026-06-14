// Agent gallery — Kanban #1017 AC[1][2]. Browse every Claude Code agent
// (.claude/agents/*.md) as a responsive card grid with sort + filter controls.
//
// Server Component: fetches the listing SSR via getAgents() (platform-level,
// no X-Project-Id), mirroring the dashboard / inbox server-fetch convention.
// The interactive shell (sort + filter state) lives in the <AgentGallery>
// client component, which operates purely client-side over the SSR'd data —
// no client fetch, so no async-fetch RTL race (cf. FE-determinism #1310).
//
// Empty state: API returns []. Error state: any non-2xx / network failure is
// caught here and rendered inline (the gallery is a leaf surface — we don't
// want a backend hiccup to throw into app/error.tsx and blank the whole page).

import Link from "next/link";

import { getAgents, type AgentSummary } from "@/lib/api";
import { AgentGallery } from "@/components/AgentGallery";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  let agents: AgentSummary[] | null = null;
  let errorMessage: string | null = null;
  try {
    agents = await getAgents();
  } catch (e) {
    errorMessage = e instanceof Error ? e.message : "Failed to load agents";
  }

  const invalidCount = agents?.filter((a) => !a.valid).length ?? 0;

  return (
    <main
      data-agents-page
      className="flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950"
    >
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/dashboard"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Dashboard
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Agents
        </span>
        {agents ? (
          <span className="ml-1.5 text-[11px] font-normal text-zinc-500 dark:text-zinc-400 tabular-nums">
            ({agents.length})
          </span>
        ) : null}
        {invalidCount > 0 ? (
          <span
            data-agents-invalid-count
            className="ml-1.5 inline-flex items-center rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-700 dark:bg-red-900/40 dark:text-red-200"
            title={`${invalidCount} agent${invalidCount === 1 ? "" : "s"} with blocking validation errors`}
          >
            {invalidCount} invalid
          </span>
        ) : null}
      </header>

      <div className="mx-auto w-full max-w-6xl">
        {errorMessage ? (
          <p
            data-agents-error
            className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
          >
            Couldn’t load agents: {errorMessage}
          </p>
        ) : agents && agents.length === 0 ? (
          <p
            data-agents-empty
            className="rounded border border-zinc-200 bg-zinc-50 p-4 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-400"
          >
            No agents found. Agent definitions live in{" "}
            <code className="font-mono">.claude/agents/*.md</code>.
          </p>
        ) : agents ? (
          <AgentGallery agents={agents} />
        ) : null}
      </div>
    </main>
  );
}
