// Agent detail — Kanban #1017 AC[3]. /agents/[name] surface for a single agent.
//
// Server Component: fetches the detail SSR via getAgentDetail(name). 404 →
// Next.js notFound() (unknown agent name); any other non-2xx / network error
// re-throws into app/error.tsx — symmetric with /p/[name]/page.tsx. The
// presentational body lives in <AgentDetail>.

import { notFound } from "next/navigation";
import Link from "next/link";

import { getAgentDetail, HttpError } from "@/lib/api";
import { AgentDetail } from "@/components/AgentDetail";
import { AgentDetailActions } from "@/components/AgentDetailActions";

export const dynamic = "force-dynamic";

type Props = { params: { name: string } };

export default async function AgentDetailPage({ params }: Props) {
  const name = decodeURIComponent(params.name);
  let agent;
  try {
    agent = await getAgentDetail(name);
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
  }

  return (
    <main
      data-agent-detail-page
      className="glass-board flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950"
    >
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/agents"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Agents
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="truncate text-base font-semibold text-zinc-900 dark:text-zinc-100">
          {agent.name}
        </span>
        {/* #2481 — edit entry point (client island; pre-fills from this detail). */}
        <div className="ml-auto">
          <AgentDetailActions agent={agent} />
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl">
        <AgentDetail agent={agent} />
      </div>
    </main>
  );
}
